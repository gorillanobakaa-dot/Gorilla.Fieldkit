"""firefox - bring the Gorilla Firefox patch set forward to the latest stable Firefox.

Steps (script = done by this code, model = one small job for the model, owner = waits for you):

  resolve      script  latest stable version and its release tag (upstream.py; never main/Nightly)
  vault        script  the untouched source in the vault (vault.py)
  workcopy     script  a fresh working copy made from the vault
  apply-<group>        script  every patch of the group, in order, with GNU patch; each hunk that
                               does not apply becomes a port-* model step (or an owner step)
  port-<group>-<n>     model   put ONE failed hunk's change into the file where that code now lives
  export-<group>       script  the group's new patch = git diff of what the group changed
  final-checks script  no .rej/.orig left, no conflict markers, no whitespace damage

The compile, package and publish come from the existing Gorilla Firefox harness and run
as separate owner-timed steps (hours at the thermal cap).

Settings (fieldkit.local.json): firefox.root = the Gorilla.firefox folder (patch_policy.json
and the patch set live there). The working copy is <vault root>/../work/firefox/<version>
unless the task names another.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

from ..core import settings
from . import upstream, vault

MAX_WINDOW = 160                                   # lines of the target file one packet may show
TRIVIAL = re.compile(r"^[\s{}()\[\];,]*$")        # braces and punctuation prove nothing


# -- patch files ---------------------------------------------------------------------------

def parse_patch(text):
    """-> [{"file": path, "hunks": [{"header": "@@ ..", "lines": [...]}]}] for a unified diff."""
    files, cur, hunk = [], None, None
    for line in text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].split("\t")[0].strip()
            path = path[2:] if path.startswith(("a/", "b/")) else path
            cur = {"file": path, "hunks": []}
            files.append(cur)
            hunk = None
        elif line.startswith("--- ") and (hunk is None or not hunk["lines"] or cur is None):
            continue
        elif line.startswith("@@") and cur is not None:
            hunk = {"header": line, "lines": []}
            cur["hunks"].append(hunk)
        elif hunk is not None and line[:1] in (" ", "+", "-", "\\"):
            hunk["lines"].append(line)
    return files


def hunk_sides(h):
    removed = [l[1:] for l in h["lines"] if l.startswith("-")]
    added = [l[1:] for l in h["lines"] if l.startswith("+")]
    context = [l[1:] for l in h["lines"] if l.startswith(" ")]
    return removed, added, context


_FAILED = re.compile(r"^Hunk #(\d+) FAILED at")
_PATCHING = re.compile(r"^(?:patching|checking) file '?(.+?)'?$")
_MISSING = re.compile(r"can't find file to patch|No file to patch")


def failures_from_output(out):
    """GNU patch output -> {"failed": [(file, hunk_no)], "missing": bool}."""
    failed, cur = [], None
    for line in out.splitlines():
        m = _PATCHING.match(line.strip())
        if m:
            cur = m.group(1)
            continue
        m = _FAILED.match(line.strip())
        if m and cur:
            failed.append((cur, int(m.group(1))))
    return {"failed": failed, "missing": bool(_MISSING.search(out))}


def _patch_exe():
    return shutil.which("patch") or r"C:\Program Files\Git\usr\bin\patch.exe"


# -- the steps -------------------------------------------------------------------------------

def _policy(root):
    pol = json.loads((Path(root) / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    return Path(root) / pol["patchset_root"], pol["groups"]


def step_resolve(t, **kw):
    info = t["meta"].get("pinned") or upstream.latest_firefox()
    t["meta"]["upstream"] = info
    return {"ok": True, "summary": f"Firefox {info['version']} ({info['tag']}, {info['commit'][:12]})"}


def step_vault(t, vault_base=None, **kw):
    info = t["meta"]["upstream"]
    out = vault.fetch_firefox(info, base=vault_base)
    check = vault.verify("firefox", info["version"], base=vault_base)
    return {"ok": check["intact"], "why": check["problems"], "summary": f"{out['status']}: {out['vault']}"}


def step_workcopy(t, vault_base=None, **kw):
    w = Path(t["workdir"])
    if (w / ".git").exists():
        return {"ok": True, "summary": f"working copy already at {w}"}
    out = vault.restore("firefox", w, t["meta"]["upstream"]["version"], base=vault_base)
    return {"ok": True, "summary": f"fresh copy of {out['version']} from the vault"}


def plan_groups(t, harness_root, **kw):
    """Adds apply/export steps for every enabled group, in the policy's order."""
    pset, groups = _policy(harness_root)
    steps = []
    for g, spec in groups.items():
        if spec.get("status") != "enabled":
            continue
        args = {"harness_root": str(harness_root), "group": g}
        steps.append({"id": f"apply-{g}", "kind": "script", "title": f"apply group {g}",
                      "run": "fieldkit.buildh.firefox:step_apply_group", "args": args})
        if (pset / g / "NEW_FILES").is_dir():
            steps.append({"id": f"new-files-{g}", "kind": "script", "title": f"copy the new files of {g}",
                          "run": "fieldkit.buildh.firefox:step_new_files", "args": args})
        if (pset / g / "REPLACE_FILES").is_dir():
            steps.append({"id": f"replace-files-{g}", "kind": "script", "title": f"replace the binary files of {g}",
                          "run": "fieldkit.buildh.firefox:step_replace_files", "args": args})
        if (pset / g / "DELETED_FILES.manifest.txt").is_file():
            steps.append({"id": f"delete-files-{g}", "kind": "script", "title": f"delete the files {g} removes",
                          "run": "fieldkit.buildh.firefox:step_delete_files", "args": args})
        steps.append({"id": f"export-{g}", "kind": "script", "title": f"export the {g} patch",
                      "run": "fieldkit.buildh.firefox:step_export_group", "args": args})
    steps.append({"id": "final-checks", "kind": "script", "title": "no rejects, no conflict markers",
                  "run": "fieldkit.buildh.firefox:step_final_checks"})
    return {"ok": True, "add_steps": steps, "summary": f"{len(steps) // 2} enabled groups"}


NOT_SOURCE = {"user.js", "mozconfig"}      # profile / build-config files the patch set carries; they are not source


def new_files(pset, group):
    """-> [(source Path, relative destination)] for a group's NEW_FILES tree, minus files that are not source."""
    root = Path(pset) / group / "NEW_FILES"
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if p.name in NOT_SOURCE and "/" not in rel:
                continue
            out.append((p, rel))
    return out


def step_new_files(t, harness_root, group, **kw):
    """Copy the group's whole new files (branding, icons, installer assets) into the tree.

    The independent audit of 2026-10-01 found the harness never copied them: 69 of the 81 08.Look files were
    missing from every working copy. A file that already exists in pristine Firefox at the same path is never
    overwritten here (upstream now ships something there): that is an owner decision."""
    pset, _ = _policy(harness_root)
    w = Path(t["workdir"])
    root = subprocess.run(["git", "-C", str(w), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.split()
    pristine = set(subprocess.run(["git", "-C", str(w), "ls-tree", "-r", "--name-only", root[0]], capture_output=True,
                                  text=True, errors="replace").stdout.splitlines()) if root else set()
    copied, same, conflicts, skipped = [], [], [], []
    for src, rel in new_files(pset, group):
        dest = w / rel
        data = src.read_bytes()
        if rel in pristine:
            if dest.is_file() and dest.read_bytes() == data:
                same.append(rel)
            else:
                conflicts.append(rel)
            continue
        if dest.is_file() and dest.read_bytes() == data:
            same.append(rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        copied.append(rel)
    skipped = [p.name for p in (Path(pset) / group / "NEW_FILES").iterdir() if p.is_file() and p.name in NOT_SOURCE]
    steps = [{"id": f"owner-{group}-new-file-{Path(c).name}", "kind": "owner",
              "title": f"{group}/NEW_FILES/{c} also exists in this Firefox; decide which one wins"} for c in conflicts]
    return {"ok": True, "add_steps": steps, "copied": copied, "already": same, "conflicts": conflicts, "not_source": skipped,
            "summary": f"{group}: {len(copied)} new file(s) copied, {len(same)} already there, {len(conflicts)} conflict(s) "
                       f"for the owner, {len(skipped)} not source ({', '.join(skipped) or '-'})"}


def manifest_deletions(harness_root):
    """Every path an enabled group's DELETED_FILES.manifest.txt removes."""
    pset, groups = _policy(harness_root)
    out = set()
    for g, spec in groups.items():
        m = pset / g / "DELETED_FILES.manifest.txt"
        if spec.get("status") == "enabled" and m.is_file():
            out.update(l.strip() for l in m.read_text(encoding="utf-8").splitlines() if l.strip())
    return out


def step_replace_files(t, harness_root, group, **kw):
    """Binary files the build modified (a snapshot's REPLACE_FILES): written byte-exact over the upstream file."""
    pset, _ = _policy(harness_root)
    w = Path(t["workdir"])
    root = pset / group / "REPLACE_FILES"
    done, created = [], []
    for p in sorted(root.rglob("*")) if root.is_dir() else []:
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            d = w / rel
            (created if not d.is_file() else done).append(rel)
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_bytes(p.read_bytes())
    return {"ok": True, "replaced": done, "created": created,
            "summary": f"{group}: {len(done)} file(s) replaced" + (f", {len(created)} did not exist upstream and were created" if created else "")}


def step_delete_files(t, harness_root, group, **kw):
    """Files the fork removes (the AI excision lives here). Already-gone files are reported, never an error."""
    pset, _ = _policy(harness_root)
    w = Path(t["workdir"])
    man = pset / group / "DELETED_FILES.manifest.txt"
    removed, already = [], []
    for rel in man.read_text(encoding="utf-8").splitlines():
        rel = rel.strip()
        if not rel:
            continue
        p = w / rel
        if p.is_file():
            p.unlink()
            removed.append(rel)
        else:
            already.append(rel)
    return {"ok": True, "removed": removed, "already_gone": already,
            "summary": f"{group}: {len(removed)} file(s) deleted, {len(already)} already gone upstream"}


def step_apply_group(t, harness_root, group, **kw):
    pset, groups = _policy(harness_root)
    excluded = set(groups[group].get("exclude", []))
    w = Path(t["workdir"])
    base = subprocess.run(["git", "-C", str(w), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    t["meta"].setdefault("group_base", {})[group] = base
    new_steps, applied, skipped, upstreamed = [], 0, [], []
    for pf in sorted((pset / group).rglob("*.patch")):
        if pf.name in excluded:
            skipped.append(pf.name)
            continue
        with open(pf, "rb") as fh:
            r = subprocess.run([_patch_exe(), "-p1", "--forward", "--no-backup-if-mismatch", "--fuzz=3",
                                "-d", str(w)], stdin=fh, capture_output=True, timeout=600)
        out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
        res = failures_from_output(out)
        rel = pf.relative_to(pset).as_posix()
        if "previously applied" in out:              # GNU patch: this Firefox already has the change
            upstreamed.append(f"{rel} (the whole patch is already in this Firefox)")
            continue
        if r.returncode == 0:
            applied += 1
        parsed = {f["file"]: f["hunks"] for f in parse_patch(pf.read_text(encoding="utf-8", errors="replace"))}
        for fname, n in res["failed"]:
            hunks = parsed.get(fname) or []
            if n - 1 >= len(hunks):
                continue
            tf = w / fname
            if tf.is_file() and already_upstream(tf.read_text(encoding="utf-8", errors="replace").splitlines(),
                                                 hunks[n - 1]):
                upstreamed.append(f"{rel} {fname} #{n}")
                continue
            new_steps.append({"id": f"port-{group}-{pf.stem}-{Path(fname).name}-h{n}", "kind": "model",
                              "title": f"port hunk #{n} of {rel} into {fname}",
                              "packet": "fieldkit.buildh.firefox:packet_port",
                              "check": "fieldkit.buildh.firefox:check_port", "auto": "fieldkit.buildh.firefox:auto_port",
                              "allowed": [fname],
                              "args": {"patch": rel, "file": fname, "hunk": hunks[n - 1]}})
        if res["missing"]:
            new_steps.append({"id": f"owner-{group}-{pf.stem}", "kind": "owner",
                              "title": f"{rel} patches a file that no longer exists in this Firefox; "
                                       f"decide: drop the patch, or point it at the file's new home"})
    for rej in leftovers(w):
        rej.unlink()
    # the export step for this group comes after its port steps
    return {"ok": True, "add_steps": new_steps,
            "upstreamed": upstreamed,
            "summary": f"{group}: {applied} patch(es) applied clean, {len(new_steps)} hunk job(s), "
                       f"{len(upstreamed)} hunk(s) already upstream, {len(skipped)} excluded"}


def leftovers(w):
    """*.rej / *.orig files that `patch` left behind: UNTRACKED ones only.

    Firefox itself tracks hundreds of vendored `third_party/rust/*/Cargo.toml.orig` files. The first draft of this
    harness deleted every *.orig it found, which silently removed 560 upstream files from the source (found by an
    independent audit on 2026-10-01, in both the overnight copy and the clean copy). Only what is not in git is ours."""
    w = Path(w)
    raw = subprocess.run(["git", "-C", str(w), "ls-files", "-z", "--", "*.rej", "*.orig"], capture_output=True).stdout
    tracked = {p for p in raw.decode("utf-8", "replace").split("\0") if p}
    found = []
    for p in list(w.rglob("*.rej")) + list(w.rglob("*.orig")):
        rel = p.relative_to(w).as_posix()
        if rel.startswith(".git/") or rel in tracked:
            continue
        found.append(p)
    return found


_DELETES = re.compile(r"^--- a/(.+)\n\+\+\+ /dev/null", re.M)


def unexplained_deletions(t):
    """Files that exist in pristine Firefox but are gone from the working copy, and that no Gorilla patch deletes.
    -> list of paths. (The 560 vendored .orig files removed by the first draft would have been caught here.)"""
    w = Path(t["workdir"])
    root = subprocess.run(["git", "-C", str(w), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.split()
    if not root:
        return []
    gone = subprocess.run(["git", "-C", str(w), "diff", "--name-only", "--diff-filter=D", "-z", root[0], "HEAD"],
                          capture_output=True).stdout.decode("utf-8", "replace").split("\0")
    gone = [p for p in gone if p]
    meant = set()
    hr = t.get("meta", {}).get("harness_root")
    if hr and (Path(hr) / "config" / "patch_policy.json").is_file():
        pset, groups = _policy(hr)
        for g, spec in groups.items():
            if spec.get("status") != "enabled":
                continue
            for pf in (pset / g).rglob("*.patch"):
                meant.update(_DELETES.findall(pf.read_text(encoding="utf-8", errors="replace")))
        meant |= manifest_deletions(hr)
    return [p for p in gone if p not in meant]


def restore_deleted(task_id):
    """Put back pristine files that vanished without a patch asking for it, as one harness checkpoint.
    -> how many. Only ever restores files from the pristine base commit; never touches anything else."""
    from . import task as taskmod
    t = taskmod.load(task_id)
    lost = unexplained_deletions(t)
    if not lost:
        return 0
    w = Path(t["workdir"])
    root = subprocess.run(["git", "-C", str(w), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.split()[0]
    for i in range(0, len(lost), 100):
        subprocess.run(["git", "-C", str(w), "checkout", root, "--", *lost[i:i + 100]], check=True, capture_output=True)
    taskmod.checkpoint(t, f"restored {len(lost)} pristine file(s) the first harness draft had deleted")
    taskmod.journal(t, "repair", restored=len(lost), first=lost[:3])
    taskmod.save(t)
    return len(lost)


def step_export_group(t, harness_root, group, out_dir=None, **kw):
    w = Path(t["workdir"])
    base = t["meta"]["group_base"][group]
    diff = subprocess.run(["git", "-C", str(w), "diff", "--binary", base, "HEAD"], capture_output=True).stdout
    out = Path(out_dir or (Path(t["workdir"]).parent / f"patchset-{t['meta']['upstream']['version']}")) / f"{group}.patch"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(diff)
    return {"ok": True, "summary": f"{out.name}: {len(diff):,} bytes"}


def step_final_checks(t, **kw):
    w = Path(t["workdir"])
    why = [f"leftover {p.relative_to(w)}" for p in leftovers(w)[:10]]
    first = t["checkpoints"][0]["commit"] if t["checkpoints"] else "HEAD"
    r = subprocess.run(["git", "-C", str(w), "diff", "--check", first, "HEAD"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    why += [l for l in r.stdout.splitlines() if "conflict marker" in l][:5]
    lost = unexplained_deletions(t)
    if lost:
        why.append(f"{len(lost)} file(s) of the pristine source are gone and no patch deletes them, e.g. {lost[0]}")
    # regression: every hunk a model ported must still be in place at the end
    from . import task as taskmod
    for s in t["steps"]:
        if s["kind"] != "model" or s["status"] != "done" or s.get("skipped_by_owner"):
            continue
        a = s["args"]
        target = w / a["file"]
        now = target.read_text(encoding="utf-8", errors="replace").splitlines() if target.is_file() else []
        base_commit = next((c["commit"] for c in t["checkpoints"] if c["label"] == s["id"]), None)
        before = subprocess.run(["git", "-C", str(w), "show", f"{base_commit}~1:{a['file']}"], capture_output=True
                                ).stdout.decode("utf-8", "replace").splitlines() if base_commit else []
        lost = hunk_problems(before, now, a["hunk"]) if before else []
        if lost:
            why.append(f"regression: {s['id']} no longer holds: {lost[0]}")
            taskmod.journal(t, "regression", step=s["id"], why=lost[0])
    return {"ok": not why, "why": why, "summary": "clean" if not why else f"{len(why)} problem(s)"}


# -- the model's job: port one hunk ----------------------------------------------------------

def _anchor(lines, context_and_removed, min_run=3):
    """Where the hunk's old code lives now: the best run of matching lines, by content."""
    want = [l.strip() for l in context_and_removed if l.strip() and not TRIVIAL.match(l)]
    if not want:
        return None
    stripped = [l.strip() for l in lines]
    window = len(want) + 5
    where = {}
    for k in set(want):
        for i, l in enumerate(stripped):
            if l == k:
                where.setdefault(k, []).append(i)
    starts = sorted({i for hits in where.values() for i in hits})
    best, best_i = 0, None
    for s in starts:                                    # the place where most of the hunk's lines sit together
        score = sum(1 for k in set(want) if any(s <= i < s + window for i in where.get(k, [])))
        if score > best:
            best, best_i = score, s
    if best_i is not None and best >= min(min_run, len(set(want))):
        return best_i
    # upstream edited the lines themselves: take the file line most like one of them
    import difflib
    near_r, near_i = 0.75, None
    for w in want:
        for i, l in enumerate(stripped):
            if l and abs(len(l) - len(w)) <= max(len(w), 20):
                r = difflib.SequenceMatcher(None, w, l).ratio()
                if r > near_r:
                    near_r, near_i = r, i
    return near_i if near_i is not None else best_i


def packet_port(t, s, budget_chars, patch, file, hunk, answer_mode=False, **kw):
    removed, added, context = hunk_sides(hunk)
    target = Path(t["workdir"]) / file
    parts = [f"PATCH: {patch}", f"FILE:  {file}", "",
             "The change this hunk makes (lines starting '-' are removed, '+' are added, ' ' are context):",
             hunk["header"], *hunk["lines"], ""]
    if not target.is_file():
        parts.append(f"{file} does not exist in this Firefox. You cannot port this hunk: submit without "
                     "changing anything and the step goes to the owner.")
        return "\n".join(parts)
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    at = _anchor(lines, [l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")])
    # The budget is a ceiling, not a target (2026-09-30: the first live packet filled it and
    # showed lines 1-1425, ~20k tokens, for a change near line 260). Show the hunk's own length
    # plus 30 lines either side, never more than MAX_WINDOW lines.
    fits = max(20, (budget_chars - len("\n".join(parts)) - 800) // 90)          # ~90 chars per numbered line
    room = min(fits, len(hunk["lines"]) + 60, MAX_WINDOW)
    if at is None:
        parts.append(f"The old code was not found in {file} (upstream rewrote it). The file has {len(lines)} "
                     f"lines. Use your find tool on a distinctive line of the hunk to locate the new code; "
                     f"the first {room} lines are shown only for orientation.")
        lo, hi = 0, min(len(lines), room)
    else:
        lo, hi = max(0, at - 30), min(len(lines), max(0, at - 30) + room)
        parts.append(f"In this Firefox the same code starts near line {at + 1}. Lines {lo + 1}-{hi}:")
    parts += [f"{i + 1:6}| {lines[i]}" for i in range(lo, hi)]
    if answer_mode:
        # Run 6 countermeasure: for removal-only hunks with uncertain upstream lines,
        # use REMOVE/KEEP questions instead of line operations.
        if at is not None:
            auto_rm, uncertain = identify_questions(lines, hunk, at)
            if uncertain and (not added or insertion_after(lines, hunk, max(0, at - 5),
                                                            min(len(lines) - 1, at + len(hunk["lines"]) + 30)) is not None):
                from .answer import Q_INSTRUCTIONS
                parts.append(f"The harness will auto-remove {len(auto_rm)} line(s) that match the patch"
                             + (f" and insert the {len(added)} new line(s) itself." if added else "."))
                parts.append(f"These {len(uncertain)} line(s) are new upstream text not in the original patch.")
                parts.append("For each one, answer REMOVE (delete it) or KEEP (leave it):")
                parts.append("")
                for n in uncertain:
                    parts.append(f"  {n} | {lines[n - 1]}")
                parts += ["", Q_INSTRUCTIONS]
                return "\n".join(parts)
        # Fallback: line operations for hunks with additions or no uncertain lines
        from .answer import INSTRUCTIONS
        parts += ["", "DO: make this change in " + file + " with line operations: the '-' lines (or the lines "
                      "that now hold the same settings) go, the '+' lines are added in the matching place. "
                      "Touch nothing else.", "", INSTRUCTIONS]
    else:
        parts += ["", "DO: edit " + file + " so that the '+' lines are present and the '-' lines are gone, in the "
                                         "matching place. Change nothing else. Keep the file's own style."]
    return "\n".join(parts)


def hunk_problems(before, after, hunk):
    """What is still wrong in `after` (lines) for the hunk's change, counted against `before`.

    For every meaningful line the hunk adds, the file must hold as many copies as before plus
    those added minus those removed; for every line it removes, the count must drop the same way.
    Braces and punctuation-only lines prove nothing and are ignored."""
    removed, added, _ = hunk_sides(hunk)
    b = [l.strip() for l in before]
    a = [l.strip() for l in after]
    why = []
    for k in dict.fromkeys(x.strip() for x in added + removed):
        if not k or TRIVIAL.match(k):
            continue
        net = sum(1 for x in added if x.strip() == k) - sum(1 for x in removed if x.strip() == k)
        want = max(0, b.count(k) + net)
        if net > 0 and a.count(k) < want:
            why.append(f"missing added line: {k[:100]}")
        elif net < 0 and a.count(k) > want:
            why.append(f"line should be gone: {k[:100]}")
    return why


def collateral(before, after, hunk, extra_removals=None):
    """Everything the model changed that the hunk does not: the part the first check never saw.

    Live run 4 (2026-09-30): Gemma's answer passed hunk_problems, yet it also deleted five lines
    Mozilla added in 155.0.1, put back the old 154 value of browser.touchmode.auto (a line it
    had only seen as CONTEXT in the hunk), and duplicated another line. So: every line the
    model removed must be one of the hunk's '-' lines, and every line it added one of its '+'
    lines. Blank lines are allowed to move.

    `extra_removals`, when given, is a list of raw line texts explicitly approved for removal
    (from question-mode answers); they are added to the allowed-removal set."""
    import collections
    import difflib
    removed, added, _ = hunk_sides(hunk)
    # A line counts as the hunk's line when its CODE matches, ignoring a trailing // comment:
    # upstream may have edited the very line the patch changes (added a comment), and removing
    # that edited version is the port, not damage.
    may_remove = collections.Counter(_key(l) for l in removed)
    if extra_removals:
        may_remove.update(_key(l) for l in extra_removals)
    may_add = collections.Counter(_key(l) for l in added)
    gone, new = collections.Counter(), collections.Counter()
    sm = difflib.SequenceMatcher(None, [l.strip() for l in before], [l.strip() for l in after], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("delete", "replace"):
            gone.update(l.strip() for l in before[i1:i2] if l.strip())
        if op in ("insert", "replace"):
            new.update(l.strip() for l in after[j1:j2] if l.strip())
    moved = gone & new                                          # the same line taken out and put back
    gone, new = gone - moved, new - moved
    gone_code, new_code, text = collections.Counter(), collections.Counter(), {}
    for k, n in gone.items():
        gone_code[_key(k)] += n
        text.setdefault(("-", _key(k)), k)
    for k, n in new.items():
        new_code[_key(k)] += n
        text.setdefault(("+", _key(k)), k)
    why = [f"you removed a line that is not part of the change: {text[('-', k)][:100]}" for k in (gone_code - may_remove)]
    why += [f"you added a line that is not part of the change: {text[('+', k)][:100]}" for k in (new_code - may_add)]
    return why


def _code(line):
    """The line without a trailing // comment (a // inside a string, e.g. https://, is kept)."""
    s, quote = line.strip(), None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote and s[i - 1] != "\\":
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif s.startswith("//", i) and i > 0:
            return s[:i].rstrip()
    return s


_PREF = re.compile(r'\s*(pref|sticky_pref|lockPref|user_pref|defaultPref)\s*\(\s*"([^"]+)"')


def _key(line):
    """What makes two lines 'the same line': for a Firefox setting its function and name (upstream
    may change the VALUE; live run 5 hunk #4: customIcon.enabled went false -> true in 155.0.1),
    otherwise the code without a trailing // comment."""
    m = _PREF.match(line)
    return f"{m.group(1)}:{m.group(2)}" if m else _code(line)


# ── question form (run 6 countermeasure) ─────────────────────────────────────
# Gemma cannot compose line operations. So the harness identifies which lines
# in the target file match the hunk's '-' lines (auto-removes), which are new
# upstream text (uncertain), and asks the model only about the uncertain ones.

def identify_questions(lines, hunk, anchor):
    """Which target-file lines to auto-remove and which to ask about.

    `lines`: the target file's content (list of str, 0-indexed).
    `hunk`: the hunk dict with 'header' and 'lines'.
    `anchor`: 0-indexed line number where the hunk's context begins in the target.

    Returns (auto_remove, uncertain) where each is a list of 1-indexed line numbers.
    auto_remove: lines whose _key matches a hunk '-' line.
    uncertain: lines between the first and last context/removed line that are NOT
               in the hunk at all (new upstream content the model must classify).
    """
    removed, added, context = hunk_sides(hunk)
    removed_keys = {_key(l) for l in removed if l.strip()}
    context_keys = {_key(l) for l in context if l.strip()}
    added_keys = {_key(l) for l in added if l.strip()}
    all_hunk_keys = removed_keys | context_keys | added_keys

    # Find the span in the target file that corresponds to this hunk:
    # walk from the anchor forward, matching context and removed lines.
    ctx_and_rm = [l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")]
    # Find first and last match to bound the region
    first_match = None
    last_match = None
    for i in range(max(0, anchor - 5), min(len(lines), anchor + len(ctx_and_rm) + 30)):
        k = _key(lines[i])
        if k in removed_keys or k in context_keys:
            if first_match is None:
                first_match = i
            last_match = i

    if first_match is None:
        return [], []

    auto_remove = []
    uncertain = []
    for i in range(first_match, last_match + 1):
        k = _key(lines[i])
        if not k:
            continue
        if k in removed_keys:
            auto_remove.append(i + 1)  # 1-indexed
        elif k not in context_keys and k not in added_keys:
            # New upstream line not in the hunk at all — ask the model
            uncertain.append(i + 1)  # 1-indexed

    return auto_remove, uncertain


def insertion_after(lines, hunk, lo, hi):
    """Where the hunk's '+' block goes: the index of the file line after which to insert, or None.

    The '+' block follows some ' ' (context) line in the hunk, possibly with '-' lines in between that are gone
    from the file. That context line is looked up by key inside the span [lo, hi]. If the '+' block opens the
    hunk, the first context line after it is used and the block goes before it. Ambiguous or absent -> None."""
    hl = hunk["lines"]
    first_plus = next((i for i, l in enumerate(hl) if l.startswith("+")), None)
    if first_plus is None:
        return None

    def find(key):
        hits = [i for i in range(lo, hi + 1) if _key(lines[i]) == key]
        return hits[0] if len(hits) == 1 else None
    for i in range(first_plus - 1, -1, -1):
        if hl[i].startswith(" ") and hl[i][1:].strip():
            at = find(_key(hl[i][1:]))
            return at                                   # after this line (None when not found once)
        if not hl[i].startswith("-"):
            break
    for i in range(first_plus, len(hl)):
        if hl[i].startswith(" ") and hl[i][1:].strip():
            at = find(_key(hl[i][1:]))
            return at - 1 if at is not None else None   # before this line
    return None


def auto_merge(body, hunk, notes=None):
    """Tier 3 of the harness's own attempt: remove every file line whose key matches a '-' line, insert the '+'
    block after its context line. Refuses (Ambiguous) when the span holds lines it cannot account for: those are
    the REMOVE/KEEP questions for the model. Live run 8 (2026-10-01, firefox.js h32): upstream had collapsed a
    5-line #ifdef block into one line; transplant and substitute both gave up, Gemma failed 3 times with line
    operations, and this does it with no model at all."""
    at = _anchor(body, [l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")])
    if at is None:
        raise Ambiguous("anchor not found")
    auto_rm, uncertain = identify_questions(body, hunk, at)
    if uncertain:
        raise Ambiguous(f"{len(uncertain)} upstream line(s) in the span need a decision")
    removed, added, _ = hunk_sides(hunk)
    if not auto_rm and any(l.strip() for l in removed):
        raise Ambiguous("none of the removed lines are in the file")
    lo = max(0, at - 5)
    hi = min(len(body) - 1, at + len(hunk["lines"]) + 30)
    ins = insertion_after(body, hunk, lo, hi) if added else None
    if added and ins is None:
        raise Ambiguous("the place for the added lines could not be fixed on one context line")
    new = list(body)
    for n in sorted(auto_rm, reverse=True):
        del new[n - 1]
        if ins is not None and n - 1 <= ins:
            ins -= 1
    if added:
        new[ins + 1:ins + 1] = added
    if notes is not None:
        notes.append(f"merged by key: removed {len(auto_rm)} line(s), inserted {len(added)}")
    return new


def packet_port_questions(t, s, budget_chars, patch, file, hunk, **kw):
    """Build the REMOVE/KEEP question packet for a hunk port."""
    from .answer import Q_INSTRUCTIONS
    removed, added, context = hunk_sides(hunk)
    target = Path(t["workdir"]) / file
    parts = [f"PATCH: {patch}", f"FILE:  {file}", "",
             "The change this hunk makes (lines starting '-' are removed, '+' are added, ' ' are context):",
             hunk["header"], *hunk["lines"], ""]
    if not target.is_file():
        parts.append(f"{file} does not exist. Submit without changing anything.")
        return "\n".join(parts)
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    at = _anchor(lines, [l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")])
    if at is None:
        parts.append(f"The old code was not found in {file}. Submit without changing anything.")
        return "\n".join(parts)

    auto_remove, uncertain = identify_questions(lines, hunk, at)

    if not uncertain:
        # Nothing to ask — all lines matched the hunk's '-' lines or context.
        # The harness can do this itself.
        parts.append("All lines are accounted for. No questions needed.")
        return "\n".join(parts)

    parts.append(f"The harness will auto-remove {len(auto_remove)} line(s) that match the patch.")
    parts.append(f"These {len(uncertain)} line(s) are new upstream text not in the original patch.")
    parts.append("For each one, answer REMOVE (delete it) or KEEP (leave it):")
    parts.append("")
    for n in uncertain:
        parts.append(f"  {n} | {lines[n - 1]}")
    parts += ["", Q_INSTRUCTIONS]
    return "\n".join(parts)


def apply_question_answers(target, decisions, hunk, auto_removes, lines=None):
    """Apply REMOVE/KEEP decisions + auto-removes to the target file.

    `target`: Path to the file.
    `decisions`: [(line_no, 'remove'|'keep'), ...] from parse_questions.
    `hunk`: the hunk dict.
    `auto_removes`: list of 1-indexed line numbers to remove automatically.

    Returns (count, summary, removed_texts) where removed_texts is the list of
    raw line texts removed by the model's REMOVE decisions (for collateral checking).
    """
    with open(target, encoding="utf-8", errors="replace", newline="") as f:
        raw = f.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    file_lines = raw.split(nl)
    trailing = file_lines[-1] == ""
    if trailing:
        file_lines = file_lines[:-1]

    # Collect all lines to remove (auto + model-chosen REMOVE)
    model_removes = [n for n, v in decisions if v == "remove"]
    all_removes = sorted(set(auto_removes + model_removes), reverse=True)  # bottom-up

    # Record the text of model-chosen removals for collateral checking
    removed_texts = [file_lines[n - 1] for n in model_removes if 1 <= n <= len(file_lines)]

    # Also remove blank lines adjacent to removed blocks (the hunk's '-' lines
    # include trailing blanks; match that)
    _, _, _ = hunk_sides(hunk)
    hunk_removed_blanks = sum(1 for l in hunk["lines"] if l == "-")

    # The '+' block is the harness's job, never the model's: place it by its context line (mixed hunks).
    removed_, added, _ = hunk_sides(hunk)
    ins = None
    if added:
        at = _anchor(file_lines, [l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")])
        if at is None:
            raise OSError("the added lines have no place: the hunk's context was not found")
        ins = insertion_after(file_lines, hunk, max(0, at - 5), min(len(file_lines) - 1, at + len(hunk["lines"]) + 30))
        if ins is None:
            raise OSError("the place for the added lines could not be fixed on one context line")

    # Delete from bottom up so indices stay valid
    for n in all_removes:
        if 1 <= n <= len(file_lines):
            del file_lines[n - 1]
            if ins is not None and n - 1 <= ins:
                ins -= 1
    if added:
        file_lines[ins + 1:ins + 1] = added

    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(nl.join(file_lines) + (nl if trailing else ""))

    summary = f"removed {len(all_removes)} line(s) ({len(auto_removes)} auto, {len(model_removes)} by model)" + \
        (f", inserted {len(added)} line(s) by the harness" if added else "")
    return len(all_removes) + len(added), summary, removed_texts


class Ambiguous(ValueError):
    """The transplant cannot be done without judgement: the job goes to the model."""


def change_blocks(hunk):
    """The hunk as runs of changes: [(context line before or None, removed lines, added lines)]."""
    blocks, cur, before = [], None, None
    offset = 0
    for l in hunk["lines"]:
        tag, body = l[:1], l[1:]
        if tag in "+-":
            if cur is None:
                cur = {"before": before, "-": [], "+": [], "offset": offset}
                blocks.append(cur)
            cur[tag].append(body)
            if tag == "-":
                offset += 1
        elif tag == " ":
            cur, before = None, body
            offset += 1
    return blocks


def transplant(lines, hunk, notes=None):
    """Apply the hunk by finding its removed lines themselves, ignoring context that upstream changed.

    Live run 5 (2026-09-30): GNU patch refused the hunk because Mozilla had rewritten the lines
    AROUND it in 155.0.1, but every line it removes was still there, word for word and together,
    in exactly one place. Gemma failed it nine times; this does it without a model. Refuses
    (Ambiguous) the moment a block is missing or could sit in more than one place."""
    out = list(lines)
    keyed = [_key(l) for l in lines]
    edits = []
    for b in change_blocks(hunk):
        R = [_key(x) for x in b["-"]]
        if R:
            hits = [i for i in range(len(keyed) - len(R) + 1) if keyed[i:i + len(R)] == R]
            if len(hits) != 1:
                raise Ambiguous(f"the removed lines are {'missing' if not hits else f'in {len(hits)} places'}")
            for old, now in zip(b["-"], lines[hits[0]:hits[0] + len(R)]):
                if notes is not None and old.strip() != now.strip():
                    notes.append(f"upstream changed this line; removed as the patch intends: {now.strip()[:120]}"
                                 f" (the patch expected: {old.strip()[:120]})")
            edits.append((hits[0], len(R), b["+"]))
        else:
            if b["before"] is None or not b["before"].strip():
                # Use anchor + offset
                ctx_and_rm = [l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")]
                at = _anchor(lines, ctx_and_rm)
                if at is None:
                    raise Ambiguous("an insertion with no distinctive line before it, and anchor not found")
                hits = [at + b["offset"]]
            else:
                hits = [i for i, l in enumerate(keyed) if l == _key(b["before"])]
                
            if len(hits) != 1:
                raise Ambiguous(f"the line to insert after is {'missing' if not hits else f'in {len(hits)} places'}")
            edits.append((hits[0] + (0 if (b["before"] is None or not b["before"].strip()) else 1), 0, b["+"]))
    edits.sort(key=lambda e: e[0])
    if any(a[0] + a[1] > b[0] for a, b in zip(edits, edits[1:])):
        raise Ambiguous("two changes overlap")
    for at, n, new in reversed(edits):
        out[at:at + n] = new
    return out


def auto_substitute(lines, hunk, notes=None):
    """Apply the hunk by matching removed lines individually, even when they are not contiguous.

    transplant() requires the removed block to sit together in the target file.
    auto_substitute() relaxes this: it matches each removed line individually by _key,
    accepts unique matches even when upstream inserted new lines between them, and
    replaces them with the added lines.  Raises Ambiguous if any line cannot be uniquely
    found or the structure is too complex for a mechanical replacement."""
    out = list(lines)
    keyed = [_key(l) for l in lines]
    edits = []                                          # (position, n_to_remove, replacement_lines)

    for b in change_blocks(hunk):
        R, A = b["-"], b["+"]
        if not R and not A:
            continue

        if R:
            R_keys = [_key(x) for x in R]
            # Try contiguous match first (same as transplant)
            hits = [i for i in range(len(keyed) - len(R_keys) + 1) if keyed[i:i + len(R_keys)] == R_keys]
            if len(hits) == 1:
                edits.append((hits[0], len(R), A))
                continue

            # Contiguous match failed.  For 1-to-1 substitutions, try individual matching.
            if len(R) == len(A):
                positions = []
                for rk in R_keys:
                    r_hits = [i for i, k in enumerate(keyed) if k == rk]
                    if len(r_hits) != 1:
                        raise Ambiguous(f"line {rk[:60]} is {'missing' if not r_hits else f'in {len(r_hits)} places'}")
                    positions.append(r_hits[0])
                for pos, new_line in zip(positions, A):
                    edits.append((pos, 1, [new_line]))
                if notes is not None:
                    notes.append(f"substituted {len(R)} line(s) individually")
                continue

            # Pure deletion (removals with no additions)
            if not A:
                positions = []
                for rk in R_keys:
                    r_hits = [i for i, k in enumerate(keyed) if k == rk]
                    if len(r_hits) != 1:
                        raise Ambiguous(f"line {rk[:60]} is {'missing' if not r_hits else f'in {len(r_hits)} places'}")
                    positions.append(r_hits[0])
                for pos in positions:
                    edits.append((pos, 1, []))
                if notes is not None:
                    notes.append(f"removed {len(R)} line(s) individually")
                continue

            raise Ambiguous(f"block with {len(R)} removals and {len(A)} additions: structure too complex")
        else:
            # Pure insertion — same logic as transplant
            if b["before"] is None or not b["before"].strip():
                raise Ambiguous("an insertion with no distinctive line before it")
            hits = [i for i, l in enumerate(keyed) if l == _key(b["before"])]
            if len(hits) != 1:
                raise Ambiguous(f"the line to insert after is {'missing' if not hits else f'in {len(hits)} places'}")
            edits.append((hits[0] + 1, 0, A))

    edits.sort(key=lambda e: e[0])
    if any(a[0] + a[1] > b[0] for a, b in zip(edits, edits[1:])):
        raise Ambiguous("two changes overlap")
    for at, n, new in reversed(edits):
        out[at:at + n] = new
    return out


SPECIFIC = 25      # a line shorter than this (a brace, "#endif", a short pref) proves nothing by itself


def obsolete_upstream(body, hunk):
    """Is the thing this hunk changes simply gone from the new source? Returns the reason, or None.

    Live run 7 (2026-10-01, hunk h23 of firefox.js): Gorilla's old comment block and `#if` around
    browser.preonboarding.enabled do not exist in Firefox 157 at all (upstream removed the feature), yet
    Gemma was asked to port it, invented edits and was refused twice. A model must not be asked to port
    what is no longer there; whether to drop or re-create it is the owner's decision."""
    removed, added, context = hunk_sides(hunk)
    have = {l.strip() for l in body}
    spec_removed = [l.strip() for l in removed if len(l.strip()) >= SPECIFIC and not TRIVIAL.match(l.strip())]
    spec_added = [l.strip() for l in added if len(l.strip()) >= SPECIFIC and not TRIVIAL.match(l.strip())]
    anchored = any(len(l.strip()) >= 12 and l.strip() in have for l in context)
    if spec_removed and anchored and not any(l in have for l in spec_removed) and not any(l in have for l in spec_added):
        return ("every specific line this hunk would remove is already gone from the new source, and none of the lines it "
                "would add are there: upstream removed or replaced this. Not a job for a model; the owner decides whether "
                "the Gorilla change is still wanted")
    return None


def auto_port(t, s, patch, file, hunk, **kw):
    """The harness's own attempt before a model is asked. -> {"ok", "why"}; writes the file only on success.

    Three tiers, tried in order:
      1. transplant: exact contiguous-block matching (cheapest, most reliable)
      2. auto_substitute: individual line matching for 1-to-1 substitutions
      3. answer-key fallback: copy the known-good file from Gorilla.firefox/src"""
    target = Path(t["workdir"]) / file
    if not target.is_file():
        return {"ok": False, "why": [f"{file} does not exist"]}
    raw = target.read_text(encoding="utf-8", errors="replace")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(nl)
    trailing = lines and lines[-1] == ""
    body = lines[:-1] if trailing else lines
    notes = []

    # (Tier 0 answer-key fallback removed: copying Firefox 156 files over 157 breaks upstream changes)

    gone = obsolete_upstream(body, hunk)
    if gone:
        return {"ok": False, "defer": True, "why": [gone]}

    # Tier 1: transplant (exact contiguous-block matching)
    try:
        new = transplant(body, hunk, notes)
        target.write_text(nl.join(new) + (nl if trailing else ""), encoding="utf-8", newline="")
        return {"ok": True, "why": [], "notes": notes}
    except Ambiguous:
        pass

    # Tier 2: auto-substitute (individual line matching)
    try:
        new = auto_substitute(body, hunk, notes)
        target.write_text(nl.join(new) + (nl if trailing else ""), encoding="utf-8", newline="")
        return {"ok": True, "why": [], "notes": notes + ["applied via auto-substitute"]}
    except Ambiguous:
        pass

    # Tier 3: merge by key (removals matched line by line, the '+' block placed after its context line)
    try:
        new = auto_merge(body, hunk, notes)
        target.write_text(nl.join(new) + (nl if trailing else ""), encoding="utf-8", newline="")
        return {"ok": True, "why": [], "notes": notes}
    except Ambiguous as e:
        notes.append(f"merge: {e}")

    return {"ok": False, "why": ["transplant, auto-substitute and merge all failed: " + "; ".join(notes)]}


def check_port(t, s, patch, file, hunk, **kw):
    target = Path(t["workdir"]) / file
    if not target.is_file():
        return {"ok": False, "why": [f"{file} does not exist; this hunk needs the owner"]}
    before = subprocess.run(["git", "-C", t["workdir"], "show", f"HEAD:{file}"], capture_output=True).stdout.decode(
        "utf-8", "replace").splitlines()
    after = target.read_text(encoding="utf-8", errors="replace").splitlines()
    # No flag, answer key or other shortcut may skip these checks (the overnight run of 2026-10-01 did).
    why = hunk_problems(before, after, hunk)
    extra = s.get("question_removals")  # set by apply_question_answers via the driver
    why += collateral(before, after, hunk, extra_removals=extra)
    _, added, _ = hunk_sides(hunk)
    if len(after) > len(before) + 3 * max(1, len(added)) + 20:
        why.append(f"{len(after) - len(before)} lines added for a {len(added)}-line hunk: change only what the hunk changes")
    return {"ok": not why, "why": why[:8]}


def already_upstream(file_lines, hunk):
    """True when the file already has the hunk's result: the added lines are there and the removed ones gone."""
    removed, added, _ = hunk_sides(hunk)
    a = [l.strip() for l in file_lines]
    meaningful_added = [x.strip() for x in added if x.strip() and not TRIVIAL.match(x.strip())]
    meaningful_removed = [x.strip() for x in removed if x.strip() and not TRIVIAL.match(x.strip())
                          and x.strip() not in meaningful_added]
    return bool(meaningful_added or meaningful_removed) and all(k in a for k in meaningful_added) and         not any(k in a for k in meaningful_removed)


# -- building the plan -------------------------------------------------------------------------

def plan(harness_root, vault_base=None, pinned=None):
    """The steps as the owner approves them. Group steps are added once the source is in place."""
    common = {"vault_base": str(vault_base) if vault_base else None}
    return [
        {"id": "resolve", "kind": "script", "title": "find the latest stable Firefox (release tag, never main)",
         "run": "fieldkit.buildh.firefox:step_resolve"},
        {"id": "vault", "kind": "script", "title": "put the untouched source in the vault and verify it",
         "run": "fieldkit.buildh.firefox:step_vault", "args": common},
        {"id": "workcopy", "kind": "script", "title": "make a fresh working copy from the vault",
         "run": "fieldkit.buildh.firefox:step_workcopy", "args": common},
        {"id": "plan-groups", "kind": "script", "title": "one apply + export step per enabled patch group",
         "run": "fieldkit.buildh.firefox:plan_groups", "args": {"harness_root": str(harness_root)}},
    ]
