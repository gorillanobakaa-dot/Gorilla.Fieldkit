"""Unexplained shutdowns, investigated the same way every time (plan P20).

    fieldkit power investigate [--since YYYY-MM-DD] [--json]

Replays the checks done by hand on 2026-11-09, then ranks the suspects:
  1 the "cause unknown" cuts (power incidents)            2 the thermal journal's last 3 minutes before each cut
  3 battery health (design vs full-charge mWh, cycles)    4 BIOS and model
  5 vendor power daemons + the power-plan reset storm     6 services, drivers and updates installed since the first cut
  7 fan control (TPFanControl writes the embedded controller): state, dates, mode, running at each cut
Windows reads 3-7 with PowerShell/powercfg; Linux and macOS read 1, 2 and 4 with their own sources and say
not-this-platform for the rest. Exit 3 when a suspect is found or a cut stays unexplained, 0 with no unknown cut.
The rules are pure functions (`rank`) so each is tested with the numbers of 2026-11-09.
"""
import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import incidents

WINDOW_S = 180               # the last 3 minutes before a cut
COOL_C = 50.0                # a cut below this is not heat ...
LIGHT_LOAD = 40.0            # ... and a cut below this load is not load
HOT_C = 90.0                 # at or above: heat is a suspect (between COOL_C and this: unproven)
BUSY_LOAD = 90.0             # at or above: load is a suspect
MIN_HEALTH = 70.0            # battery full-charge / design, percent
STORM_MIN = 10               # scheme writes per day by one daemon
DAEMON = re.compile(r"Dynamic Tuning|Innovation Platform|DTT|ipf", re.I)
DAEMON_PROCS = ("ipf_uf.exe", "wudfhost.exe", "powercfg.exe")
NOT_THIS = "not-this-platform"

FAN_STOP = "Stop-Service TPFanControl; sc.exe config TPFanControl start= demand"
FAN_UNDO = "sc.exe config TPFanControl start= auto; Start-Service TPFanControl"

PS = r"""
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$s=[datetime]::Parse('%SINCE%')
$o=@{}
$b=Get-CimInstance Win32_BIOS; $c=Get-CimInstance Win32_ComputerSystem
$o.machine=@{bios=[string]$b.SMBIOSBIOSVersion;date=if($b.ReleaseDate){$b.ReleaseDate.ToString('yyyy-MM-dd')}else{''};model=[string]$c.Model}
$o.daemons=@(Get-Service | ?{ $_.DisplayName -match 'Dynamic Tuning|Innovation Platform|DTT|ipf' -or $_.Name -match 'DTT|ipf|Dynamic' } |
  %{ [pscustomobject]@{name=$_.Name;display=$_.DisplayName;state=[string]$_.Status;start=[string]$_.StartType} })
$ch=@()
Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Service Control Manager';Id=7045;StartTime=$s} | %{
  $x=[xml]$_.ToXml(); $d=@{}; foreach($n in $x.Event.EventData.Data){$d[$n.Name]=[string]$n.'#text'}
  $ch+=[pscustomobject]@{t=$_.TimeCreated.ToUniversalTime().ToString('o');kind='7045';name=$d.ServiceName;detail=$d.ServiceType+' '+$d.ImagePath} }
Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WindowsUpdateClient';Id=19;StartTime=$s} | %{
  $ch+=[pscustomobject]@{t=$_.TimeCreated.ToUniversalTime().ToString('o');kind='WU19';name=([string]$_.Message -replace '^.*?:\s*','');detail=''} }
$o.changes=$ch
$f=@{}
$sv=Get-CimInstance Win32_Service -Filter "Name='TPFanControl'"
if($sv){ $f.service=$true; $f.state=[string]$sv.State; $f.start=[string]$sv.StartMode
  $p=if([string]$sv.PathName -match '^"([^"]+)"'){$Matches[1]}else{([string]$sv.PathName -split '\s+')[0]}
  if(Test-Path $p){ $f.exe=(Get-Item $p).LastWriteTime.ToString('yyyy-MM-dd'); $ini=Join-Path (Split-Path $p) 'TPFanControl.ini'
    if(Test-Path $ini){ $f.ini=(Get-Item $ini).LastWriteTime.ToString('yyyy-MM-dd'); $m=Select-String -Path $ini -Pattern '^Active=(\d+)' | select -First 1; if($m){$f.active=$m.Matches[0].Groups[1].Value} } }
} else { $f.service=$false }
$h=@()
Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Service Control Manager';Id=7036;StartTime=$s.AddDays(-1)} | ?{ $_.Message -match 'TPFanControl' } | %{
  $h+=[pscustomobject]@{t=$_.TimeCreated.ToUniversalTime().ToString('o');state=if($_.Message -match 'running'){'running'}else{'stopped'}} }
$f.history=$h
$o.fan=$f
$o|ConvertTo-Json -Depth 6 -Compress
"""


def _list(v):
    return [] if v is None else v if isinstance(v, list) else [v]


# ---- readers --------------------------------------------------------------------------------------------------

def read_windows(since, run=subprocess.run):
    if sys.platform != "win32":
        return {}
    script = PS.replace("%SINCE%", time.strftime("%Y-%m-%d", time.localtime(since)))
    r = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300)
    try:
        j = json.loads(r.stdout)
    except ValueError:
        return {"error": (r.stderr or r.stdout)[:300]}
    for k in ("daemons", "changes"):
        j[k] = _list(j.get(k))
    fan = j.get("fan") or {}
    fan["history"] = [dict(h, t=incidents._ts(h["t"])) for h in _list(fan.get("history"))]
    for c in j["changes"]:
        c["t"] = incidents._ts(c["t"])
    return j


def parse_battery(xml):
    """powercfg /batteryreport /xml text -> {design_mwh, full_mwh, cycles, health_pct}; {} when absent."""
    def num(tag):
        m = re.search(r"<%s>\s*(\d+)\s*</%s>" % (tag, tag), xml)
        return int(m.group(1)) if m else None
    d, f, c = num("DesignCapacity"), num("FullChargeCapacity"), num("CycleCount")
    if not d or not f:
        return {}
    return {"design_mwh": d, "full_mwh": f, "cycles": c, "health_pct": round(100.0 * f / d, 1)}


def read_battery(run=subprocess.run):
    if sys.platform != "win32":
        return {}
    import tempfile
    with tempfile.TemporaryDirectory(prefix="fk-bat-") as d:
        out = Path(d) / "battery.xml"
        try:
            run(["powercfg", "/batteryreport", "/xml", "/output", str(out)], capture_output=True, text=True, timeout=60)
            return parse_battery(out.read_text(encoding="utf-8", errors="replace"))
        except (OSError, subprocess.SubprocessError):
            return {}


def read_machine_posix(run=subprocess.run):
    if sys.platform.startswith("linux"):
        def dmi(n):
            try:
                return Path("/sys/class/dmi/id", n).read_text().strip()
            except OSError:
                return ""
        return {"bios": dmi("bios_version"), "date": dmi("bios_date"), "model": dmi("product_name")}
    if sys.platform == "darwin":
        try:
            o = run(["sysctl", "-n", "hw.model"], capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            o = ""
        return {"bios": "", "date": "", "model": o}
    return {}


# ---- evidence -------------------------------------------------------------------------------------------------

def journal_modes(folder=None):
    """-> [(epoch, mode)]: the journal's mode column is `no-status` when TPFanControl was not running (no status file)."""
    from ..thermal import journal
    rows = []
    for p in sorted(Path(folder or journal.folder()).glob("journal-*.csv")):
        try:
            for r in csv.DictReader(p.open(encoding="ascii", errors="replace")):
                try:
                    rows.append((time.mktime(time.strptime(r["time"], "%Y-%m-%dT%H:%M:%S")), (r.get("mode") or "").strip()))
                except (ValueError, KeyError, TypeError):
                    continue
        except OSError:
            continue
    return rows


def fan_from_journal(modes, t, window=WINDOW_S):
    """'running' | 'stopped' | None from the last journal line in the window before t."""
    v = [m for m in modes if t - window <= m[0] <= t]
    if not v:
        return None
    return "stopped" if max(v)[1] in ("no-status", "") else "running"


def thermal_windows(unknown, jrows, window=WINDOW_S, modes=()):
    """-> per unknown cut: {t, t_text, peak_c, peak_load, samples, fan} over the `window` seconds before it."""
    out = []
    for i in unknown:
        v = [(c, ld) for ts, c, ld in jrows if i["t"] - window <= ts <= i["t"]]
        cs = [c for c, _ in v if c is not None]
        ls = [ld for _, ld in v if ld is not None]
        out.append({"t": i["t"], "t_text": i["t_text"], "peak_c": max(cs) if cs else None,
                    "peak_load": max(ls) if ls else None, "samples": len(v), "fan": fan_from_journal(modes, i["t"], window)})
    return out


def fan_state_at(history, t):
    """'running' | 'stopped' | None from the service's 7036 history before t."""
    prior = [h for h in history if h["t"] <= t]
    return max(prior, key=lambda h: h["t"])["state"] if prior else None


def gather(since):
    """Everything read from the machine; rank() judges it."""
    inc = incidents.collect(since)
    unknown = [i for i in inc["incidents"] if i["class"] in incidents.OPEN_CLASSES]
    jrows = incidents.journal_rows()
    ev = {"since": since, "incidents": len(inc["incidents"]), "unknown": unknown,
          "thermal": thermal_windows(unknown, jrows, modes=journal_modes()), "resets": inc["scheme_resets"], "platform": sys.platform}
    if sys.platform == "win32":
        w = read_windows((min(i["t"] for i in unknown) if unknown else since) - 7 * 86400)
        ev.update(machine=w.get("machine") or {}, daemons=w.get("daemons", []), changes=w.get("changes", []),
                  fan=w.get("fan") or {}, battery=read_battery(), error=w.get("error") or inc.get("error"))
    else:
        ev.update(machine=read_machine_posix(), daemons=None, changes=None, fan=None, battery=None, error=inc.get("error"))
    return ev


# ---- rules (pure) ---------------------------------------------------------------------------------------------

def _suspect(name, kind, verdict, weight, evidence, steps=()):
    return {"suspect": name, "group": kind, "verdict": verdict, "weight": weight, "evidence": evidence, "steps": list(steps)}


def rule_heat_load(windows):
    """A cut below COOL_C or below LIGHT_LOAD is neither heat nor load; at HOT_C / BUSY_LOAD it is a suspect; between, unproven."""
    if not windows:
        return [_suspect("heat", "heat", "no data", 0, ["no unknown cut"]), _suspect("load", "load", "no data", 0, ["no unknown cut"])]
    num = lambda v: "-" if v is None else format(v, "g")
    lines = [f"{w['t_text']}: {num(w['peak_c'])} C, {num(w['peak_load'])} % load in the last 3 min" for w in windows]
    peak_c = max((w["peak_c"] for w in windows if w["peak_c"] is not None), default=None)
    peak_l = max((w["peak_load"] for w in windows if w["peak_load"] is not None), default=None)
    cool = [w for w in windows if (w["peak_c"] or 0) < COOL_C or (w["peak_load"] or 0) < LIGHT_LOAD]
    if peak_c is not None and peak_c >= HOT_C:
        heat = _suspect("heat", "heat", "suspect", 80, lines, ["clean the fans and vents; watch the thermal log through a build"])
    elif len(cool) == len(windows):
        heat = _suspect("heat", "heat", "ruled out", 0, lines + [f"not heat: every cut below {COOL_C:g} C or below {LIGHT_LOAD:g} % load"])
    else:
        heat = _suspect("heat", "heat", "unproven", 10, lines + [f"warm ({num(peak_c)} C) but under {HOT_C:g} C and under load"])
    if peak_l is not None and peak_l >= BUSY_LOAD:
        load = _suspect("load", "load", "suspect", 40, lines, ["note what runs at full load; try a lower power limit"])
    elif len(cool) == len(windows):
        load = _suspect("load", "load", "ruled out", 0, lines + [f"not load: every cut below {COOL_C:g} C or below {LIGHT_LOAD:g} % load"])
    else:
        load = _suspect("load", "load", "unproven", 10, lines + [f"{num(peak_l)} % load before a cut, under {BUSY_LOAD:g} %"])
    return [heat, load]


def rule_battery(bat):
    if bat is None:
        return _suspect("battery", "battery", NOT_THIS, 0, [NOT_THIS])
    if not bat:
        return _suspect("battery", "battery", "no data", 0, ["powercfg gave no battery report (no battery?)"])
    ev = [f"design {bat['design_mwh']:,} mWh, full charge {bat['full_mwh']:,} mWh ({bat['health_pct']:g} %), {bat['cycles']} cycles"]
    if bat["health_pct"] < MIN_HEALTH:
        return _suspect("battery", "battery", "suspect", 75, ev, ["replace the battery (Lenovo), then run on AC without it"])
    return _suspect("battery", "battery", "ruled out", 0, ev + [f"health at or above {MIN_HEALTH:g} %"])


def rule_daemons(daemons, resets):
    if daemons is None:
        return _suspect("vendor power daemon", "vendor daemon", NOT_THIS, 0, [NOT_THIS])
    names = [f"{d['display']} ({d['state']}/{d['start']})" for d in daemons]
    by = {}
    for r in resets:
        by.setdefault(r["day"], {})[r["process"]] = r["n"]
    storms = []
    for day, procs in sorted(by.items()):
        vend = {p: n for p, n in procs.items() if p.lower() in DAEMON_PROCS}
        if sum(vend.values()) >= STORM_MIN:
            storms.append(f"{day}: UserModePowerService 12 x{sum(vend.values())} (" + ", ".join(f"{p} {n}" for p, n in vend.items()) + ")")
    ev = ["services: " + ("; ".join(names) if names else "none matching Dynamic Tuning|Innovation Platform|DTT|ipf")] + storms
    if storms:
        return _suspect("vendor power daemon", "vendor daemon", "suspect", 60, ev,
                        ["update the BIOS and Intel Dynamic Tuning from Lenovo; if it persists, disable the Dynamic Tuning service for a day"])
    return _suspect("vendor power daemon", "vendor daemon", "ruled out", 0, ev + ["no power-plan reset storm"])


def rule_changes(changes, first_cut):
    """Drivers and non-sandbox services installed in the 7 days before the first cut; Security Intelligence is ignored."""
    if changes is None:
        return _suspect("recent driver/service change", "change", NOT_THIS, 0, [NOT_THIS])
    lo = (first_cut or 0) - 7 * 86400
    kept, hits = [], []
    for c in changes:
        if first_cut is not None and c["t"] < lo:
            continue
        if c["kind"] == "WU19" and re.search(r"Security Intelligence", c["name"], re.I):
            continue
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(c["t"]))
        kept.append(f"{when} {c['kind']} {c['name']}")
        if (c["kind"] == "7045" and re.search(r"driver", c.get("detail", ""), re.I)) or \
           (c["kind"] == "WU19" and re.search(r"driver|firmware|BIOS", c["name"], re.I)):
            hits.append(f"{when} {c['kind']} {c['name']}")
    if hits:
        return _suspect("recent driver/service change", "change", "suspect", 50, hits,
                        ["roll the named driver back (Device Manager > Roll Back Driver) and watch a day"])
    return _suspect("recent driver/service change", "change", "ruled out", 0,
                    (kept[:6] + [f"{len(kept)} change(s) since the first cut minus 7 days, none a driver or firmware"]) if kept
                    else ["no service, driver or update installed since the first cut minus 7 days"])


def rule_fan(fan, cuts):
    if fan is None:
        return _suspect("fan control (EC writer)", "EC writer", NOT_THIS, 0, [NOT_THIS])
    if not fan.get("service"):
        return _suspect("fan control (EC writer)", "EC writer", "ruled out", 0, ["no TPFanControl service installed"])
    files = f"exe {fan.get('exe') or '?'}, ini {fan.get('ini') or '?'}, Active={fan.get('active') or '?'}"
    now = f"service {fan.get('state')}/{fan.get('start')}; {files}"
    at = [(c["t_text"], c.get("fan") or fan_state_at(fan.get("history") or [], c["t"])) for c in cuts]
    ev = [now] + [f"{t}: TPFanControl {s or 'unknown (no thermal journal line or service record before the cut)'}" for t, s in at]
    running = [t for t, s in at if s == "running"]
    if running or str(fan.get("state")).lower() == "running":
        who = f"was running at {len(running)} of {len(at)} cut(s)" if at else "is running now"
        return _suspect("fan control (EC writer)", "EC writer", "suspect", 70, ev + [f"it writes the embedded controller and {who}"],
                        [f"a day with it stopped (admin PowerShell): {FAN_STOP}", f"undo: {FAN_UNDO}"])
    stopped = bool(at) and all(s == "stopped" for _, s in at)
    return _suspect("fan control (EC writer)", "EC writer", "ruled out" if stopped else "unproven", 20 if not stopped else 0, ev)


def rank(ev):
    """Evidence -> ranked suspect rows. Hardware is the residual: first when nothing else is suspect and a cut stays unexplained."""
    cuts = ev["thermal"]
    unknown = ev["unknown"]
    first = min((i["t"] for i in unknown), default=None)
    rows = rule_heat_load(cuts) + [rule_battery(ev.get("battery")), rule_daemons(ev.get("daemons"), ev.get("resets", [])),
                                   rule_changes(ev.get("changes"), first), rule_fan(ev.get("fan"), cuts)]
    others = any(r["verdict"] == "suspect" for r in rows)
    hw_ev = [f"{len(unknown)} cut(s) with no logged cause: " + ", ".join(i["t_text"] for i in unknown)] if unknown else ["no unknown cut"]
    if unknown:
        hw_ev.append("nothing else explains it" if not others else "other suspects exist; hardware stays possible")
    rows.append(_suspect("hardware: charger, cable, board", "hardware", "suspect" if unknown else "no data",
                         (30 if others else 90) if unknown else 0, hw_ev,
                         ["Lenovo hardware diagnostics: power off, press F10 at the logo, run the full test",
                          "run a day on another charger and cable (note the date)"] if unknown else []))
    order = {"suspect": 0, "unproven": 1, "no data": 2, "ruled out": 3, NOT_THIS: 4}
    return sorted(rows, key=lambda r: (order.get(r["verdict"], 5), -r["weight"]))


def owner_steps(rows):
    seen, out = set(), []
    for r in rows:
        if r["verdict"] == "suspect":
            for s in r["steps"]:
                if s not in seen:
                    seen.add(s)
                    out.append(s)
    return out


def table(res):
    L = [f"{'#':>2}  {'verdict':12}  suspect"]
    for n, r in enumerate(res["suspects"], 1):
        L.append(f"{n:>2}  {r['verdict']:12}  {r['suspect']}")
        L.extend("      " + e for e in r["evidence"])
    m = res["evidence"].get("machine") or {}
    if m:
        L.append(f"machine: {m.get('model') or '?'}, BIOS {m.get('bios') or '?'} ({m.get('date') or '?'})")
    if res["owner_steps"]:
        L.append("owner steps:")
        L.extend(f"  {n}. {s}" for n, s in enumerate(res["owner_steps"], 1))
    return L


def investigate(since=None, ev=None):
    t = incidents.parse_since(since)
    ev = ev if ev is not None else gather(t)
    rows = rank(ev)
    return {"suspects": rows, "owner_steps": owner_steps(rows), "evidence": {k: v for k, v in ev.items() if k != "unknown"},
            "unknown_cuts": [i["t_text"] for i in ev["unknown"]]}


def run(since=None, as_json=False, out=print, ev=None):
    res = investigate(since, ev)
    if as_json:
        out(json.dumps(res, indent=1, default=str))
    else:
        for ln in table(res):
            out(ln)
        if res["evidence"].get("error"):
            out("reader error: " + str(res["evidence"]["error"]))
        out(f"{len(res['unknown_cuts'])} unknown cut(s); top suspect: {res['suspects'][0]['suspect']} ({res['suspects'][0]['verdict']}).")
        out("NEXT: " + ("nothing to do" if not res["unknown_cuts"] else "do the owner steps above, one at a time, then run it again"))
    return 3 if res["unknown_cuts"] else 0
