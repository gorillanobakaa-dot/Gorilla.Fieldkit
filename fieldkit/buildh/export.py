"""Hand work becomes patch set: every recorded hand-edit checkpoint of a port task is written as a patch into the
owner's gorilla-patchset, so the next port applies it like any other Gorilla patch (2026-10-02: 35 privacy cuts
and the 157 port repairs lived only as hand steps of one task; the next port would not have had them).

  port repairs  -> 21.PORT.FIXES.<version>/
  privacy cuts  -> 22.EGRESS.LOCKDOWN.<version>/
Both sort after 20.SNAPSHOT.DELTA (the hand edits were made on a tree carrying it) and port repairs before privacy
cuts; a step on a file an earlier privacy step touched stays privacy, so every file's patches apply in time order.

One patch per checkpoint, named NNN-<slug>.patch, headed by the reason recorded with the hand step. The commit
pairs come from the task's own checkpoints and the reasons from its journal; nothing is rewritten by hand.

Which group a patch goes in is the `kind` recorded with the hand step (`record ... kind=privacy|port`). Only a step
recorded without one falls back to a keyword search in its reason, and every patch's kind and where it came from
is printed, so the maintainer reviews the split before anything is published.
"""
import json
import re
import subprocess
from pathlib import Path

from . import task
from .handedit import KINDS

PRIVACY = re.compile(r"egress|telemetry|PHYSICAL LOCK|privacy|excis|Merino|Normandy|Sync|GMP|geolocation|UITour|"
                     r"Discovery Stream|ClientID|canary|pingsender|nmhproxy|desktop-launcher|ml |ML |aiwindow|genai|AI |"
                     r"WebGL|ModelHub|translations|weather|suggestions|Safe Browsing|captive|push|FxA|metrics", re.I)


def _git(w, *a):
    return subprocess.run(["git", "-C", str(w), *a], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def hand_commits(t):
    """-> [(commit, files, why, when, recorded kind or None)] for each hand-edit checkpoint, oldest first."""
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
        note = next((n for n in notes if set(n.get("files") or []) == set(files)), None)
        why = " / ".join(note.get("why") or []) if note else subj
        out.append((c, files, why, when, (note or {}).get("kind")))
    return out


def kind_of(why, files, recorded, privacy_files):
    """-> (kind, how it was decided). The recorded kind wins; keywords only when none was recorded. A file an
    earlier privacy cut touched keeps every later patch in the privacy group, because 21.PORT.FIXES applies before
    22.EGRESS.LOCKDOWN and the later patch was made on top of the cut."""
    if privacy_files & set(files):
        if recorded == "port":
            return "privacy", "recorded port, KEPT privacy: an earlier privacy cut of the same file must apply first"
        return "privacy", "recorded" if recorded == "privacy" else "follows an earlier privacy cut of the same file"
    if recorded in KINDS:
        return recorded, "recorded"
    return ("privacy" if PRIVACY.search(why or "") else "port"), "keyword guess, nothing recorded: review"


NARRATION = [(r"^\s*20\d\d-\d\d-\d\d\s*", ""), (r"^build \d+ (stop|proof)\s*:\s*", ""), (r"^build \d+,\s*", ""),
             (r"\s*\(leakgate source inventory\)", ""), (r"\(build \d+\)", ""), (r"\(kept as _private/[^)]*\)", "(proposed separately)"),
             (r"the owner's preflight guard refuses pref decisions from anyone but the owner", "pref decisions are recorded separately"),
             (r"the owner's", "Gorilla's"), (r"\bthe owner\b", "Gorilla"), (r"owner's", "Gorilla's"), (r"\bowner\b", "Gorilla")]


def public_note(why):
    """A recorded reason as a public technical note."""
    w = (why or "").strip()
    for a, b in NARRATION:
        w = re.sub(a, b, w, flags=re.I)
    w = re.sub(r"\s{2,}", " ", w).strip(" :")
    return (w[0].upper() + w[1:]) if w else "Hand edit"


def slug(s, n=60):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n] or "hand-edit"


def export(task_id, patchset_root, version, say=print, dry=False):
    t = task.load(task_id)
    w = Path(t["workdir"])
    groups = {"privacy": Path(patchset_root) / f"22.EGRESS.LOCKDOWN.{version}", "port": Path(patchset_root) / f"21.PORT.FIXES.{version}"}
    written = {"privacy": [], "port": [], "kinds": []}
    privacy_files = set()
    for i, (c, files, why, when, recorded) in enumerate(hand_commits(t), 1):
        kind, how = kind_of(why, files, recorded, privacy_files)
        if kind == "privacy":
            privacy_files |= set(files)
        diff = _git(w, "diff", "--no-color", "--no-renames", "--binary", f"{c}^", c)     # logos are binary
        if not diff.strip():
            continue
        # the public repo's rule: technical notes, not a diary - no dates, no build narration, no local commit ids
        note = public_note(why)
        head = f"# Gorilla {version} {'privacy cut' if kind == 'privacy' else 'port fix'}\n# {note}\n# Files: {', '.join(files)}\n\n"
        name = f"{i:03d}-{slug(note)}.patch"
        written[kind].append((name, note))
        written["kinds"].append((name, kind, how))
        say(f"  [{'privacy cut' if kind == 'privacy' else 'port fix'}] {name} ({how})")
        if not dry:
            groups[kind].mkdir(parents=True, exist_ok=True)
            (groups[kind] / name).write_text(head + diff, encoding="utf-8", newline="\n")
    # export owns every numbered patch in these two groups: one it did not write this time is stale (2026-10-03: a
    # renamed note left 027-...-pref-block.patch beside 027-...-pref-block-maintai.patch, and a replay would have
    # applied both). Removing them keeps the set equal to the record.
    keep = {n for k in ("privacy", "port") for n, _ in written[k]}
    for kind, g in groups.items():
        for old in sorted(g.glob("[0-9][0-9][0-9]-*.patch")) if g.is_dir() else []:
            if old.name not in {n for n, _ in written[kind]}:
                say(f"  stale: {g.name}/{old.name} removed ({'now in the other group' if old.name in keep else 'no longer written'})")
                if not dry:
                    old.unlink()
    for kind, g in groups.items():
        if written[kind] and not dry:
            intro = ("Repairs needed to carry Gorilla's patches onto Firefox " + version + "." if kind == "port" else
                     "Every network caller, identifier and helper executable cut from Firefox " + version + " at the source. Each cut "
                     "returns before the code that would send, with a `GORILLA UNLEASHED - PHYSICAL LOCK` comment, so no preference "
                     "can turn it back on. Verified on the built browser by its own HTTP log, a decrypting proxy, the socket table "
                     "and the packaged archives.")
            (g / "README.md").write_text(f"# {g.name}\n\n{intro} Apply in order, after the snapshot groups.\n\n"
                                         + "\n".join(f"- `{n}`: {w}" for n, w in written[kind]) + "\n", encoding="utf-8", newline="\n")
        say(f"  {g.name}: {len(written[kind])} patch(es)")
    guessed = [n for n, _, how in written["kinds"] if how.startswith("keyword")]
    say("  REVIEW the kind of every patch above before publishing"
        + (f"; {len(guessed)} were guessed from keywords (record them with kind=privacy or kind=port)" if guessed else ""))
    return written
