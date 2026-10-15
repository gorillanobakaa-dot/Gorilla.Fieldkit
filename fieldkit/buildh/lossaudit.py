# Version: 1.0.0 · updated 26-10-15-11-00
"""How much of the owner's earlier work is in a ported tree: every line every Gorilla patch ever added, counted.

    fieldkit build-harness loss-audit TASK [--out FILE]

Born 2026-10-15. Owner: "the patches were supposed to carry out our tweaks from one version to the other ... what have
we skipped and missed and how much of my original work has been silently discarded?" The shared set had been
regenerated from one Windows tree (gorilla-firefox 1070063) and 83 patch files vanished; nothing measured the loss.

Sources, newest first; for each tree file the NEWEST source that patches it is the reference (a later version of a
patch replaces an earlier one), and lines only the OLDER sources added are counted separately as "earlier only":
  git history of the shared repo's patches/ (the last version of every patch file that ever existed there)
  any extra folders given (patchset-155.0b4, the 155 recreate kit, the 154 masters)

For each file: the lines the reference patch adds (stripped; blank lines, lone braces and comment fences ignored)
and how many of them are in the tree file now. A pref line (pref(, user_pref(, lockPref(, a StaticPrefList name/value)
is listed one by one, because a lost pref is silent at build time and visible only to the person using the browser.
Deterministic: text in, counts out. It does not judge intent: a deliberate removal shows as lost and a person says so.
"""
import json
import re
import subprocess
from pathlib import Path

from .companions import sections

TRIVIAL = re.compile(r"^(\{|\}|\};|\);|\)|\(|/\*|\*/|\*|//|#|<!--|-->|\]|\[|,|;|else|\} else \{)$")
PREF = re.compile(r"""\b(?:pref|user_pref|lockPref|sticky_pref|defaultPref)\(\s*["']([^"']+)["']\s*,\s*(.+?)\s*\)\s*;?""")
YAML_NAME = re.compile(r"^-\s*name:\s*(\S+)")


def _git(repo, *args, text=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    out = r.stdout.decode("utf-8", "replace") if text else r.stdout
    return r.returncode, out


def added_lines(section):
    out = []
    for l in section.splitlines():
        if l.startswith("+") and not l.startswith("+++"):
            s = l[1:].strip()
            if s and not TRIVIAL.match(s):
                out.append(s)
    return out


def from_git(repo, prefix="patches/"):
    """{target file: (label, section text)} from the last version of every patch file ever under `prefix`."""
    _, log = _git(repo, "log", "--format=%H %ad", "--date=short", "--name-status", "--", prefix)
    last = {}                                   # patch path -> commit where its content is last available
    commit = date = None
    for line in log.splitlines():
        if re.match(r"^[0-9a-f]{40} ", line):
            commit, date = line.split()
            continue
        parts = line.split("\t")
        if len(parts) < 2 or not parts[-1].endswith(".patch"):
            continue
        status, path = parts[0], parts[-1]
        if path in last:
            continue                            # log is newest first: the first sighting is the latest
        last[path] = (f"{commit}^" if status.startswith("D") else commit, date, status.startswith("D"))
    out = {}
    for path, (rev, date, deleted) in last.items():
        rc, text = _git(repo, "show", f"{rev}:{path}")
        if rc != 0:
            continue
        label = f"git {path} ({'deleted ' if deleted else ''}{date})"
        for target, sec in sections(text):
            out.setdefault(target, (label, sec))
    return out


def from_folder(folder, label):
    out = {}
    for p in sorted(Path(folder).rglob("*.patch")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for target, sec in sections(text):
            out.setdefault(target, (f"{label} {p.relative_to(folder)}", sec))
    return out


def prefs_in(lines):
    out = []
    for i, l in enumerate(lines):
        m = PREF.search(l)
        if m:
            out.append((m.group(1), l))
        m = YAML_NAME.match(l)
        if m:
            out.append((m.group(1), l))
    return out


def audit(workdir, sources):
    """sources: [(label, {target: (where, section)})], newest first. -> report dict."""
    w = Path(workdir)
    targets = sorted(set().union(*[set(s) for _, s in sources]))
    files = []
    for t in targets:
        ref = next(((lab, s[t]) for lab, s in sources if t in s), None)
        lab, (where, sec) = ref
        want = added_lines(sec)
        older = set()
        for lab2, s in sources:
            if t in s and s[t][1] is not sec:
                older |= set(added_lines(s[t][1]))
        older -= set(want)
        p = w / t
        if not want:
            continue                              # a pure removal: nothing to look for (deletions are judged elsewhere)
        if not p.is_file():
            files.append({"file": t, "source": where, "added": len(want), "kept": 0, "state": "file-absent",
                          "lost_prefs": [l for _, l in prefs_in(want)], "earlier_only": len(older)})
            continue
        have = {x.strip() for x in p.read_text(encoding="utf-8", errors="replace").splitlines()}
        kept = [x for x in want if x in have]
        lost = [x for x in want if x not in have]
        ratio = len(kept) / len(want)
        state = "kept" if ratio >= 0.9 else ("lost" if ratio < 0.1 else "partial")
        files.append({"file": t, "source": where, "added": len(want), "kept": len(kept), "state": state,
                      "lost_prefs": [l for _, l in prefs_in(lost)], "lost_sample": lost[:5],
                      "earlier_only": len([x for x in older if x not in have])})
    tot_add = sum(f["added"] for f in files)
    tot_kept = sum(f["kept"] for f in files)
    by = {}
    for f in files:
        by[f["state"]] = by.get(f["state"], 0) + 1
    return {"files": files, "summary": {"files": len(files), "by_state": by, "lines_added": tot_add,
                                        "lines_in_tree": tot_kept,
                                        "lost_prefs": sum(len(f["lost_prefs"]) for f in files)}}


def group_of(f):
    m = re.search(r"patches/([^/]+)/|^\S+ ([0-9]{2}\.[^/]+)/", f["source"])
    return (m.group(1) or m.group(2)) if m else "?"


def lines(r):
    s = r["summary"]
    out = [f"LOSS-AUDIT: {s['files']} files the Gorilla sets ever added code to; {s['by_state']}; "
           f"{s['lines_in_tree']} of {s['lines_added']} added lines are in the tree "
           f"({100 * s['lines_in_tree'] // max(s['lines_added'], 1)}%); {s['lost_prefs']} pref line(s) missing"]
    for f in sorted(r["files"], key=lambda f: (f["state"] == "kept", group_of(f), f["file"])):
        if f["state"] == "kept":
            continue
        out.append(f"  {f['state']:12} {f['kept']:4}/{f['added']:<4} {group_of(f):30} {f['file']}")
        for p in f["lost_prefs"][:8]:
            out.append(f"               pref missing: {p[:140]}")
    return out
