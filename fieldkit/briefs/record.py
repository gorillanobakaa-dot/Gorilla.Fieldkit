"""Recording a person's answer to a brief. The maintainer's alone, at a real terminal: an AI can never record a
decision (the MCP door lists briefs read-only and has no decide tool; here task.owner_terminal() is required).

    fieldkit build-harness decide BRIEF-ID OPTION --words "the person's own words" [--task TASK]

Order of checks: a real terminal; words given; the brief regenerated from the harness state now; the option exists;
the brief with exactly this sha256 was shown (`brief show ID`) before; the option is not one that changes files
(those keep their own guarded doors, named in the brief). Then, by the option's `records`:

    register     a NEW entry appended to decisions/PRODUCT-DECISIONS.yaml (text append; nothing above it is
                 touched; the file is re-read and the append undone if it no longer loads cleanly). Provenance =
                 the date + the person's words + the brief's sha256. Status pending, unless the option attaches
                 checks (then enforced: the checks decide, not the words).
    allowlist    leakgate.allow.approve (owner terminal), then the approval carries the words and the sha256
    disposition  the disposition's approval {by: owner, how: terminal, at, quote: words, brief, brief_sha256}
    journal      nothing else

Always: the task journal gets event "decide" with the brief id, its sha256, the option and the words, and the brief
itself is saved as state/build-harness/TASK/briefs/<id>.<sha12>.json, so what was shown can be shown again.
"""
import json
import re
import time
from pathlib import Path

import yaml

from . import schema


def _dir(task_id):
    from ..buildh import task
    return task.STATE / task_id / "briefs"


def mark_shown(task_id, b):
    """`brief show` calls this: the brief, exactly as shown, is saved and the showing is logged."""
    d = _dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    digest = schema.sha256(b)
    snap = d / f"{b['id']}.{digest[:12]}.json"
    if not snap.is_file():
        snap.write_text(json.dumps(b, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    with open(d / "shown.jsonl", "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "id": b["id"], "sha256": digest}) + "\n")
    return digest


def was_shown(task_id, brief_id, digest):
    p = _dir(task_id) / "shown.jsonl"
    if not p.is_file():
        return False
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("id") == brief_id and e.get("sha256") == digest:
            return True
    return False


def _next_id(entries, release):
    major = str(release or "").split(".")[0] or "0"
    nums = [int(m.group(1)) for e in entries if (m := re.match(rf"D-{re.escape(major)}-(\d+)$", str(e.get("id"))))]
    return f"D-{major}-{max(nums, default=0) + 1:02d}"


def append_register(owner, b, o, words, digest, today=None):
    """-> the new entry's id. Append-only: the file's existing bytes are kept exactly."""
    from ..buildh import decisions as dec
    p = Path(owner) / dec.REGISTER
    raw = p.read_bytes()
    reg = dec.load(owner)
    before = set(reg["problems"])
    today = today or time.strftime("%Y-%m-%d")
    nid = _next_id(reg["entries"], reg["release"])
    tgt = b.get("target") or {}
    checks = o.get("verify") or []
    entry = {"id": nid, "title": f"{b['topic'][:180]}: {o['label']}", "decided": today, "by": "maintainer",
             "provenance": f"{today} fieldkit build-harness decide {b['id']} {o['id']}: \"{words}\" (brief sha256 {digest})",
             "status": "enforced" if checks else "pending",
             "why": (f"{o['label']}. What changes: {o['what_changes']} Users: {o['user_impact']} "
                     f"Credibility: {o['credibility_impact']} Cost: {o['cost']}"),
             "answers_brief": {"id": b["id"], "sha256": digest, "option": o["id"]}}
    if not checks:
        entry["pending_on"] = o["what_changes"]
    for k in ("decision", "patch", "disposition", "allow", "step", "path"):
        if tgt.get(k):
            entry["amends" if k == "decision" else k] = tgt[k]
    if tgt.get("claims"):
        entry["claims"] = list(tgt["claims"])
    if checks:
        entry["verify"] = checks
    text = yaml.safe_dump([entry], sort_keys=False, allow_unicode=True, width=4096, default_flow_style=None)
    block = "\n" + "".join("  " + l + "\n" for l in text.splitlines())
    tail = b"" if raw.endswith(b"\n") else b"\n"
    with open(p, "ab") as f:
        f.write(tail + block.encode("utf-8"))
    try:
        after = dec.load(owner)
        ok = any(e.get("id") == nid for e in after["entries"]) and set(after["problems"]) <= before
    except Exception:
        ok = False
    if not ok:
        p.write_bytes(raw)
        raise schema.Invalid(f"the register would not load cleanly with the new entry; nothing was changed ({p})")
    return nid


def _approve_allow(owner, b, o, words, digest):
    from ..leakgate import allow as la
    path = la.path_for(owner)
    eid = b["target"]["allow"]
    done = la.approve(path, {eid}, say=lambda m: None)
    if eid not in done:
        raise schema.Invalid(f"allowlist entry {eid} is not in {path} any more")
    data = la.load(path)
    for e in data["entries"]:
        if e["id"] == eid:
            e["approval"].update({"quote": words, "brief": b["id"], "brief_sha256": digest, "option": o["id"]})
    la.save(path, data)
    return f"{path} entry {eid} approved"


def _approve_disposition(owner, b, o, words, digest):
    path = Path(owner) / "leakgate" / "dispositions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    key = b["target"]["disposition"]
    d = data.get(key)
    if not isinstance(d, dict):
        raise schema.Invalid(f"{key} has no disposition in {path} any more")
    if d.get("approval"):
        raise schema.Invalid(f"{key} is approved already ({d['approval'].get('at')}); nothing is overwritten")
    d["approval"] = {"by": "owner", "how": "terminal", "at": time.strftime("%Y-%m-%d %H:%M:%S"), "quote": words,
                     "choice": o["label"], "brief": b["id"], "brief_sha256": digest}
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return f"{path} {key} approved ({o['label']})"


def decide(task_id, brief_id, option_id, words, ctx=None):
    """-> {"brief", "sha256", "option", "recorded"}. Raises task.Refused / schema.Invalid; never records partially."""
    from ..buildh import task
    from . import producers
    if not task.owner_terminal():
        raise task.Refused("a decision is the maintainer's, typed at a real terminal; an assistant's shell has none, and an "
                           "AI never records a decision")
    words = (words or "").strip()
    if len(words) < 3:
        raise task.Refused("say what you decided in your own words: --words \"...\" (they are recorded as the provenance)")
    ctx = ctx or producers.Context(task_id)
    b = producers.find(ctx, brief_id)
    o = schema.option(b, option_id)
    digest = schema.sha256(b)
    if not was_shown(task_id, brief_id, digest):
        raise task.Refused(f"read the brief first, as it is now: fieldkit build-harness brief show {brief_id} --task {task_id}"
                           " (a decision is tied to exactly what was shown; if the data changed, the brief changed too)")
    if o.get("carried_out_by"):
        raise task.Refused(f"option {o['id']} changes files and has its own guarded door: {o['carried_out_by']}")
    rec = o["records"]
    owner = ctx.owner
    if rec == "register":
        nid = append_register(owner, b, o, words, digest)
        where = f"decisions/PRODUCT-DECISIONS.yaml new entry {nid}"
    elif rec == "allowlist":
        where = _approve_allow(owner, b, o, words, digest)
    elif rec == "disposition":
        where = _approve_disposition(owner, b, o, words, digest)
    else:
        where = "the task journal only"
    d = _dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    snap = d / f"{b['id']}.{digest[:12]}.json"
    if not snap.is_file():
        snap.write_text(json.dumps(b, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    task.journal(task.load(task_id), "decide", brief=b["id"], sha256=digest, option=o["id"], words=words, recorded=where,
                 snapshot=str(snap))
    return {"brief": b["id"], "sha256": digest, "option": o["id"], "recorded": where, "snapshot": str(snap)}
