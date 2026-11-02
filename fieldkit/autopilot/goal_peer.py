# Version: 1.0.0 · updated 26-11-02-12-30
"""Goals use both computers, and a silent supervisor is an alarm (HARNESS-PLAN R12, defect 4 of
state/reports/2026-11-02-why-the-harness-failed.md).

Measured 2026-11-01: the supervisor's main loop logged nothing from 22:37:45 to 00:17 while job integ-wire held the
laptop's one heavy slot; 7 harvest jobs never started; idle shares 61 of 78 min (harvest), 53 of 71 min
(integrated-everywhere), 18 h 56 min of 26 h 43 min (windows-capabilities). The cause was in goal.step: it blocked in
futures.wait(FIRST_COMPLETED) for as long as ANY job of its goal ran, and supervise() steps the goals one after the
other, so the second goal never got a turn (and the first logged nothing). step now waits at most ROUND_WAIT.

    heavy job     a job with "heavy": true in its spec needs the laptop's one heavy slot. While a heavy job (this or
                  another goal's) holds it, the job goes to the peer (`fieldkit task start BRIEF --on peer`, the brief
                  carrying the goal and the checker files) when `fieldkit orchestrate load` says the peer is idle; its
                  result comes back through the task ledger. "windows_only": true waits for the laptop, the log line
                  says why. A job without "heavy" runs here as before.
    heartbeat     one line per goal per round: jobs here / on the peer / waiting, the slot holder.
    alarm         no round for STALL_ROUNDS x ROUND_NOMINAL, or a goal idle over IDLE_LIMIT of the last hour: a defect
                  row in state/coordination/ZERO-OPEN-PROBLEMS.md (never the inbox) and a ROOM line.
"""
import json
import re
import threading
import time
from collections import deque
from concurrent import futures
from pathlib import Path

ROUND_WAIT = 60                 # a goal's step blocks on its workers at most this long, then the next goal gets its turn
ROUND_NOMINAL = 600             # a round with a planner call or a verifier run may take this long
STALL_ROUNDS = 3                # rounds of silence that make the alarm
IDLE_LIMIT = 0.5                # a goal idle more than this share of IDLE_WINDOW raises the alarm
IDLE_WINDOW = 3600
IDLE_MIN_SEEN = 1200            # ...once at least this much of the window was observed
PEER_PLAN = "R12"               # the plan row a job sent to the peer names (brief lint refuses a brief with none)
BUNDLE_MAX = 24000              # bytes of goal and checker files carried in a peer brief
_LOCK = threading.Lock()
_HOLDERS = {}                   # (gid, jid) -> started: heavy jobs running on THIS computer (this process)
_SEEN = {}                      # gid -> deque[(t, dt, idle)] samples of the last IDLE_WINDOW
_LAST = {"t": None, "note": ""}  # the main loop's last sign of life


# ---- the laptop's one heavy slot ------------------------------------------------------------------------------
def heavy(spec):
    return bool((spec or {}).get("heavy"))


def claim(gid, jid, now=time.time):
    with _LOCK:
        _HOLDERS[(gid, jid)] = now()


def release(gid, jid):
    with _LOCK:
        _HOLDERS.pop((gid, jid), None)


def holder():
    """-> 'goal/job' of the heavy job holding the slot here, or ''."""
    with _LOCK:
        k = next(iter(_HOLDERS), None)
    return f"{k[0]}/{k[1]}" if k else ""


def gate_busy():
    """True when the machine-wide gate (fieldkit admission) has no free slot: another session holds it."""
    try:
        from .. import admission
        return admission.state()["free"] <= 0
    except Exception:  # noqa: BLE001 - no gate to read: the registry alone decides
        return False


def pick_peer(load_all=None):
    """-> the other computer's load row when it is idle (orchestrate load), else None."""
    try:
        from .. import orchestrate_load as ol
        rows = (load_all or ol.load_all)()
        return ol.choose(rows, "", any_os=True)
    except Exception:  # noqa: BLE001 - no cable, no listener: the peer is not free
        return None


def place(spec, peer=None):
    """-> (where, why, row). where: 'here' | 'peer' | 'wait'. Only a heavy job is placed; the others run here."""
    if not heavy(spec):
        return "here", "", None
    held = holder()
    if not held and not gate_busy():
        return "here", "", None
    who = held or "another session (admission gate)"
    if spec.get("windows_only"):
        return "wait", f"windows-only: waits for the laptop, whose heavy slot {who} holds", None
    row = (peer or pick_peer)()
    if row is None:
        return "wait", f"the heavy slot is held by {who} and the peer is not idle (orchestrate load)", None
    return "peer", f"the heavy slot is held by {who}: runs on {row['name']}", row


# ---- a job on the peer ----------------------------------------------------------------------------------------
def bundle(gid, goal, root, repo="."):
    """The goal file and every checker file a verify line names (local/goal-checks/...), as text the peer's worker
    writes back under the same paths (the cable carries the brief, not a copy of the tree). -> text."""
    parts = []
    files = [(f"state/goals/{gid}/goal.json", Path(root) / gid / "goal.json")]
    seen = set()
    for c in goal.get("criteria") or []:
        for m in re.findall(r"local[/\\]goal-checks[/\\][\w./\\-]+\.py", c.get("run") or ""):
            rel = m.replace("\\", "/")
            if rel not in seen:
                seen.add(rel)
                files.append((rel, Path(repo) / rel))
    room = BUNDLE_MAX
    for rel, p in files:
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(t) > room:
            parts.append(f"FILE {rel}: {len(t)} bytes, too long to carry; fetch it with `fieldkit peers link send`")
            continue
        room -= len(t)
        parts.append(f"FILE {rel} (write it to this path in your worktree before you start):\n```\n{t}\n```")
    return "\n\n".join(parts)


def send(gid, goal, jid, spec, brief, row, root, start=None, repo="."):
    """Start the job on the peer through `fieldkit task start --on`. -> (ok, task id or why)."""
    d = Path(root) / gid / "jobs" / jid
    d.mkdir(parents=True, exist_ok=True)
    f = d / "peer-brief.md"
    f.write_text(f"# Job: goal {gid}, job {jid} (sent over the cable: the laptop's heavy slot is held)\n\n{brief}\n\n"
                 f"PLAN ITEM: {spec.get('plan') or goal.get('plan') or PEER_PLAN}\n\n"
                 f"Run on Linux in your own worktree of Gorilla.Fieldkit from origin/main. Ship with "
                 f"`fieldkit ship push --auto` and report done only after it merged.\n\n"
                 + bundle(gid, goal, root, repo) + "\n", encoding="utf-8")
    if start is None:
        from ..lifecycle import tasks
        start = tasks.start
    try:
        wall = int(((spec.get("budget") or {}).get("wall_minutes")) or 240)
        rec = start(str(f), on=row["name"], wall=wall, title=f"goal {gid} job {jid}", again=True, plan=None)
    except Exception as e:  # noqa: BLE001 - a refusal is the answer: the job stays queued
        return False, f"{type(e).__name__}: {e}"[:300]
    if rec.get("status") != "started":
        return False, str(rec.get("answer") or rec.get("status"))[:300]
    return True, rec["id"]


def remote_future(tid, on):
    fut = futures.Future()
    fut.remote = {"task": tid, "on": on}
    return fut


def poll(gid, workers, ledger=None, ingest=None):
    """Settle the peer jobs of a goal whose task reported: the future gets the worker's result. -> [jid]."""
    mine = {j: w for j, w in workers.items() if w.get("remote") and not w["future"].done()}
    if not mine:
        return []
    try:
        from ..lifecycle import tasks
        (ingest or tasks.ingest)()
        led = (ledger or tasks.ledger)()
    except Exception:  # noqa: BLE001 - cable down: the job still runs there; look again next round
        return []
    out = []
    for jid, w in mine.items():
        t = led.get(w["remote"]["task"]) or {}
        s = t.get("status")
        if s == "done":
            w["future"].set_result({"status": "done"})
        elif s in ("failed", "blocked", "not-started", "died"):
            w["future"].set_result({"status": "failed",
                                    "why": f"on {w['remote']['on']}: {s} - {t.get('note') or t.get('answer') or ''}"[:400]})
        else:
            continue
        out.append(jid)
    return out


def reattach(gid, st, workers):
    """A supervisor that restarted finds a job running on the peer: it waits for that task again, never sends a
    second copy. -> [jid] re-attached."""
    out = []
    try:
        from ..lifecycle import tasks
        led = tasks.ledger()
    except Exception:  # noqa: BLE001
        return out
    for jid, j in st["jobs"].items():
        r = j.get("remote")
        if j["status"] == "running" and r and jid not in workers and (led.get(r["task"]) or {}).get("status") == "started":
            workers[jid] = {"future": remote_future(r["task"], r["on"]), "spend": [], "remote": r}
            out.append(jid)
    return out


# ---- heartbeat and alarm --------------------------------------------------------------------------------------
def beat(note="", now=time.time):
    """The main loop (or a planner or verifier run inside it) is alive."""
    _LAST.update(t=now(), note=note)


def heartbeat_line(gid, st, workers, now=time.time):
    """-> the one line a round logs for a goal: jobs here / on the peer / waiting, and the slot holder."""
    here = sorted(j for j, w in workers.items() if not w.get("remote"))
    peer = sorted(f"{j}@{w['remote']['on']}" for j, w in workers.items() if w.get("remote"))
    wait = sorted(j for j, v in (st.get("jobs") or {}).items() if v["status"] in ("queued", "paused-resumable"))
    return (f"HEARTBEAT {gid}: here {here or '-'}; peer {peer or '-'}; waiting {wait or '-'}; "
            f"slot holder {holder() or 'none'}")


def sample(gid, st, workers, now=time.time):
    """Record one round of a goal: dt since the last sample and whether the goal was idle. Idle = nothing of it ran
    here or on the peer while it was not waiting for a reason (an owner answer, a pause, its budget)."""
    t = now()
    q = _SEEN.setdefault(gid, deque())
    last = q[-1][0] if q else t
    reason = bool(st.get("paused_until")) or any(qq not in (st.get("answers") or {}) for qq in st.get("questions") or {}) \
        or st.get("status") not in ("active", "waiting")
    idle = not workers and not reason
    q.append((t, max(0.0, t - last), idle))
    while q and q[0][0] < t - IDLE_WINDOW:
        q.popleft()


def idle_share(gid, now=time.time):
    """-> (share, seen seconds) of the last IDLE_WINDOW."""
    t = now()
    rows = [r for r in _SEEN.get(gid, ()) if r[0] >= t - IDLE_WINDOW]
    seen = sum(r[1] for r in rows)
    return (sum(r[1] for r in rows if r[2]) / seen if seen else 0.0), seen


def problems_path():
    from ..core.settings import state_root
    return state_root(Path(__file__).resolve().parents[2]) / "state" / "coordination" / "ZERO-OPEN-PROBLEMS.md"


def alarm(key, text, path=None, room=None, now=time.time):
    """A defect row in ZERO-OPEN-PROBLEMS.md (never the inbox) and a ROOM line; the same key once per hour.
    -> True when a row was written."""
    p = Path(path or problems_path())
    tag = f"[alarm {key} {time.strftime('%Y-%m-%d %H', time.localtime(now()))}h]"
    try:
        body = p.read_text(encoding="utf-8") if p.is_file() else ""
    except OSError:
        body = ""
    if tag in body:
        return False
    nums = [int(m) for m in re.findall(r"^\|\s*(\d+)\s*\|", body, re.M)]
    row = f"| {max(nums, default=0) + 1} | {tag} {text} | OPEN (R12) | read state/goals/supervisor.log |\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(("" if body.endswith("\n") or not body else "\n") + row)
    if room is None:
        from ..lifecycle import tasks
        room = tasks._room
    room(f"goal-supervisor:{key}", f"ALARM {text}")
    return True


def check_alarms(goals, now=time.time, path=None, room=None):
    """Called from the supervisor's own heartbeat thread, so it runs when the main loop is silent. -> [text]."""
    t = now()
    out = []
    if _LAST["t"] is not None and t - _LAST["t"] > STALL_ROUNDS * ROUND_NOMINAL:
        out.append(("silent", f"the goal supervisor's main loop has been silent for {int((t - _LAST['t']) / 60)} min "
                              f"(last: {_LAST['note'] or '-'}); more than {STALL_ROUNDS} rounds"))
    for gid in goals:
        share, seen = idle_share(gid, now)
        if seen >= IDLE_MIN_SEEN and share > IDLE_LIMIT:
            out.append((f"idle-{gid}", f"goal {gid} was idle {share:.0%} of the last {int(seen / 60)} min "
                                       f"(limit {IDLE_LIMIT:.0%}) with no job running here or on the peer"))
    return [text for key, text in out if alarm(key, text, path=path, room=room, now=now)]
