"""Decision briefs: when the harness cannot decide something itself, it does the analysis first and hands the
owner a brief, never a bare question. Facts are measured by code; anything the code cannot know is listed as
NOT DETERMINED, not guessed. Nothing here assumes the owner will read carefully: the safe default needs no
action, and applying the other option means typing the consequence, not clicking yes.

Doors: a TWO-WAY door (the change is saved before it is undone, and nothing consumed it) may be settled by the
owner in one typed line; a ONE-WAY door is held, the build gate stays closed, and the brief explains why.
"""
import hashlib
import json
import subprocess
import time
from pathlib import Path

from . import task


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace").stdout


def _journal_near(ts, window=900):
    """Harness events within `window` seconds of a time: what was running when the file changed."""
    out = []
    for jp in sorted(task.STATE.glob("*/journal.jsonl")) if task.STATE.exists() else []:
        for line in jp.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
                t = time.mktime(time.strptime(e["t"], "%Y-%m-%d %H:%M:%S"))
            except (ValueError, KeyError):
                continue
            if abs(t - ts) <= window:
                out.append((e["t"], e["task"], e["event"], e.get("step", "")))
    return out[:6], len(out)


def owner_file_edit(repo, path):
    """An uncommitted change to a tracked file in the owner's own repository -> a decision brief (a dict)."""
    repo, f = Path(repo), Path(repo) / path
    diff = _git(repo, "diff", "--", path)
    if not diff.strip():
        return {"kind": "owner-file-edit", "what": f"{path} has no uncommitted change", "options": [], "recommended": None}
    mtime = f.stat().st_mtime
    last = _git(repo, "log", "-1", "--format=%h|%ad|%an|%s", "--date=format:%Y-%m-%d %H:%M", "--", path).strip().split("|", 3)
    changed = [l for l in diff.splitlines() if l[:1] in ("+", "-") and l[:3] not in ("+++", "---")]
    near, n_near = _journal_near(mtime)
    consumers = []
    for jp in (task.STATE.glob("*/build-record.json") if task.STATE.exists() else []):
        rec = json.loads(jp.read_text(encoding="utf-8"))
        if "mozconfig_sha256" in rec:
            consumers.append((jp.parent.name, rec["mozconfig_sha256"][:12]))
    all_logs = [p for p in (repo / "logs").glob("*.log")] if (repo / "logs").is_dir() else []
    logs = [p for p in (repo / "logs").glob("*") if p.stat().st_mtime > mtime] if (repo / "logs").is_dir() else []
    two_way = not logs
    # was the old text ever exercised, and the new text? (a recorded build that used it is the strongest evidence there is)
    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in all_logs if p.stat().st_size < 20_000_000}

    def seen(line):
        toks = sorted((t.strip('"\'') for t in line[1:].split() if len(t.strip('"\'')) >= 8), key=len, reverse=True)
        key = toks[0] if toks else None            # the most specific token only: generic words match every log
        return next((p.name for p, body in texts.items() if key and key in body), None)
    removed = [l for l in changed if l.startswith("-")]
    added = [l for l in changed if l.startswith("+")]
    proven = [(l[1:].strip(), seen(l)) for l in removed]
    unproven = [(l[1:].strip(), seen(l)) for l in added]

    evidence = [f"changed {time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))}; last committed edit "
                f"{last[1] if len(last) > 1 else '?'} by {last[2] if len(last) > 2 else '?'}: "
                f"\"{last[3] if len(last) > 3 else '?'}\"",
                f"{len(changed)} changed line(s); fingerprint of the change {hashlib.sha256(diff.encode()).hexdigest()[:12]}"]
    evidence += [f"harness activity within 15 min of the change: {n_near} event(s), e.g. {near[0][2]} on {near[0][1]} at {near[0][0]}"
                 if n_near else "no harness activity within 15 min of the change"]
    for text, where in proven:
        evidence.append(f"OLD text \"{text[:70]}\" " + (f"appears in the recorded log {where}: it was in use during a real run" if where
                                                     else "appears in no recorded log"))
    for text, where in unproven:
        evidence.append(f"NEW text \"{text[:70]}\" " + (f"appears in the recorded log {where}" if where else
                                                     "appears in no recorded log: it has never been exercised"))
    explained = any(l.startswith("+") and l.lstrip("+").lstrip().startswith("#") for l in changed)
    hypotheses = [
        ("an agent or script edited it",
         "harness events were running at that moment" if n_near else "no harness event nearby, which weakens this",
         "agents were told never to touch the owner's folders"),
        ("the owner edited it on purpose",
         "the change carries its own explanation comment" if explained else "the change carries no explanation, which weakens this",
         "only the owner can confirm"),
        ("a tool changed it as a side effect", "possible if the change is mechanical", "no tool here is meant to write this file")]
    blast_past = ([f"{len(logs)} log file(s) in the repo are newer than the change: something ran after it; check whether a build used it"]
                  if logs else ["nothing in the repo's logs is newer than the change: no build has used it yet"])
    blast_past += [f"build record(s) naming a mozconfig fingerprint: {consumers}" if consumers else "no build record captured it"]
    blast_future = ["the next build compiles with whatever this file says; the build gate records its hash, so a later change is detected",
                    "the audit baseline cannot be re-taken while it differs from the committed version (it would bless it)"]
    undetermined = ["whether the change is functionally right: the harness cannot run the build to find out",
                    "who made it: only timing evidence exists, not a signature"]
    options = [
        {"key": "hold", "label": "do nothing", "consequence": "the build gate keeps refusing; no harm, nothing proceeds", "reversible": True},
        {"key": "revert", "label": f"restore the committed version of {path}", "reversible": True,
         "consequence": "the file returns to the last committed state; the change is saved first and can be re-applied"},
        {"key": "keep", "label": "adopt the change (commit it yourself)", "reversible": True,
         "consequence": "the changed file becomes the verified build config; re-baseline the audit afterwards"}]
    why = ("it is a two-way door (the change is saved before reverting and nothing has consumed it), and the committed version is "
           "the last one that was verified" + (", and the old text is proven by a recorded run while the new text never ran"
                                               if any(w for _, w in proven) and not any(w for _, w in unproven) else "") if two_way else
           "something ran after the change; reverting could hide what was built, so nothing is changed until that is understood")
    return {"kind": "owner-file-edit", "what": f"{path} in {repo.name} differs from its committed version", "evidence": evidence,
            "hypotheses": hypotheses, "blast_past": blast_past, "blast_future": blast_future, "not_determined": undetermined,
            "door": "two-way" if two_way else "one-way", "options": options, "recommended": "revert" if two_way else "hold",
            "why": why, "confirm": f"REVERT {path}", "diff": diff, "repo": str(repo), "path": path}


def apply(brief, option, typed, out_dir):
    """Carry out an option. Reverting needs the typed sentence AND a real terminal, and saves the diff first."""
    if option == "hold":
        return "nothing changed"
    if option != "revert" or typed != brief["confirm"]:
        raise task.Refused(f"to apply this, type exactly: {brief['confirm']}   (hold is the default and needs nothing)")
    if not task.owner_terminal():
        raise task.Refused("this changes the owner's repository: it needs the owner at a real terminal")
    out = Path(out_dir) / f"{Path(brief['path']).name}.{time.strftime('%Y%m%d-%H%M%S')}.diff"
    out.write_text(brief["diff"], encoding="utf-8")
    subprocess.run(["git", "-C", brief["repo"], "checkout", "--", brief["path"]], check=True)
    return f"restored; the change is saved in {out} (git apply puts it back)"


def render(b):
    if not b.get("options"):
        return [b["what"]]
    out = [f"DECISION BRIEF: {b['what']}", "", "What the harness measured:"] + [f"  - {x}" for x in b["evidence"]]
    out += ["", "How it may have happened (ranked by evidence, none proven):"]
    out += [f"  {i}. {h}: {a}; {c}" for i, (h, a, c) in enumerate(b["hypotheses"], 1)]
    out += ["", "What it has already affected:"] + [f"  - {x}" for x in b["blast_past"]]
    out += ["What it will affect:"] + [f"  - {x}" for x in b["blast_future"]]
    out += ["", "NOT DETERMINED (the harness will not guess):"] + [f"  - {x}" for x in b["not_determined"]]
    out += ["", f"Door: {b['door'].upper()}", "Options:"]
    out += [f"  [{o['key']}] {o['label']} -> {o['consequence']}" for o in b["options"]]
    out += ["", f"Recommended: {b['recommended']} - {b['why']}",
            "Default if you do nothing: hold (nothing changes, the build gate stays closed).",
            f"To apply a change you must type: {b['confirm']}"]
    return out
