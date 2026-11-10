"""Stop a process only when the PID is still the process you meant: name AND start time must match, or nothing is
stopped (a PID can be reused by another program after yours exited). Default is the check only; --stop ends the
process and its children (terminate, then kill after 5 s) and --stop needs --started, so the check cannot be skipped.

Start time: the creation time of the process as `fieldkit repair pid-guarded-stop --pid N --name NAME` prints it
(YYYY-MM-DD HH:MM:SS, local time). `--proc-json FILE` checks a saved table [{pid, name, started}] instead of the live
one (tests, other machines). `fieldkit hog` and the liveness runs stop their own registered processes the same way.

    fieldkit repair pid-guarded-stop --pid N --name NAME [--started "YYYY-MM-DD HH:MM:SS"] [--stop] [--proc-json F] [--json]
Exit: 0 would stop / stopped / already gone, 1 error, 3 refused (name or start time differs).
"""
import argparse
import json
import time
from pathlib import Path

from ._common import emit


def verify_pid_target(table, pid, name, started=None):
    """-> (verdict, message): STOP, SKIP (not running) or REFUSED."""
    p = table.get(pid)
    if not p:
        return "SKIP", f"PID {pid} is not running"
    actual = (p.get("name") or "").lower().removesuffix(".exe")
    if actual != name.lower().removesuffix(".exe"):
        return "REFUSED", f"PID {pid} is {p.get('name')}, expected {name}"
    if started and p.get("started") != started:
        return "REFUSED", f"PID {pid} started {p.get('started')}, expected {started}"
    return "STOP", f"PID {pid} {p.get('name')} started {p.get('started')}"


def live_table(pid):
    import psutil
    try:
        p = psutil.Process(pid)
        return {pid: {"pid": pid, "name": p.name(), "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.create_time())),
                      "ctime": p.create_time()}}
    except psutil.Error:
        return {}


def stop(pid, ctime):
    from ..hog import kill
    from ..hog.registry import ct
    return kill({"pid": pid, "ctime": ct(ctime)})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair pid-guarded-stop", description=__doc__.split("\n")[0])
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--started", help="expected start time YYYY-MM-DD HH:MM:SS (required with --stop)")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--proc-json", help="a saved process table instead of the live one (no --stop)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.stop and a.proc_json:
        emit(a.json, "FAIL", "--stop works on the live process table only", "drop --proc-json")
        return 1
    if a.stop and not a.started:
        emit(a.json, "FAIL", "--stop needs --started: name alone is not proof it is the same process", "run once without --stop to read the start time")
        return 1
    if a.proc_json:
        try:
            table = {int(x["pid"]): x for x in json.loads(Path(a.proc_json).read_text(encoding="utf-8"))}
        except (OSError, ValueError, KeyError, TypeError) as e:
            emit(a.json, "FAIL", f"cannot read the process table: {e}", "pass [{pid, name, started}]")
            return 1
    else:
        table = live_table(a.pid)
    verdict, msg = verify_pid_target(table, a.pid, a.name, a.started)
    if verdict == "REFUSED":
        emit(a.json, "FAIL", msg, "do not stop it: the PID was reused by another process", {"result": verdict})
        return 3
    if verdict == "SKIP" or not a.stop:
        emit(a.json, "PASS", msg if verdict == "SKIP" else f"{msg} (check only)", "none" if verdict == "SKIP" else
             f"add --stop --started \"{table[a.pid]['started']}\" to stop it", {"result": verdict})
        return 0
    ok, why = stop(a.pid, table[a.pid]["ctime"])
    emit(a.json, "PASS" if ok else "FAIL", f"{msg}: {why}", "none" if ok else "look at the process by hand", {"result": verdict, "stopped": ok})
    return 0 if ok else 3
