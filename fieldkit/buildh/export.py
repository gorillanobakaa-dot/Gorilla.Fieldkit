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
    notes, carried = [], []
    for line in (task.STATE / t["id"] / "journal.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "hand-edit":
            notes.append(e)
        elif e.get("event") == "hand-carried":
            carried.append(e)
    roots = _git(w, "rev-list", "--max-parents=0", "HEAD").split()      # the pristine release: never a change
    log = _git(w, "log", "--reverse", "--format=%H\t%ad\t%s", "--date=iso", "HEAD", *[f"^{r}" for r in roots]).splitlines()
    commits = [l.split("\t", 2) for l in log if len(l.split("\t", 2)) == 3 and is_hand_change(l.split("\t", 2)[2])]
    out = []
    for c, when, subj in commits:
        files = _git(w, "show", "--name-only", "--format=", c).split()
        # 2026-10-18: the hand step was also made in the shared set's own patches; exporting it again applied it twice
        by = next((e for e in carried if e.get("commit", "")[:12] == c[:12]), None)
        if by:
            out.append((c, files, None, when, {"carried": list(by.get("by") or []), "why": by.get("why", "")}))
            continue
        if subj.startswith("checkpoint: hand edit"):
            note = next((n for n in notes if set(n.get("files") or []) == set(files)), None)
            why = " / ".join(note.get("why") or []) if note else subj
            if DERIVED.match(why or ""):
                continue
            out.append((c, files, why, when, (note or {}).get("kind")))
        else:
            # committed straight into the tree instead of through `record` (2026-10-15: 23 commits of the Linux 158
            # port, D-158-08, D-158-13, T-158-10-A and the build fixes among them, were in no patch). The subject is
            # the reason; a "Kind: privacy|port" trailer is the recorded kind.
            body = _git(w, "show", "-s", "--format=%b", c)
            m = re.search(r"^Kind:\s*(privacy|port)\s*$", body, re.M | re.I)
            if DERIVED.match(subj):
                continue
            out.append((c, files, subj, when, m.group(1).lower() if m else None))
    return out


#: commit subjects the harness writes for work that is NOT a Gorilla change of its own: a group's patches applied
#: (the patch set already carries them), a checksum recomputed (derived, D-158-02), pictures restored from the set
HARNESS_SUBJECTS = re.compile(r"^(checkpoint: (?!hand edit)|binary-patches: )")
#: recorded steps whose content a harness tool regenerates for every build: never a patch (2026-10-18: the overnight
#: export put lists-refresh's 39,000 lines of security lists into 22.EGRESS.LOCKDOWN, where they go stale)
DERIVED = re.compile(r"^D-157-42: security lists refreshed")


def carry(task_id, commit, by, why, patchset_root):
    """Record that a hand step's change is already in the shared set's patches `by`, so export skips it. Each named
    patch must exist in the set (fail closed)."""
    root = Path(patchset_root)
    missing = [b for b in by if not list(root.glob(f"*/{b}"))]
    if missing:
        raise task.Refused(f"not in the patch set: {missing}")
    task.journal(task.load(task_id), "hand-carried", commit=commit, by=list(by), why=why)


def is_hand_change(subject):
    """True for a commit that is a change of the port's own: a recorded hand edit, or a commit made directly."""
    return not HARNESS_SUBJECTS.match(subject or "")


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
             (r"the owner's", "Gorilla's"), (r"\bthe owner\b", "Gorilla"), (r"owner's", "Gorilla's"), (r"\bowner\b", "Gorilla"),
             # public text is US spelling (owner 2026-10-16)
             (r"\bcolour", "color"), (r"\bColour", "Color"), (r"\bbehaviour", "behavior"), (r"\bfavour", "favor"),
             (r"\b(organi|recogni|minimi|optimi|customi|authori)s(e|ed|es|ing|ation)\b", r"\1z\2")]


def public_note(why):
    """A recorded reason as a public technical note."""
    w = (why or "").strip()
    for a, b in NARRATION:
        w = re.sub(a, b, w, flags=re.I)
    w = re.sub(r"\s{2,}", " ", w).strip(" :")
    return (w[0].upper() + w[1:]) if w else "Hand edit"


def slug(s, n=60):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n] or "hand-edit"


MARK = "# Exported-by: build-harness export-hand "


def _body(text):
    """A patch without its comment header: the change itself."""
    i = text.find("diff --git ")
    return text[i:].strip() if i >= 0 else text.strip()


def is_mine(f, task_id):
    """The patch's header (everything before its first diff) carries this task's export mark (2026-10-18: a header
    listing 90 files put the mark past a fixed 4000-character window)."""
    text = f.read_text(encoding="utf-8", errors="replace")
    i = text.find("diff --git ")
    return f"{MARK}{task_id}\n" in (text[:i] if i >= 0 else text)


def owned(group, task_id):
    """{slug: file name} of the patches this task's export wrote in `group` (its mark in the header)."""
    out = {}
    for f in sorted(group.glob("[0-9][0-9][0-9]-*.patch")) if group.is_dir() else []:
        if is_mine(f, task_id):
            out[f.name[4:-6]] = f.name
    return out


def same_change(group, diff, task_id=None):
    """The patch in `group` that carries exactly this change, or None. A patch this task's export wrote is not a
    duplicate: it is rewritten (its header may have changed)."""
    want = _body(diff)
    mine = set(owned(group, task_id).values()) if task_id else set()
    for f in sorted(group.glob("[0-9][0-9][0-9]-*.patch")) if group.is_dir() else []:
        if f.name not in mine and _body(f.read_text(encoding="utf-8", errors="replace")) == want:
            return f.name
    return None


def next_number(group, written):
    nums = [int(f.name[:3]) for f in group.glob("[0-9][0-9][0-9]-*.patch")] if group.is_dir() else []
    nums += [int(n[:3]) for n, _ in written if n[:3].isdigit()]
    return max(nums or [0]) + 1


KINDS3 = ("privacy", "port", "final")
LABEL = {"privacy": "privacy cut", "port": "port fix", "final": "change on top of the set"}


def written_later(patchset_root, groups, policy=None):
    """{kind: {file: later group}} for the files an enabled group applied AFTER the kind's group writes. A hand step
    is made on the finished tree; one that edits such a file cannot sit in 21/22, because the later group (a
    rebase-set group) rewrites the file from a base that lacks the step (2026-10-18: 034 failed on master-redirect.css,
    which 23.REBASE.158/0164 carries)."""
    root = Path(patchset_root)
    pf = root.parent / "config" / "patch_policy.json"
    pol = policy if policy is not None else (json.loads(pf.read_text(encoding="utf-8")) if pf.is_file() else {})
    order = [g for g, spec in (pol.get("groups") or {}).items() if (spec or {}).get("status") == "enabled"]
    out = {}
    for kind, g in groups.items():
        out[kind] = {}
        if kind == "final" or g.name not in order:
            continue
        for later in order[order.index(g.name) + 1:]:
            if later == groups["final"].name:
                continue
            for f in sorted((root / later).glob("*.patch")) if (root / later).is_dir() else []:
                for path in re.findall(r"^diff --git a/(\S+) b/", f.read_text(encoding="utf-8", errors="replace"), re.M):
                    out[kind].setdefault(path, later)
    return out


def export(task_id, patchset_root, version, say=print, dry=False, policy=None):
    t = task.load(task_id)
    w = Path(t["workdir"])
    groups = {"privacy": Path(patchset_root) / f"22.EGRESS.LOCKDOWN.{version}", "port": Path(patchset_root) / f"21.PORT.FIXES.{version}",
              "final": Path(patchset_root) / f"24.FINAL.{version}"}
    written = {"privacy": [], "port": [], "final": [], "kinds": []}
    deleted = {"privacy": set(), "port": set(), "final": set()}
    later = written_later(patchset_root, groups, policy)
    # every file a patch already in the privacy group touches, whoever wrote it (2026-10-18: 22/032 edits
    # SearchService.sys.mjs; the theme step's lock on top of it went to 21, which applies BEFORE 22, and failed)
    privacy_files = set()
    for f in sorted(groups["privacy"].glob("[0-9][0-9][0-9]-*.patch")) if groups["privacy"].is_dir() else []:
        if not is_mine(f, task_id):
            privacy_files |= set(re.findall(r"^diff --git a/(\S+) b/", f.read_text(encoding="utf-8", errors="replace"), re.M))
    final_files = set()
    for f in sorted(groups["final"].glob("[0-9][0-9][0-9]-*.patch")) if groups["final"].is_dir() else []:
        if not is_mine(f, task_id):
            final_files |= set(re.findall(r"^diff --git a/(\S+) b/", f.read_text(encoding="utf-8", errors="replace"), re.M))
    for i, (c, files, why, when, recorded) in enumerate(hand_commits(t), 1):
        if isinstance(recorded, dict):
            say(f"  carried by the set ({', '.join(recorded['carried'])}): {c[:12]} not written")
            continue
        kind, how = kind_of(why, files, recorded, privacy_files)
        if kind == "privacy":
            privacy_files |= set(files)
        clash = sorted(f for f in files if f in later.get(kind, {}))
        if clash:
            how += f"; MOVED to {groups['final'].name}: {clash[0]} is written afterwards by {later[kind][clash[0]]}"
            kind = "final"
        # a file with a patch in the final group (from an earlier step of this record, or already there) keeps every
        # later edit there: final applies last, so an edit put in 21/22 would come before the one it builds on
        # (2026-10-23, D-158-22: the PdfJsDefaultPrefs.js follow-ups went to 22 after 042 had put the first edit in 24)
        in_final = sorted(f for f in files if f in final_files)
        if kind != "final" and in_final:
            how += f"; MOVED to {groups['final'].name}: {in_final[0]} already has a patch there"
            kind = "final"
        if kind == "final":
            final_files |= set(files)
        # a deleted file goes to the group's DELETED_FILES.manifest.txt, not into the patch: a deletion patch carries
        # the whole old file (2026-10-07: patch 070 was 8.1 MB of removed macOS art; the retired logo copies would
        # have added 10 MB more), and replay and the port apply the manifest after the group's patches anyway
        # only a file still gone at the end of the record: one a later edit creates again keeps its deletion in this
        # patch (replay, 2026-10-07: 045 deletes smartwindow-themes-notice.mjs, 046 re-creates it empty; the manifest,
        # applied after the group's patches, removed it again)
        gone = [p for p in _git(w, "diff", "--name-only", "--no-renames", "--diff-filter=D", f"{c}^", c).splitlines() if p.strip()]
        final = [p for p in gone if not _git(w, "ls-tree", "--name-only", "HEAD", "--", p).strip()]
        again = [p for p in gone if p not in final]
        deleted[kind].update(final)
        diff = _git(w, "diff", "--no-color", "--no-renames", "--binary", "--diff-filter=d", f"{c}^", c)     # logos are binary
        if again:
            diff += _git(w, "diff", "--no-color", "--no-renames", "--binary", f"{c}^", c, "--", *again)
        if not diff.strip():
            continue
        # the public repo's rule: technical notes, not a diary - no dates, no build narration, no local commit ids
        note = public_note(why)
        head = (f"# Gorilla {version} {LABEL[kind]}\n# {note}\n# Files: {', '.join(files)}\n"
                f"{MARK}{task_id}\n\n")
        # 2026-10-18 (owner: "make sure duplicate hunks never happen again"): the same change already in the group,
        # whoever wrote it, is not written a second time
        dup = next(((k, d) for k in (kind,) + tuple(x for x in KINDS3 if x != kind)
                    for d in [same_change(groups[k], diff, task_id)] if d), None)       # in ANY group: one change, one patch
        if dup:
            say(f"  [{kind}] already in {groups[dup[0]].name}/{dup[1]}: not written again")
            written[dup[0]].append((dup[1], note))
            continue
        # a stable name: the number this task's export gave the same note before, else the next free number in the
        # group (2026-10-18: names came from the edit's position in THIS task, so the Linux and the Windows export
        # could both write 001-... into the one shared group and overwrite each other)
        # one number means one patch across BOTH groups (as before 2026-10-18), now continuing after every existing one
        taken = {n for k in KINDS3 for n, _ in written[k]}
        name = owned(groups[kind], task_id).get(slug(note))
        if not name or name in taken:              # two steps with one note get two numbers, never one file
            name = f"{max(next_number(g, written[k]) for k, g in groups.items()):03d}-{slug(note)}.patch"
        written[kind].append((name, note))
        written["kinds"].append((name, kind, how))
        say(f"  [{LABEL[kind]}] {name} ({how})")
        if not dry:
            groups[kind].mkdir(parents=True, exist_ok=True)
            (groups[kind] / name).write_text(head + diff, encoding="utf-8", newline="\n")
    # export owns the patches IT wrote (its mark in the header): one it did not write this time is stale (2026-10-03: a
    # renamed note left 027-...-pref-block.patch beside 027-...-pref-block-maintai.patch, and a replay would have
    # applied both). A patch from the other machine, another task or a hand is never removed (2026-10-18; an
    # overnight edit had kept every tracked patch instead, so a patch moved to the other group stayed in both).
    keep = {n for k in KINDS3 for n, _ in written[k]}
    for kind, g in groups.items():
        mine = set(owned(g, task_id).values())
        for old in sorted(g.glob("[0-9][0-9][0-9]-*.patch")) if g.is_dir() else []:
            if old.name not in {n for n, _ in written[kind]}:
                if old.name not in mine:
                    continue
                say(f"  stale: {g.name}/{old.name} removed ({'now in the other group' if old.name in keep else 'no longer written'})")
                if not dry:
                    old.unlink()
    # export owns ITS lines of the groups' manifests: the lines it wrote last time are replaced by this record's, every
    # other writer's line stays (2026-10-18: rewriting the whole file removed the Linux port's 7 deletions)
    mine_file = task.STATE / task_id / "exported-deletions.json"
    before = json.loads(mine_file.read_text(encoding="utf-8")) if mine_file.is_file() else {}
    for kind, g in groups.items():
        man = g / "DELETED_FILES.manifest.txt"
        have = {l.strip() for l in man.read_text(encoding="utf-8").splitlines() if l.strip()} if man.is_file() else set()
        lines = (have - set(before.get(g.name, []))) | deleted[kind]
        if dry:
            say(f"  {g.name}: manifest would hold {len(lines)} line(s) ({len(deleted[kind])} from this task)")
            continue
        if lines:
            g.mkdir(parents=True, exist_ok=True)
            man.write_text("".join(p + "\n" for p in sorted(lines)), encoding="utf-8", newline="\n")
            say(f"  {g.name}: {len(lines)} deleted file(s) in DELETED_FILES.manifest.txt ({len(deleted[kind])} from this task)")
        elif man.is_file():
            man.unlink()
    if not dry:
        mine_file.parent.mkdir(parents=True, exist_ok=True)
        mine_file.write_text(json.dumps({g.name: sorted(deleted[k]) for k, g in groups.items()}, indent=1), encoding="utf-8")
    for kind, g in groups.items():
        if written[kind] and not dry:
            intro = ("Repairs needed to carry Gorilla's patches onto Firefox " + version + "." if kind == "port" else
                     "Gorilla changes made on the finished " + version + " tree that edit files a later group (rebase-set) "
                     "writes, so they apply last, on top of every other group." if kind == "final" else
                     "Every network caller, identifier and helper executable cut from Firefox " + version + " at the source. Each cut "
                     "returns before the code that would send, with a `GORILLA UNLEASHED - PHYSICAL LOCK` comment, so no preference "
                     "can turn it back on. Verified on the built browser by its own HTTP log, a decrypting proxy, the socket table "
                     "and the packaged archives.")
            man = g / "DELETED_FILES.manifest.txt"
            n_del = sum(1 for l in man.read_text(encoding="utf-8").splitlines() if l.strip()) if man.is_file() else 0
            if n_del:
                intro += (f" Files the edits delete are listed in DELETED_FILES.manifest.txt ({n_del}), "
                          "removed after the patches are applied.")
            # every patch in the group, whoever wrote it (2026-10-18: the README held only this task's patches and
            # dropped 25 lines of the Linux port's); a line not written now keeps its old text, else its header note
            old = {}
            if (g / "README.md").is_file():
                for l in (g / "README.md").read_text(encoding="utf-8").splitlines():
                    m = re.match(r"^- `([^`]+)`: (.*)$", l)
                    if m:
                        old[m.group(1)] = m.group(2)
            mine = dict(written[kind])
            rows = []
            for f in sorted(g.glob("[0-9][0-9][0-9]-*.patch")):
                head = f.read_text(encoding="utf-8", errors="replace").splitlines()[:2]
                rows.append((f.name, mine.get(f.name) or old.get(f.name) or (head[1][2:] if len(head) > 1 else "")))
            (g / "README.md").write_text(f"# {g.name}\n\n{intro} Apply in order, after the snapshot groups.\n\n"
                                         + "\n".join(f"- `{n}`: {w}" for n, w in rows) + "\n", encoding="utf-8", newline="\n")
        say(f"  {g.name}: {len(written[kind])} patch(es)")
    guessed = [n for n, _, how in written["kinds"] if how.startswith("keyword")]
    say("  REVIEW the kind of every patch above before publishing"
        + (f"; {len(guessed)} were guessed from keywords (record them with kind=privacy or kind=port)" if guessed else ""))
    return written
