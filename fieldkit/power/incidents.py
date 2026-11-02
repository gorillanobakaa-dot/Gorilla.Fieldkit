"""Every unexpected shutdown, cutoff or sleep has a named cause (plan R9).

    fieldkit power incidents [--since YYYY-MM-DD] [--json]

Readers (each checks sys.platform and returns [] elsewhere): Windows reads the System log with one Get-WinEvent call
(Kernel-Power 41/42/107/187/506/507/566, EventLog 6005/6006/6008, User32 1074, Power-Troubleshooter 1, and
UserModePowerService 12 counted per process per day); Linux reads journalctl; macOS reads `pmset -g log`.
Each incident gets the thermal journal's peak CPU C in the 5 minutes before and, on Windows, the battery state.
Class: heat | power-plan reset | program sleep request (PROCESS) | battery | button/lid | standby (REASON) | planned |
unexpected cut (cause unknown) | unknown. Exit 3 when any is "unknown" or "unexpected cut (cause unknown)"; each such
one is a defect row in state/coordination/ZERO-OPEN-PROBLEMS.md, once per incident.
Real cut time: a 6008 states the log's last heartbeat; the real cut is the last thermal journal line before that boot
when it is later.
"""
import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HOT_C = 90.0
LOW_BATTERY_PCT = 5
WINDOW_S = 300
NEAR_S = 120         # events this close are one incident (187 + 566 + 42 + 506)
UNKNOWN_CLASSES = ("unknown", "unexpected cut (cause unknown)")

PS = r"""
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$s=[datetime]::Parse('%SINCE%')
$o=@()
$f=@(@{P='Microsoft-Windows-Kernel-Power';I=41,42,107,187,506,507,566},@{P='EventLog';I=6005,6006,6008},
     @{P='User32';I=1074},@{P='Microsoft-Windows-Power-Troubleshooter';I=1})
foreach($x in $f){ Get-WinEvent -FilterHashtable @{LogName='System';ProviderName=$x.P;Id=$x.I;StartTime=$s} | %{
  $d=@{}; $xml=[xml]$_.ToXml(); foreach($n in $xml.Event.EventData.Data){ if($n.Name){$d[$n.Name]=[string]$n.'#text'} }
  $o+=[pscustomobject]@{t=$_.TimeCreated.ToUniversalTime().ToString('o');id=$_.Id;prov=$_.ProviderName;msg=[string]$_.Message;data=$d} } }
$u=@{}
Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-UserModePowerService';Id=12;StartTime=$s} | %{
  $xml=[xml]$_.ToXml(); $h=@{}; foreach($n in $xml.Event.EventData.Data){ $h[$n.Name]=[string]$n.'#text' }
  $k=$_.TimeCreated.ToString('yyyy-MM-dd')+'|'+(Split-Path $h.ProcessPath -Leaf)+'|'+[int]($h.OldSchemeGuid -ne $h.NewSchemeGuid)
  $u[$k]=1+[int]$u[$k]
  if($h.OldSchemeGuid -ne $h.NewSchemeGuid){ $o+=[pscustomobject]@{t=$_.TimeCreated.ToUniversalTime().ToString('o');id=12;prov='UMPS';msg='scheme change';data=$h} } }
[pscustomobject]@{events=$o;resets=$u.GetEnumerator()|%{[pscustomobject]@{k=$_.Key;n=$_.Value}}}|ConvertTo-Json -Depth 6 -Compress
"""


def _ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def read_windows(since, run=subprocess.run):
    """-> ({events: [{t, id, prov, msg, data}], resets: [{day, process, changed, n}]}) from one PowerShell call."""
    if sys.platform != "win32":
        return {"events": [], "resets": []}
    script = PS.replace("%SINCE%", time.strftime("%Y-%m-%d", time.localtime(since)))
    r = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300)
    try:
        j = json.loads(r.stdout)
    except ValueError:
        return {"events": [], "resets": [], "error": (r.stderr or r.stdout)[:300]}
    ev = j.get("events") or []
    if isinstance(ev, dict):
        ev = [ev]
    for e in ev:
        e["t"] = _ts(e["t"])
    rs = j.get("resets") or []
    if isinstance(rs, dict):
        rs = [rs]
    resets = []
    for x in rs:
        day, proc, changed = (x["k"].split("|") + ["", "", "0"])[:3]
        resets.append({"day": day, "process": proc, "changed": changed == "1", "n": x["n"]})
    return {"events": ev, "resets": resets}


def read_linux(since, run=subprocess.run):
    """journalctl: the last line of every earlier boot (no shutdown target = a cut) and the sleep/button/heat lines."""
    if not sys.platform.startswith("linux"):
        return []
    out = []
    try:
        s = time.strftime("%Y-%m-%d", time.localtime(since))
        pat = r"PM: suspend entry|Lid closed|Power key pressed|Suspending system|critical temperature|thermal.*shut"
        r = run(["journalctl", "--no-pager", "-o", "short-unix", "--since", s, "-g", pat], capture_output=True,
                text=True, timeout=120)
        for ln in r.stdout.splitlines():
            m = re.match(r"(\d+\.\d+) \S+ (.*)", ln)
            if m:
                out.append({"t": float(m.group(1)), "line": m.group(2)[:200]})
        b = run(["journalctl", "--list-boots", "--no-pager"], capture_output=True, text=True, timeout=60).stdout
        for ln in b.splitlines():
            m = re.match(r"\s*(-\d+)\s+\S+\s+\S+ (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \S+\s*[-—]+\s*\S+ (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", ln)
            if not m or int(m.group(1)) == 0:
                continue
            end = time.mktime(time.strptime(m.group(3), "%Y-%m-%d %H:%M:%S"))
            if end < since:
                continue
            tail = run(["journalctl", "-b", m.group(1), "--no-pager", "-n", "40", "-o", "cat"], capture_output=True,
                       text=True, timeout=60).stdout
            clean = bool(re.search(r"Reached target.*(Shutdown|Reboot|Power-Off|Halt)|System Power Off|Journal stopped", tail, re.I))
            out.append({"t": end, "line": "boot ended " + ("cleanly" if clean else "without a shutdown line"), "boot": m.group(1), "clean": clean})
    except (OSError, subprocess.SubprocessError):
        return out
    return out


def read_macos(since, run=subprocess.run):
    """pmset -g log: Sleep / Wake / Shutdown Cause lines."""
    if sys.platform != "darwin":
        return []
    try:
        r = run(["pmset", "-g", "log"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for ln in r.stdout.splitlines():
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) [+-]\d{4}\s+(Sleep|Shutdown Cause|Wake)\b(.*)", ln)
        if m and m.group(2) != "Wake":
            t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            if t >= since:
                out.append({"t": t, "kind": m.group(2), "line": m.group(3).strip()[:200]})
    return out


# ---- thermal journal ------------------------------------------------------------------------------------------

def journal_rows(folder=None):
    """-> [(epoch, cpu_c|None)] of every day file, sorted."""
    from ..thermal import journal
    rows = []
    for p in sorted(Path(folder or journal.folder()).glob("journal-*.csv")):
        try:
            for r in csv.DictReader(p.open(encoding="ascii", errors="replace")):
                try:
                    t = time.mktime(time.strptime(r["time"], "%Y-%m-%dT%H:%M:%S"))
                except (ValueError, KeyError, TypeError):
                    continue
                try:
                    c = float(r.get("cpu_c") or "")
                except ValueError:
                    c = None
                try:
                    load = float(r.get("load_pct") or "")
                except ValueError:
                    load = None
                rows.append((t, c, load))
        except OSError:
            continue
    rows.sort(key=lambda x: x[0])
    return rows


def peak_before(rows, t, window=WINDOW_S):
    """-> (peak C, samples) in the window before t; (None, 0) with no data."""
    v = [c for ts, c, _ in rows if t - window <= ts <= t and c is not None]
    return (max(v) if v else None), len(v)


def last_line_before(rows, t):
    prior = [ts for ts, _, _ in rows if ts <= t]
    return max(prior) if prior else None


# ---- battery --------------------------------------------------------------------------------------------------

def battery_usage(run=subprocess.run, tmp=None):
    """Windows `powercfg /batteryreport /xml` Recent usage -> [(epoch, 'AC'|'Battery', charge %|None)]; [] when unavailable."""
    if sys.platform != "win32":
        return []
    import tempfile
    out = Path(tmp or tempfile.gettempdir()) / f"fk-battery-{int(time.time())}.xml"
    try:
        run(["powercfg", "/batteryreport", "/xml", "/duration", "14", "/output", str(out)], capture_output=True, text=True, timeout=60)
        text = out.read_text(encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []
    finally:
        try:
            out.unlink()
        except OSError:
            pass
    rows = []
    for m in re.finditer(r"<UsageEntry\b([^>]*)>", text):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        try:
            t = datetime.fromisoformat(a["Timestamp"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError):
            continue
        cap, full = a.get("ChargeCapacity"), a.get("FullChargeCapacity")
        pct = None
        try:
            pct = round(100 * int(cap) / int(full))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        rows.append((t, "AC" if a.get("Ac", "").lower() in ("1", "true") else "Battery", pct))
    return sorted(rows)


def battery_at(usage, t):
    prior = [u for u in usage if u[0] <= t + 5]
    return (prior[-1][1], prior[-1][2]) if prior else (None, None)


# ---- classification -------------------------------------------------------------------------------------------

def _stated_6008(msg, year_of):
    m = re.search(r"at (\d{1,2}:\d\d:\d\d(?: [AP]M)?) on \D{0,3}(\d{1,2})/\D{0,3}(\d{1,2})", re.sub(r"[^\x00-\x7f]", "", msg or ""))
    if not m:
        return None
    try:
        fmt = "%I:%M:%S %p" if m.group(1).endswith("M") else "%H:%M:%S"
        hms = time.strptime(m.group(1), fmt)
        return time.mktime((year_of, int(m.group(2)), int(m.group(3)), hms.tm_hour, hms.tm_min, hms.tm_sec, 0, 0, -1))
    except ValueError:
        return None


def _basename(path):
    return re.split(r"[\\/]", path or "")[-1]


def _sleep_class(reason):
    r = (reason or "").lower()
    if "application api" in r:
        return None
    if "lid" in r or "button" in r or "power key" in r:
        return "button/lid"
    if "battery" in r and "austerity" not in r:
        return "battery"
    return "standby (%s)" % (reason.strip().rstrip(".") if reason else "no reason logged")


def classify_windows(events, jrows, usage=(), now=None):
    """Pure: normalized events + journal rows + battery usage -> incidents, oldest first."""
    ev = sorted(events, key=lambda e: e["t"])
    used = set()
    incidents = []

    def near(e, ids, span=NEAR_S):
        return [x for x in ev if x["id"] in ids and abs(x["t"] - e["t"]) <= span and x is not e]

    def finish(inc, t, cut=None):
        peak, n = peak_before(jrows, cut or t)
        bat, pct = battery_at(usage, t)
        inc.update(t=t, t_text=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), peak_c=peak, journal_samples=n,
                   power_source=bat, battery_pct=pct)
        incidents.append(inc)

    for e in ev:
        if id(e) in used:
            continue
        if e["id"] == 187:
            used.update(id(x) for x in near(e, (42, 506, 566, 187)))
            proc = _basename(e["data"].get("ApiCallerName"))
            trig = next((re.search(r"Reason (\w+)", x["msg"]).group(1) for x in near(e, (566,))
                         if re.search(r"Reason (\w+)", x["msg"])), None)
            inc = {"class": f"program sleep request ({proc})", "what": "Kernel-Power 187", "process": proc,
                   "sub_trigger": trig or "not logged", "detail": f"SystemAction {e['data'].get('SystemAction')}"}
            finish(inc, e["t"])
        elif e["id"] in (42, 506):
            used.add(id(e))
            near_ids = near(e, (42, 506, 566, 187))
            used.update(id(x) for x in near_ids)
            if any(x["id"] == 187 for x in near_ids):
                continue
            rs = re.search(r"Reason:?\s*([^\r\n]+)", e["msg"] or "")
            reason = rs.group(1) if rs else ""
            if e["id"] == 42 and not rs:
                reason = ""
            cls = _sleep_class(reason)
            if cls is None:
                cls = "program sleep request (unnamed API caller)"
            finish({"class": cls, "what": f"Kernel-Power {e['id']}", "detail": reason}, e["t"])
        elif e["id"] in (41, 6008):
            grp = [e] + [x for x in ev if x["id"] in (41, 6008) and x is not e and abs(x["t"] - e["t"]) <= 600]
            used.update(id(x) for x in grp)
            e6 = next((x for x in grp if x["id"] == 6008), None)
            logged = min(x["t"] for x in grp)
            boots = [x["t"] for x in ev if x["id"] == 6005 and logged - 600 <= x["t"] <= logged + 600]
            boot_t = max(boots) if boots else logged
            stated = _stated_6008(e6["msg"], time.localtime(e6["t"]).tm_year) if e6 else None
            if stated is not None and stated > e6["t"]:                      # year rollover
                stated = _stated_6008(e6["msg"], time.localtime(e6["t"]).tm_year - 1)
            last = last_line_before(jrows, boot_t - 1)
            cut, note = stated, "stated by the event log"
            if last is not None and (cut is None or last > cut) and boot_t - last < 12 * 3600:
                cut, note = last, "last thermal journal line (later than the log's last heartbeat)"
            if cut is None:
                cut, note = logged, "log time"
            peak, n = peak_before(jrows, cut)
            bat, pct = battery_at(usage, cut)
            if peak is not None and peak >= HOT_C:
                cls = "heat"
            elif bat == "Battery" and pct is not None and pct <= LOW_BATTERY_PCT:
                cls = "battery"
            elif any(x["id"] == 12 and 0 <= cut - x["t"] <= 60 for x in ev):
                cls = "power-plan reset"
            else:
                cls = "unexpected cut (cause unknown)"
            inc = {"class": cls, "what": "EventLog 6008" + (" + Kernel-Power 41" if any(x["id"] == 41 for x in grp) else ""),
                   "detail": f"cut time: {note}; logged {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(logged))}"}
            finish(inc, cut)
            if cls == "unexpected cut (cause unknown)" and peak is not None:
                incidents[-1]["class_note"] = f"cause unknown, not heat (peak {peak:g} C)"
            elif cls == "unexpected cut (cause unknown)":
                incidents[-1]["class_note"] = "cause unknown; no journal"
        elif e["id"] == 1074:
            used.add(id(e))
            finish({"class": "planned", "what": "User32 1074", "detail": (e["msg"] or "")[:120].replace("\n", " ")}, e["t"])
    incidents.sort(key=lambda x: x["t"])
    return incidents


def classify_linux(lines, jrows):
    inc = []
    for x in sorted(lines, key=lambda x: x["t"]):
        ln = x["line"]
        if "clean" in x:
            if x["clean"]:
                continue
            cls = "unexpected cut (cause unknown)"
        elif re.search(r"critical temperature|thermal.*shut", ln, re.I):
            cls = "heat"
        elif re.search(r"Lid closed|Power key", ln):
            cls = "button/lid"
        elif re.search(r"suspend entry|Suspending", ln):
            cls = "standby (suspend)"
        else:
            cls = "unknown"
        peak, n = peak_before(jrows, x["t"])
        if cls == "unexpected cut (cause unknown)" and peak is not None and peak >= HOT_C:
            cls = "heat"
        inc.append({"class": cls, "what": "journalctl", "detail": ln, "t": x["t"],
                    "t_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(x["t"])), "peak_c": peak,
                    "journal_samples": n, "power_source": None, "battery_pct": None})
    return inc


def classify_macos(lines, jrows):
    inc = []
    for x in lines:
        ln = x["line"]
        m = re.search(r"due to '([^']*)'", ln) or re.search(r"Cause:?\s*(-?\d+)", ln)
        why = m.group(1) if m else ln
        if x["kind"] == "Shutdown Cause":
            code = re.search(r"-?\d+", ln)
            cls = "planned" if code and code.group(0) in ("3", "5") else "unexpected cut (cause unknown)"
        elif re.search(r"clamshell|lid|power button", why, re.I):
            cls = "button/lid"
        elif re.search(r"battery", why, re.I):
            cls = "battery"
        elif re.search(r"thermal|heat", why, re.I):
            cls = "heat"
        else:
            cls = "standby (%s)" % why
        inc.append({"class": cls, "what": "pmset -g log " + x["kind"], "detail": ln, "t": x["t"],
                    "t_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(x["t"])), "peak_c": None,
                    "journal_samples": 0, "power_source": None, "battery_pct": None})
    return inc


# ---- defect rows ----------------------------------------------------------------------------------------------

def defect_rows(incidents, path=None):
    """One row per unknown incident in ZERO-OPEN-PROBLEMS.md, once per incident (its tag). -> number written."""
    from ..autopilot.goal_peer import problems_path
    p = Path(path or problems_path())
    try:
        body = p.read_text(encoding="utf-8") if p.is_file() else ""
    except OSError:
        body = ""
    n = 0
    for i in incidents:
        if i["class"] not in UNKNOWN_CLASSES:
            continue
        tag = f"[power-incident {i['t_text']}]"
        if tag in body:
            continue
        nums = [int(m) for m in re.findall(r"^\|\s*(\d+)\s*\|", body, re.M)]
        peak = f"peak {i['peak_c']:g} C in the 5 min before" if i.get("peak_c") is not None else "no thermal journal before it"
        row = (f"| {max(nums, default=0) + 1} | {tag} {i['class']} at {i['t_text']} ({i['what']}; {peak}); cause NOT named "
               f"| OPEN (R9) | `fieldkit power incidents --since {i['t_text'][:10]}` |\n")
        if body and not body.endswith("\n"):
            row = "\n" + row
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(row)
        body += row
        n += 1
    return n


# ---- the command ----------------------------------------------------------------------------------------------

def collect(since):
    jrows = journal_rows()
    resets = []
    if sys.platform == "win32":
        w = read_windows(since)
        inc = classify_windows(w["events"], jrows, battery_usage())
        resets = [r for r in w["resets"] if not r["changed"]]
        err = w.get("error")
    elif sys.platform.startswith("linux"):
        inc, err = classify_linux(read_linux(since), jrows), None
    elif sys.platform == "darwin":
        inc, err = classify_macos(read_macos(since), jrows), None
    else:
        inc, err = [], None
    return {"incidents": [i for i in inc if i["t"] >= since], "scheme_resets": resets, "error": err}


def parse_since(v, now=None):
    if not v:
        return (now or time.time()) - 7 * 86400
    return time.mktime(time.strptime(v, "%Y-%m-%d"))


def table(res):
    lines = [f"{'when':19}  {'class':44}  {'peak C':>6}  {'src':7}  what"]
    for i in res["incidents"]:
        pk = "-" if i["peak_c"] is None else f"{i['peak_c']:g}"
        src = (i.get("power_source") or "-") + ("" if i.get("battery_pct") is None else f" {i['battery_pct']}%")
        cls = i.get("class_note") or i["class"]
        lines.append(f"{i['t_text']:19}  {cls[:44]:44}  {pk:>6}  {src:7}  {i['what']}")
    if res["scheme_resets"]:
        by = {}
        for r in res["scheme_resets"]:
            by.setdefault(r["day"], []).append(f"{r['process']} x{r['n']}")
        lines.append("power-plan reset storm (same scheme to itself, UserModePowerService 12; a finding, not an incident):")
        for d in sorted(by):
            lines.append(f"  {d}: " + ", ".join(by[d]))
    return lines


def run(since=None, as_json=False, write_rows=True, out=print):
    t = parse_since(since)
    res = collect(t)
    unknown = [i for i in res["incidents"] if i["class"] in UNKNOWN_CLASSES]
    wrote = defect_rows(res["incidents"]) if write_rows else 0
    if as_json:
        out(json.dumps(dict(res, unknown=len(unknown), rows_written=wrote), indent=1))
    else:
        for ln in table(res):
            out(ln)
        if res["error"]:
            out("reader error: " + res["error"])
        out(f"{len(res['incidents'])} incident(s) since {time.strftime('%Y-%m-%d', time.localtime(t))}, "
            f"{len(unknown)} unknown ({wrote} new defect row(s)).")
        out("NEXT: " + ("nothing to do" if not unknown else "read the defect rows in ZERO-OPEN-PROBLEMS.md; find the cause"))
    return 3 if unknown else 0
