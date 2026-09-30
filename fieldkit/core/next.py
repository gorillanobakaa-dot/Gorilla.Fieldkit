"""next - the one next thing to do. A small model never has to plan.

Harvested from two document harnesses that were built to be driven by an agent
in a loop: each kept "where the work has got to, and the one next thing to do".

Given a pipeline, `next` reads its saved state and last report - it never runs a
stage - and answers with exactly one of:

    DO: <one command>                       the next stage to run, and why
    BLOCKED: <stage> failed. <cause/fix>    then CHOOSE: 2-3 numbered options
    CANNOT HERE: <stage> needs <system>     and where to run it
    DONE: all N stages verified

The model's whole job becomes: run the DO command, or pick a CHOOSE number,
then ask `next` again.
"""
import json

from .host import host, platform_ok
from .pipeline import Pipeline


def decide(pipeline):
    """-> dict {kind: DO|BLOCKED|CANNOT_HERE|DONE, stage, command, why, choices}."""
    p = pipeline if isinstance(pipeline, Pipeline) else Pipeline.load(pipeline, strict=False)
    st = p.state()
    try:
        last = json.loads((p.state_dir / "last-report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    run = f"fieldkit pipeline run {p.name}"
    for s in p.stages:
        sid = s["id"]
        rec = st.get(sid, {})
        done = rec.get("status") == "done" and rec.get("fingerprint") == p.fingerprint(s)
        if done:
            continue
        if not platform_ok(s.get("platforms")):
            if s.get("optional"):
                continue
            return {"kind": "CANNOT_HERE", "stage": sid, "command": None,
                    "why": f"stage '{sid}' needs {', '.join(s['platforms'])}; this is {host()['system']}.",
                    "choices": [f"run `{run} --from {sid}` on a {s['platforms'][0]} machine",
                                "stop here"]}
        if rec.get("status") == "failed":
            entry = next((e for e in last.get("stages", []) if e.get("id") == sid), {})
            tri = entry.get("triage") or {}
            if tri.get("matches"):
                m = tri["matches"][0]
                why = f"stage '{sid}' failed: {m['cause']} Fix: {m['fix']}"
            elif tri.get("errors"):
                why = f"stage '{sid}' failed: {tri['errors'][0]['line'][:200]}"
            else:
                res = entry.get("result") or {}
                why = f"stage '{sid}' failed" + (f": {res.get('detail')}" if isinstance(res, dict) and
                                                 res.get("detail") else ".")
            return {"kind": "BLOCKED", "stage": sid, "command": None, "why": why,
                    "choices": [f"apply the fix, then `{run} --only {sid}`",
                                f"retry as is: `{run} --only {sid}`",
                                "stop and report the failure"]}
        why = ("never run" if not rec else
               "its definition changed since it last ran" if rec.get("fingerprint") != p.fingerprint(s) else
               "it ran but nothing proved its result" if rec.get("status") == "ran-unverified" else
               f"last status: {rec.get('status')}")
        return {"kind": "DO", "stage": sid, "command": f"{run} --only {sid}", "why": why, "choices": []}
    return {"kind": "DONE", "stage": None, "command": None,
            "why": f"all {len(p.stages)} stages verified.", "choices": []}


def lines(d):
    if d["kind"] == "DO":
        return [f"DO: {d['command']}", f"   why: stage '{d['stage']}' - {d['why']}",
                "NEXT: run that command, then ask `fieldkit next` again."]
    if d["kind"] == "DONE":
        return [f"DONE: {d['why']}"]
    head = "BLOCKED" if d["kind"] == "BLOCKED" else "CANNOT HERE"
    return ([f"{head}: {d['why']}", "CHOOSE:"] + [f"  {i}. {c}" for i, c in enumerate(d["choices"], 1)]
            + ["NEXT: pick one number; do only that."])
