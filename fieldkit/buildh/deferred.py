"""Briefs for DEFERRED steps: hunks the harness parked because the thing they change is gone from the new source.

The harness measures everything it can (what the hunk does, whether the settings it touches still exist, what the
new source says now, what the owner's own earlier port did) and says plainly what it cannot know. Doing nothing
is always safe: the step stays parked and the build gate stays closed. Dropping a change needs the owner at a real
terminal, the explanation shown 30 s earlier, and a typed sentence; it is recorded and counts as a briefed
decision, never as a skip.
"""
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from . import decision, firefox, task

PREF = re.compile(r'pref\("([^"]+)"')
PREPROC = re.compile(r"^#\s*(if|ifdef|ifndef|else|elif|endif|define|include|undef|expand|filter)\b")


def _is_comment(line, file):
    s = line.strip()
    if not s:
        return True
    if s.startswith(("//", "/*", "*", "<!--")):
        return True
    return s.startswith("#") and not PREPROC.match(s) and file.endswith((".ftl", ".py", ".sh", ".yaml", ".yml", ".toml", ".properties"))


def _step(t, step_id):
    s = next((x for x in t["steps"] if x["id"] == step_id), None)
    if s is None:
        s = next((x for x in t["steps"] if x["id"].endswith(step_id) and x["status"] == "deferred"), None)
    if s is None:
        raise task.Refused(f"no step called {step_id}")
    return s


def listing(task_id):
    t = task.load(task_id)
    return [(s["id"], (("OBSOLETE (resolved by default, review): " if s["status"] == "obsolete" else "") + (s.get("last_why") or [""])[0])[:120])
            for s in t["steps"] if s["status"] in ("deferred", "obsolete")]


def _grep(wd, needle):
    dirs = [d for d in ("browser", "toolkit", "modules") if (Path(wd) / d).is_dir()]      # a missing folder must not fail the whole search
    if not dirs:
        raise task.Refused(f"cannot search {wd}: none of browser/toolkit/modules exists, so 'not used anywhere' would be a guess")
    r = subprocess.run(["git", "-C", str(wd), "grep", "-lF", "-e", needle, "--", *dirs], capture_output=True, text=True, errors="replace")
    if r.returncode not in (0, 1):
        raise task.Refused(f"the search for {needle} failed ({r.stderr.strip()[:120]}), so nothing is concluded from it")
    return [l for l in r.stdout.splitlines() if l and "/test" not in l and "/tests/" not in l]


def short(step_id):
    m = re.search(r"([^-]+\.\w+)-h(\d+)$", step_id)
    return f"{m.group(1)} h{m.group(2)}" if m else step_id


def build(task_id, step_id):
    """-> a brief (dict). Read-only: nothing in the task or the working copy is changed."""
    t = task.load(task_id)
    s = _step(t, step_id)
    if s["status"] != "deferred":
        return {"kind": "deferred", "what": f"{s['id']} is not deferred (status: {s['status']})", "options": []}
    wd, file, hunk = Path(t["workdir"]), s["args"]["file"], s["args"]["hunk"]
    removed, added, context = firefox.hunk_sides(hunk)
    body = (wd / file).read_text(encoding="utf-8", errors="replace").splitlines()
    changed = [l for l in removed + added if l.strip()]
    comment_only = all(_is_comment(l, file) for l in changed)
    prefs = sorted({m for l in removed + added + context for m in PREF.findall(l)})
    pref_state = {}
    for p in prefs:
        in_file = any(f'pref("{p}"' in l for l in body)
        elsewhere = [] if in_file else _grep(wd, p)
        pref_state[p] = ("defined in this file now" if in_file else
                         f"no longer defined here; the name still appears in {len(elsewhere)} other file(s)" if elsewhere else
                         "not defined or used anywhere in the new source")
    changed_prefs = {m for l in removed + added for m in PREF.findall(l)}     # context lines only anchor; they do not decide
    gone_everywhere = bool(changed_prefs) and all(pref_state[p].startswith("not defined") for p in changed_prefs)
    # what the owner's own earlier port says (evidence only: it is never copied)
    key = Path(t["meta"].get("harness_root", "")) / "src" / file
    older = None
    if key.is_file():
        k = {l.strip() for l in key.read_text(encoding="utf-8", errors="replace").splitlines()}
        sp_rem = [l.strip() for l in removed if len(l.strip()) >= firefox.SPECIFIC]
        sp_add = [l.strip() for l in added if len(l.strip()) >= firefox.SPECIFIC]
        older = (any(x in k for x in sp_rem), any(x in k for x in sp_add))
    anchor = next((i for i, l in enumerate(body) if any(len(c.strip()) >= 12 and c.strip() == l.strip() for c in context)), None)
    nearby = body[anchor:anchor + 6] if anchor is not None else []
    fp = hashlib.sha256(json.dumps(hunk, sort_keys=True).encode()).hexdigest()[:12]

    evidence = [f"step {short(s['id'])} in group {s['id'].split('-')[1]}, patch {s['args']['patch']}",
                f"the hunk changes {len(removed)} line(s) to {len(added)} line(s); "
                + ("every changed line is a comment, so it has no effect on how Firefox behaves" if comment_only else
                   "it changes more than comments: code or settings"),
                f"why it was parked: {(s.get('last_why') or ['?'])[0][:230]}"]
    evidence += [f"setting {p}: {v}" for p, v in pref_state.items()]
    if older is not None:
        evidence.append("your own earlier port: " + ("still has the old text" if older[0] else "does NOT have the old text") +
                        " and " + ("has the new text" if older[1] else "does not have the new text"))
    hypotheses = [
        ("Mozilla removed the feature this change adjusted", "the old text is gone and the surrounding lines are still there",
         "cannot be proven from the text alone"),
        ("Mozilla reworded or moved it", "possible when only comments differ" if comment_only else
         "possible when the setting still exists elsewhere" if any("still appears" in v for v in pref_state.values()) else
         "no sign of it in the new source", "only reading the upstream change would settle it")]
    blast_future = ["dropping: the exported patch for this group will not contain this change, so the build will not have it",
                    "keeping it parked: nothing is built; the build gate refuses until every parked step is decided"]
    blast_past = ["nothing: the step was parked before any file was touched (the working copy has no edit for it)"]
    undetermined = ["whether you still WANT this change: only the owner knows what it was for",
                    "what Mozilla intended: the harness has no access to the reasoning behind the upstream change"]
    if comment_only or gone_everywhere:
        rec = "drop"
        why = ("it only changes a comment, so dropping it cannot change what Firefox does" if comment_only else
               "the setting it adjusts is not defined or used anywhere in the new source, so the change would have nothing to act on")
    else:
        rec, why = "hold", "it changes behaviour and the setting still exists elsewhere: a person should read what Mozilla changed first"
    options = [
        {"key": "hold", "label": "leave it parked", "consequence": "nothing changes; the build gate stays closed", "reversible": True},
        {"key": "drop", "label": "drop this one change", "reversible": True,
         "consequence": "it is recorded as dropped on purpose, after this explanation; the group's patch will not contain it, and the "
                        "record says exactly what was dropped so it can be put back by hand later"},
        {"key": "recreate", "label": "re-create it by hand in the new place", "reversible": True,
         "consequence": "you edit the file yourself, then ask the harness to check it; the harness never writes it for you"}]
    return {"kind": "deferred", "what": f"{short(s['id'])} cannot be applied because the lines it changes are gone from Firefox",
            "evidence": evidence, "hypotheses": hypotheses, "blast_past": blast_past, "blast_future": blast_future,
            "not_determined": undetermined, "door": "two-way", "options": options, "recommended": rec, "why": why,
            "confirm": f"DROP THIS CHANGE: {short(s['id'])}", "nearby": nearby, "removed": removed, "added": added,
            "task": task_id, "step": s["id"], "fingerprint": fp, "comment_only": comment_only,
            # fields the shared log expects
            "path": file, "diff": json.dumps(hunk, sort_keys=True)}


def render_plain(b):
    if not b.get("options"):
        return [b["what"]]
    out = ["ONE OF YOUR CHANGES COULD NOT BE MOVED INTO THE NEW FIREFOX - NOTHING HAS BEEN HURT.", "",
           f"What happened: you (or an earlier you) made a small change to Firefox's files: {b['what'].split(' cannot')[0]}. "
           "In the new Firefox the lines that change was meant to adjust no longer exist, so it cannot be copied across. "
           "The assistant that helps with this was NOT asked to guess, on purpose.",
           "", "What kind of change it was:  " + ("only a note (a comment) that people read; it does nothing to how Firefox works."
                                                  if b["comment_only"] else
                                                  "it changes how Firefox behaves, so it matters whether it is kept.")]
    out += ["", "What you need to do:", "  Nothing. That is the safe answer. The new Firefox just will not be built until this is decided.", ""]
    out += [f"What I would do: {'drop it' if b['recommended'] == 'drop' else 'leave it and ask someone who knows what it was for'}. "
            f"Reason: {b['why']}."]
    if b["recommended"] == "drop":
        out += ["  Dropping means the new Firefox simply will not have this one small change. The decision is written down,",
                "  with exactly what was dropped, so it can be put back by hand later."]
    out += ["", "What nobody can tell you from the files: whether you still WANT this change. Only you know what it was for.",
            "If you do not remember, do nothing."]
    if b["recommended"] == "drop":
        out += ["", "To drop it, open a normal terminal yourself (not through an assistant) and paste this one line:",
                f"    fieldkit build-harness deferred {b['task']} {short(b['step']).replace(' ', '-')} --do \"{b['confirm']}\"",
                f"  The computer makes you wait {decision.COOLING_OFF_SECONDS} seconds first. That is on purpose."]
    out += ["", "Things that will NOT work, on purpose: pressing yes, typing 'ok', or having an assistant do it for you."]
    return out


def render(b):
    out = decision.render(b)
    if b.get("nearby"):
        out += ["", "What the new source says at that spot:"] + [f"    {l[:110]}" for l in b["nearby"]]
    return out


def show(b, plain=True):
    decision._log("shown", b)
    return render_plain(b) if plain else render(b)


def apply_drop(task_id, step_id, typed):
    """Record an owner's decision to drop a deferred change. Same guards as every other change of the owner's data."""
    b = build(task_id, step_id)
    if not b.get("options"):
        raise task.Refused(b["what"])
    if typed != b["confirm"]:
        raise task.Refused(f"to apply this, type exactly: {b['confirm']}   (leaving it parked is the default and needs nothing)")
    if not task.owner_terminal():
        raise task.Refused("this records a decision about your project: it needs the owner at a real terminal")
    shown = decision._shown_at(b)
    if shown is None:
        raise task.Refused("you have not been shown the explanation yet: run `build-harness deferred TASK STEP` and read it first")
    wait = decision.COOLING_OFF_SECONDS - (time.time() - shown)
    if wait > 0:
        raise task.Refused(f"wait {wait:.0f} more seconds: the pause is on purpose")
    log = task.STATE / task_id / "drive.log"
    if log.is_file() and time.time() - log.stat().st_mtime < 180:
        raise task.Refused("a job is running right now; wait until it stops, then decide")
    t = task.load(task_id)
    s = _step(t, step_id)
    s["status"], s["dropped_by_owner"], s["drop_fingerprint"] = "done", True, b["fingerprint"]
    task.save(t)
    task.journal(t, "owner-drop", step=s["id"], fingerprint=b["fingerprint"], removed=b["removed"][:8], added=b["added"][:8],
                 recommended=b["recommended"], comment_only=b["comment_only"])
    decision._log("dropped", b, step=s["id"])
    return (f"recorded: {short(s['id'])} dropped on purpose. The exact lines are in the journal "
            f"(fingerprint {b['fingerprint']}), so it can be put back by hand later.")
