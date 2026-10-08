"""Decision brief producers for migration control (registered in fieldkit/briefs/producers.py).

    from_intake       an INTAKE cluster (S1) with items that have neither an automatic nor a recorded disposition
    from_consistency  a document or tool that disagrees with the register or the shipped build (S8), and a value the
                      build ships that no decision covers
    from_lost_layer   a defence layer the documents describe that is missing from the install (S8)
    from_publish      S9 with every earlier gate green: the maintainer's recorded yes to push

They read only what `migrate check` measured (state/build-harness/TASK/migrate/cache/), so listing briefs stays fast;
a brief whose data is missing is simply not produced, and the gate that needs the data says so itself.
"""
import json

from ..briefs import producers as bp
from . import plan as mplan

PRIVACY = bp.PRIVACY_NOTE[0].upper() + bp.PRIVACY_NOTE[1:]


def _cache(ctx, name):
    p = mplan.state_dir(ctx.task_id) / "cache" / f"{name}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def from_intake(ctx):
    from . import intake
    c = _cache(ctx, "intake")
    if not c:
        return []
    data = c["data"]
    by_id = {i["id"]: i for i in data["items"]}
    decided = intake.decided_items(ctx.task_id)
    out = []
    for cl in intake.open_clusters(data, decided):
        its = [by_id[i] for i in cl["items"] if i in by_id]
        net = [i for i in its if i["network"]]
        ai = [i for i in its if i["ai"]]
        items = [{"what": f"new in Firefox {data.get('new_version') or data['new'][:12]}: {i['category']} {i['name']}"
                          + (" (network-capable)" if i["network"] else "") + (" (AI/ML)" if i["ai"] else ""),
                  "excerpt": i["detail"] + (f"\nhosts named: {', '.join(i['hosts'])}" if i.get("hosts") else ""),
                  "location": f"{i['where']} (pristine {data['new'][:12]})"} for i in its]
        what = f"{len(its)} new {cl['category']} item(s) under {cl['component']}"
        cut_rec = bool(net or ai)
        opts = [
            {"id": "cut", "label": "Cut it at the source, as a new intent of the patch set",
             "what_changes": f"A PENDING register entry commits this migration to cutting {what} at the source (a hand edit "
                             "recorded with `build-harness record`, with a GORILLA marker), so it becomes an intent with a "
                             "behaviour check in the ledger.",
             "user_impact": "Users never meet the new feature or its network traffic.",
             "credibility_impact": "THE RULE (D-157-00, this browser never calls home) stays true for code Mozilla added "
                                   "after the rule was written.",
             "cost": f"Port work in S3 for {len(its)} item(s), then the leak gate proves it.", "reversible": True,
             "records": "register"},
            {"id": "accept", "label": "Accept it as it is, as a recorded decision",
             "what_changes": "A PENDING register entry records that this release ships the new thing unchanged; it turns "
                             "into a trade-off only when the maintainer edits it so.",
             "user_impact": "Users get the new upstream behaviour, including any traffic it makes.",
             "credibility_impact": "Fine when it cannot reach the network; for network-capable code it is a named exception "
                                   "to THE RULE that the public pages must state.",
             "cost": "No port work; documentation work when it is network-capable.", "reversible": True,
             "records": "register"},
            {"id": "not-network", "label": "Confirm it cannot reach the network (no decision needed)",
             "what_changes": "The task journal records the maintainer's confirmation; nothing else changes.",
             "user_impact": "None.", "credibility_impact": "None, provided the confirmation is right; the leak gate still "
                                                            "tests the build.",
             "cost": "Nothing.", "reversible": True, "records": "journal"},
            bp._hold("the S1 gate stays red and the migration cannot leave intake")]
        why = ((f"{len(net)} of the {len(its)} item(s) can reach the network and {len(ai)} name an AI/ML feature; new code "
                "that can call home is cut by default under D-157-00. ") + PRIVACY + "." if cut_rec else
               "none of these items calls a network API or names an AI/ML feature by the leak gate's own patterns; they "
               "were kept for a person only because their names or values suggest network use.")
        out.append({"id": cl["brief"], "kind": "intake", "task": ctx.task_id,
                    "topic": f"Firefox's new {cl['category']} code under {cl['component']} has no disposition yet ({len(cl['open'])} item(s))",
                    "source": {"check": "migrate intake (pristine N-1 against pristine N)", "verdict": "NO DISPOSITION",
                               "command": f"fieldkit build-harness migrate intake {ctx.task_id}"},
                    "affected": {"count": len(its), "counts": {"items": len(its), "network-capable": len(net), "AI/ML": len(ai)},
                                 "items": items},
                    "options": opts, "recommendation": {"option": "cut" if cut_rec else "not-network", "why": why},
                    "what_gets_recorded": "cut / accept: a NEW pending entry appended to decisions/PRODUCT-DECISIONS.yaml "
                                          "with your words; not-network: the task journal. Always: the journal gets the "
                                          "brief's sha256, and the S1 gate reads the items this brief covered.",
                    "blocking": {"blocks": ["the S1 gate (upstream intake), so every later stage"],
                                 "deadline": "before S2 (mechanical apply)"},
                    "evidence": [{"file": i["where"], "line": None, "text": i["detail"]} for i in its],
                    "target": {"intake": [i["id"] for i in its], "_sort": (not ai, not net, cl["id"])}})
    return out


def _statement_items(x):
    out = []
    for s in x["statements"]:
        bad = [c for c in s["checks"] if c[2] != "ok"]
        out.append({"what": f"{s['where']} says this; against the shipped build: {s['state']}",
                    "excerpt": (s.get("quote") or "") + ("\n" + "\n".join(f"{c[2]}: {c[3]}" for c in bad) if bad else
                                                         ("\n" + s["why"] if s.get("why") else "")),
                    "location": s["where"]})
    return out


def _checks_from(x, want_ok):
    """Checks to attach to a decision: what the statements that agree with the build assert (want_ok), or what the
    disagreeing ones assert (the documents' version, to be made true)."""
    out = []
    for s in x["statements"]:
        if s.get("asserts") and (s["state"] == "AGREE") == want_ok:
            out += [c for c in s["asserts"] if c not in out]
    return out


def _consistency_brief(ctx, x):
    lost = x["kind"] == "lost-layer"
    items = _statement_items(x)
    o = x.get("origin") or {}
    items.insert(0, {"what": "where this finding comes from", "excerpt": f"{o.get('source', '')}\n{o.get('section', '')}"
                     + (f"\nmeasured: {o['measured']}" if o.get("measured") else ""), "location": "intents/CONSISTENCY.yaml " + x["id"]})
    if lost:
        opts = [{"id": "restore", "label": "Restore the layer: make the install carry it again",
                 "what_changes": "A register entry with the failing checks attached (status enforced: the checks decide), "
                                 "so the decision stays VIOLATED until the packaging puts the file in the install.",
                 "user_impact": "Users get the defence the documents describe.", "credibility_impact": "The published sentence "
                 "becomes true again and is checked on every build.", "cost": "A packaging fix, a build and an install.",
                 "reversible": True, "records": "register", "verify": _checks_from(x, want_ok=False)},
                {"id": "retire-layer", "label": "Retire the layer and correct every document that describes it",
                 "what_changes": "A PENDING register entry records the layer as dropped; the documents are reworded.",
                 "user_impact": "One fewer defence; the other layers must hold alone.",
                 "credibility_impact": "A promised layer is withdrawn; it must be said plainly.", "cost": "Documentation work.",
                 "reversible": True, "records": "register", "removes_claim": True},
                bp._hold("the S8 gate stays red")]
        rec = "restore"
    elif x["kind"] == "undecided":
        opts = [{"id": "record-as-is", "label": "Record the shipped value as the maintainer's decision",
                 "what_changes": "A register entry with the shipped values attached as checks (enforced), so a later port "
                                 "cannot change them silently.", "user_impact": "None now; the behaviour stays as shipped.",
                 "credibility_impact": "An accident becomes a stated, checked choice.", "cost": "Nothing.",
                 "reversible": True, "records": "register", "verify": _checks_from(x, want_ok=True)},
                {"id": "change", "label": "Change the value in the next build",
                 "what_changes": "A PENDING register entry asks for the value to change; the maintainer adds the checks.",
                 "user_impact": "The behaviour changes for everyone (for example: logged out after closing the browser).",
                 "credibility_impact": "Stronger privacy, if that is the change.", "cost": "A prefs patch, a build and an install.",
                 "reversible": True, "records": "register"},
                bp._hold("the S8 gate stays red")]
        rec = "record-as-is"
    else:
        wording = x["kind"] == "wording"
        opts = [{"id": "build-wins", "label": "The shipped build is right: record it and bring the documents and tools in line",
                 "what_changes": "A register entry with the build's current values attached as checks (enforced); the "
                                 "disagreeing documents are marked as history or reworded, and the tool lists corrected.",
                 "user_impact": "None now; the build already behaves this way.",
                 "credibility_impact": "The records stop contradicting each other." + (" The sentence that over-claims is "
                                       "reworded to what the code does." if wording else ""),
                 "cost": "Documentation and tool edits.", "reversible": True, "records": "register",
                 "removes_claim": wording, "verify": _checks_from(x, want_ok=True)},
                {"id": "docs-win", "label": "The documents are right: change the build to match them",
                 "what_changes": "A register entry with the documents' assertions attached as checks (enforced: VIOLATED "
                                 "until the build obeys), so the next port makes them true.",
                 "user_impact": "Users get what the documents describe.",
                 "credibility_impact": "The published statement becomes true and is checked on every build.",
                 "cost": "Port work, a build and an install; check it against THE RULE (D-157-00) first.",
                 "reversible": True, "records": "register", "verify": _checks_from(x, want_ok=False)},
                bp._hold("the S8 gate stays red")]
        rec = "docs-win"
    want = x.get("recommend")
    if want in [o["id"] for o in opts] and not next(o for o in opts if o["id"] == want).get("removes_claim"):
        rec = want
    for o in opts:
        if "verify" in o and not o["verify"]:
            o.pop("verify")
    why = x.get("recommend_why") or ("a defence the project describes is missing; " + PRIVACY if lost else
                                     "a recorded choice is checked on every build; an unrecorded one drifts" if x["kind"] == "undecided"
                                     else PRIVACY)
    return {"id": x["brief"], "kind": "lost-layer" if lost else "consistency", "task": ctx.task_id,
            "topic": f"{x['id']}: {x['topic']} ({x['verdict']})",
            "source": {"check": "migrate consistency (documents and tools against the register and the build)",
                       "verdict": x["verdict"], "command": f"fieldkit build-harness migrate consistency {ctx.task_id}"},
            "affected": {"count": len(x["statements"]), "counts": {"statements": len(x["statements"]),
                         "disagreeing": sum(1 for s in x["statements"] if s["state"] != "AGREE")}, "items": items},
            "options": opts, "recommendation": {"option": rec, "why": why},
            "what_gets_recorded": "A NEW entry appended to decisions/PRODUCT-DECISIONS.yaml with your words (hold: the task "
                                  "journal only). The S8 gate reads the decision from the journal.",
            "blocking": {"blocks": ["the S8 gate (claims and consistency), so publishing"], "deadline": "before S9 (publish)"},
            "evidence": [{"file": s["where"], "line": None, "text": f"{c[0]} {c[2]}: {c[3]}"} for s in x["statements"]
                         for c in s["checks"] if c[2] != "ok"],
            "target": {"consistency": x["id"], "_sort": (not lost, x["id"])}}


def _fresh_consistency(ctx):
    """The consistency cache, measured again first when it is for another build or tree (never a stale brief)."""
    try:
        from .measure import fresh_consistency
        from .plan import Migration
        return fresh_consistency(Migration(ctx.task_id, install=ctx.install, find_install=False), say=lambda s: None)
    except Exception:                                    # no task or owner here: fall back to what is cached
        return _cache(ctx, "consistency")


def from_consistency(ctx):
    c = _fresh_consistency(ctx)
    if not c:
        return []
    return [_consistency_brief(ctx, x) for x in c["data"]["items"] if not x["closed"] and x["kind"] != "lost-layer"]


def from_lost_layer(ctx):
    c = _fresh_consistency(ctx)
    if not c:
        return []
    return [_consistency_brief(ctx, x) for x in c["data"]["items"] if not x["closed"] and x["kind"] == "lost-layer"]


def from_publish(ctx):
    p = mplan.load_plan(ctx.task_id)
    if not p or p.get("stage") != "S9":
        return []
    g = p.get("last_gates") or {}
    if not all(g.get(s) for s in mplan.IDS[:-1]):
        return []
    if any(e.get("event") == "decide" and e.get("brief") == f"B-PUBLISH-{ctx.task_id}" for e in mplan.journal_events(ctx.task_id)):
        return []
    items = [{"what": f"gate {s} green", "excerpt": f"{s} {mplan.STAGE[s]['name']}: every exit check passed",
              "location": f"state/build-harness/{ctx.task_id}/migrate/plan.json"} for s in mplan.IDS[:-1]]
    return [{"id": f"B-PUBLISH-{ctx.task_id}", "kind": "publish", "task": ctx.task_id,
             "topic": f"{ctx.task_id} has passed every gate from S0 to S8 and waits for the maintainer's yes to publish",
             "source": {"check": "migrate plan (S9 publish)", "verdict": "READY",
                        "command": f"fieldkit build-harness migrate sitrep {ctx.task_id}"},
             "affected": {"count": len(items), "counts": {"gates green": len(items)}, "items": items},
             "options": [{"id": "publish", "label": "Publish: push the patch set and the release",
                          "what_changes": "The journal records the yes; the push itself is still typed by the maintainer.",
                          "user_impact": "People can download the new build.", "credibility_impact": "Everything published "
                          "is proven by the harness.", "cost": "The push.", "reversible": False, "records": "journal"},
                         bp._hold("nothing is published")],
             "recommendation": {"option": "publish", "why": "every gate is green, including the leak gate and the claims audit"},
             "what_gets_recorded": "The task journal: the brief's sha256, the option and your words.",
             "blocking": {"blocks": ["the S9 gate (publish)"], "deadline": None}, "target": {"publish": ctx.task_id}}]
