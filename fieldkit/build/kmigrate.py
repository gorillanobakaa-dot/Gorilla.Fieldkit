"""Kernel patch-set migration, as steps: what carries over to a newer kernel, what a model must port, and the proof.

    fieldkit kernel migrate-check  --project P --old OLD_TREE --new NEW_TREE [--out REPORT]
    fieldkit kernel migrate-apply  --project P --new NEW_TREE
    fieldkit kernel migrate-verify --project P --new NEW_TREE --old-patches DIR [--out REPORT]

Born 2026-10-10, from the 7.1.2 -> 7.2.9 migration of the debian-kernel project, done by hand in shell first. The
project layout is that repository's: patches/series and patches/*.patch (patch -p1 from the source root), the shipped
patched source files at the project root, and PATCHED_FILES_PATH_REGISTRY.txt mapping each to its path in the tree
("name -> path").

migrate-check (before porting) answers, from files only:
  1. baseline: do the project's patches, applied to OLD_TREE's files, rebuild the shipped files byte for byte? If not,
     the old tree is not the pristine the patches were made from (or the set is incomplete): stop.
  2. for each patch, on NEW_TREE with --fuzz=0: applies / applies with offsets / which hunks FAIL;
  3. what upstream changed in each registry file between OLD_TREE and NEW_TREE (+added / -removed lines);
  4. DO: one line per failed patch. Porting a failed hunk is the one step that needs judgement (the model's step):
     place the SAME added and removed lines at the same logical anchor in the new file. Nothing else is guessed.
migrate-apply writes the project's shipped files as the NEW tree's files with the changes: patches that fit are
applied; a failed hunk is placed by its anchors (hunk_aid) only when every change in it has exactly one; otherwise it
writes nothing and names the changes left to port. It never guesses: migrate-verify still has to pass.
migrate-verify (after porting and regenerating the patches) proves:
  1. every patch changes exactly the lines it changed before (the sorted multiset of +/- lines, per patch, against
     --old-patches, a copy of the previous patches/ folder);
  2. the new patches, applied to NEW_TREE's files, rebuild the shipped files byte for byte;
  3. the totals (+added / -removed, patch count).

Both fail closed: a file that cannot be read or a patch that cannot run is a finding. Nothing here changes the
project or either tree; work happens in a temporary folder. Exit 3 on any finding.
"""
import difflib
import re
import shlex
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path


def registry(project):
    """-> [(shipped name, path in the tree)] from PATCHED_FILES_PATH_REGISTRY.txt."""
    out = []
    for line in (Path(project) / "PATCHED_FILES_PATH_REGISTRY.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "->" in line:
            name, dest = (x.strip() for x in line.split("->", 1))
            out.append((name, dest))
    return out


def series(patch_dir):
    return [l.strip() for l in (Path(patch_dir) / "series").read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]


def _patch_exe():
    from ..buildh.firefox import _patch_exe
    return _patch_exe()


def _apply(patch_file, root, dry=False):
    args = [_patch_exe(), "-p1", "--fuzz=0", "--no-backup-if-mismatch", "-d", str(root)] + (["--dry-run"] if dry else [])
    with open(patch_file, "rb") as fh:
        r = subprocess.run(args, stdin=fh, capture_output=True, timeout=600)
    return r.returncode, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")


def changed_lines(patch_text):
    """The sorted multiset of a patch's added and removed lines (headers and context excluded)."""
    out = []
    for l in patch_text.splitlines():
        if (l.startswith("+") and not l.startswith("+++")) or (l.startswith("-") and not l.startswith("---")):
            out.append(l)
    return Counter(out)


def _rebuild(project, tree, patch_dir, tmp):
    """Copy the registry files out of `tree`, apply the patches -> ({name: identical?}, [problems])."""
    tmp = Path(tmp)
    problems, same = [], {}
    regs = registry(project)
    for name, dest in regs:
        src = Path(tree) / dest
        if not src.is_file():
            problems.append(f"{dest} is not in {tree}")
            continue
        (tmp / dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, tmp / dest)
    for p in series(patch_dir):
        code, out = _apply(Path(patch_dir) / p, tmp)
        if code:
            problems.append(f"{p} does not apply: {out.strip()[-200:]}")
    for name, dest in regs:
        shipped = Path(project) / name
        if not shipped.is_file():
            problems.append(f"shipped file {name} is missing")
            continue
        same[name] = (tmp / dest).is_file() and (tmp / dest).read_bytes() == shipped.read_bytes()
    return same, problems


def _diffstat(a, b):
    try:
        x = Path(a).read_text(encoding="utf-8", errors="replace").splitlines()
        y = Path(b).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    plus = minus = 0
    for l in difflib.unified_diff(x, y, lineterm="", n=0):
        if l.startswith("+") and not l.startswith("+++"):
            plus += 1
        elif l.startswith("-") and not l.startswith("---"):
            minus += 1
    return plus, minus


def hunks(patch_text):
    """-> [{"n", "old_start", "lines": [(" "|"+"|"-", text)]}] for a single-file unified diff."""
    out, cur = [], None
    for l in patch_text.splitlines():
        m = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", l)
        if m:
            cur = {"n": len(out) + 1, "old_start": int(m.group(1)), "lines": []}
            out.append(cur)
        elif cur is not None and l[:1] in (" ", "+", "-") and not l.startswith(("+++", "---")):
            cur["lines"].append((l[:1], l[1:]))
    return out


def _groups(hunk):
    """A hunk split into its change groups: each run of +/- lines with the context line just before and after it."""
    lines, out, i = hunk["lines"], [], 0
    while i < len(lines):
        if lines[i][0] == " ":
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j][0] != " ":
            j += 1
        before = next((t for k, t in reversed(lines[:i]) if k == " " and t.strip()), None)
        after = next((t for k, t in lines[j:] if k == " " and t.strip()), None)
        out.append({"removed": [t for k, t in lines[i:j] if k == "-"], "added": [t for k, t in lines[i:j] if k == "+"],
                    "before": before, "after": after})
        i = j
    return out


def hunk_aid(hunk, new_text, window=3):
    """Where each change of a failed hunk goes in the NEW file, so the port is copying, not reasoning (2026-10-10: the
    one step of a migration that needed judgement; a small model is given line numbers instead of a puzzle).
    Per change group: the removed lines and where they are now; else the context line just before it, found once in
    the new file (insert after it), and the next line after it that matches the old context after the change (insert
    before that). The lines to write are given exactly, indentation included.
    -> {"n", "groups": [{"removed": [(text, [lines])], "added": [text], "after_line", "before_line", "how", "window"}]}"""
    new = new_text.splitlines()
    where = {}
    for i, l in enumerate(new, 1):
        where.setdefault(l, []).append(i)
    groups = []
    for g in _groups(hunk):
        rem = [(t, where.get(t, [])) for t in g["removed"]]
        after_line = before_line = None
        if rem and all(len(ls) == 1 for _, ls in rem):
            how = f"replace line(s) {[ls[0] for _, ls in rem]} with the added lines"
            at = rem[0][1][0]
        else:
            cand = where.get(g["before"], []) if g["before"] else []
            if len(cand) == 1:
                after_line = cand[0]
            if g["after"] is not None:
                later = [n for n in where.get(g["after"], []) if after_line is None or n > after_line]
                before_line = later[0] if later else None
            if after_line and before_line and before_line == after_line + 1:
                how = f"insert between line {after_line} and line {before_line}"
            elif after_line and before_line:
                # upstream added lines between the two anchors: the change goes right before the closing anchor, so
                # lines added at the end of a list stay at its end (2026-10-10, alc269.c's enum)
                how = (f"insert right before line {before_line} (upstream added lines {after_line + 1}-{before_line - 1} "
                       f"between the old neighbours; keep them)")
            elif after_line:
                how = f"insert right after line {after_line}"
            elif before_line:
                how = f"insert right before line {before_line}"
            else:
                how = "no anchor of this change is in the new file once: read upstream's change to this file"
            at = after_line or before_line
        win = ([f"{i:>6}| {new[i - 1]}" for i in range(max(1, at - window), min(len(new), (before_line or at) + window) + 1)]
               if at else [])
        groups.append({"removed": rem, "added": g["added"], "after_line": after_line, "before_line": before_line,
                       "how": how, "window": win})
    return {"n": hunk["n"], "groups": groups}


def _apply_groups(new_text, aid):
    """The new file with every change group of `aid` placed by its anchor -> (text, [problems]). Groups are applied from
    the bottom of the file up, so earlier line numbers stay valid."""
    lines = new_text.split("\n")
    ops, problems = [], []
    for a in aid:
        for k, g in enumerate(a["groups"], 1):
            rem = g["removed"]
            if rem and all(len(ls) == 1 for _, ls in rem):
                ns = sorted(ls[0] for _, ls in rem)
                if ns != list(range(ns[0], ns[0] + len(ns))):
                    problems.append(f"hunk {a['n']} change {k}: the removed lines are no longer together")
                    continue
                ops.append((ns[0], len(ns), g["added"]))
            elif rem:
                problems.append(f"hunk {a['n']} change {k}: a removed line is missing or not unique in the new file")
            elif g["before_line"]:
                ops.append((g["before_line"], 0, g["added"]))
            elif g["after_line"]:
                ops.append((g["after_line"] + 1, 0, g["added"]))
            else:
                problems.append(f"hunk {a['n']} change {k}: no anchor")
    for at, n, add in sorted(ops, key=lambda o: o[0], reverse=True):
        lines[at - 1:at - 1 + n] = add
    return "\n".join(lines), problems


def apply(project, new_tree):
    """Write every registry file of the project as NEW tree's file with the project's changes: patches that fit are
    applied with patch --fuzz=0; patches that do not are placed by the porting aid, only when every change of every
    failed hunk has one unambiguous anchor. Refuses (writes nothing) otherwise.
    -> {"ok", "written": [names], "ported": {patch: [how]}, "problems": [...], "next"}"""
    project, new_tree = Path(project), Path(new_tree)
    problems, ported, staged = [], {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name, dest in registry(project):
            if not (new_tree / dest).is_file():
                problems.append(f"{dest} is not in {new_tree}")
                continue
            (tmp / dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(new_tree / dest, tmp / dest)
        for p in series(project / "patches"):
            text = (project / "patches" / p).read_text(encoding="utf-8", errors="replace")
            code, out = _apply(project / "patches" / p, tmp, dry=True)
            if code == 0 and "FAILED" not in out:
                _apply(project / "patches" / p, tmp)
                continue
            m = re.search(r"^\+\+\+ b/(\S+)", text, re.M)
            target = tmp / m.group(1) if m else None
            if not target or not target.is_file():
                problems.append(f"{p}: its file is not in the new tree")
                continue
            new_text = target.read_text(encoding="utf-8", errors="replace")
            aid = [hunk_aid(h, new_text) for h in hunks(text)]
            ported_text, probs = _apply_groups(new_text, aid)
            if probs:
                problems += [f"{p}: {x}" for x in probs]
                continue
            target.write_text(ported_text, encoding="utf-8", newline="")
            ported[p] = [g["how"] for a in aid for g in a["groups"]]
        if not problems:
            for name, dest in registry(project):
                staged[name] = (tmp / dest).read_bytes()
    if problems:
        return {"ok": False, "written": [], "ported": ported, "problems": problems,
                "next": "port the listed changes by hand (fieldkit kernel migrate-check shows where), then "
                        "fieldkit kernel migrate-verify"}
    for name, data in staged.items():
        (project / name).write_bytes(data)
    return {"ok": True, "written": sorted(staged), "ported": ported, "problems": [],
            "next": "regenerate the project's patches against the new tree, then fieldkit kernel migrate-verify"}


def check(project, old_tree, new_tree):
    project, old_tree, new_tree = Path(project), Path(old_tree), Path(new_tree)
    findings, patches = [], []
    with tempfile.TemporaryDirectory() as tmp:
        same, probs = _rebuild(project, old_tree, project / "patches", tmp)
    findings += [f"baseline: {p}" for p in probs]
    differ = sorted(n for n, ok in same.items() if not ok)
    if differ:
        findings.append(f"baseline: the patches on {old_tree} do not rebuild {differ}: the old tree is not the "
                        f"pristine they were made from, or the set is incomplete. Stop here")
    with tempfile.TemporaryDirectory() as tmp:
        for name, dest in registry(project):
            if (new_tree / dest).is_file():
                (Path(tmp) / dest).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(new_tree / dest, Path(tmp) / dest)
        for p in series(project / "patches"):
            code, out = _apply(project / "patches" / p, tmp, dry=True)
            failed = [int(x) for x in re.findall(r"Hunk #(\d+) FAILED", out)]
            offsets = re.findall(r"Hunk #(\d+) succeeded at \d+ \(offset (-?\d+) lines?\)", out)
            missing = "can't find file" in out or "No such file" in out
            status = "fails" if (code or failed or missing) else ("offset" if offsets else "applies")
            aid = []
            if failed:
                text = (project / "patches" / p).read_text(encoding="utf-8", errors="replace")
                m = re.search(r"^\+\+\+ b/(\S+)", text, re.M)
                target = new_tree / m.group(1) if m else None
                if target and target.is_file():
                    new_text = target.read_text(encoding="utf-8", errors="replace")
                    aid = [hunk_aid(h, new_text) for h in hunks(text) if h["n"] in failed]
            patches.append({"patch": p, "status": status, "failed_hunks": failed,
                            "offsets": [(int(h), int(o)) for h, o in offsets], "output": out.strip()[-400:],
                            "aid": aid})
    upstream = {}
    for name, dest in registry(project):
        st = _diffstat(old_tree / dest, new_tree / dest)
        upstream[dest] = None if st is None else {"added": st[0], "removed": st[1]}
        if st is None:
            findings.append(f"upstream: {dest} is missing from one of the trees")
    todo = [f"port {p['patch']}: hunks {p['failed_hunks'] or 'all'} do not fit {new_tree.name}. Put the SAME added and "
            f"removed lines at the same logical anchor in the new file, ship the new file at the project root, "
            f"regenerate the patches, then: fieldkit kernel migrate-verify" for p in patches if p["status"] == "fails"]
    apply_cmd = f"fieldkit kernel migrate-apply --project {shlex.quote(str(project))} --new {shlex.quote(str(new_tree))}"
    # a command, not a sentence: migrate-apply ports what has unambiguous anchors and refuses the rest by name
    nxt = "fix the baseline first" if differ or probs else apply_cmd
    return {"ok": not findings, "baseline_identical": sum(same.values()), "baseline_files": len(same),
            "patches": patches, "upstream": upstream, "todo": todo, "findings": findings, "next": nxt}


def verify(project, new_tree, old_patches):
    project, new_tree, old_patches = Path(project), Path(new_tree), Path(old_patches)
    findings, rows = [], []
    new_series = series(project / "patches")
    old_series = series(old_patches)
    if set(new_series) != set(old_series):
        findings.append(f"the patch list changed: added {sorted(set(new_series) - set(old_series))}, "
                        f"removed {sorted(set(old_series) - set(new_series))}")
    plus = minus = 0
    for p in new_series:
        new = changed_lines((project / "patches" / p).read_text(encoding="utf-8", errors="replace"))
        plus += sum(c for l, c in new.items() if l.startswith("+"))
        minus += sum(c for l, c in new.items() if l.startswith("-"))
        op = old_patches / p
        if not op.is_file():
            rows.append({"patch": p, "same_lines": None})
            continue
        old = changed_lines(op.read_text(encoding="utf-8", errors="replace"))
        ok = new == old
        rows.append({"patch": p, "same_lines": ok, "only_new": sorted((new - old).elements())[:5],
                     "only_old": sorted((old - new).elements())[:5]})
        if not ok:
            findings.append(f"{p} changes different lines than before: new {sorted((new - old).elements())[:3]}, "
                            f"lost {sorted((old - new).elements())[:3]}")
    with tempfile.TemporaryDirectory() as tmp:
        same, probs = _rebuild(project, new_tree, project / "patches", tmp)
    findings += probs
    differ = sorted(n for n, ok in same.items() if not ok)
    if differ:
        findings.append(f"the patches on {new_tree} do not rebuild the shipped {differ}")
    return {"ok": not findings, "patches": rows, "same_lines": sum(1 for r in rows if r["same_lines"]),
            "patch_count": len(new_series), "added": plus, "removed": minus,
            "rebuilt_identical": sum(same.values()), "registry_files": len(same), "findings": findings,
            "next": "the migration is proven; build it: fieldkit pipeline run debian-kernel --var version=NEW"
            if not findings else "fix each finding, then fieldkit kernel migrate-verify again"}


def check_lines(r):
    out = [f"baseline: {r['baseline_identical']}/{r['baseline_files']} shipped files rebuilt byte for byte from the old tree"]
    for p in r["patches"]:
        extra = (f" hunks {p['failed_hunks']} FAILED" if p["failed_hunks"] else "") + \
                (f" offsets {p['offsets']}" if p["offsets"] else "")
        out.append(f"  {p['status']:<8} {p['patch']}{extra}")
        for a in p.get("aid") or []:
            for k, g in enumerate(a["groups"], 1):
                out.append(f"      hunk {a['n']}, change {k}: {g['how']}")
                for t, ls in g["removed"]:
                    out.append(f"        remove (now at line {ls or 'nowhere'}): {t!r}")
                for t in g["added"]:
                    out.append(f"        add exactly: {t!r}")
                out += [f"        {w}" for w in g["window"]]
    out.append("upstream changes in the registry files:")
    for dest, st in r["upstream"].items():
        out.append(f"  {dest}: " + ("missing" if st is None else f"+{st['added']} / -{st['removed']}"))
    out += [f"FINDING: {f}" for f in r["findings"]]
    out += [f"DO: {t}" for t in r["todo"]]
    fit = sum(p["status"] != "fails" for p in r["patches"])
    out.append(f"{fit} of {len(r['patches'])} patches fit the new tree" + ("" if r["ok"] else " (with findings)"))
    out.append(f"NEXT: {r['next']}")
    return out


def verify_lines(r):
    out = [f"  {'same' if x['same_lines'] else 'DIFF' if x['same_lines'] is False else 'NEW '} {x['patch']}"
           for x in r["patches"]]
    out.append(f"same changed lines: {r['same_lines']}/{r['patch_count']}; rebuilt byte for byte: "
               f"{r['rebuilt_identical']}/{r['registry_files']}; {r['patch_count']} patches, +{r['added']} / -{r['removed']}")
    out += [f"FINDING: {f}" for f in r["findings"]]
    out.append("MIGRATION PROVEN" if r["ok"] else "MIGRATION NOT PROVEN")
    out.append(f"NEXT: {r['next']}")
    return out
