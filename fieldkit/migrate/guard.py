"""The drift guard: work happens on an item of the current stage, or it is parked, never chased in passing.

The maintainer's words (2026-10-03): "WE DO NOT WORK IN SCRAP and be reactive". The 157 port spent days on the
fifth-generation problem of a side track. So, once a task is under migration control (`migrate init`):

    migrate work TASK ITEM     opens a work item. ITEM must be an item of the CURRENT stage's gate (its id as the gate
                               lists it, a bare intent or intake id that gate lists, or the brief a gate item names)
    record, repair, build-run, decide
                               are refused with no active item, or when the active item belongs to another stage; each
                               allowed one is journalled with the item it serves (event migrate-action)
    --park "why"               on any of those actions: nothing runs; a PARKED TICKET is written instead (shown in the
                               SITREP), so a side problem is remembered without taking over
    migrate park TASK "why"    the same, without an action; `migrate unpark TASK P-001 "resolution"` closes a ticket

One writer (MIGRATION-PLAN.md section 4): record, repair, build-run, install, leakgate and submit take the task's
writer lock; a second writer while one runs is refused (analysis may run in parallel; writing never does).
"""
import atexit
import json
import os
import time

from ..buildh import task
from . import plan

GUARDED = ("record", "repair", "build-run", "decide")
WRITERS = ("record", "repair", "build-run", "install", "leakgate", "submit")


def _wpath(task_id):
    return plan.state_dir(task_id) / "work.json"


def active(task_id):
    try:
        return json.loads(_wpath(task_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clear_work(task_id):
    try:
        _wpath(task_id).unlink()
    except OSError:
        pass


def _tickets_path(task_id):
    return plan.state_dir(task_id) / "parked.jsonl"


def tickets(task_id):
    p = _tickets_path(task_id)
    out = {}
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            out.setdefault(e["id"], {}).update(e)
    return list(out.values())


def open_tickets(task_id):
    return [t for t in tickets(task_id) if t.get("state") == "open"]


def park(task_id, why, action=None, args=None):
    why = (why or "").strip()
    if len(why) < 3:
        raise task.Refused("say why it is parked: --park \"what it is and why it does not block this stage\"")
    p = plan.load_plan(task_id) or {}
    n = len(tickets(task_id)) + 1
    w = active(task_id)
    tk = {"id": f"P-{n:03d}", "t": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": p.get("stage"), "item": (w or {}).get("item"),
          "action": action, "args": list(args or [])[:8], "why": why, "state": "open"}
    d = plan.state_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    with open(_tickets_path(task_id), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(tk) + "\n")
    task.journal(task.load(task_id), "migrate-park", ticket=tk["id"], why=why, action=action, stage=tk["stage"])
    return tk


def unpark(task_id, ticket_id, resolution):
    have = {t["id"]: t for t in tickets(task_id)}
    if ticket_id not in have:
        raise task.Refused(f"no parked ticket {ticket_id}; open ones: {[t['id'] for t in open_tickets(task_id)]}")
    if have[ticket_id].get("state") != "open":
        raise task.Refused(f"{ticket_id} is closed already")
    if len((resolution or "").strip()) < 3:
        raise task.Refused("say how it was resolved (it is recorded)")
    e = {"id": ticket_id, "state": "closed", "closed": time.strftime("%Y-%m-%d %H:%M:%S"), "resolution": resolution.strip()}
    with open(_tickets_path(task_id), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(e) + "\n")
    task.journal(task.load(task_id), "migrate-unpark", ticket=ticket_id, resolution=resolution.strip())
    return e


def stage_items(m, stage):
    g = plan.gate(m, stage)
    ids = {}
    for i in g["items"]:
        ids[i["id"]] = i
        if i.get("brief"):
            ids.setdefault(i["brief"], i)
        if "." in i["id"]:
            ids.setdefault(i["id"].split(".", 1)[1], i)        # S3.I-abc -> I-abc, S1.INTAKE-x -> INTAKE-x
    return g, ids


def work(m, item_id, by="cli"):
    p = m.plan()
    if not p:
        raise task.Refused(f"{m.tid} is not under migration control; start it: {plan.FH} migrate init {m.tid}")
    stage = p["stage"]
    g, ids = stage_items(m, stage)
    if item_id not in ids:
        # 2026-10-03: carried red items of EARLIER stages must be green before S9, so working on them is the plan,
        # not a side quest; an open parked ticket may be picked up deliberately. Only LATER stages stay refused.
        earlier = [s for s in plan.IDS[:plan.IDS.index(stage)]]
        for s in earlier:
            g2, ids2 = stage_items(m, s)
            if item_id in ids2 and not ids2[item_id].get("ok"):    # only a CARRIED (red) item; green ones are done
                ids = {item_id: dict(ids2[item_id], carried_from=s)}
                break
        else:
            tk = next((t for t in open_tickets(m.tid) if t["id"] == item_id), None)
            if tk:
                ids = {item_id: {"id": item_id, "what": tk.get("why", ""), "command": None, "brief": None, "ok": False}}
    if item_id not in ids:
        owner = next((s for s in plan.IDS if s != stage and item_id in stage_items(m, s)[1]), None)
        red = [i["id"] for i in g["red"]][:6]
        raise task.Refused((f"{item_id} belongs to {owner} {plan.STAGE[owner]['name']}, " if owner else f"{item_id} is not an item of ")
                           + f"the current stage {stage} {plan.STAGE[stage]['name']}"
                           + (". Do not chase it now: park it with `migrate park` (or --park on the action)" if owner else "")
                           + f". The open items of {stage}: {', '.join(red) or 'none (advance)'}")
    gi = ids[item_id]
    w = {"item": gi["id"], "asked": item_id, "stage": stage, "since": time.strftime("%Y-%m-%d %H:%M:%S"), "by": by,
         "what": gi["what"], "command": gi.get("command"), "brief": gi.get("brief"), "carried_from": gi.get("carried_from")}
    d = plan.state_dir(m.tid)
    d.mkdir(parents=True, exist_ok=True)
    _wpath(m.tid).write_text(json.dumps(w, indent=1) + "\n", encoding="utf-8", newline="\n")
    task.journal(m.t, "migrate-work", item=gi["id"], stage=stage, green=gi["ok"])
    return w


def _task_of(act, a, current_id):
    if act == "decide":
        return current_id(getattr(a, "task", None))
    words = [x for x in (a.args or []) if x != "baseline"]
    return current_id(getattr(a, "task", None) or (words[0] if words else None))


def enforce(act, a, current_id, say=print):
    """Called by `fieldkit build-harness` before a guarded or writing action. -> None (no plan: nothing to guard),
    "parked" (the action must not run), or the active work item. Raises task.Refused with the NEXT hint."""
    if act not in GUARDED + WRITERS:
        return None
    try:
        tid = _task_of(act, a, current_id)
    except task.Refused:
        return None                                   # the action itself will say there is no task
    p = plan.load_plan(tid)
    why = getattr(a, "park", None)
    if not p or p.get("guard") == "off":
        if why is not None and act in GUARDED:          # never run what the caller asked to park
            raise task.Refused(f"--park needs migration control ({tid} has no plan): nothing was run. Start it with "
                               f"`{plan.FH} migrate init {tid}`, or run the action without --park")
        return None
    if why is not None and act in GUARDED:
        tk = park(tid, why, action=act, args=a.args)
        say(f"PARKED {tk['id']}: {act} was NOT run. The ticket is in the SITREP; it is taken up when it blocks a gate.")
        return "parked"
    w = active(tid) if act in GUARDED else None
    if act in GUARDED:
        if not w:
            m = plan.Migration(tid)
            red = [i["id"] for i in plan.gate(m, p["stage"])["red"]][:6]
            raise task.Refused(f"no active work item: {act} serves nothing on the checklist. The migration is at {p['stage']} "
                               f"{plan.STAGE[p['stage']]['name']}; its open items: {', '.join(red) or 'none'}. "
                               f"NEXT: {plan.FH} migrate work {tid} <item>  (or add --park \"why\" to park this as a ticket)")
        if w.get("stage") != p["stage"]:
            raise task.Refused(f"the active work item {w['item']} belongs to {w.get('stage')}, but the migration is at "
                               f"{p['stage']}: pick an item of {p['stage']} (NEXT: {plan.FH} migrate sitrep {tid}), or "
                               f"add --park \"why\"")
        task.journal(task.load(tid), "migrate-action", action=act, item=w["item"], stage=p["stage"], args=list(a.args or [])[:6])
    if act in WRITERS:
        take_writer(tid, act)
    return w


# -- one writer -----------------------------------------------------------------------------------------------------
def _alive(pid):
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return ctypes.GetLastError() == 5          # access denied: it exists
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259                       # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def take_writer(task_id, act):
    d = plan.state_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    lock = d / "writer.lock"
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                cur = json.loads(lock.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cur = {}
            if cur.get("pid") == os.getpid():
                return cur
            if cur.get("pid") and _alive(cur["pid"]):
                raise task.Refused(f"one writer at a time: `{cur.get('action')}` (pid {cur['pid']}, since {cur.get('t')}) is "
                                   f"writing to {task_id}; recording, building, installing and the leak gate never run in "
                                   "parallel. Wait for it, then run this again")
            try:
                lock.unlink()                          # its process is gone: a stale lock
            except OSError:
                pass
            continue
        rec = {"pid": os.getpid(), "action": act, "t": time.strftime("%Y-%m-%d %H:%M:%S")}
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec))
        atexit.register(release_writer, task_id)
        return rec
    raise task.Refused(f"could not take the writer lock {lock}")


def release_writer(task_id):
    lock = plan.state_dir(task_id) / "writer.lock"
    try:
        cur = json.loads(lock.read_text(encoding="utf-8"))
        if cur.get("pid") == os.getpid():
            lock.unlink()
    except (OSError, ValueError):
        pass
