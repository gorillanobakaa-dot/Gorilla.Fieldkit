"""Hand work becomes patch set: every recorded hand-edit checkpoint of a port task is written as a patch into the
owner's gorilla-patchset, so the next port applies it like any other Gorilla patch (2026-10-02: 35 privacy cuts
and the 157 port repairs lived only as hand steps of one task; the next port would not have had them).

  port repairs  -> 21.PORT.FIXES.<version>/
  privacy cuts  -> 22.EGRESS.LOCKDOWN.<version>/
Both sort after 20.SNAPSHOT.DELTA (the hand edits were made on a tree carrying it) and port repairs before privacy
cuts; a step on a file an earlier privacy step touched stays privacy, so every file's patches apply in time order.

One patch per checkpoint, named NNN-<slug>.patch, headed by the reason recorded with the hand step. The commit
pairs come from the task's own checkpoints and the reasons from its journal; nothing is rewritten by hand.
"""
import json
import re
import subprocess
from pathlib import Path

from . import task

PRIVACY = re.compile(r"egress|telemetry|PHYSICAL LOCK|privacy|excis|Merino|Normandy|Sync|GMP|geolocation|UITour|"
                     r"Discovery Stream|ClientID|canary|pingsender|nmhproxy|desktop-launcher|ml |ML |aiwindow|genai|AI |"
                     r"WebGL|ModelHub|translations|weather|suggestions|Safe Browsing|captive|push|FxA|metrics", re.I)


def _git(w, *a):
    return subprocess.run(["git", "-C", str(w), *a], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def hand_commits(t):
    """-> [(commit, files, why, when)] for each hand-edit checkpoint, oldest first."""
    w = Path(t["workdir"])
    notes = []
    for line in (task.STATE / t["id"] / "journal.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "hand-edit":
            notes.append(e)
    log = _git(w, "log", "--reverse", "--format=%H\t%ad\t%s", "--date=iso").splitlines()
    commits = [l.split("\t", 2) for l in log if "\tcheckpoint: hand edit" in l]
    out = []
    for c, when, subj in commits:
        files = _git(w, "show", "--name-only", "--format=", c).split()
        why = next((" / ".join(n.get("why") or []) for n in notes if set(n.get("files") or []) == set(files)), subj)
        out.append((c, files, why, when))
    return out


def slug(s, n=60):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n] or "hand-edit"


def export(task_id, patchset_root, version, say=print, dry=False):
    t = task.load(task_id)
    w = Path(t["workdir"])
    groups = {"privacy": Path(patchset_root) / f"22.EGRESS.LOCKDOWN.{version}", "port": Path(patchset_root) / f"21.PORT.FIXES.{version}"}
    written = {"privacy": [], "port": []}
    privacy_files = set()
    for i, (c, files, why, when) in enumerate(hand_commits(t), 1):
        kind = "privacy" if PRIVACY.search(why) or privacy_files & set(files) else "port"
        if kind == "privacy":
            privacy_files |= set(files)
        diff = _git(w, "diff", "--no-color", "--no-renames", f"{c}^", c)
        if not diff.strip():
            continue
        head = f"# Gorilla {version} {kind} hand step, {when}\n# why: {why}\n# files: {', '.join(files)}\n# source commit (157.0-truth task checkpoint): {c}\n"
        name = f"{i:03d}-{slug(why)}.patch"
        written[kind].append((name, len(files)))
        if not dry:
            groups[kind].mkdir(parents=True, exist_ok=True)
            (groups[kind] / name).write_text(head + diff, encoding="utf-8", newline="\n")
    for kind, g in groups.items():
        if written[kind] and not dry:
            (g / "README.md").write_text(
                f"# {g.name}\n\nWritten by `fieldkit build-harness export-hand TASK` from the recorded hand steps of the {version} port.\n"
                f"Each patch carries the reason recorded with its step. {len(written[kind])} patch(es).\n\n"
                + "\n".join(f"- `{n}` ({k} file(s))" for n, k in written[kind]) + "\n", encoding="utf-8")
        say(f"  {g.name}: {len(written[kind])} patch(es)")
    return written
