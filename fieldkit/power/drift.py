"""R10: the Lenovo power service (ibmpmsvc.exe) never puts the laptop to sleep during work.

    fieldkit power drift [--days N] [--apply] [--json]

Fails (exit 3) when ibmpmsvc.exe asked for sleep (Kernel-Power 187) in the last N days (default 7) while the machine
was on AC and busy (thermal journal: CPU >= 50 C or load >= 25 % in the 5 minutes before), or when the sleep-button
action on AC (powercfg SBUTTONACTION, the path a Lenovo sleep hot key takes) is not "do nothing".
Reads without admin: powercfg /qh SUB_BUTTONS (SBUTTONACTION, PBUTTONACTION, LIDACTION; /qh because Modern Standby
hides them from /q) and the Lenovo keys under HKCU/HKLM, read only. --apply sets SBUTTONACTION AC to 0 on the current
scheme (powercfg /setacvalueindex needs no admin in the user's own session; if it is refused the elevate line is
printed) and records before/after in state/power/drift-*.json.
Windows only: elsewhere the check passes with "not-this-platform" (no ibmpmsvc exists).
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from . import incidents

BUSY_C, BUSY_LOAD = 50.0, 25.0
SETTINGS = {"SBUTTONACTION": "sleep button", "PBUTTONACTION": "power button", "LIDACTION": "lid close"}
ACTION = {0: "do nothing", 1: "sleep", 2: "hibernate", 3: "shut down"}
SUB_BUTTONS = "SUB_BUTTONS"
LENOVO_KEYS = (r"HKCU\Software\Lenovo", r"HKLM\SOFTWARE\Lenovo")


def read_settings(run=subprocess.run):
    """-> {alias: {"ac": int, "dc": int}} from powercfg /qh; {} when unreadable."""
    if sys.platform != "win32":
        return {}
    try:
        text = run(["powercfg", "/qh", "SCHEME_CURRENT", SUB_BUTTONS], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    out, alias = {}, None
    for ln in text.splitlines():
        m = re.search(r"GUID Alias:\s*(\w+)", ln)
        if m:
            alias = m.group(1)
            continue
        m = re.search(r"Current (AC|DC) Power Setting Index:\s*0x([0-9a-fA-F]+)", ln)
        if m and alias in SETTINGS:
            out.setdefault(alias, {})[m.group(1).lower()] = int(m.group(2), 16)
    return out


def lenovo_keys(run=subprocess.run, limit=40):
    """Read-only: value lines under the Lenovo keys whose text speaks of sleep, power, hot keys or Fn."""
    if sys.platform != "win32":
        return []
    out = []
    for k in LENOVO_KEYS:
        try:
            t = run(["reg", "query", k, "/s"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        key = ""
        for ln in t.splitlines():
            if ln.startswith("HKEY"):
                key = ln.strip()
            elif ln.strip() and "{" not in ln and "Battery" not in ln and re.search(r"sleep|hibern|standby|power|hotkey|fnkey|\bfn\b", ln, re.I):
                out.append(f"{key}: {ln.strip()}"[:220])
    return out[:limit]


def sleep_events(events, jrows, usage, days, now=None):
    """Pure: 187 events from ibmpmsvc in the window -> [{t, t_text, on_ac, busy, peak_c, load_pct}] (busy = work ran)."""
    t0 = (now or time.time()) - days * 86400
    res = []
    for e in events:
        if e["id"] != 187 or e["t"] < t0:
            continue
        if "ibmpmsvc" not in incidents._basename(e["data"].get("ApiCallerName")).lower():
            continue
        peak, _ = incidents.peak_before(jrows, e["t"])
        load = max([ld for ts, _, ld in jrows if e["t"] - incidents.WINDOW_S <= ts <= e["t"] and ld is not None],
                   default=None)
        src, _pct = incidents.battery_at(usage, e["t"])
        busy = (peak is not None and peak >= BUSY_C) or (load is not None and load >= BUSY_LOAD)
        res.append({"t": e["t"], "t_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["t"])),
                    "on_ac": src == "AC", "busy": busy, "peak_c": peak, "load_pct": load})
    return res


def verdict(found, settings):
    """-> [problem text]. Pure."""
    p = []
    for s in found:
        if s["on_ac"] and s["busy"]:
            p.append(f"ibmpmsvc.exe asked for sleep at {s['t_text']} on AC during work "
                     f"(CPU {s['peak_c']} C, load {s['load_pct']}%)")
    sb = settings.get("SBUTTONACTION", {}).get("ac")
    if sb is not None and sb != 0:
        p.append(f"sleep button on AC is '{ACTION.get(sb, sb)}', not 'do nothing'")
    return p


def apply(settings, run=subprocess.run):
    """Set SBUTTONACTION AC to 0 on the current scheme. -> {"ok", "before", "after", "command", ["elevate"]}."""
    before = settings.get("SBUTTONACTION", {}).get("ac")
    cmd = ["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_BUTTONS, "SBUTTONACTION", "0"]
    act = ["powercfg", "/setactive", "SCHEME_CURRENT"]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            run(act, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "before": before, "after": before, "error": str(e)}
    after = read_settings(run).get("SBUTTONACTION", {}).get("ac")
    res = {"ok": after == 0, "before": before, "after": after, "command": " ".join(cmd) + " ; " + " ".join(act)}
    if after != 0:
        res["elevate"] = ("needs admin: put the command in a .ps1 and run it with the elevate tool "
                          "(skill fieldkit-admin-unattended); never an inbox item")
    return res


def run(days=7, do_apply=False, as_json=False, out=print, state_dir=None):
    if sys.platform != "win32":
        r = {"ok": True, "status": "not-this-platform", "problems": []}
        out(json.dumps(r) if as_json else "not-this-platform: no ibmpmsvc.exe here; nothing to check\nNEXT: nothing to do")
        return 0
    w = incidents.read_windows(time.time() - days * 86400)
    found = sleep_events(w["events"], incidents.journal_rows(), incidents.battery_usage(), days)
    st = read_settings()
    app = apply(st) if do_apply else None
    if app:
        st = read_settings()
        d = Path(state_dir or Path(__file__).resolve().parents[2] / "state" / "power")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"drift-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(app, indent=1), encoding="utf-8")
    problems = verdict(found, st)
    res = {"ok": not problems, "problems": problems, "ibmpmsvc_sleeps": found,
           "settings": {k: dict({"name": SETTINGS[k]}, **{m: ACTION.get(v, v) for m, v in d.items()}) for k, d in st.items()},
           "lenovo_keys": lenovo_keys(), "apply": app}
    if as_json:
        out(json.dumps(res, indent=1))
    else:
        for d in res["settings"].values():
            out(f"  {d['name']:13} AC: {d.get('ac')}  DC: {d.get('dc')}")
        out(f"  ibmpmsvc sleep requests in {days} d: {len(found)}")
        for ln in res["lenovo_keys"][:10]:
            out("  lenovo: " + ln)
        if app:
            out(f"  apply: {'OK' if app['ok'] else 'FAILED'} before {app['before']} after {app['after']}"
                + (f"\n  {app['elevate']}" if app.get("elevate") else ""))
        for p in problems:
            out("DRIFT: " + p)
        out("PASS: no ibmpmsvc sleep during work, sleep button off on AC" if not problems
            else f"FAIL: {len(problems)} drift finding(s)")
        out("NEXT: " + ("nothing to do" if not problems else "fieldkit power drift --apply"))
    return 3 if problems else 0
