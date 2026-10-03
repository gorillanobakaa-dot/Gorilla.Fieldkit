"""`fieldkit build-harness migrate ...` - migration control from the command line.

    migrate plan TASK                      the plan's stages S0-S9 with the current gate status of each
    migrate sitrep TASK [--json]           where it stands, and THE ONE NEXT ACTION
    migrate seed TASK                      (re)generate intents/INTENTS.yaml and judge every intent for TASK
    migrate check TASK [--only S0,S4]      measure what the gates read (all stages by default; S4 verify takes minutes)
    migrate gate TASK [STAGE] [--json]     one stage's gate, every item with its evidence
    migrate init TASK [from=PREV] [at=S6]  put the task under migration control (adopts an in-flight one by evidence)
    migrate advance TASK                   enter the next stage; refused while the current gate is red
    migrate work TASK ITEM                 open a work item of the current stage (the drift guard needs one)
    migrate park TASK "why"                park a side problem as a ticket;  migrate unpark TASK P-001 "resolution"
    migrate intents TASK [--json]          ledger coverage and the per-intent statuses that are not green
    migrate intake TASK [--json]           the S1 intake items and their dispositions
    migrate consistency TASK [--json]      documents and tools against the register and the build

TASK may be left out when `--task` is given or a current task exists.
"""
import json

from ..buildh import task
from . import plan

SUBS = ("plan", "sitrep", "seed", "check", "gate", "init", "advance", "work", "park", "unpark", "intents", "intake",
        "consistency")


def _split(a, current_id):
    if not a.args or a.args[0] not in SUBS:
        raise task.Refused(f"migrate what? one of: {', '.join(SUBS)} (see fieldkit/migrate/cli.py)")
    sub, rest = a.args[0], list(a.args[1:])
    kv = {x.split("=", 1)[0]: x.split("=", 1)[1] for x in rest if "=" in x and not x.startswith(("-", "\"")) and " " not in x.split("=", 1)[0]}
    rest = [x for x in rest if not ("=" in x and x.split("=", 1)[0] in kv)]
    known = set()
    if task.STATE.is_dir():
        known = {d.name for d in task.STATE.iterdir() if (d / "task.json").is_file()}
    if getattr(a, "task", None):
        tid = a.task
    elif rest and rest[0] in known:
        tid = rest.pop(0)
    else:
        tid = current_id(None)
    return sub, tid, rest, kv


def run(a, emit, current_id):
    sub, tid, rest, kv = _split(a, current_id)
    as_json = getattr(a, "json", False)
    say = (lambda msg: None) if as_json else (lambda msg: print(msg, flush=True))
    if sub == "park":
        from . import guard
        why = " ".join(rest) or getattr(a, "park", None)
        tk = guard.park(tid, why)
        emit(tk, lambda r: print(f"PARKED {r['id']} at {r['stage']}: {r['why']}"))
        return 0
    if sub == "unpark":
        from . import guard
        if len(rest) < 2:
            raise task.Refused("migrate unpark TASK P-001 \"how it was resolved\"")
        e = guard.unpark(tid, rest[0], " ".join(rest[1:]))
        emit(e, lambda r: print(f"CLOSED {r['id']}: {r['resolution']}"))
        return 0
    m = plan.Migration(tid)
    if sub == "seed":
        from . import measure
        r = measure.seed(m, say=say)
        emit(r, lambda r: print(_seed_lines(r)))
        return 0
    if sub == "check":
        from . import measure
        only = [s.strip().upper() for s in (getattr(a, "only", None) or "").split(",") if s.strip()] or None
        bad = [s for s in only or [] if s not in plan.IDS]
        if bad:
            raise task.Refused(f"no stage {bad}; stages are {', '.join(plan.IDS)}")
        r = measure.check(m, only=only, say=say)
        emit(r, lambda r: print(f"CHECKED {', '.join(sorted(r))}\nNEXT: {plan.FH} migrate sitrep {tid}"))
        return 0
    if sub == "sitrep":
        from . import sitrep
        rep = sitrep.build(m)
        emit(rep, lambda r: print("\n".join(sitrep.lines(r))))
        return 0 if rep["gate"]["ok"] else 3
    if sub == "plan":
        from . import sitrep
        g = plan.all_gates(m)
        emit({s: {"ok": v["ok"], "red": v["red"]} for s, v in g.items()}, lambda r: print("\n".join(sitrep.plan_lines(m, g))))
        return 0
    if sub == "gate":
        p = m.plan()
        stage = (rest[0].upper() if rest else None) or (p["stage"] if p else plan.position(m))
        if stage not in plan.IDS:
            raise task.Refused(f"no stage {stage!r}; stages are {', '.join(plan.IDS)}")
        g = plan.gate(m, stage) if stage != "S9" else {"stage": "S9", "items": (its := plan.gate_S9(m)), "ok": all(i["ok"] for i in its),
                                                         "red": [i for i in its if not i["ok"]], "name": plan.STAGE["S9"]["name"]}
        emit(g, lambda g: print("\n".join(_gate_lines(g))))
        return 0 if g["ok"] else 3
    if sub == "init":
        p = plan.init(m, previous=kv.get("from"), at=kv.get("at", "").upper() or None)
        emit(p, lambda p: print(f"UNDER MIGRATION CONTROL: {tid} at {p['stage']} ({plan.STAGE[p['stage']]['name']})"
                                + (f", adopted; {len(p['carried'])} red item(s) of earlier gates carried (green before S9)" if p['history'][0].get('adopted') else "")
                                + f"\nthe drift guard is on.\nNEXT: {plan.FH} migrate sitrep {tid}"))
        return 0
    if sub == "advance":
        p = plan.advance(m)
        emit(p, lambda p: print(f"ENTERED {p['stage']} {plan.STAGE[p['stage']]['name']}\nNEXT: {plan.FH} migrate sitrep {tid}"))
        return 0
    if sub == "work":
        from . import guard
        if not rest:
            raise task.Refused(f"migrate work {tid} ITEM: which item? the open ones are in: {plan.FH} migrate sitrep {tid}")
        w = guard.work(m, rest[0])
        emit(w, lambda w: print(f"WORKING ON {w['item']} ({w['stage']}): {w['what']}" + (f"\nNEXT: {w['command']}" if w.get("command") else "")))
        return 0
    if sub == "intents":
        from . import ledger
        rec = m.cache("intents")
        led = ledger.load(m.owner)
        cov = ledger.coverage(led["intents"])
        rows = (rec or {}).get("data", {}).get("rows", {})
        bad = {i: r for i, r in rows.items() if r[0] not in ("VERIFIED-IN-TREE", "PROVEN-IN-BUILD", "OUT-OF-SCOPE", "EXPLAINED")}
        out = {"coverage": cov, "counts": (rec or {}).get("data", {}).get("counts"), "measured": (rec or {}).get("at"), "not_green": bad}
        emit(out, lambda o: print("\n".join(_intent_lines(o, led))))
        return 0
    if sub == "intake":
        from . import intake
        rec = m.cache("intake")
        if not rec or not rec.get("data"):
            raise task.Refused(f"intake not measured: {plan.FH} migrate check {tid} --only S1")
        d = rec["data"]
        decided = intake.decided_items(tid)
        open_ = intake.open_clusters(d, decided)
        emit({"counts": d["counts"], "open_clusters": open_, "items": d["items"]}, lambda _: print("\n".join(_intake_lines(d, open_))))
        return 0 if not open_ else 3
    if sub == "consistency":
        from . import consistency
        rec = m.cache("consistency")
        if not rec:
            raise task.Refused(f"consistency not measured: {plan.FH} migrate check {tid} --only S8")
        emit(rec["data"], lambda r: print("\n".join(consistency.lines(r))))
        return 0 if all(x["closed"] for x in rec["data"]["items"]) else 3
    raise task.Refused(f"unknown migrate action {sub}")


def _seed_lines(r):
    c, s = r["coverage"], r["stats"]
    out = [f"LEDGER {r['path'] or '(not written)'}",
           f"  patch set: {s['patch_files']} patch files, {s['hunks']} hunks, +{s['added_lines']}/-{s['removed_lines']} lines, "
           f"{s['pref_lines_added']} pref lines added, {s['new_files']} new files, {s['deleted_files']} deletions",
           f"  intents: {c['intents']} ({c['in_scope']} in scope, {c['out_of_scope']} out of scope, {c['retired']} retired)",
           f"  behaviour checks: {c['with_check']} of {c['in_scope']} ({c['with_check_pct']}%); on the installed build: "
           f"{c['with_build_check']} ({c['with_build_check_pct']}%); uncheckable {c['uncheckable']}",
           f"  purpose quoted from: " + ", ".join(f"{k} {v}" for k, v in c["purpose_sources"].items()),
           "  statuses: " + ", ".join(f"{k.lower()} {v}" for k, v in r["counts"].items() if v)]
    return "\n".join(out)


def _gate_lines(g):
    out = [f"GATE {g['stage']} {g['name']}: {'GREEN' if g['ok'] else 'RED'} ({len(g['items']) - len(g['red'])}/{len(g['items'])})"]
    for i in g["items"]:
        out.append(f"  {'ok ' if i['ok'] else 'RED'}  {i['id']}: {i['what'][:100]}")
        out.append(f"        {i['evidence'][:240]}")
        if not i["ok"]:
            out.append(f"        NEXT: {i['command']}" + (f"   brief: {i['brief']}" if i.get("brief") else ""))
    return out


def _intent_lines(o, led):
    c = o["coverage"]
    by = {e["id"]: e for e in led["intents"]}
    out = [f"INTENTS {c['intents']} ({c['in_scope']} in scope): behaviour checks {c['with_check_pct']}%, on the installed build "
           f"{c['with_build_check_pct']}%; measured {o['measured']}", f"  statuses: {o['counts']}"]
    for i, r in sorted(o["not_green"].items(), key=lambda kv: (kv[1][0], kv[0])):
        e = by.get(i, {})
        out.append(f"  {r[0]:<20} {i}  {e.get('patch', '')}")
        out += [f"      {w[:200]}" for w in r[2][:2]]
    return out


def _intake_lines(d, open_):
    out = [f"INTAKE Firefox {d.get('old_version')} -> {d.get('new_version')} (pristine {d['old'][:10]} -> {d['new'][:10]})"]
    for k, v in d["counts"].items():
        out.append(f"  {k:<15} {v['new']:4} new, {v['auto']:4} disposed automatically, {v['needs_disposition']:4} need a disposition")
    out.append(f"  {len(open_)} cluster(s) without a disposition (one brief each):")
    by = {i["id"]: i for i in d["items"]}
    for c in open_:
        out.append(f"    {c['brief']}  ({len(c['open'])} item(s)): " + ", ".join(by[i]["name"] for i in c["open"][:4])
                   + (" ..." if len(c["open"]) > 4 else ""))
    return out
