# Version: 1.0.0 · updated 26-10-23-15-41
"""A long project on the boss: the task list, the startup check, and the one morning report (plan section 17.2).

    fieldkit mandate tasks init TASKS.yaml       the initializer: every task written down, all FAILING (item 3)
    fieldkit mandate tasks list                  each task, passing or failing, with its evidence
    fieldkit mandate tasks pass ID --evidence TEXT [--screenshot FILE]    passing only with evidence (item 6)
    fieldkit mandate start                       the startup check of a session: ledger, smoke test, the next thing (item 5)
    fieldkit mandate report [--since N]          the one morning report, from the ledger (the owner reads one page)

Sources the owner approved (2026-10-23): Anthropic's "effective harnesses for long-running agents" (an initializer
writes the whole feature list marked failing; a session starts by reading progress and running a smoke test; a
feature is done only when tested as a user would, never on the model's word) and "managed agents" (an append-only
session log). Tasks are never removed: a task once written stays, as the tests may not shrink (proofs.py measures the
suite against main). A task that `needs: screenshot` passes only with a real PNG: "tested as a person would".

TASKS.yaml:  tasks: [{id: layout-air, what: "the banner is whole on the Air at 167x39", needs: screenshot}, ...]
"""
import json
import time
from pathlib import Path

import yaml

PNG = b"\x89PNG\r\n\x1a\n"


def _link():
    from ..peers import link
    return link


def tasks_path():
    return _link().home() / "tasks.json"


def _load():
    try:
        return json.loads(tasks_path().read_text(encoding="utf-8")) if tasks_path().is_file() else {}
    except ValueError:
        return {}


def _save(t):
    tasks_path().parent.mkdir(parents=True, exist_ok=True)
    tasks_path().write_text(json.dumps(t, indent=1, ensure_ascii=False), encoding="utf-8")


def init(path):
    """Write every task, all failing. A task already written is kept as it is (never removed, never reset)."""
    from . import jobs
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    t, added = _load(), []
    for item in spec.get("tasks") or []:
        tid = str(item.get("id") or "")
        if not jobs.ID.fullmatch(tid):
            raise ValueError(f"a task needs an id of letters, digits, . _ - : {item!r}")
        if tid not in t:
            t[tid] = {"what": str(item.get("what", "")), "needs": item.get("needs"), "status": "failing",
                      "added": time.strftime("%Y-%m-%d %H:%M:%S")}
            added.append(tid)
    _save(t)
    jobs.event("tasks-written", added=added, total=len(t))
    return added, len(t)


def mark_pass(tid, evidence, screenshot=None):
    """-> (ok, why). Evidence is required; a task that needs a screenshot needs a real PNG of some size."""
    from . import jobs
    t = _load()
    if tid not in t:
        return False, f"no task {tid} (tasks are written by the initializer: fieldkit mandate tasks init)"
    if not str(evidence or "").strip():
        return False, "a task passes only with evidence: the test name, the VERDICT line, the command and its output"
    if t[tid].get("needs") == "screenshot":
        p = Path(screenshot or "")
        if not p.is_file() or p.stat().st_size < 10_000 or p.read_bytes()[:8] != PNG:
            return False, ("this task is done only when seen as a person sees it: give --screenshot with a real PNG "
                           "of the app (scrot on Linux, fieldkit mac shot on the Mac)")
    t[tid].update(status="passing", evidence=str(evidence)[:500], screenshot=str(screenshot) if screenshot else None,
                  passed=time.strftime("%Y-%m-%d %H:%M:%S"))
    _save(t)
    jobs.event("task-passing", id=tid, evidence=str(evidence)[:200], screenshot=str(screenshot) if screenshot else None)
    return True, f"{tid} passing"


def task_lines():
    t = _load()
    out = [f"  {e['status']:8} {tid}: {e['what']}" + (f"  [{e['evidence'][:80]}]" if e.get("evidence") else "")
           for tid, e in t.items()]
    done = sum(1 for e in t.values() if e["status"] == "passing")
    return out + [f"  {done} of {len(t)} passing"] if t else ["  no task list yet: fieldkit mandate tasks init TASKS.yaml"]


def start(smoke=None, health_fn=None):
    """The startup check (item 5): what the ledger says happened last, the mandate in force, the link, a smoke test,
    and the one next thing. -> lines, the next thing."""
    from . import jobs
    from . import mandate as M
    out = []
    last = (jobs.events() or [{}])[-1]
    out.append(f"LEDGER: {len(jobs.events())} event(s); last #{last.get('n', 0)} {last.get('what', 'none')} "
               f"at {last.get('at', '-')}")
    force = M.in_force()
    out.append("MANDATE: " + (", ".join(p.stem for p in force) + " in force" if force else "none in force here"))
    from . import upkeep
    w = upkeep.warning()
    if w:
        out.append(w)                                     # the expiry warning, WARN_DAYS before the end (one line)
    if smoke is None:
        try:
            smoke = _link().call({"verb": "hello"})
            out.append(f"SMOKE: the link answers ({smoke.get('host', '?')})" if smoke.get("ok") else
                       f"SMOKE: the link does not answer ({smoke.get('error')})")
        except Exception as e:  # noqa: BLE001
            out.append(f"SMOKE: the link does not answer ({e})")
    open_jobs = [j for j, e in jobs._ledger().items() if e.get("status") in (None, "sent")]
    failing = [tid for tid, e in _load().items() if e["status"] != "passing"]
    nxt = (f"wait for open job(s) {', '.join(open_jobs)}: fieldkit mandate job wait {open_jobs[0]}" if open_jobs else
           f"work on the first failing task: {failing[0]}" if failing else
           "every task passes: release (fieldkit release-cycle) or report" if _load() else
           "write the task list: fieldkit mandate tasks init TASKS.yaml")
    out.append(f"NEXT: {nxt}")
    return out, nxt


def report(since=0):
    """The one morning report: what was done, what failed, every concern with its fix, what waits for the owner."""
    from . import jobs
    ev = jobs.events(since)
    results = [e for e in ev if e["what"] == "result"]
    concerns = [(e.get("worker"), c) for e in results for c in e.get("concerns") or []]
    t = _load()
    passing = [tid for tid, e in t.items() if e["status"] == "passing"]
    failing = [tid for tid, e in t.items() if e["status"] != "passing"]
    open_jobs = [j for j, e in jobs._ledger().items() if e.get("status") in (None, "sent")]
    out = [f"REPORT from event #{since + 1} to #{ev[-1]['n'] if ev else since}",
           f"  tasks: {len(passing)} of {len(t)} passing" + (f"; failing: {', '.join(failing[:8])}" if failing else ""),
           f"  jobs: {sum(1 for e in ev if e['what'] == 'job-sent')} sent, {len(results)} result(s): "
           + ", ".join(f"{e.get('id')} {e.get('status')}" for e in results[:8])]
    out += [f"  concern from {w}: {c}" for w, c in concerns[:12]]
    if open_jobs:
        out.append(f"  still open: {', '.join(open_jobs)}")
    resumed = [e for e in ev if e["what"] == "boss-resumed"]
    out += [f"  the boss restarted at {e['at']}; open then: {', '.join(e.get('open') or [])}" for e in resumed]
    asks = [e for e in ev if e["what"] == "ask"]                # what the mandate's ask_when sent to the owner
    out.append("  NEEDS YOU: " + ("nothing" if not asks else
                                  "; ".join(str(e.get("why", e)) for e in asks[:6])))
    return out
