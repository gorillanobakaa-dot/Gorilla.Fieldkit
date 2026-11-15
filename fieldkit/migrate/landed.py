"""An intent whose hunk no longer matches the tree text, but whose change IS in the tree in another form - with the
proof re-checked on every measure (`migrate landed`).

2026-11-15 (Firefox 160, S2): five intents read NOT-IN-TREE although the tree carries them - the NEVER CALLS HOME
pref block (rewritten by 23.REBASE.158), the sandbox level under #ifdef XP_LINUX, media.peerconnection.ice.no_host
kept NOT locked by the owner's decision of 2026-10-03. The hunk judge compares the old hunk's text, and nothing could
say "it landed, here". A record step cannot either: it needs a working-tree diff, and there is none to make.

This is not an exemption: the record names a file and a verbatim quote, `record` refuses unless the quote is in the
file now, and `proven` re-reads it each time the statuses are computed. Remove the line from the tree and the intent
is NOT-IN-TREE again. Every record is journaled with the reason.
"""
import json
import time
from pathlib import Path

from ..buildh import task


def _path(task_id):
    from . import plan
    return plan.state_dir(task_id) / "landed.json"


def load(task_id):
    p = _path(task_id)
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def proven(workdir, rec):
    """-> (ok, evidence): the quote is still in the named file of the tree."""
    f = Path(workdir) / rec.get("file", "")
    if not f.is_file():
        return False, f"{rec.get('file')}: not in the tree"
    if rec.get("quote") and rec["quote"] in f.read_text(encoding="utf-8", errors="replace"):
        return True, f"landed in another form: {rec['file']} carries {rec['quote'][:80]!r} ({rec.get('why', '')[:80]})"
    return False, f"{rec['file']}: the recorded quote is no longer in it"


def record(task_id, intent, file, quote, why):
    why, quote = (why or "").strip(), (quote or "")
    if len(why) < 10:
        raise task.Refused("say where and why it landed differently (it is recorded): migrate landed TASK I-x FILE \"QUOTE\" \"why\"")
    if len(quote.strip()) < 8:
        raise task.Refused("the quote must be a verbatim line of the tree (8 characters or more), not a fragment")
    t = task.load(task_id)
    ok, ev = proven(t["workdir"], {"file": file, "quote": quote, "why": why})
    if not ok:
        raise task.Refused(f"not recorded: {ev}")
    d = load(task_id)
    d[intent] = {"file": file, "quote": quote, "why": why, "t": time.strftime("%Y-%m-%d %H:%M:%S")}
    p = _path(task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    task.journal(t, "migrate-landed", intent=intent, file=file, why=why)
    return {"intent": intent, "evidence": ev}


def proven_hunks(t):
    """-> {(patch, file, n): evidence} for the hunks of every intent whose `landed` record is proven in the tree now.

    For `build-harness verify` (2026-11-15, Firefox 160 S4): a port step of such a hunk, closed as obsolete, read as a
    false completion because the old hunk's lines are still there in their new form - the same text the intent judge
    already accepts by this record. The proof is the same: the verbatim quote re-read from the tree on every call."""
    recs = load(t.get("id") or "")
    if not recs:
        return {}
    try:
        from . import ledger, plan
        owner = plan.Migration(t["id"], t=t, find_install=False).owner      # the same owner the migration uses
        intents = ledger.load(owner)["intents"] if owner else []
    except Exception:                                   # no ledger, no proof: nothing is accepted
        return {}
    out = {}
    for e in intents:
        rec = recs.get(e.get("id"))
        if not rec or e.get("retired"):
            continue
        ok, ev = proven(t["workdir"], rec)
        if not ok:
            continue
        for h in e.get("hunks") or []:
            out[(e.get("patch"), h.get("file"), h.get("n"))] = f"{e['id']} {ev}"
    return out

