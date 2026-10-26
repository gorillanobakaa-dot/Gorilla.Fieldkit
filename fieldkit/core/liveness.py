# Version: 1.0.0 · updated 26-10-26
"""Process liveness: no harness-started process is orphaned, stalls silently or blocks its supervisor. Deterministic.

    fieldkit liveness check [--act] [--json]   every registered long run: OK, STALLED or ORPHAN; --act stops the
                                               harness's own orphans and overdue steps (by PID + creation time)
    fieldkit liveness status [--json]          the runs, their steps, deadlines and members
    fieldkit liveness modes                    the named failure modes and the check that catches each
    fieldkit liveness doctor                   the containment this system gives, the state folder, the schedule

Exit codes: 0 OK, 3 findings (STALLED or ORPHAN), 1 error, 2 bad usage.

WHY (2026-10-26 18:23-19:43, goal windows-capabilities): an orphaned go.exe spun for 80 min holding the verifier's
pipes; the supervisor wrote no line, its lock heartbeat (written once at 13:19) said nothing, `fieldkit hog` warned
for an hour but could not act (the goal's processes were not registered) and a non-elevated session could not stop
an elevated orphan. Owner: "make sure via deterministic tools and checks that problems like these OR SIMILAR or
CLOSELY RELATED does not happen anymore".

THE PROTOCOL
  1. Every long run (goal supervisor, runbook, emerge, autopilot claude, link jobs) holds a BEACON: a file in
     state/liveness/runs/ with its owner process (PID + creation time), a heartbeat refreshed by a timer thread, and
     its open STEPS (label, started, wall deadline) with every container MEMBER seen (PID + creation time + step).
  2. Every step runs in a container (contain.py) with a wall deadline: on expiry the whole tree is stopped and the
     step is TIMEOUT, not FAIL. The beacon's own thread is a second watchdog: a step past deadline + grace is stopped
     from inside the owner process, so an ELEVATED supervisor reaps its own orphans and nobody else needs admin.
  3. `liveness check` (on hog's 5-minute schedule, at supervisor start, in the ONE preflight before each job) reads
     every beacon:
        ORPHAN   a member alive whose owner is gone, or whose step already ended          -> --act stops it
        STALLED  the owner lives but its heartbeat is older than stale_s (the timer died),
                 or a step is past its deadline + grace (stuck despite a fresh heartbeat) -> --act stops the step
        OK       otherwise; a beacon whose owner and members are all gone is removed
     Only members recorded in a beacon are ever stopped, and only while PID AND creation time match: never by name,
     never the owner's own programs, never the owner process itself. Every stop: state/liveness/actions.jsonl and
     ONE inbox line per pass, never a prompt.
"""
import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

from . import contain

HEARTBEAT_S = 30
STALE_MIN_S = 180                      # a heartbeat older than max(3 x every, this) is stalled
GRACE_S = 60                           # a step this long past its deadline is overdue (the run loop should have acted)
HARD_CAP_S = 3600                      # the longest wall deadline a derived default may give
FLOOR_S = 300                          # the shortest
FACTOR = 3                             # derived deadline = FACTOR x the slowest measured run

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_FINDINGS = 0, 1, 2, 3

#: the named failure modes and the check that catches each (fieldkit liveness modes; each has a test)
MODES = [
    {"id": "FM-1", "name": "orphaned grandchild",
     "what": "a step's process dies and leaves a descendant running (go.exe 2672, 2026-10-26)",
     "check": "the container stops every member when the step ends (Outcome.leftovers); liveness ORPHAN for a member "
              "of an ended step or a dead owner", "test": "tests/test_liveness.py::test_reproduction_orphan_grandchild"},
    {"id": "FM-2", "name": "silent supervisor",
     "what": "a supervisor blocks in a step with no deadline and writes nothing",
     "check": "every verifier criterion and post-job step has a wall deadline (deadline_for); liveness STALLED for a "
              "step past deadline + grace; the beacon thread stops it from inside",
     "test": "tests/test_liveness.py::test_a_step_past_its_deadline_is_stalled_and_stopped"},
    {"id": "FM-3", "name": "pipe held by an orphan",
     "what": "a child exits but a descendant keeps its stdout pipe open; communicate() never returns",
     "check": "containers write output to files, never pipes; no bare communicate() in the harness's runners",
     "test": "tests/test_liveness.py::test_a_grandchild_holding_output_never_blocks"},
    {"id": "FM-4", "name": "stale lock with a live but stuck owner",
     "what": "a lock's PID lives (or was reused) and a heartbeat thread keeps it fresh while nothing progresses",
     "check": "lock = PID + creation time + heartbeat (goal.lock); progress = step deadlines in the beacon",
     "test": "tests/test_liveness.py::test_a_lock_held_by_a_reused_pid_is_taken_over"},
    {"id": "FM-5", "name": "hung test in a pre-commit hook",
     "what": "one test hangs and the commit (and the session waiting on it) never returns",
     "check": "the hook's suite runs through the preflight guard, which runs it in a container with a wall deadline",
     "test": "tests/test_liveness.py::test_the_guard_stops_a_hung_command_at_its_deadline"},
    {"id": "FM-6", "name": "background job outliving its session",
     "what": "a run's supervisor ends and its children keep running",
     "check": "Windows: KILL_ON_JOB_CLOSE; elsewhere the beacon's members are ORPHAN once the owner is gone",
     "test": "tests/test_liveness.py::test_members_of_a_dead_owner_are_orphans_and_stopped"},
    {"id": "FM-7", "name": "PID reuse",
     "what": "a recorded PID now belongs to another program (the owner's)",
     "check": "every stop compares PID AND creation time; a mismatch is never touched",
     "test": "tests/test_liveness.py::test_a_reused_pid_is_never_stopped"},
    {"id": "FM-8", "name": "elevated orphan, non-elevated reaper",
     "what": "an elevated supervisor's orphan cannot be stopped by a normal session (taskkill: Access is denied)",
     "check": "the elevated owner stops its own steps (container deadline + the beacon thread); a denied stop is "
              "logged, never retried as admin",
     "test": "tests/test_liveness.py::test_the_beacon_thread_stops_an_overdue_step_from_inside"},
    {"id": "FM-9", "name": "silent stall",
     "what": "a headless run makes no progress (no output, no CPU) until its wall deadline: autopilot claude ran "
             "90 min and returned rc 124 with empty output (2026-10-26)",
     "check": "containers with idle_s stop a run with no progress for that long (status stalled, autopilot claude "
              "default 10 min), keep the output tail in state/liveness/tails, log it and put a ROOM line; liveness "
              "STALLED for a step whose progress marker is older than idle_s + grace",
     "test": "tests/test_liveness.py::test_a_silent_child_is_a_stall_with_its_tail_kept_and_reported"},
]


def state_dir():
    from . import settings
    d = Path(os.environ.get("FIELDKIT_LIVENESS_DIR") or (settings.ROOT / "state" / "liveness"))
    (d / "runs").mkdir(parents=True, exist_ok=True)
    return d


def _wj(p, data):
    tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _rj(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---- deadlines --------------------------------------------------------------------------------------------------
def deadline_for(history_s=(), explicit=None, floor=FLOOR_S, cap=HARD_CAP_S, factor=FACTOR):
    """The wall deadline of a step: an explicit one (never above the cap), else FACTOR x the slowest measured run,
    clamped to [floor, cap]; with no history, the cap. Never None: no step waits forever."""
    if explicit:
        return float(min(float(explicit), cap))
    hist = [float(h) for h in history_s or () if h]
    if not hist:
        return float(cap)
    return float(max(floor, min(cap, factor * max(hist))))


# ---- the beacon -------------------------------------------------------------------------------------------------
_CURRENT = None


def current():
    """The beacon of the long run this process belongs to, or None."""
    return _CURRENT


class Beacon:
    """A long run's liveness file + its heartbeat thread + its in-process watchdog."""

    def __init__(self, kind, rid, label="", every=HEARTBEAT_S, now=time.time, grace=GRACE_S):
        self.kind, self.rid, self.label = kind, str(rid), label
        self.every, self.now, self.grace = every, now, grace
        self.pid = os.getpid()
        self.ct = contain.ctime(self.pid)
        self.path = state_dir() / "runs" / f"{kind}-{_safe(self.rid)}-{self.pid}.json"
        self._lock = threading.RLock()
        self._seq = 0
        self._killers = {}
        self._stop = threading.Event()
        self.rec = {"kind": kind, "id": self.rid, "label": label, "pid": self.pid, "ctime": self.ct,
                    "host": socket.gethostname(), "started": now(), "heartbeat": now(), "every": every,
                    "stale_s": max(3 * every, STALE_MIN_S), "note": "", "steps": {}, "members": {}}

    def _write(self):
        with self._lock:
            self.rec["heartbeat"] = self.now()
            _wj(self.path, self.rec)

    def start(self):
        global _CURRENT
        self._write()
        self._prev, _CURRENT = _CURRENT, self
        threading.Thread(target=self._loop, name=f"liveness-{self.kind}", daemon=True).start()
        return self

    def _loop(self):
        while not self._stop.wait(self.every):
            try:
                self.beat()
            except OSError:
                pass

    def beat(self, note=None):
        """Refresh the heartbeat; stop any step of THIS process that is past deadline + grace (the inside watchdog)."""
        with self._lock:
            if note is not None:
                self.rec["note"] = note
            overdue = [sid for sid, s in self.rec["steps"].items()
                       if s.get("deadline_at") and self.now() > s["deadline_at"] + self.grace]
        for sid in overdue:
            kill = self._killers.get(sid)
            if kill:
                killed = kill("watchdog")
                log_action({"kind": "watchdog", "run": self.rid, "step": sid, "pids": killed,
                            "why": f"step {self.rec['steps'].get(sid, {}).get('label', sid)} passed its deadline "
                                   f"+ {self.grace} s; stopped from inside its owner"})
                self.clear_step(sid)
        self._write()

    def set_step(self, label, deadline_s=None, killer=None):
        with self._lock:
            self._seq += 1
            sid = str(self._seq)
            t = self.now()
            self.rec["steps"][sid] = {"label": label, "started": t,
                                      "deadline_at": t + deadline_s if deadline_s else None}
            if killer:
                self._killers[sid] = killer
            self._write()
        return sid

    def clear_step(self, sid=None):
        with self._lock:
            if sid is None:
                return
            self.rec["steps"].pop(str(sid), None)
            self._killers.pop(str(sid), None)
            self._write()

    def progress(self, sid, at, idle_s):
        """A container saw progress (output or CPU) at `at`; with no progress for idle_s the step is STALLED."""
        with self._lock:
            s = self.rec["steps"].get(str(sid))
            if s is not None:
                s["progress_at"], s["idle_s"] = at, idle_s
                self._write()

    def add_members(self, members, sid=None):
        with self._lock:
            for pid, ct in members.items():
                self.rec["members"][str(pid)] = {"ctime": ct, "step": sid, "name": _name(pid)}
            self._write()

    def close(self):
        global _CURRENT
        self._stop.set()
        if _CURRENT is self:
            _CURRENT = getattr(self, "_prev", None)
        try:
            self.path.unlink()
        except OSError:
            pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
        return False


def _safe(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)[:60]


def _name(pid):
    try:
        return contain.psutil.Process(int(pid)).name()
    except Exception:  # noqa: BLE001
        return ""


def beacon(kind, rid, label="", **kw):
    """A started beacon for a long run (use `with`). Linux: also makes this process the child subreaper."""
    contain.enable_subreaper()
    return Beacon(kind, rid, label, **kw)


# ---- the check --------------------------------------------------------------------------------------------------
def runs():
    out = []
    for p in sorted((state_dir() / "runs").glob("*.json")):
        r = _rj(p)
        if isinstance(r, dict):
            r["_path"] = str(p)
            out.append(r)
    return out


def assess(rec, now=None, same=contain.same):
    """One beacon -> findings [{state, line, kill: [[pid, ctime]], forget}]. Pure apart from `same`."""
    now = time.time() if now is None else now
    owner = same(rec.get("pid"), rec.get("ctime"))
    steps = rec.get("steps") or {}
    members = rec.get("members") or {}
    live = {int(p): m for p, m in members.items() if same(int(p), m.get("ctime"))}
    who = f"{rec.get('kind')} {rec.get('id')} (PID {rec.get('pid')})"
    out = []
    if not owner:
        if live:
            out.append({"state": "ORPHAN", "run": rec.get("id"),
                        "line": f"ORPHAN {who}: its owner is gone, {len(live)} process(es) still run: "
                                + ", ".join(f"{m.get('name') or '?'} {p}" for p, m in sorted(live.items())),
                        "kill": [[p, m["ctime"]] for p, m in sorted(live.items())]})
        else:
            out.append({"state": "GONE", "run": rec.get("id"), "line": f"gone {who}: nothing left running",
                        "forget": rec.get("_path")})
        return out
    age = now - float(rec.get("heartbeat") or 0)
    if age > float(rec.get("stale_s") or STALE_MIN_S):
        out.append({"state": "STALLED", "run": rec.get("id"),
                    "line": f"STALLED {who}: heartbeat {int(age)} s old (limit {int(rec.get('stale_s') or STALE_MIN_S)} s)",
                    "kill": []})
    for sid, s in steps.items():
        d = s.get("deadline_at")
        if d and now > d + GRACE_S:
            ks = [[p, m["ctime"]] for p, m in sorted(live.items()) if str(m.get("step")) == str(sid)]
            out.append({"state": "STALLED", "run": rec.get("id"),
                        "line": f"STALLED {who}: step '{s.get('label')}' is {int(now - d)} s past its deadline"
                                + (f"; {len(ks)} process(es) to stop" if ks else ""),
                        "kill": ks})
        idle, since = s.get("idle_s"), s.get("progress_at")
        if idle and since and now - since > idle + GRACE_S:
            ks = [[p, m["ctime"]] for p, m in sorted(live.items()) if str(m.get("step")) == str(sid)]
            out.append({"state": "STALLED", "run": rec.get("id"),
                        "line": f"STALLED {who}: step '{s.get('label')}' made no progress (no output, no CPU) for "
                                f"{int(now - since)} s (limit {int(idle)} s)", "kill": ks})
    ended = [(p, m) for p, m in sorted(live.items()) if m.get("step") is not None and str(m["step"]) not in steps]
    if ended:
        out.append({"state": "ORPHAN", "run": rec.get("id"),
                    "line": f"ORPHAN {who}: {len(ended)} process(es) outlived their step: "
                            + ", ".join(f"{m.get('name') or '?'} {p}" for p, m in ended),
                    "kill": [[p, m["ctime"]] for p, m in ended]})
    return out


def stop_pid(pid, ct, same=contain.same):
    """Stop one recorded member, only while PID AND creation time match. -> (ok, result)."""
    if not same(pid, ct):
        return True, "already gone or the PID was reused: left alone"
    try:
        p = contain.psutil.Process(int(pid))
        kids = p.children(recursive=True)
        for x in kids + [p]:
            try:
                x.kill()
            except contain.psutil.NoSuchProcess:
                pass
        return True, f"stopped (+{len(kids)} child process(es))"
    except contain.psutil.AccessDenied:
        return False, "access denied (an elevated owner's process): its own owner stops it at its deadline"
    except contain.psutil.Error as e:
        return False, str(e)[:200]


def log_action(rec):
    rec = dict(rec, t=time.time(), at=time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        with open(state_dir() / "actions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def room_line(verb, text):
    """One line in the shared ROOM (every session, the command post included, sees it on its next hook)."""
    try:
        from ..autopilot import room
        room.append(room.line_for("fieldkit-liveness", "", verb, "liveness", text[:600]))
    except Exception:  # noqa: BLE001
        pass


def check(act=False, now=None, same=contain.same, stopper=None, inbox_add=None, records=None):
    """-> {"status": OK|FINDINGS, "findings": [...], "acted": [...], "runs": n}."""
    recs = runs() if records is None else records
    found = []
    for r in recs:
        found += assess(r, now, same)
    acted = []
    for f in found:
        if f.get("forget") and act:
            try:
                Path(f["forget"]).unlink()
            except OSError:
                pass
        if not act:
            continue
        for pid, ct in f.get("kill") or []:
            ok, res = (stopper or stop_pid)(pid, ct)
            a = {"kind": "stop", "state": f["state"], "run": f.get("run"), "pid": pid, "ok": ok, "result": res,
                 "why": f["line"]}
            log_action(a)
            acted.append(a)
    real = [f for f in found if f["state"] in ("STALLED", "ORPHAN")]
    if acted and inbox_add is not False:
        ok_n = sum(1 for a in acted if a["ok"])
        line = (f"liveness: stopped {ok_n} of {len(acted)} harness process(es) - "
                + "; ".join(sorted({f['line'][:160] for f in real}))[:600])
        try:
            if inbox_add is None:
                from ..autopilot import inbox
                inbox_add = inbox.add
            inbox_add(line, "fieldkit liveness check --act (state/liveness/actions.jsonl has every PID)",
                      kind="liveness")
        except Exception:  # noqa: BLE001
            pass
        room_line("stop", line)
    return {"status": "FINDINGS" if real else "OK", "findings": found, "acted": acted, "runs": len(recs)}


def summary(res):
    real = [f for f in res["findings"] if f["state"] in ("STALLED", "ORPHAN")]
    if not real:
        return f"liveness: OK ({res['runs']} run(s))"
    return "liveness: " + "; ".join(f["line"] for f in real)


def preflight_check(ctx=None):
    """The ONE preflight's step (fieldkit/core/preflight.py STEPS): stop the harness's own orphans and overdue steps,
    warn on a stall; never refuses a run."""
    res = check(act=True)
    return ("ok" if res["status"] == "OK" else "warn"), summary(res)


def doctor():
    rows = []
    if contain.IS_WIN:
        try:
            j = contain._Job()
            j.close()
            rows.append(("job object", True, "CreateJobObject + KILL_ON_JOB_CLOSE available"))
        except OSError as e:
            rows.append(("job object", False, str(e)))
    else:
        rows.append(("process group", True, "start_new_session + killpg"))
        if sys.platform.startswith("linux"):
            rows.append(("child subreaper", True, "PR_SET_CHILD_SUBREAPER set by each supervisor (Linux 3.4+)"))
    rows.append(("psutil", contain.psutil is not None, "creation times for PID-reuse safety"))
    rows.append(("state folder", os.access(state_dir(), os.W_OK), str(state_dir())))
    try:
        from ..hog import install
        rows.append(("schedule", True, f"runs with `fieldkit hog watch` ({install.TASK}; `fieldkit hog install`)"))
    except Exception as e:  # noqa: BLE001
        rows.append(("schedule", False, str(e)))
    return rows


def main(argv=None, prog="fieldkit liveness"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check")
    c.add_argument("--act", action="store_true")
    c.add_argument("--json", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    m = sub.add_parser("modes")
    m.add_argument("--json", action="store_true")
    d = sub.add_parser("doctor")
    d.add_argument("--json", action="store_true")
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return EXIT_USAGE if e.code else EXIT_OK
    try:
        if a.cmd == "check":
            res = check(act=a.act)
            if a.json:
                print(json.dumps(res, indent=1, default=str))
            else:
                print(summary(res))
                for x in res["acted"]:
                    print(f"  {'stopped' if x['ok'] else 'NOT stopped'} PID {x['pid']}: {x['result']}")
                print("NEXT: " + ("nothing to do" if res["status"] == "OK" else
                                  "fieldkit liveness check --act stops the harness's own orphans and overdue steps"
                                  if not a.act else "fieldkit liveness status shows what is left"))
            return EXIT_OK if res["status"] == "OK" else EXIT_FINDINGS
        if a.cmd == "status":
            rs = runs()
            if a.json:
                print(json.dumps(rs, indent=1, default=str))
                return EXIT_OK
            now = time.time()
            for r in rs:
                print(f"{r['kind']} {r['id']}  PID {r['pid']}  heartbeat {int(now - r.get('heartbeat', 0))} s ago"
                      f"  {r.get('note') or ''}")
                for sid, st in (r.get("steps") or {}).items():
                    left = f"{int(st['deadline_at'] - now)} s left" if st.get("deadline_at") else "no deadline"
                    print(f"   step {sid}: {st['label']}  ({left})")
                print(f"   members: {len(r.get('members') or {})}")
            if not rs:
                print("no registered long run")
            return EXIT_OK
        if a.cmd == "modes":
            if a.json:
                print(json.dumps(MODES, indent=1))
            else:
                for x in MODES:
                    print(f"{x['id']}  {x['name']}: {x['what']}\n      check: {x['check']}\n      test:  {x['test']}")
            return EXIT_OK
        if a.cmd == "doctor":
            rows = doctor()
            if a.json:
                print(json.dumps([{"check": n, "ok": ok, "detail": d} for n, ok, d in rows], indent=1))
            else:
                for n, ok, det in rows:
                    print(f"[{'OK' if ok else 'MISSING'}] {n}: {det}")
            return EXIT_OK if all(ok for _n, ok, _d in rows) else EXIT_FINDINGS
        ap.print_help()
        return EXIT_USAGE
    except Exception as e:  # noqa: BLE001
        print(f"liveness: error: {type(e).__name__}: {e}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
