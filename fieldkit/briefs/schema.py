"""The Brief: one decision a person must make, with everything needed to make it, built by code from harness data.

Rule (the maintainer, 2026-10-03): never present a one-line "should I do X?". A brief shows exactly what is affected
and what it contains (counts, verbatim examples, where it is linked from), every option, what each option changes,
its cost to users and to the project's credibility, a recommendation with the reason, and what will be recorded.
Default direction for unproven privacy or security claims: make them true and prove them, never delete them.

These rules live here, in code, so a small model driving the harness cannot forget them: `validate` refuses a brief
that breaks any of them, and only a valid brief is ever rendered or recorded.

    {
      "id": "B-DECISION-D-157-03",                 unique, stable across runs for the same need
      "topic": "...",                              a statement, never a question
      "kind": "decision" | "patch" | "claims" | "disposition" | "allowlist" | "deferred" | "owner-edit",
      "task": "firefox-157.0-truth",
      "source": {"check": "decisions.check", "verdict": "VIOLATED", "command": "fieldkit build-harness decisions TASK"},
      "affected": {"count": 3, "counts": {"label": n}, "items": [
          {"what": "...", "excerpt": "verbatim text", "location": "path:line", "linked_from": ["path:line", ...]}]},
      "options": [{"id", "label", "what_changes", "user_impact", "credibility_impact", "cost", "reversible": bool,
                   "records": "register" | "allowlist" | "disposition" | "journal",
                   optional "removes_claim": true, "carried_out_by": "command", "verify": [decision checks]}],
      "recommendation": {"option": "id", "why": "..."},
      "what_gets_recorded": "...",
      "blocking": {"blocks": ["..."], "deadline": "..." | None},
      "evidence": [{"file", "line", "text"}],      technical: shown with --technical
      "target": {...}                              what the record path needs (decision id, allowlist id, ...)
    }

The brief holds no timestamp: the same harness state always gives the same brief and the same sha256, which is what
a recorded decision is tied to.
"""
import hashlib
import json

REQUIRED = ("id", "topic", "kind", "task", "source", "affected", "options", "recommendation", "what_gets_recorded",
            "blocking")
OPTION_FIELDS = ("id", "label", "what_changes", "user_impact", "credibility_impact", "cost", "reversible", "records")
RECORDS = ("register", "allowlist", "disposition", "journal")
KINDS = ("decision", "patch", "claims", "disposition", "allowlist", "deferred", "owner-edit")
SHOW_ITEMS = 5          # the plain rendering shows this many items in full; --technical and --json show all


class Invalid(ValueError):
    """A brief that breaks the rule. It is never shown and never recorded."""


def problems(b):
    """-> [what is wrong]. Empty = valid."""
    out = []
    if not isinstance(b, dict):
        return ["a brief is a dict"]
    for f in REQUIRED:
        if f not in b or b[f] in (None, "", [], {}):
            out.append(f"missing {f}")
    if out:
        return out
    if b["kind"] not in KINDS:
        out.append(f"kind must be one of {KINDS}")
    if str(b["topic"]).rstrip().endswith("?"):
        out.append("the topic is a question: a brief states what needs deciding, it never asks a bare question")
    src = b["source"]
    if not isinstance(src, dict) or not src.get("check"):
        out.append("source must name the check that produced the need (source.check)")
    aff = b["affected"]
    items = aff.get("items") if isinstance(aff, dict) else None
    if not isinstance(aff, dict) or not isinstance(aff.get("count"), int) or items is None:
        out.append("affected needs count (int) and items (list)")
    else:
        if aff["count"] and not items:
            out.append(f"affected says {aff['count']} item(s) but lists none")
        if items and not any(str(i.get("excerpt") or "").strip() for i in items):
            out.append("affected items need at least one verbatim excerpt: show the content, not a summary of it")
        for n, i in enumerate(items):
            if not i.get("what") or not i.get("location"):
                out.append(f"affected item {n + 1} needs what and location")
    opts = b["options"]
    if not isinstance(opts, list) or len(opts) < 2:
        out.append("a brief needs at least 2 options (one of them may be to do nothing)")
        opts = opts if isinstance(opts, list) else []
    ids = [o.get("id") for o in opts]
    if len(set(ids)) != len(ids):
        out.append("option ids must be unique")
    for o in opts:
        for f in OPTION_FIELDS:
            v = o.get(f)
            if f == "reversible":
                if not isinstance(v, bool):
                    out.append(f"option {o.get('id')}: reversible must be true or false")
            elif v in (None, "") or (isinstance(v, str) and not v.strip()):
                out.append(f"option {o.get('id')}: missing {f}")
        if o.get("records") and o["records"] not in RECORDS:
            out.append(f"option {o.get('id')}: records must be one of {RECORDS}")
    rec = b["recommendation"]
    if not isinstance(rec, dict) or rec.get("option") not in ids or not str(rec.get("why") or "").strip():
        out.append("recommendation needs an option that exists and the reason (why)")
    else:
        chosen = next(o for o in opts if o.get("id") == rec["option"])
        if chosen.get("removes_claim"):
            out.append("the recommendation deletes a privacy or security claim: the default is to make it true and prove it, "
                       "never to delete it")
    blk = b["blocking"]
    if not isinstance(blk, dict) or "blocks" not in blk or "deadline" not in blk:
        out.append("blocking needs blocks (list) and deadline (text or null)")
    return out


def validate(b):
    p = problems(b)
    if p:
        raise Invalid(f"brief {b.get('id') if isinstance(b, dict) else '?'} refused: " + "; ".join(p))
    return b


def sha256(b):
    """The fingerprint of exactly what was shown: canonical JSON of the whole brief."""
    return hashlib.sha256(json.dumps(b, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def option(b, oid):
    o = next((x for x in b["options"] if x["id"] == oid), None)
    if o is None:
        raise Invalid(f"brief {b['id']} has no option {oid!r}; its options are: {', '.join(x['id'] for x in b['options'])}")
    return o


def _wrap(text, indent, width=110):
    import textwrap
    return textwrap.wrap(str(text), width=width, initial_indent=indent, subsequent_indent=indent) or [indent]


def render(b, technical=False):
    """Plain words for a person with no IT knowledge; technical adds every item and the file:line evidence."""
    validate(b)
    out = [f"DECISION NEEDED: {b['topic']}", f"  brief {b['id']}  (fingerprint {sha256(b)[:12]})", ""]
    src = b["source"]
    out += _wrap(f"Why you are seeing this: the check '{src['check']}' reported {src.get('verdict', 'a problem')}"
                 + (f" (run it yourself: {src['command']})" if src.get("command") else "") + ".", "")
    aff = b["affected"]
    counts = ", ".join(f"{k} {v}" for k, v in (aff.get("counts") or {}).items())
    out += ["", f"What is affected: {counts or aff['count']}. What it says, word for word:"]
    items = aff["items"] if technical else aff["items"][:SHOW_ITEMS]
    for n, i in enumerate(items, 1):
        out += _wrap(f"{n}. {i['what']}", "  ")
        if i.get("excerpt"):
            quoted = [w for line in str(i["excerpt"]).splitlines() for w in (_wrap(line, "", 100) if line.strip() else [""])]
            cap = 60 if technical else 10
            out += [f"       | {line}" for line in quoted[:cap]]
            if len(quoted) > cap:
                out.append(f"       | ... ({len(quoted) - cap} more line(s): --technical or --json)")
        out.append(f"       where: {i['location']}")
        if i.get("linked_from"):
            lf = i["linked_from"] if technical else i["linked_from"][:3]
            more = len(i["linked_from"]) - len(lf)
            out.append(f"       linked from: {'; '.join(lf)}" + (f" and {more} more" if more else ""))
    if len(aff["items"]) > len(items):
        out.append(f"  ... and {len(aff['items']) - len(items)} more: fieldkit build-harness brief show {b['id']} --technical")
    out += ["", "Your options:"]
    for o in b["options"]:
        rec = "   <- recommended" if o["id"] == b["recommendation"]["option"] else ""
        out.append(f"  [{o['id']}] {o['label']}{rec}")
        out += _wrap(f"What changes: {o['what_changes']}", "      ")
        out += _wrap(f"For the people who use the browser: {o['user_impact']}", "      ")
        out += _wrap(f"For the project's credibility: {o['credibility_impact']}", "      ")
        out += _wrap(f"Cost: {o['cost']}", "      ")
        out.append(f"      Can it be undone later? {'yes' if o['reversible'] else 'NO'}")
        if o.get("carried_out_by"):
            out += _wrap(f"This option changes files; it is carried out by: {o['carried_out_by']}", "      ")
    why = str(b["recommendation"]["why"]).strip()
    out += [""] + _wrap(f"Recommendation: [{b['recommendation']['option']}]. Why: {why[0].upper() + why[1:]}", "")
    out += [""] + _wrap(f"What will be recorded: {b['what_gets_recorded']}", "")
    blk = b["blocking"]
    if blk.get("blocks"):
        out += ["", "Until this is decided:"] + [x for s in blk["blocks"] for x in _wrap(f"- {s}", "  ")]
    if blk.get("deadline"):
        out += _wrap(f"Deadline: {blk['deadline']}", "")
    if technical and b.get("evidence"):
        out += ["", "Evidence (file:line):"] + [f"  {e['file']}:{e.get('line', '?')}: {str(e.get('text', ''))[:220]}"
                                                for e in b["evidence"]]
    out += ["", "To answer, the maintainer types this at a real terminal (an assistant cannot record it):",
            f"  fieldkit build-harness decide {b['id']} OPTION --words \"what you decided, in your own words\""]
    return out


def summary_line(b):
    return (f"{b['id']}  [{b['kind']}] {b['topic'][:110]}  ({b['affected']['count']} affected; "
            f"recommended: {b['recommendation']['option']})")
