"""Why did each run stop? Every window run of the harness classified: the browser, the check, or the machine.

    fieldkit build-harness stops <task> [since=YYYY-MM-DD]

Born 2026-10-09, item 3 of the proactive programme. The owner: "every time we were trying to build, you were telling
me that the build failed ... although only a couple of hours before we had just successfully finished building a
complete browser". Read by hand that evening, ten stops in two days: the compiler failed none of them. Six were the
temperature-sensor proof on a cool laptop, four were checks that were later fixed (gate rename check, pre-build page
check, stamp check), and three were the machine (standby overnight, an unexpected shutdown). This does that reading
every time, from the records only:
  - each window log (state/windows/<stamp>-<command>-<task>.log): start (its name), end (its last write), exit code
    and the first line that says why it stopped;
  - the task journal: the temperature peak of the attempt a thermal stop ended;
  - git: what changed between the stop and the next run of the same command that passed - the browser's source tree,
    or only the harness (Fieldkit, the owner's harness/);
  - Windows' power log (Kernel-Power 506 standby, 41 / EventLog 6008 unexpected shutdown, Kernel-General 12 start).
Verdicts (deterministic, in this order):
  machine      standby or a shutdown inside the run, and no exit line or exit -1;
  false alarm  a thermal stop at a peak under THERMAL_REAL C (the sensor proof, not heat); or the next pass of the
               same command came after harness changes and no change to the browser's source (the check was wrong);
  real         the browser's source changed before the same command passed again;
  transient    it passed again with nothing changed;
  open         it has not passed since.
Per check: how many stops it caused and how many were false alarms; a check that raises false alarms is the next to
calibrate (calibrate.py).
"""
import datetime
import json
import re
import subprocess
from pathlib import Path

from . import task, window

THERMAL_REAL = 70.0
NAME = re.compile(r"^(\d{8}-\d{6})-([a-z-]+?)-(firefox-[\w.-]+)\.log$")
WHY = re.compile(r"GATE FAILED[^\n]*|STOPPED[^\n]*|\[FAIL\][^\n]*|^\s*FAIL\s[^\n]*|NOT PASSED[^\n]*|POST-INSTALL NOT OK[^\n]*|"
                 r"BUILD NOT OK[^\n]*|HALT[^\n]*|Traceback[^\n]*", re.M)
EXIT = re.compile(r"^exit (-?\d+)\s*$", re.M)
CHECK = [("thermal", re.compile(r"thermal|sensor|HALT", re.I)), ("gate", re.compile(r"GATE FAILED|false completion", re.I)),
         ("pre-build pages", re.compile(r"pre-build", re.I)), ("stamp", re.compile(r"stamp", re.I)),
         ("decisions", re.compile(r"decisions", re.I)), ("visual", re.compile(r"visual", re.I)),
         ("claims", re.compile(r"claims", re.I)), ("about pages", re.compile(r"about-pages|removed pages", re.I)),
         ("icons", re.compile(r"desktop icons|icon cache", re.I))]
# the code each check is made of: ("fk", path in Fieldkit) or ("owner", path in the owner's repository); a check whose
# code is not in git (IconKit) has none, so a fix to it cannot be seen and its verdicts stay "unclear"
CHECK_CODE = {"thermal": [("owner", "harness/lib/thermal.py"), ("owner", "harness/stages/s40_build.py")],
              "gate": [("fk", "fieldkit/buildh/firefox.py"), ("fk", "fieldkit/buildh/compile.py"), ("fk", "fieldkit/buildh/verify.py")],
              "pre-build pages": [("fk", "fieldkit/buildh/aboutpages.py"), ("fk", "fieldkit/buildh/buildrun.py")],
              "stamp": [("fk", "fieldkit/buildh/buildstamp.py"), ("owner", "harness/stages/s40_build.py")],
              "decisions": [("fk", "fieldkit/buildh/decisions.py")], "claims": [("fk", "fieldkit/buildh/claims.py")],
              "visual": [("fk", "fieldkit/visual")], "about pages": [("fk", "fieldkit/buildh/aboutpages.py"),
                                                                    ("fk", "fieldkit/buildh/removedpages.py")]}
RUNNING_MINUTES = 60


def runs(folder, task_id, since=None):
    """Window logs of the task -> [{log, cmd, start, end, exit, why}] in time order."""
    out = []
    for p in sorted(Path(folder).glob("*.log")):
        m = NAME.match(p.name)
        if not m or m.group(3) != task_id:
            continue
        start = datetime.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
        if since and start < since:
            continue
        text = window.read_log(p)
        codes = EXIT.findall(text)
        why = WHY.search(text)
        # post-install exits non-zero while the keyboard check is skipped: with nothing failed it counts as passed
        only_skipped = ("POST-INSTALL NOT OK" in text and not re.search(r"\[FAIL\]|^\s*FAIL:", text, re.M)
                        and "SKIPPED" in text)
        out.append({"log": p.name, "cmd": m.group(2), "start": start,
                    "end": datetime.datetime.fromtimestamp(p.stat().st_mtime),
                    "exit": int(codes[-1]) if codes else None, "passed": bool(codes) and (int(codes[-1]) == 0 or only_skipped),
                    "why": why.group(0).strip()[:160] if why else ""})
    return out


def check_of(why):
    for name, rx in CHECK:
        if rx.search(why or ""):
            return name
    return "other"


def commits(repo, a, b, paths=(), run=subprocess.run):
    """Subjects of the commits made in (a, b] -> [str]."""
    if not repo or not Path(repo).exists():
        return []
    args = ["git", "-C", str(repo), "log", f"--since={a:%Y-%m-%d %H:%M:%S}", f"--until={b:%Y-%m-%d %H:%M:%S}",
            "--format=%s"] + (["--"] + list(paths) if paths else [])
    r = run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [l for l in (r.stdout or "").splitlines() if l.strip()]


def power_events(since, run=subprocess.run):
    """[(time, kind)] from Windows' System log: standby (506), wake (507), unexpected shutdown (41, 6008), start (12)."""
    ps = ("Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date '" + f"{since:%Y-%m-%d %H:%M:%S}" + "'); "
          "ProviderName='Microsoft-Windows-Kernel-Power','Microsoft-Windows-Kernel-General','EventLog'} "
          "-ErrorAction SilentlyContinue | Where-Object { $_.Id -in 41,506,507,6008,12 } | "
          "ForEach-Object { $x = [xml]$_.ToXml(); $d = @{}; foreach ($e in $x.Event.EventData.Data) { $d[$e.Name] = $e.'#text' }; "
          "$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') + '|' + $_.Id + '|' + $d['LidOpenState'] + '|' + "
          "$d['BatteryRemainingCapacityOnEnter'] + '|' + $d['BatteryFullChargeCapacityOnEnter'] }")
    try:
        out = run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    kinds = {"506": "standby", "507": "wake", "41": "unexpected shutdown", "6008": "unexpected shutdown", "12": "start"}
    ev = []
    for l in (out or "").splitlines():
        f = l.strip().split("|") + ["", "", "", ""]
        if f[1] in kinds:
            kind = kinds[f[1]]
            # a standby says why (2026-10-10: the lid closed at 00:02 and the build froze until 06:20) and on what
            # charge (2026-10-09: 18 % four minutes before an unexpected shutdown: the battery ran out)
            if kind == "standby" and f[2] == "false":
                kind = "standby (lid closed)"
            if f[3].isdigit() and f[4].isdigit() and int(f[4]):
                kind += f", battery {round(100 * int(f[3]) / int(f[4]))} %"
            ev.append((datetime.datetime.strptime(f[0], "%Y-%m-%d %H:%M:%S"), kind))
    return sorted(ev)


def thermal_peak(journal, a, b):
    """Highest temperature the journal recorded for a build attempt in [a, b] -> float or None."""
    peaks = [e.get("peak") for e in journal if e.get("event") == "thermal"
             and a <= datetime.datetime.strptime(str(e.get("t"))[:19], "%Y-%m-%d %H:%M:%S") <= b
             and isinstance(e.get("peak"), (int, float))]
    return max(peaks) if peaks else None


def classify(rs, journal, events, changed, now=None):
    """-> rows: each run with a verdict. `changed(a, b, check)` -> {"tree": [...], "harness": [...], "check": [...]}."""
    out = []
    now = now or datetime.datetime.now()
    for i, r in enumerate(rs):
        if r.get("passed", r["exit"] == 0):
            continue
        if r["exit"] is None and now - r["end"] < datetime.timedelta(minutes=RUNNING_MINUTES):
            continue                                     # still running
        v, ev = None, r["why"] or "no reason printed"
        inside = [(t, k) for t, k in events if r["start"] <= t <= r["end"] + datetime.timedelta(minutes=15)
                  and k.split(" (")[0].split(",")[0] in ("standby", "unexpected shutdown", "start")]
        if (r["exit"] in (None, -1)) and inside:
            v, ev = "machine", f"{inside[0][1]} at {inside[0][0]:%m-%d %H:%M} during the run"
        elif check_of(r["why"]) == "thermal":
            peak = thermal_peak(journal, r["start"], r["end"])
            if peak is not None and peak < THERMAL_REAL:
                v, ev = "false alarm", f"thermal stop at a peak of {peak:.0f} C: the sensor proof, not heat"
            elif peak is not None:
                v, ev = "real", f"thermal stop at {peak:.0f} C"
        if v is None:
            nxt = next((x for x in rs[i + 1:] if x["cmd"] == r["cmd"] and x.get("passed", x["exit"] == 0)), None)
            ck = check_of(r["why"])
            if not nxt:
                v = "open"
            else:
                c = changed(r["end"], nxt["start"], ck)
                if c["tree"] and (c["check"] or ck not in CHECK_CODE):
                    v, ev = "unclear", f"{ev}; both the source and the check (or code not in git) changed before it passed"
                elif c["tree"]:
                    v, ev = "real", f"{ev}; the source changed before it passed ({c['tree'][0][:70]})"
                elif c["harness"]:
                    v, ev = "false alarm", f"{ev}; passed after harness fixes only ({(c['check'] or c['harness'])[0][:70]})"
                else:
                    v, ev = "transient", (f"{ev}; passed again with no commit in between (a check fixed before its "
                                          "commit looks the same)")
        out.append({"log": r["log"], "cmd": r["cmd"], "at": f"{r['start']:%m-%d %H:%M}", "check": check_of(r["why"]),
                    "verdict": v, "evidence": ev})
    return out


def run(task_id, since=None, folder=None, run_cmd=subprocess.run):
    """-> {"stops": [...], "by_check": {check: {"stops", "false alarms"}}}."""
    from . import buildrun
    t = task.load(task_id)
    folder = Path(folder) if folder else task.STATE.parent / "windows"
    rs = runs(folder, task_id, since)
    jp = task.STATE / task_id / "journal.jsonl"
    journal = [json.loads(l) for l in jp.read_text(encoding="utf-8").splitlines() if l.strip()] if jp.is_file() else []
    events = power_events(rs[0]["start"], run=run_cmd) if rs else []
    owner = buildrun._owner_root(t) if t.get("meta", {}).get("harness_root") else None
    fk = Path(__file__).resolve().parents[2]

    def changed(a, b, check):
        code = CHECK_CODE.get(check, [])
        return {"tree": commits(t.get("workdir"), a, b, run=run_cmd),
                "harness": commits(fk, a, b, paths=("fieldkit",), run=run_cmd)
                + (commits(owner, a, b, paths=("harness",), run=run_cmd) if owner else []),
                "check": [x for repo, path in code
                          for x in commits(fk if repo == "fk" else owner, a, b, paths=(path,), run=run_cmd)]}
    stops = classify(rs, journal, events, changed)
    by = {}
    for s in stops:
        b = by.setdefault(s["check"], {"stops": 0, "false alarms": 0})
        b["stops"] += 1
        b["false alarms"] += s["verdict"] == "false alarm"
    return {"runs": len(rs), "stops": stops, "by_check": by}
