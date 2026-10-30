"""Start a job from a brief file with no click: a headless autopilot run here, or the peer's link listener over the
cable. Never a desktop session card (2026-10-26: a session-started turn cannot start a desktop session, so relief and
side jobs became cards the owner had to click, on the VAIO and on Windows).

    here       a one-step runbook (`agent:` = the brief) under fieldkit autopilot, in its own window: the hard lines,
               the budget, checkpoints and the owner inbox hold; `fieldkit ship push` is open under the mandate
    --on DEV   verb `ask` over the cable (HMAC-signed, cable only): the peer's link listener starts `fieldkit
               autopilot claude` under ITS owner's approved link.yaml. No session on the peer is asked anything, so
               none can refuse a relayed order or ask its owner (2026-10-26 18:4x: the VAIO session did both)
    --on DEV --run --tree DIR -- LINE
               no AI (HARNESS-PLAN C3 a): the tree and a `run` job go over the cable, the worker runs the build or
               test line on a copy and signs the exit code, test count, artefact sha256s and log tail (start_run).
               A model only where one is needed: a brief is an AI task, a command line is not

Every brief gets the side-jobs rule and the done protocol appended (protocol.brief_tail) with the task's id, so the
job reports back by itself: here `fieldkit task report ID ...`, on a peer `fieldkit peers link send "fieldkit-task ID
done ..."`, which `fieldkit task status` reads from the link inbox. Every start and report is a ROOM line (every
session, the command post included, sees it at its next hook) and a ledger line in state/lifecycle/tasks.jsonl.
"""
import hashlib
import json
import os
import re
import socket
import time
from pathlib import Path

from . import protocol

REPORT = re.compile(r"fieldkit-task (?P<id>T-[\w.-]+) (?P<status>done|failed|blocked)"
                    r"(?: branch=(?P<branch>\S+))?(?: pr=(?P<pr>\S+))?(?: (?P<note>.*))?")
STATUSES = ("started", "done", "failed", "blocked")
SLACK_MINUTES = 30                      # past wall + this, a started task cannot still run: the budget stopped it


class AlreadyStarted(ValueError):
    """The same brief is started and not finished; the message names the task."""


def state_dir():
    from .friction import state_dir as d
    return d()


def ledger_path():
    return state_dir() / "tasks.jsonl"


def ledger():
    """-> {id: the task, its later reports merged in order}."""
    out = {}
    try:
        lines = ledger_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        out.setdefault(r["id"], {}).update({k: v for k, v in r.items() if v not in (None, "")})
    return out


def _append(rec):
    with open(ledger_path(), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _room(obj, detail):
    try:
        from ..autopilot import room
        from ..peers import machine                    # the line says which computer wrote it ([VAIO-LNX] task ...)
        room.append(room.line_for(os.environ.get("CLAUDE_CODE_SESSION_ID", "fieldkit-task"), os.getcwd(), "start",
                                  obj, machine.prefix(detail, tag_for())))
    except Exception:  # noqa: BLE001 - the ledger is the record; the room line is a courtesy
        pass


def task_id(brief_text, now=None):
    return (f"T-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-"
            f"{hashlib.sha256(brief_text.encode('utf-8')).hexdigest()[:6]}")


def tag_for(on=None):
    """-> the machine tag of where the task runs: this machine, or the device it was sent to (peers/machine.py)."""
    try:
        from ..peers import machine
        return machine.tag_of(on) if on else machine.current()
    except Exception:  # noqa: BLE001 - no registry: no tag, said by the check (fieldkit sessions tag --check)
        return ""


def full_brief(text, tid, on=None, title=""):
    head = f"\nSESSION TITLE: {title} (title yourself exactly this; it starts with the machine's tag)\n" if title else ""
    back = (f"\nREPORT BACK (the command post reads it; do this before you exit): `fieldkit peers link send "
            f"\"fieldkit-task {tid} done branch=<branch> <one line>\"` (failed or blocked instead of done when so).\n"
            if on else "")
    return text.rstrip() + "\n" + head + protocol.brief_tail(None if on else tid, headless=True) + back


def runbook_text(tid, brief, cwd, wall, model=None):
    import yaml
    step = {"id": "work", "title": f"task {tid}", "agent": brief, "cwd": str(cwd),
            "budget": {"wall_minutes": int(wall)}}
    if model:
        step["model"] = model                  # the worker's model, from roles.yaml or --model (HARNESS-PLAN I5)
    return (f"# fieldkit task {tid}\n\nWritten by `fieldkit task start`; run headless by fieldkit autopilot.\n\n"
            "```runbook\n" + yaml.safe_dump({"steps": [step]}, sort_keys=False, allow_unicode=True) + "```\n")


def brief_hash(text):
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def running_twin(h, title, now=None, tasks=None):
    """-> the started task with this brief hash or title that may still run, or None. A task whose process is gone
    (settle) or whose wall budget plus SLACK_MINUTES has passed does not count."""
    tasks = ledger() if tasks is None else tasks
    settle(tasks)
    now = time.time() if now is None else now
    for t in tasks.values():
        if t.get("status") != "started" or not (t.get("hash") == h or t.get("title") == title):
            continue
        try:
            age = (now - time.mktime(time.strptime(t["at"], "%Y-%m-%d %H:%M:%S"))) / 60
        except (KeyError, ValueError):
            age = 0
        if age <= int(t.get("wall") or 120) + SLACK_MINUTES:
            return t
    return None


class Parked(ValueError):
    """The brief names a branch or PR that failed CI twice (TWO STRIKES); the message says which."""


def start(brief_path, on=None, cwd=None, wall=120, title=None, call=None, launch=None, now=None, again=False,
          worktree=None, model=None, accept=None, allow_expensive=False, unpark=None):
    """-> the ledger record. Raises ValueError when the brief is missing or empty or the worker model is an expensive
    one, AlreadyStarted when the same brief is still started somewhere (unless again). `worktree`: (repo, tid) ->
    (path or None, why); default worktrees.make. `model`: the worker's model (default: roles.yaml worker.default);
    `accept`: the acceptance command a reviewer verdict needs before the task counts as done (I5, I7, gate())."""
    p = Path(brief_path)
    text = p.read_text(encoding="utf-8").strip() if p.is_file() else ""
    if not text:
        raise ValueError(f"no brief in {brief_path}")
    from ..method import strikes
    if unpark:                                         # owner-only flag; strikes logs it
        strikes.unpark(unpark, "task start --unpark")
    hits = strikes.named_in(text)
    if hits:
        raise Parked(strikes.refusal(hits))
    tid = task_id(text, now)
    from . import roles
    model = roles.worker_model(model, allow_expensive=allow_expensive)
    from . import models
    model = models.require(model, "worker", "task start" + (f" --on {on}" if on else ""))   # a hard refusal, logged
    from ..peers import machine
    title = machine.prefix(title or text.splitlines()[0][:100], tag_for(on))
    h = brief_hash(text)
    twin = None if again else running_twin(h, title, now)
    if twin:
        raise AlreadyStarted(f"already started: {twin['id']} on {twin.get('on')} at {twin.get('at')} "
                             f"({twin.get('title', '')[:60]}); `fieldkit task status` says how it stands; "
                             "--again starts it anyway")
    brief = full_brief(text, tid, on, title)
    rec = {"id": tid, "title": title, "brief": str(p.resolve()), "on": on or "here", "tag": tag_for(on),
           "from": socket.gethostname(), "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
           "status": "started", "hash": h, "wall": int(wall), "model": model}
    if accept:
        rec.update(accept=accept, accept_cwd=str(Path(cwd or os.getcwd()).resolve()), failures=0)
    if on:
        from ..peers import link
        r = (call or link.call)({"verb": "ask", "agent": "claude", "brief": brief, "model": model}, timeout=30,
                                device=on)
        ok = bool(r.get("ok") and r.get("started"))
        rec.update(how="link ask", answer=r.get("why") or r.get("error") or "",
                   status="started" if ok else "not-started")
    else:
        d = state_dir() / "tasks" / tid
        d.mkdir(parents=True, exist_ok=True)
        rb = d / f"{tid}.md"
        from . import worktrees
        try:
            wt, wwhy = (worktree or worktrees.make)(Path(cwd or os.getcwd()).resolve(), tid)
        except worktrees.Refused as e:
            rec.update(how="autopilot runbook", status="not-started", answer=f"no worktree of its own: {e}"[:300])
            _append(rec)
            _room(f"task:{tid}", f"task not-started {rec['on']}: {rec['title']} ({rec['answer']})")
            return rec
        rec.update(worktree=str(wt) if wt else "", worktree_why=wwhy)
        rb.write_text(runbook_text(tid, brief, wt or Path(cwd or os.getcwd()).resolve(), wall, model),
                      encoding="utf-8")
        if launch is None:
            from ..buildh import window
            launch = window.launch
        try:
            r = launch(["runbook", "run", str(rb)])
            rec.update(how="autopilot runbook", runbook=str(rb), pid=r.get("pid"), log=r.get("log"))
            worktrees.own(wt, r.get("pid"), tid)
        except Exception as e:  # noqa: BLE001 - the refusal is the answer, recorded
            rec.update(how="autopilot runbook", runbook=str(rb), status="not-started", answer=str(e)[:300])
            worktrees.own(wt, -1, tid)               # no process: reap takes the tree back once it is clean
    _append(rec)
    _room(f"task:{tid}", f"task {rec['status']} {rec['on']}: {rec['title']} ({rec.get('answer') or rec.get('how')})")
    return rec


def start_run(argv, on, tree, project="Gorilla.Fieldkit", artefacts=(), wall=60, send=None, now=None, again=False):
    """A task with no AI (HARNESS-PLAN C3 a): plain heavy work, a build or test line, on a device of the household,
    which needs no model and no agent there (the VAIO and the Mac run no local model). The tree goes first, then a
    `run` job (fieldkit/orchestrate_work.py): `fieldkit ARGS` is a Fieldkit command line, anything else a build or
    test program (orchestrate_work.PROGRAMS). The worker signs a result with the exit code, the test count, every
    artefact's sha256 and the log tail; `fieldkit task status` turns it into done or failed (collect_jobs).
    -> the ledger record. Raises ValueError for a bad line, AlreadyStarted as start() does."""
    from .. import orchestrate_work as ow
    argv = [str(a) for a in argv or []]
    if not on:
        raise ValueError("a run task goes to a device (--on DEVICE); here, run the line itself (fieldkit emerge, "
                         "fieldkit guard --)")
    if not argv or not tree or not Path(tree).exists():
        raise ValueError(f"a run task needs the line after -- and an existing --tree (got {tree!r})")
    plain = argv[0] != "fieldkit"
    line = argv if plain else argv[1:]
    from ..peers import machine
    title = machine.prefix(f"run: {' '.join(argv)}"[:100], tag_for(on))
    blob = json.dumps([line, plain, str(Path(tree).resolve()), list(artefacts), on])
    h = brief_hash(blob)
    twin = None if again else running_twin(h, title, now)
    if twin:
        raise AlreadyStarted(f"already started: {twin['id']} on {twin.get('on')} at {twin.get('at')} "
                             f"({twin.get('title', '')[:60]}); `fieldkit task status` says how it stands; "
                             "--again starts it anyway")
    tid = task_id(blob, now)
    jid, said, t = (send or ow.send)("run", on, str(tree), project, argv=line, artefacts=list(artefacts), wall=wall,
                                     plain=plain)
    rec = {"id": tid, "title": title, "brief": "", "on": on, "tag": tag_for(on), "from": socket.gethostname(),
           "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "hash": h, "wall": int(wall),
           "how": "link job run (no AI)", "job": jid or "", "project": project,
           "digest": (t or {}).get("digest") or "", "answer": said,
           "status": "started" if said == "sent" or str(said).startswith("already") else "not-started"}
    _append(rec)
    _room(f"task:{tid}", f"task {rec['status']} {on}: {title} ({said})")
    return rec


def collect_jobs(tasks, result_of=None, check=None):
    """Each started run task whose job has a result here -> a done or failed report, once. done needs a result
    signed by a pinned worker key, status pass, and every artefact here with the sha256 the worker signed (the boss
    checks the artefact, not the exit code). -> [report records]."""
    from .. import orchestrate_work as ow
    from ..mandate import jobs
    result_of = result_of or ow.verified_result
    check = check or ow.artefacts_check
    led = None
    out = []
    for t in tasks.values():
        if t.get("status") != "started" or not t.get("job"):
            continue
        led = jobs._ledger() if led is None else led
        e = led.get(t["job"]) or {}
        if e.get("status") in (None, "sent"):
            continue
        r = result_of(t["job"], t.get("project") or "Gorilla.Fieldkit", t.get("digest") or "")
        if r is None:
            status, note = "failed", f"the worker said {e.get('status')}, but no result signed by a pinned worker key"
        else:
            bad = [f"{n}: {why}" for n, ok, why in check(r) if not ok]
            first = (r.get("evidence") or "").splitlines()[:1]
            status = "done" if r.get("status") == "pass" and not bad else "failed"
            note = "; ".join([f"{r.get('status')} on {r.get('host')}", *first,
                              *[c.get("text", "") for c in r.get("concerns") or []], *bad])
        rec = report(t["id"], status, note=note, source=f"job {t['job']}")
        t.update(status=status, note=rec["note"])
        out.append(rec)
    return out


def gate(tid, task, result, reviewer=None, fixer=None):
    """C3b: an AI task that has an acceptance command is done only on a passing verdict (roles.py). -> the verdict
    {kind, why}; a task without one is not gated (None). The worker's own report of done is only the claim; the
    verdict is recorded on the task (verdict, failures) and sent back/fixed by the reviewer per roles.yaml."""
    if not task.get("accept"):
        return None
    from . import roles
    cwd = task.get("worktree") or task.get("accept_cwd")
    failures = int(task.get("failures") or 0)
    brief = Path(task["brief"]).read_text(encoding="utf-8") if task.get("brief") and Path(task["brief"]).is_file() \
        else task.get("title", "")
    v = (reviewer or roles.review)(brief, result, task["accept"], failures, cwd=cwd)
    if v["kind"] == "fix_itself":
        f = (fixer or roles.fix_itself)(brief, result, v["why"], task["accept"], cwd=cwd)
        v = dict(v, kind=f["kind"], why=f["why"], fixed_by=roles.reviewer_model() if f["kind"] == "pass" else "")
    return v


def report(tid, status, branch="", pr="", note="", source="local", reviewer=None, fixer=None):
    if status not in STATUSES[1:]:
        raise ValueError(f"status is one of {', '.join(STATUSES[1:])}")
    task = ledger().get(tid) or {}
    verdict = {}
    if status == "done" and task.get("accept") and source != "verdict":
        verdict = gate(tid, task, note, reviewer, fixer) or {}
        if verdict and verdict["kind"] != "pass":
            # not done: the worker's claim failed its acceptance. send_back: the task goes again (failures + 1)
            status = "failed"
            note = f"verdict {verdict['kind']}: {verdict['why']} | worker said: {note}"
    rec = {"id": tid, "status": status, "branch": branch, "pr": pr, "note": note[:300], "source": source,
           "reported": time.strftime("%Y-%m-%d %H:%M:%S")}
    if verdict:
        rec.update(verdict=verdict["kind"], verdict_why=verdict["why"][:300],
                   failures=int(task.get("failures") or 0) + (0 if verdict["kind"] == "pass" else 1))
    _append(rec)
    _room(f"task:{tid}", f"task {status}: branch {branch or '-'} PR {pr or '-'} {note[:200]}")
    board(tid)
    return rec


def attach_owner_actions(tasks, jobs=None):
    """Each task whose job ran on this computer gets `owner_actions` (its count) and `owner_causes` (HARNESS-PLAN I9;
    owner_actions.py). In place; a task that ran elsewhere is counted there and keeps no number here."""
    if jobs is None:
        try:
            from . import owner_actions
            starts = [t.get("at") for t in tasks.values() if t.get("at")]
            jobs = owner_actions.count(min(starts)[:10]) if starts else []
        except Exception:  # noqa: BLE001 - the count is a courtesy on the status; `friction jobs` says why
            return tasks
    from .owner_actions import by_task
    for tid, j in by_task(jobs).items():
        if tid in tasks:
            tasks[tid]["owner_actions"] = j["count"]
            tasks[tid]["owner_causes"] = [f"{a['cause']}: {a['detail']}" for a in j["actions"]]
    return tasks


def board(tid, jobs=None):
    """One row per task in state/coordination/PLAN.md (only when the plan exists: a missing plan is said, not made)."""
    t = attach_owner_actions({tid: dict(ledger().get(tid) or {})}, jobs).get(tid) or {}
    try:
        from ..tokens.rotate import coordination_dir
        plan = coordination_dir() / "PLAN.md"
    except Exception:  # noqa: BLE001
        return False
    if not plan.is_file():
        return False
    cell = lambda s: " ".join(str(s or "-").split()).replace("|", "/")   # noqa: E731
    from ..peers import machine
    title = machine.prefix(t.get("title"), t.get("tag") or tag_for(None if t.get("on") in (None, "here") else t["on"]))
    with open(plan, "a", encoding="utf-8") as f:
        f.write(f"| Task (fieldkit task) {cell(tid)} | {cell(t.get('on'))} | {cell(t.get('status'))} | "
                f"{cell(title)} | branch {cell(t.get('branch'))}, PR {cell(t.get('pr'))} | "
                f"{cell(t.get('note'))}{owner_cell(t)} |\n")
    return True


def owner_cell(t):
    """'; owner actions N' (WARN above 0) when the task was counted here; nothing when it ran elsewhere."""
    if "owner_actions" not in t:
        return ""
    from .owner_actions import label
    return f"; {label(t['owner_actions'])}"


def ingest(items=None):
    """Reports that came over the cable ("fieldkit-task ID done ...") -> the ledger, once each. -> [records]."""
    if items is None:
        try:
            from ..peers import link
            items = link.inbox()
        except Exception:  # noqa: BLE001
            return []
    have = ledger()
    seen = {r.get("link_n") for r in have.values()}
    out = []
    for i in items:
        m = REPORT.search(i.get("text") or "")
        if not m or m["id"] not in have or f"{i.get('from_host')}:{i.get('n')}" in seen:
            continue
        rec = {"id": m["id"], "status": m["status"], "branch": m["branch"] or "", "pr": m["pr"] or "",
               "note": (m["note"] or "")[:300], "source": f"link {i.get('from_host')}",
               "link_n": f"{i.get('from_host')}:{i.get('n')}", "reported": i.get("at")}
        _append(rec)
        board(m["id"])
        out.append(rec)
    return out


def settle(tasks, alive=None):
    """A task started here whose process is gone and that never reported is `died`, not `started` forever
    (2026-10-28: 5 of 12 ledger tasks said "started" for days; one died in the 10-27 17:10 cutoff). In place; -> the
    ids it marked. A peer task is the peer's to report."""
    if alive is None:
        from ..peers.peers import pid_alive as alive
    out = []
    for t in tasks.values():
        if t.get("status") == "started" and t.get("on") == "here" and t.get("pid"):
            try:
                gone = not alive(int(t["pid"]))
            except (OSError, ValueError):
                gone = False
            if gone:
                t["status"] = "died"
                t.setdefault("answer", f"process {t['pid']} ended with no report; its log: {t.get('log') or '-'}")
                out.append(t["id"])
    return out


def lines(tasks):
    L = []
    for t in tasks.values():
        L.append(f"{t['id']}  {t.get('status', '?'):<11} {t.get('on', '?'):<10} {t.get('title', '')[:60]}"
                 + (f"  branch {t['branch']}" if t.get("branch") else "") + (f"  PR {t['pr']}" if t.get("pr") else "")
                 + (f"  ({t['answer'][:120]})" if t.get("status") in ("not-started", "died") else "")
                 + (f"  {owner_cell(t)[2:]}" if "owner_actions" in t else ""))
    return L or ["no tasks"]
