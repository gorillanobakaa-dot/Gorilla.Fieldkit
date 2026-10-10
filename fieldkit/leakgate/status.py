"""Where is a leak-gate run, and how hot has the machine been since it started? Read only; safe while it runs.

    fieldkit build-harness leakgate-status <task> [run=<YYYYmmdd-HHMMSS>] [thermal=<log>] [hot=70]

Born 2026-10-06: a release run takes four hours, and the owner asks for a sitrep every hour (progress and CPU
temperatures). The throwaway sitrep script had the run folder, the gate's PID, the 141 expected runs and the start
time typed in; the next run needed a new script. Everything comes from the run's own files now:

  run       the newest run folder of the task (state/build-harness/leakgate/<task>/<stamp>), or run=<stamp>
  started   the folder's name (the gate names it by its start time)
  expected  gate-plan.json, written by the gate when it starts (every planned "<scenario>-r<rep>-<mode>" and the
            gate's PID); for a run older than that file: artifacts.json when the run has finished, else the
            launcher's log header ("repeat 3, release durations -> <run>") with the scenario table as it is now
  progress  runs/profile-* folders (one per scenario run started), the newest by modification time
  alive     the PID from gate-plan.json (or the task's writer lock, when it names `leakgate`) is still that process
  result    test-results.json present: the run has judged (FINAL_RESULT)
  CPU       the thermal watcher's log (thermal/watchlog.py) since the start; default <owner>/state/thermal_watch.log
"""
import datetime as dt
import json
import re
from pathlib import Path, PureWindowsPath

from ..buildh import task

STAMP = re.compile(r"^\d{8}-\d{6}$")
HEADER = re.compile(r"repeat (\d+), (quick|release) durations -> (.+)$")


def runs_root(task_id):
    return task.STATE / "leakgate" / task_id


def run_dir(task_id, name=None):
    root = runs_root(task_id)
    if name:
        d = root / name
        if not d.is_dir():
            raise task.Refused(f"no leak-gate run {name} in {root}")
        return d
    runs = sorted(p for p in root.iterdir() if p.is_dir() and STAMP.match(p.name)) if root.is_dir() else []
    if not runs:
        raise task.Refused(f"no leak-gate run for task {task_id} in {root}")
    return runs[-1]


def started_at(d):
    return dt.datetime.strptime(Path(d).name, "%Y%m%d-%H%M%S")


def _launcher_header(d, launcher_dir):
    """The launcher log whose first line names this run -> (repeat, quick) or None. The log is PowerShell's
    Tee-Object output (UTF-16 with a byte-order mark)."""
    for log in sorted(Path(launcher_dir).glob("*.log")) if launcher_dir and Path(launcher_dir).is_dir() else []:
        raw = log.read_bytes()[:4000]
        text = raw.decode("utf-16", "replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
        for line in text.splitlines()[:5]:
            m = HEADER.search(line.strip())
            if m and PureWindowsPath(m.group(3).strip()).name == Path(d).name:   # a Windows path, read on any platform
                return int(m.group(1)), m.group(2) == "quick"
    return None


def plan(d, launcher_dir=None):
    """-> {"runs": [...] or None, "total": n or None, "source": why we believe it, "pid": int or None}"""
    from . import gate
    p = Path(d) / gate.PLAN_FILE
    if p.is_file():
        pl = json.loads(p.read_text(encoding="utf-8"))
        return {"runs": pl["runs"], "total": len(pl["runs"]), "source": gate.PLAN_FILE, "pid": pl.get("pid"),
                "repeat": pl.get("repeat"), "quick": pl.get("quick")}
    art = Path(d) / "artifacts.json"
    if art.is_file():
        runs = list(json.loads(art.read_text(encoding="utf-8")))
        return {"runs": runs, "total": len(runs), "source": "artifacts.json (the run has finished)", "pid": None}
    head = _launcher_header(d, launcher_dir)
    if head:
        runs = gate.planned_runs(head[0], head[1])
        return {"runs": runs, "total": len(runs), "pid": None, "repeat": head[0], "quick": head[1],
                "source": f"launcher log (repeat {head[0]}, {'quick' if head[1] else 'release'}) with today's scenario table"}
    return {"runs": None, "total": None, "pid": None, "source": "unknown: no gate-plan.json, no artifacts.json, no launcher log"}


def alive(pid, started):
    """True while `pid` is a process that was already running when the run started (a PID the system has since given
    to another program is not the gate)."""
    if not pid:
        return None
    try:
        import psutil
        try:
            return psutil.Process(int(pid)).create_time() <= started.timestamp() + 120
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return True                                    # an elevated gate: it exists
    except ImportError:
        from ..migrate import guard
        return guard._alive(pid)


def writer_pid(task_id):
    """The task's writer lock, when the writer holding it is the leak gate."""
    from ..migrate import plan as mplan
    try:
        cur = json.loads((mplan.state_dir(task_id) / "writer.lock").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cur.get("pid") if cur.get("action") == "leakgate" else None


def status(task_id, name=None, thermal_log=None, hot=70.0, launcher_dir=None):
    from ..core import settings
    from ..thermal import watchlog
    d = run_dir(task_id, name)
    t0 = started_at(d)
    pl = plan(d, launcher_dir or settings.ROOT / "state" / "leakgate-launcher")
    profiles = sorted((d / "runs").glob("profile-*"), key=lambda p: p.stat().st_mtime) if (d / "runs").is_dir() else []
    started = [p.name[len("profile-"):] for p in profiles]
    pid = pl.get("pid") or writer_pid(task_id)
    res_path = d / "test-results.json"
    final, ended = None, None
    if res_path.is_file():
        ended = dt.datetime.fromtimestamp(res_path.stat().st_mtime)       # a finished run: its CPU ends here too
        try:
            final = json.loads(res_path.read_text(encoding="utf-8")).get("FINAL_RESULT")
        except ValueError:
            final = "unreadable"
    therm = watchlog.summary(watchlog.read(thermal_log), since=t0, until=ended, hot=hot) if thermal_log else None
    return {"task": task_id, "run": str(d), "started": t0.isoformat(sep=" "), "plan_source": pl["source"],
            "expected": pl["total"], "started_runs": len(started), "latest": started[-1] if started else None,
            "not_yet": [r for r in (pl["runs"] or []) if r not in set(started)],
            "pid": pid, "alive": alive(pid, t0), "result": final,
            "thermal_log": str(thermal_log) if thermal_log else None, "thermal": therm,
            "elapsed_min": round((dt.datetime.now() - t0).total_seconds() / 60)}


def lines(s):
    from ..thermal import watchlog
    exp = s["expected"]
    pct = f" ({100 * s['started_runs'] // exp}%)" if exp else ""
    state = ("FINISHED: FINAL_RESULT " + str(s["result"])) if s["result"] else \
        ("RUNNING" if s["alive"] else "NOT RUNNING (no result file: it stopped before judging)" if s["alive"] is False
         else "running? (no PID recorded)")
    out = [f"LEAK GATE {s['task']}: {state}",
           f"  run {s['run']}, started {s['started']} ({s['elapsed_min']} min ago)" + (f", gate PID {s['pid']}" if s["pid"] else ""),
           f"  {s['started_runs']} of {exp if exp else '?'} scenario runs started{pct}; latest: {s['latest'] or '-'}"
           + f"  [expected from {s['plan_source']}]"]
    if s["not_yet"] and not s["result"]:
        out.append(f"  next: {', '.join(s['not_yet'][:4])}" + (f" ... ({len(s['not_yet'])} to go)" if len(s["not_yet"]) > 4 else ""))
    if s["thermal_log"]:
        out += ["  " + l for l in watchlog.lines(s["thermal"], label="CPU since the start")]
    return out
