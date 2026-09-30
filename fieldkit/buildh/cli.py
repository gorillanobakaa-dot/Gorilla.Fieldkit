"""fieldkit build-harness - the Firefox & kernel build harness, from the command line and over MCP.

    fieldkit build-harness latest firefox|kernel
    fieldkit build-harness vault measure firefox | fetch firefox|kernel | verify P | restore P WORKDIR | list
    fieldkit build-harness start firefox [--task ID] [--pin VERSION --source REPO_OR_PATH]
                                         [--budget TOKENS] [--workdir DIR]
    fieldkit build-harness approve TASK          the owner, only here (never over MCP)
    fieldkit build-harness next|status [TASK]
    fieldkit build-harness submit [TASK] [--note TEXT]
    fieldkit build-harness unblock TASK STEP retry|skip      the owner
    fieldkit build-harness log [TASK]

Without TASK, the current task is used (the last one started).
The model gets three MCP tools: build_harness_status, build_harness_next, build_harness_submit.
"""
import json
from pathlib import Path

from . import firefox, task, upstream, vault

CURRENT = task.STATE / "CURRENT"


def current_id(given=None):
    if given:
        return given
    f = task.STATE / "CURRENT"
    if not f.is_file():
        raise task.Refused("no build job has been started; the owner runs: fieldkit build-harness start firefox")
    return f.read_text(encoding="utf-8").strip()


def start_firefox(task_id=None, pin=None, source=None, budget=100_000, workdir=None, harness_root=None):
    from ..core import settings
    harness_root = harness_root or settings.expand("${LOCAL:firefox.root}")
    if pin:
        info = upstream.latest_firefox(versions={"LATEST_FIREFOX_VERSION": pin},
                                       repo=source or upstream.FIREFOX_REPO)
    else:
        info = upstream.latest_firefox(repo=source or upstream.FIREFOX_REPO)
    tid = task_id or f"firefox-{info['version']}"
    wd = Path(workdir) if workdir else vault.root().parent / "Build.Work" / "firefox" / info["version"]
    t = task.start(tid, "firefox-upgrade", wd, firefox.plan(harness_root), budget_tokens=budget,
                   meta={"pinned": info, "harness_root": str(harness_root)})
    task.STATE.mkdir(parents=True, exist_ok=True)
    (task.STATE / "CURRENT").write_text(tid, encoding="utf-8")
    return t


def lines_for(result):
    if "packet" in result:
        return result["packet"]
    out = [f"{k}: {v}" for k, v in result.items() if k not in ("packet",)]
    return "\n".join(out)


def mcp_call(name, args):
    """-> (text, is_error). Three tools only; approval and unblocking are not among them."""
    try:
        tid = current_id(args.get("task"))
        if name == "build_harness_status":
            s = task.status(tid)
            cur = s["current"]
            return ("\n".join([f"task {s['task']} ({s['workflow']}): {s['counts']}, {s['checkpoints']} checkpoints",
                               f"current step: {cur['id']} ({cur['kind']}) - {cur['title']}" if cur else "no step left",
                               "NEXT: call build_harness_next"]), False)
        if name == "build_harness_next":
            r = task.packet(tid)
            if r["state"] == "MODEL STEP":
                return r["packet"], False
            if r["state"] == "BLOCKED":
                return (f"BLOCKED at {r['step']}: {'; '.join(r.get('why') or [])}\n"
                        "NEXT: stop and tell the owner. Do not work around it."), False
            return f"{r['state']}\nNEXT: tell the owner the job is finished.", False
        if name == "build_harness_submit":
            r = task.submit(tid, note=args.get("note", ""))
            if r["ok"]:
                return f"PASSED the harness check. Checkpoint {r['checkpoint']}.\nNEXT: {r['next']}", False
            return ("FAILED the harness check (your change was put back):\n- " + "\n- ".join(r["why"]) +
                    f"\nAttempts: {r['attempts']}\nNEXT: {r['next']}"), False
        return f"no tool {name!r}", True
    except task.Refused as e:
        return f"REFUSED: {e}", False


def run(a, emit):
    """The `fieldkit build-harness` command. `emit(obj, lines_fn)` prints JSON or text."""
    act = a.action
    if act == "latest":
        return emit(upstream.latest(a.args[0]), None) or 0
    if act == "vault":
        sub, rest = a.args[0], a.args[1:]
        if sub == "measure":
            m = vault.measure_firefox()
            return emit(m, lambda m: print(f"Firefox {m['version']}: {m['bytes'] / 1e6:,.0f} MB to download ({m['archive']})")) or 0
        if sub == "fetch":
            return emit(vault.fetch_firefox() if rest[0] == "firefox" else vault.fetch_kernel(), None) or 0
        if sub == "verify":
            r = vault.verify(rest[0], a.version)
            emit(r, lambda r: print(("INTACT " if r["intact"] else "DAMAGED ") + r["vault"] + "".join(f"\n  - {p}" for p in r["problems"])))
            return 0 if r["intact"] else 3
        if sub == "restore":
            return emit(vault.restore(rest[0], rest[1], a.version), None) or 0
        if sub == "list":
            return emit(vault.listing(), lambda rows: [print(f"{r['product']:8} {r['version']:10} {r['fetched']}  {r['path']}") for r in rows]) or 0
    if act == "start":
        if a.args[0] != "firefox":
            raise SystemExit("start: firefox (the kernel workflow comes next)")
        t = start_firefox(a.task, a.pin, a.source, a.budget, a.workdir)
        return emit({"task": t["id"], "steps": [s["id"] for s in t["steps"]], "workdir": t["workdir"]},
                    lambda r: print(f"task {r['task']} planned: {', '.join(r['steps'])}\n"
                                    f"working copy: {r['workdir']}\n"
                                    f"NEXT: the owner reads the plan and runs: fieldkit build-harness approve {r['task']}")) or 0
    tid = current_id(a.task or (a.args[0] if a.args else None))
    if act == "approve":
        t = task.approve(tid, "owner (command line)")
        return emit({"task": tid, "approved": t["approved"]}, lambda r: print(f"approved {tid}")) or 0
    if act == "status":
        return emit(task.status(tid), None) or 0
    if act == "next":
        r = task.packet(tid)
        return emit(r, lambda r: print(lines_for(r))) or 0
    if act == "submit":
        r = task.submit(tid, a.note or "")
        emit(r, None)
        return 0 if r["ok"] else 3
    if act == "unblock":
        task.unblock(tid, a.args[1], a.args[2])
        return emit(task.status(tid), None) or 0
    if act == "log":
        f = task.STATE / tid / "journal.jsonl"
        for line in (f.read_text(encoding="utf-8").splitlines() if f.is_file() else [])[-40:]:
            e = json.loads(line)
            print(e["t"], e["event"], e.get("step", ""), json.dumps({k: v for k, v in e.items()
                                                                    if k not in ("t", "task", "event", "step")})[:160])
        return 0
    raise SystemExit(f"unknown build-harness action {act}")
