# Version: 1.0.0 · updated 26-10-26
"""Zero friction: nothing waits on the owner's click, and finished or idle sessions do not sit eating CPU and RAM.

    fieldkit friction [--here] [--json]          everything that can wait on a person, here and on every peer over
                                                 the cable; one line each with the fix; exit 0 none, 1 some
    fieldkit friction ack KEY [KEY ...]          a line that is handled (state/lifecycle/friction-ack.json)
    fieldkit friction jobs [--since DATE] [--json]   owner actions per job, counted, with each cause (target 0;
                                                 WARN above it; exit 3 when any): what DID wait on the owner
    fieldkit task start BRIEF.md [--on DEVICE] [--cwd DIR] [--wall MIN] [--title T]
                                                 a headless job from a brief: autopilot here, the peer's link
                                                 listener with --on; never a desktop card
    fieldkit task start --on DEVICE --run --tree DIR [--artefact GLOB] [--wall MIN] -- LINE
                                                 no AI: a build or test line run on a copy of DIR there, the
                                                 worker's signed result checked by `task status`
    fieldkit task watch [--once] [--act]         Z1 NO PASSIVE WAITING: UNHANDLED / STALLED / DIED tasks, exit 3 (watch.py)
    fieldkit task status [--json]                every task, with the reports that came back (here and over the cable)
    fieldkit task start BRIEF.md --model sonnet --accept "CMD"   worker model; done only on a reviewer pass + CMD
    fieldkit task report ID --status done|failed|blocked [--branch B] [--pr URL] [--note TEXT]
                                                 the job's own report: ledger, ROOM line, Board row
    fieldkit sessions sweep [--idle-minutes N] [--keep ID] [--json]
                                                 finished / idle desktop sessions with their measured RAM and CPU,
                                                 for the command post to archive; exit 1 when one is to archive;
                                                 a finished one without its Board row says so
    fieldkit board check|add [SESSION ... | --missing] [--date D]
                                                 every finished session has its row in PLAN.md (plan R3)
    fieldkit sessions protocol [--task ID] [--headless]   the done protocol every brief ends with
    fieldkit sessions rule [--install PATH ... | --all]   the standing rule block in the agents' instruction files
    fieldkit sessions doctor                    rule files, the desktop session store, the link, the ledger
    fieldkit sessions tag --check [--here]      every live session (here and the peers') whose title lacks its
                                                 machine's tag ([VAIO-LNX]); exit 1 if any (fieldkit/peers/machine.py)

Owner, 2026-10-26 (verbatim): "i have noticed the other linux machine asked for my approval. this should not have
happened. We want zero friction. Long horizon tasks run unattended and will typically not have anyone waiting 5 days
in front of the computer for those moments when an approval might be needed. We need to fix that part of the problem
on both sides, actually on all the sides involved in the process."

Zero tokens when quiet: plain code, no model. Read-only on the desktop app's files and transcripts. The only network
is the household link (cable, HMAC-signed) to the peers the owner paired.
"""
import argparse
import json
import os
import sys

from . import friction, owner_actions, protocol, sweep, tasks


def _gb(n):
    return f"{n / 2**30:.2f} GB"


def cmd_friction(argv):
    if argv[:1] == ["jobs"]:
        return owner_actions.main(argv[1:])
    ap = argparse.ArgumentParser(prog="fieldkit friction", description=friction.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", choices=["check", "ack", "local"], default="check")
    ap.add_argument("keys", nargs="*")
    ap.add_argument("--here", action="store_true", help="this computer only (no peers)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "ack":
        if not a.keys:
            print("friction ack KEY [KEY ...]: the key is in --json output")
            return 2
        friction.ack(a.keys)
        print(f"acknowledged {len(a.keys)}")
        return 0
    result = friction.audit(peers=not a.here and a.action != "local")
    n = sum(len(r) for _, r in result)
    if a.json:
        print(json.dumps([{"where": w, "rows": r} for w, r in result], indent=1, ensure_ascii=False))
    else:
        print("\n".join(friction.lines(result)))
    return 1 if n else 0


def cmd_task(argv):
    ap = argparse.ArgumentParser(prog="fieldkit task", description=tasks.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["start", "status", "report", "watch"])
    ap.add_argument("arg", nargs="?", help="start: the brief file; report: the task id")
    ap.add_argument("--on", help="start: a device of the household (the job runs there, over the cable)")
    ap.add_argument("--cwd", help="start here: the folder the job works in (default: this one)")
    ap.add_argument("--wall", type=int, default=120, help="start here: wall-clock budget in minutes")
    ap.add_argument("--title")
    ap.add_argument("--model", help="start: the worker's model (default: roles.yaml worker.default; Opus/Fable "
                                    "are reviewers and refused as workers)")
    ap.add_argument("--allow-expensive", action="store_true", help="start: let the worker be an expensive model")
    ap.add_argument("--accept", help="start: the acceptance command; done needs a passing reviewer verdict on it")
    ap.add_argument("--again", action="store_true", help="start: even though the same brief is started and unfinished")
    ap.add_argument("--status", choices=list(tasks.STATUSES[1:]))
    ap.add_argument("--branch", default="")
    ap.add_argument("--pr", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--run", action="store_true", help="start --on: no AI, the line after -- on --tree there "
                                                       "(`fieldkit ARGS`, or a build or test program)")
    ap.add_argument("--tree", help="start --run: the folder the line works on (sent first)")
    ap.add_argument("--artefact", action="append", default=[], help="start --run: a glob to send back (repeat)")
    ap.add_argument("--project", default="Gorilla.Fieldkit", help="start --run: the repository the mandate names")
    ap.add_argument("--once", action="store_true", help="watch: look once (default: look every --every s until a task needs you)")
    ap.add_argument("--act", action="store_true", help="watch: ping silent peers, add owner-inbox lines, write the findings Zero's hook reads")
    ap.add_argument("--stalled", type=int, default=10, help="watch: minutes of silence that make a running task STALLED")
    ap.add_argument("--hours", type=int, default=24, help="watch: an ended task older than this is history")
    ap.add_argument("--every", type=int, default=60, help="watch: seconds between looks")
    ap.add_argument("--scheduled", action="store_true", help="watch: run by the timer (once, prints only findings)")
    ap.add_argument("--schedule", action="store_true", help="watch: show the 5-minute timer (with --apply: install it)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--remove", action="store_true", help="watch: remove the timer")
    ap.add_argument("--json", action="store_true")
    line = []
    if "--" in argv:
        i = argv.index("--")
        argv, line = argv[:i], argv[i + 1:]
    a = ap.parse_args(argv)
    if a.action == "start" and a.run:
        try:
            rec = tasks.start_run(line, a.on, a.tree, a.project, a.artefact, a.wall, again=a.again)
        except (ValueError, tasks.AlreadyStarted) as e:
            print(f"NOT STARTED  {e}")
            return 3 if isinstance(e, tasks.AlreadyStarted) else 2
        print(json.dumps(rec, indent=1, ensure_ascii=False) if a.json else
              f"TASK {rec['id']}  {rec['status']} on {rec['on']} ({rec['how']}): job {rec['job'] or '-'}: "
              f"{rec['answer']}\nNEXT: fieldkit task status (done or failed once the worker's signed result is here)")
        return 0 if rec["status"] == "started" else 3
    if a.action == "start":
        if not a.arg:
            print("task start BRIEF.md [--on DEVICE]")
            return 2
        try:
            rec = tasks.start(a.arg, on=a.on, cwd=a.cwd, wall=a.wall, title=a.title, again=a.again,
                             model=a.model, accept=a.accept, allow_expensive=a.allow_expensive)
        except tasks.AlreadyStarted as e:
            print(f"NOT STARTED  {e}")
            return 3
        except ValueError as e:
            print(f"NOT STARTED  {e}")
            return 2
        print(json.dumps(rec, indent=1, ensure_ascii=False) if a.json else
              f"TASK {rec['id']}  {rec['status']} on {rec['on']} ({rec.get('how')}): "
              f"{rec.get('answer') or rec.get('log') or rec.get('runbook') or ''}")
        return 0 if rec["status"] == "started" else 3
    if a.action == "report":
        if not (a.arg and a.status):
            print("task report ID --status done|failed|blocked [--branch B] [--pr URL] [--note TEXT]")
            return 2
        rec = tasks.report(a.arg, a.status, a.branch, a.pr, a.note)
        print(f"TASK {rec['id']}  {rec['status']}" + (f"  verdict {rec['verdict']}: {rec['verdict_why']}"
                                                       if rec.get("verdict") else ""))
        return 0 if rec["status"] == a.status else 3
    if a.action == "watch":
        from . import watch
        return watch.main(a)
    new = tasks.ingest()
    done = tasks.collect_jobs(tasks.ledger())          # run jobs whose signed result came back
    from . import worktrees
    gone = worktrees.reap(a.cwd or os.getcwd())          # job worktrees whose work is on origin
    led = tasks.ledger()
    tasks.settle(led)
    tasks.attach_owner_actions(led)
    if a.json:
        print(json.dumps(list(led.values()), indent=1, ensure_ascii=False))
    else:
        print("\n".join(tasks.lines(led)))
        if new:
            print(f"{len(new)} report(s) came over the cable")
        if done:
            print(f"{len(done)} run job result(s) checked")
        for w in gone["removed"]:
            print(f"worktree removed (its work is on origin): {w}")
        for w, why in gone["kept"]:
            print(f"worktree kept: {w} ({why})")
    return 0


def main(argv=None, prog="fieldkit sessions"):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["friction"]:
        return cmd_friction(argv[1:])
    if argv[:1] == ["task"]:
        return cmd_task(argv[1:])
    if argv[:1] == ["board"]:                     # every finished session leaves its Board row (plan R3)
        from . import board
        return board.main(argv[1:])
    if argv[:1] == ["tag"]:                       # session titles carry their machine's tag (peers/machine.py)
        from ..peers import machine
        return machine.sessions_main(argv[1:])
    ap = argparse.ArgumentParser(prog=prog, description="\n\n".join(__doc__.split("\n\n")[:2]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("sweep")
    s.add_argument("--idle-minutes", type=int)
    s.add_argument("--keep", action="append", default=[])
    s.add_argument("--interval", type=float, default=1.0, help="CPU is measured over this many seconds (0: not)")
    s.add_argument("--json", action="store_true")
    p = sub.add_parser("protocol")
    p.add_argument("--task")
    p.add_argument("--headless", action="store_true")
    r = sub.add_parser("rule")
    r.add_argument("--install", nargs="*", metavar="PATH")
    r.add_argument("--all", action="store_true", help="install into every agent's global file that exists")
    sub.add_parser("doctor")
    a = ap.parse_args(argv)
    cmd = a.cmd or "sweep"
    if cmd == "sweep":
        cfg = sweep.config()
        if getattr(a, "idle_minutes", None):
            cfg["idle_minutes"] = a.idle_minutes
        rows = sweep.sweep(interval=getattr(a, "interval", 1.0), cfg=cfg, keep=tuple(getattr(a, "keep", [])))
        if getattr(a, "json", False):
            print(json.dumps({"summary": sweep.summary(rows), "rows": rows}, indent=1, ensure_ascii=False))
        else:
            for x in rows:
                cpu = "-" if x["cpu_pct"] is None else f"{x['cpu_pct']:.0f}%"
                print(f"{x['state'].upper():<9} {x['id'][:14]}  {_gb(x['rss']):>9} {cpu:>5}  "
                      f"{x['title'][:50]:<50} {x['why']}" + (f"\n          fix: {x['fix']}" if x["fix"] else "")
                      + (f"\n          no Board row: fieldkit board add {x['cli']}" if x.get("board") == "missing"
                         else ""))
            print(sweep.summary(rows))
        return 1 if sweep.totals(rows)["finished"] else 0
    if cmd == "protocol":
        print(protocol.done_protocol(a.task, a.headless), end="")
        return 0
    if cmd == "rule":
        if a.install is None and not a.all:
            print(protocol.rule_text(), end="")
            return 0
        paths = list(a.install or [])
        if a.all:
            paths += [str(p) for p in protocol.rule_files().values() if p.is_file()]
        for x in paths:
            print(f"{protocol.install_rule(x):<8} {x}")
        return 0
    if cmd == "doctor":
        return doctor()
    return 2


def doctor():
    from . import desktop
    ok = True
    print("lifecycle 1.0.0 doctor")
    for name, path in protocol.rule_files().items():
        here = path.is_file()
        good = protocol.rule_present(path)
        if here and not good:
            ok = False
        print(f"  [{'ok' if good or not here else 'NO'}] rule in {name}: {path}"
              + ("" if good else " (agent not installed here)" if not here else
                 f" - fieldkit sessions rule --install {path}"))
    stores = desktop.store_dirs()
    print(f"  [{'ok' if stores else '--'}] desktop session store: {', '.join(map(str, stores)) or 'none (no Claude desktop app here)'}")
    try:
        from ..peers import link
        h = link.household()
        reg, why = link.approvals()
        print(f"  [{'ok' if h else '--'}] household: {len(link.others(h)) if h else 0} peer(s); link.yaml: {why}"
              + (f", allow_start {reg.get('allow_start')}" if reg else ""))
    except Exception as e:  # noqa: BLE001
        print(f"  [--] link: {e}")
    print(f"  [ok] ledger: {tasks.ledger_path()} ({len(tasks.ledger())} task(s))")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
