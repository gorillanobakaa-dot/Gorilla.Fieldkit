"""The SITREP: where a migration stands, in plain words, at any moment, with ONE next action.

    fieldkit build-harness migrate sitrep TASK [--json]

It reads the plan state, the cached measurements, the task record and the journal; it runs nothing heavy, so a new
session (or a small model with an empty context) can ask it as often as it likes. It answers:

    the stage and its level (S6 = level 6 of 9), and whether the migration is under control (a plan) or only computed
    the current stage's gate: green or red, with the failing items and their evidence
    what was carried in when an in-flight migration was adopted (must be green before S9)
    progress: intents total / applied / ported / in the tree / proven in the build, per group in the plan's order
    open decision briefs by kind; parked tickets; the active work item
    the last 10 journal events
    THE ONE NEXT ACTION, as a command (the active work item's first, then the gate's first item a tool can move,
    then the first that needs the maintainer, shown as the brief to read)

A SITREP block (journal_block) is written into the journal at every stage change.
"""
import json
import time

from . import guard, plan

FH = plan.FH


def build(m, gates=None):
    p = m.plan()
    g = gates or plan.all_gates(m)
    stage = p["stage"] if p else plan.position(m, g)
    cur = g[stage]
    carried = (p or {}).get("carried")
    if carried is not None:                      # carried at adoption: shown with their state NOW
        now_red = {i["id"] for s in plan.IDS for i in g[s]["red"]}
        carried = [dict(c, still_red=c["item"] in now_red) for c in carried]
    else:
        carried = [{"stage": s, "item": i["id"], "evidence": i["evidence"][:200], "still_red": True}
                   for s in plan.IDS[:plan.IDS.index(stage)] for i in g[s]["red"]]
    rec = m.cache("intents")
    prog = None
    if rec:
        fresh_t, why_t = plan._fresh(m, rec, "tree")
        fresh_b, why_b = plan._fresh(m, rec, "build")
        prog = {"by_group": rec["data"]["by_group"], "counts": rec["data"]["counts"], "coverage": rec["data"].get("coverage"),
                "measured": rec["at"], "stale": None if (fresh_t and fresh_b) else (why_t if not fresh_t else why_b)}
    br = m.cache("briefs")
    work = guard.active(m.tid)
    tickets = guard.open_tickets(m.tid)
    ev = m.events()[-10:]
    pa = (m.cache("previous_audit") or {}).get("data") or {}
    rep = {"task": m.tid, "version": m.version(), "previous": m.previous(), "previous_version": pa.get("previous_version"), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "controlled": bool(p), "guard": (p or {}).get("guard"), "stage": stage, "stage_name": plan.STAGE[stage]["name"],
           "level": f"{plan.IDS.index(stage)} of {len(plan.IDS) - 1}",
           "adopted": bool(p and p["history"] and p["history"][0].get("adopted")),
           "gate": {"ok": cur["ok"], "green": len(cur["items"]) - len(cur["red"]), "total": len(cur["items"]), "red": cur["red"]},
           "gates": {s: {"ok": g[s]["ok"], "red": len(g[s]["red"]), "total": len(g[s]["items"])} for s in plan.IDS},
           "carried": carried, "progress": prog,
           "briefs": {"by_kind": (br or {}).get("data", {}).get("by_kind"), "measured": (br or {}).get("at"),
                      "problems": (br or {}).get("data", {}).get("problems")} if br else None,
           "work": work, "parked": tickets,
           "events": [{"t": e.get("t"), "event": e.get("event"), "step": e.get("step") or e.get("stage") or e.get("brief") or "",
                       "detail": _detail(e)} for e in ev],
           "build": {"tree": m.tree(), "installed_build": m.build_id()}}
    rep["next"] = next_action(m, rep, cur)
    return rep


def _detail(e):
    skip = {"t", "task", "event", "prev", "step", "results", "rows", "sitrep", "answer"}
    return json.dumps({k: v for k, v in e.items() if k not in skip}, default=str)[:140]


def next_action(m, rep, cur):
    if not rep["controlled"]:
        first = _pick(rep, cur)
        return {"command": f"{FH} migrate init {m.tid}" + (f" from={rep['previous']}" if rep["previous"] else ""),
                "why": f"the migration is not under control yet (no plan, no drift guard): this adopts it at {rep['stage']} "
                       f"from its evidence" + (f"; then: {first['command']}" if first else "")}
    if cur["ok"]:
        return {"command": f"{FH} migrate advance {m.tid}", "why": f"the {rep['stage']} gate is green"}
    first = _pick(rep, cur)
    if not first:
        return {"command": f"{FH} migrate check {m.tid} --only {rep['stage']}", "why": "the gate is red without a command: measure it again"}
    w = rep["work"]
    if not w or w.get("item") != first["item"]:
        return {"command": f"{FH} migrate work {m.tid} {first['item']}",
                "why": f"take the gate's next item first ({first['why']}); then: {first['command']}"}
    return {"command": first["command"], "why": first["why"]}


def _pick(rep, cur):
    red = cur["red"]
    if not red:
        return None
    w = rep.get("work")
    if w:
        mine = next((i for i in red if i["id"] == w.get("item")), None)
        if mine and mine.get("command"):
            return {"item": mine["id"], "command": mine["command"], "why": f"the active work item {mine['id']}: {mine['evidence'][:140]}"}
    tool = [i for i in red if i["needs"] == "tool" and i.get("command")]
    if tool:
        i = tool[0]
        return {"item": i["id"], "command": i["command"], "why": f"{i['id']}: {i['evidence'][:140]}"}
    i = red[0]
    cmd = f"{FH} brief show {i['brief']} --task {plan_task(rep)}" if i.get("brief") else i.get("command")
    return {"item": i["id"], "command": cmd, "why": f"{i['id']} needs the maintainer: {i['evidence'][:140]}"}


def plan_task(rep):
    return rep["task"]


def journal_block(rep):
    """What goes into the journal at a stage change: enough for a fresh session to start from the record."""
    return {"stage": rep["stage"], "level": rep["level"], "gate_ok": rep["gate"]["ok"],
            "red": [i["id"] for i in rep["gate"]["red"]][:20], "gates": {s: v["ok"] for s, v in rep["gates"].items()},
            "carried_red": sum(1 for c in rep["carried"] if c.get("still_red")),
            "progress": (rep["progress"] or {}).get("counts"), "next": rep["next"]["command"]}


def lines(rep):
    out = [f"SITREP {rep['task']}  {rep['at']}   (Firefox {rep.get('previous_version') or '?'} -> {rep['version']}; previous task {rep['previous'] or '?'})"]
    ctl = ("under migration control" + (", adopted mid-migration" if rep["adopted"] else "") + f", drift guard {rep['guard']}"
           if rep["controlled"] else "NOT under migration control yet: the stage is computed from the evidence; no drift guard")
    out.append(f"STAGE {rep['stage']} {rep['stage_name']}  (level {rep['level']})  - {ctl}")
    out.append("GATES  " + "  ".join(f"{s}:{'green' if v['ok'] else 'RED'}" for s, v in rep["gates"].items()))
    g = rep["gate"]
    out.append(f"GATE {rep['stage']}: {'GREEN' if g['ok'] else 'RED'} - {g['green']} of {g['total']} item(s) green")
    for i in g["red"][:15]:
        who = "maintainer" if i["needs"] == "human" else "tool"
        out.append(f"  RED  {i['id']}  [{who}] {i['what'][:90]}")
        out.append(f"       {i['evidence'][:200]}")
    if len(g["red"]) > 15:
        out.append(f"  ... and {len(g['red']) - 15} more: fieldkit build-harness migrate gate {rep['task']} {rep['stage']}")
    still = [c for c in rep["carried"] if c.get("still_red")]
    if still:
        by = {}
        for c in still:
            by.setdefault(c["stage"], []).append(c["item"])
        out.append(f"CARRIED from earlier stages (red; must be green before S9): " +
                   "; ".join(f"{s}: {len(v)} ({', '.join(v[:3])}{', ...' if len(v) > 3 else ''})" for s, v in by.items()))
    pr = rep["progress"]
    if pr:
        out.append(f"PROGRESS (intents; measured {pr['measured']}" + (f"; {pr['stale']}" if pr["stale"] else "") + ")")
        out.append(f"  {'group':30} {'total':>5} {'applied':>7} {'ported':>6} {'authored':>8} {'in tree':>7} {'proven':>6} {'missing':>7}")
        tot = {}
        for grp, c in pr["by_group"].items():
            if not c["total"]:
                continue
            out.append(f"  {grp:30} {c['total']:5} {c['applied']:7} {c['ported']:6} {c['authored']:8} {c['in_tree']:7} "
                       f"{c['proven']:6} {c['not_in_tree']:7}")
            for k, v in c.items():
                tot[k] = tot.get(k, 0) + v
        out.append(f"  {'TOTAL':30} {tot.get('total', 0):5} {tot.get('applied', 0):7} {tot.get('ported', 0):6} {tot.get('authored', 0):8} "
                   f"{tot.get('in_tree', 0):7} {tot.get('proven', 0):6} {tot.get('not_in_tree', 0):7}")
        cv = pr.get("coverage") or {}
        out.append(f"  behaviour checks: {cv.get('with_check_pct')}% of {cv.get('in_scope')} in-scope intents "
                   f"({cv.get('with_build_check_pct')}% with a check on the installed build); purpose quoted for all but "
                   f"{cv.get('purpose_unknown')}; statuses: " + ", ".join(f"{k.lower()} {v}" for k, v in pr["counts"].items() if v))
    else:
        out.append(f"PROGRESS: not measured (run: {FH} migrate seed {rep['task']})")
    b = rep["briefs"]
    if b and b.get("by_kind") is not None:
        out.append(f"OPEN BRIEFS (as of {b['measured']}): " + (", ".join(f"{k} {v}" for k, v in sorted(b["by_kind"].items())) or "none")
                   + (f"  [problems: {'; '.join(b['problems'])[:160]}]" if b.get("problems") else ""))
    else:
        out.append(f"OPEN BRIEFS: not counted (run: {FH} migrate check {rep['task']})")
    out.append(f"PARKED TICKETS: {len(rep['parked'])}" + "".join(f"\n  {t['id']} ({t['stage']}, {t['t']}): {t['why'][:140]}" for t in rep["parked"][:8]))
    w = rep["work"]
    out.append(f"ACTIVE WORK ITEM: {w['item']} ({w['stage']}, since {w['since']})" if w else "ACTIVE WORK ITEM: none")
    out.append("LAST 10 JOURNAL EVENTS:")
    out += [f"  {e['t']}  {e['event']:<18} {e['step'][:50]:<50} {e['detail'][:90]}" for e in rep["events"]]
    out.append(f"NEXT: {rep['next']['command']}")
    out.append(f"      why: {rep['next']['why'][:300]}")
    return out


def plan_lines(m, gates=None):
    """`migrate plan TASK`: the plan's stages with the current gate status of each."""
    g = gates or plan.all_gates(m)
    p = m.plan()
    stage = p["stage"] if p else plan.position(m, g)
    out = [f"MIGRATION PLAN for {m.tid} (Gorilla.firefox/MIGRATION-PLAN.md, section 3)" +
           ("" if p else f"  - not under control yet; computed stage {stage}")]
    for s in plan.STAGES:
        gg = g[s["id"]]
        here = "  <== current stage" if s["id"] == stage else ""
        out.append(f"{s['id']} {s['name']:<24} gate {'GREEN' if gg['ok'] else 'RED  '} ({len(gg['items']) - len(gg['red'])}/{len(gg['items'])}){here}")
        out.append(f"     what: {s['what']}")
        out.append(f"     exit gate: {s['gate']}")
        out.append(f"     commands: " + "; ".join(c.replace("TASK", m.tid) for c in s["commands"]))
        for i in gg["red"][:4]:
            out.append(f"     RED {i['id']}: {i['evidence'][:150]}")
        if len(gg["red"]) > 4:
            out.append(f"     ... {len(gg['red']) - 4} more red item(s)")
    out.append("Rules: a stage is entered only through `migrate advance`, while the gate before it is green; analysis may "
               "run in parallel, but recording into the tree, building, installing and the leak gate are serial (one writer).")
    return out
