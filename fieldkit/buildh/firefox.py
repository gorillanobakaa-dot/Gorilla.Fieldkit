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
    # the owner's own preflight (Gorilla.firefox/harness/gorilla_build.py) at the very end: its BLOCKERS become
    # owner steps with the owner's fix text (2026-10-01: four of them on the ported 157 tree, none visible to the port)
    steps.append({"id": "owner-preflight", "kind": "script", "title": "the owner's preflight: every blocker becomes an owner step",
                  "run": "fieldkit.buildh.ownercheck:step_owner_preflight"})
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
            # --fuzz=0: with fuzz 3 GNU patch dropped the three context lines of FOG.cpp's hunk and put the owner's
            # `return NS_OK;` block before the function signature (live run 16, stop 8). A hunk whose context does not
            # match is a REJECT for the tiers, which anchor on content; a guess by line number is never a port.
            r = subprocess.run([_patch_exe(), "-p1", "--forward", "--no-backup-if-mismatch", "--fuzz=0",
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
            from . import relocate
            moved = []
            for fname, hunks in parsed.items():
                if not (w / fname).is_file():
                    steps, why = relocate.steps_for_missing(w, group, rel, pf.stem, fname, hunks,
                                                            {x["id"] for x in new_steps})
                    if steps:
                        moved.append(f"{fname}: {why}")
                        new_steps.extend(steps)
            if moved:
                upstreamed.append(f"{rel} (moved: " + "; ".join(moved) + ")")
                continue
            new_steps.append({"id": f"owner-{group}-{pf.stem}", "kind": "owner", "obsolete_default": True,
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


def still_holds(s, file, hunk, before, now):
    """The re-check at the end: does this step's OWN result still stand? Judged the way the step's tier judged
    it (Fluent by message with the step's rename mapping, prefs by name, lines inside the span), and only the
    hunk's result: later hunks legitimately touch the same file, so no collateral here. Live run 15: the literal
    re-check called the Fluent transfer of browser.ftl h30 a regression and failed the final checks three times."""
    if s.get("hand_port") or s.get("done_by") == "hand":
        return hand_port_holds(now, hunk, before, hand_keeps(s.get("hand_note")))
    if file.endswith(".ftl"):
        from . import fluent
        try:
            return fluent.check(before, now, hunk, {k: tuple(v) for k, v in (s.get("fluent_map") or {}).items()}, collateral=False)
        except fluent.Ambiguous:
            pass
    if Path(file).name in PREF_FILES:
        from . import prefs
        ch = prefs.changes(hunk)
        if (ch["set"] or ch["drop"]) and not ch["add"]:
            return prefs.check(before, now, hunk, collateral=False)
    from . import keyed
    if keyed.kind(file):
        return keyed.check(before, now, hunk, file, collateral=False)
    if now == before or already_upstream(now, hunk):
        return []
    return hunk_problems(before, now, hunk)


def node_check(path):
    """`node --check` on one JS/ESM file -> None when it parses, else "line N: error". None too when node is
    missing (the caller reports that once as a tool gap, never as a pass)."""
    import shutil, subprocess
    node = shutil.which("node")
    if not node:
        return None
    r = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, errors="replace", timeout=60)
    if r.returncode == 0:
        return None
    lines = [l for l in (r.stderr or "").splitlines() if l.strip()]
    err = next((l for l in lines if "Error" in l), lines[-1] if lines else "node --check failed")
    loc = next((l for l in lines if Path(path).name in l and ":" in l), "")
    return (f"line {loc.rsplit(':', 1)[-1].strip()}: " if loc else "") + err


def syntax_problems(workdir, files):
    """Changed files that do not parse: moz.build and .py with ast, .json with json, .mjs/.sys.mjs/.js with
    `node --check`. A dangling `GeneratedFile(` left by a merge stopped the 157 build at configure (live run 16);
    a half-removed actor block in DesktopActorRegistry.sys.mjs (2026-10-02) BUILT and then killed every window
    actor in the installed browser: no address bar, no extensions. A JS module that does not parse is a stop
    before the build, not after the install."""
    import ast, shutil
    out = []
    no_node = False
    for rel in files:
        p = Path(workdir) / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            if rel.endswith((".py", "moz.build")) or Path(rel).name == "moz.build":
                ast.parse(text)
                if Path(rel).name == "moz.build" or rel.endswith(".mozbuild"):
                    from .mozbuild_rules import empty_assignments
                    for ln, var in empty_assignments(text):
                        out.append(f"{rel}: line {ln}: mozbuild refuses an empty {var} assignment")
            elif rel.endswith(".json") and not rel.endswith((".in.json", ".jsonc")) and "/test" not in rel:
                json.loads(text)
            elif rel.endswith((".mjs", ".js")) and "/test" not in rel and not rel.endswith(".min.js"):
                if re.search(r"^#(ifdef|ifndef|if |filter|include|expand|define)\b", text, re.M):
                    continue                               # preprocessed (firefox.js, all.js): not plain JS until build time
                if not shutil.which("node"):
                    no_node = True
                    continue
                err = node_check(p)
                if err:
                    out.append(f"{rel}: {err[:120]}")
        except (SyntaxError, ValueError) as e:
            out.append(f"{rel}: {str(e).splitlines()[0][:120]}")
    if no_node:
        out.append("node is not installed: changed .js/.mjs files were NOT syntax-checked")
    return out


EXCISED = re.compile(r"GORILLA excised:?\s*\"?([A-Za-z_][\w./-]*)")


def excised_symbols(harness_root):
    """What the fork removes, as names worth looking for in the new tree: the quoted names in `# GORILLA excised: "x"`
    lines the patch set adds, and the stems of the files the fork deletes (DELETED_FILES manifests), 8+ chars."""
    pset, groups = _policy(harness_root)
    names = set()
    for g, spec in groups.items():
        if spec.get("status") != "enabled":
            continue
        for pf in (pset / g).rglob("*.patch"):
            for line in pf.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("+"):
                    for m in EXCISED.finditer(line):
                        names.add(Path(m.group(1)).name)
        for rel in manifest_deletions(harness_root):
            stem = Path(rel).name.split(".")[0]                          # AIWindow.sys.mjs -> AIWindow
            if len(stem) >= 8 and not stem.islower() and stem not in ("manifest",):
                names.add(stem)
    return sorted(n for n in names if len(n) >= 8)


COMMENT = re.compile(r"^\s*(//|#|/\*|\*|<!--)")
CREEP_SKIP = ("third_party/", "taskcluster/", "testing/", "tools/", "docs/")


def _generic(symbols, old_root, limit=12):
    """Symbols the OLD pristine tree used in more than `limit` files the fork did not delete are not excised
    identifiers (XPCOMUtils, manifest). ONE git grep for all symbols: a grep per symbol over the Firefox tree ran
    for three hours inside the build gate (2026-10-02 00:31-03:42) and nothing moved."""
    if not symbols or not (Path(old_root) / ".git").exists():
        return set()
    args = ["git", "-C", str(old_root), "grep", "-nIF"]
    for sym in symbols:
        args += ["-e", sym]
    r = subprocess.run(args + ["--", "*.cpp", "*.h", "*.mjs", "*.js", "moz.build", "*.ipdl"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900)
    files = {}
    for line in r.stdout.splitlines():
        f, _, rest = line.partition(":")
        _, _, text = rest.partition(":")
        if "/test" in f:
            continue
        for sym in symbols:
            if sym in text:
                files.setdefault(sym, set()).add(f)
    return {sym for sym, fs in files.items() if len(fs) > limit}


def excision_creep(workdir, old_root, symbols, exclude_dirs=()):
    """Files in the new tree with lines that name an excised symbol and did not exist in the old pristine version
    of the same file: upstream code reaching into a component the fork removes. -> [(file, n_new, example)].
    Live run 16 (2026-10-02): 157's HWInference wired PSpeechRecognition and PHWInference into PContent.ipdl,
    PUtilityProcess.ipdl, ContentParent and the sandbox - none covered by any hunk; the build found them one by one."""
    symbols = [x for x in symbols if x not in _generic(symbols, old_root)][:300]
    if not symbols:
        return []
    args = ["git", "-C", str(workdir), "grep", "-nIF"]
    for sym in symbols:
        args += ["-e", sym]
    args += ["--", "*.ipdl", "*.cpp", "*.h", "*.mm", "moz.build", "*.mjs", "*.js", "*.webidl", "*.idl", "*.mn", "*.ftl"]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    hits = {}
    for line in r.stdout.splitlines():
        f, _, rest = line.partition(":")
        n, _, text = rest.partition(":")
        if not f or "/test" in f or f.startswith(CREEP_SKIP) or any(f.startswith(d) for d in exclude_dirs):
            continue
        if COMMENT.match(text) or "GORILLA" in text:                     # the fork's own notes name what it removed
            continue
        hits.setdefault(f, []).append((n, text.strip()))
    out = []
    for f, rows in sorted(hits.items()):
        old = Path(old_root) / f
        old_text = old.read_text(encoding="utf-8", errors="replace") if old.is_file() else ""
        new = [(n, t) for n, t in rows if t not in old_text]
        if new:
            out.append((f, len(new), f"{new[0][0]}: {new[0][1][:80]}"))
    return out


def misplaced(body, hunk, gap=None):
    """For a hunk that ADDS a block next to context lines: the first added line's position in `body` must be within
    `gap` lines of a specific context line that precedes it in the hunk (or follows it, for a block added at the
    top). -> None when placed, else a reason. Live run 16, stop 8: FOG.cpp's early return sat 7 lines above its
    context `gInitializeCalled = true;`, and the verifier called the hunk APPLIED because the lines existed."""
    gap = GAP if gap is None else gap
    lines = hunk["lines"]
    first_add = next((i for i, l in enumerate(lines) if l.startswith("+")), None)
    if first_add is None:
        return None
    keys = [_key(l) for l in body]
    # the anchor is an added line with identity: specific, not a comment, occurring ONCE in the file (a stylelint
    # comment that lives in six places anchored three CSS hunks on the wrong copy)
    added_key = next((_key(l[1:]) for l in lines[first_add:] if l.startswith("+") and _specific(_key(l[1:]))
                      and keys.count(_key(l[1:])) == 1), None)       # unique in the file; a unique comment anchors too
    if added_key is None:
        return None
    before = [_key(l[1:]) for l in lines[:first_add] if l.startswith(" ") and _specific(_key(l[1:]))]
    after = [_key(l[1:]) for l in lines[first_add:] if l.startswith(" ") and _specific(_key(l[1:]))]
    at = [i for i, l in enumerate(body) if _key(l) == added_key]
    if not at:
        return None                                      # not there at all: score_hunk reports that itself
    if not any(k in keys for k in before + after):
        return None                                      # no context of the hunk exists here: nothing to place against
    for i in at:
        near_before = any(k in keys[max(0, i - gap):i] for k in before) if before else False
        near_after = any(k in keys[i + 1:i + 1 + gap + len(lines)] for k in after) if after else False
        if (before and near_before) or (not before and near_after) or (before and not any(k in keys for k in before) and near_after):
            return None
    want = before[-1] if before else (after[0] if after else "")
    return f"added block is not next to its context (`{want[:60]}`): found at line {at[0] + 1}, context elsewhere"


def changed_vs_root(workdir):
    root = subprocess.run(["git", "-C", str(workdir), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.split()
    if not root:
        return []
    r = subprocess.run(["git", "-C", str(workdir), "diff", "--name-only", root[0], "HEAD"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return [l for l in r.stdout.splitlines() if l]


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
    why += [f"does not parse: {x}" for x in syntax_problems(w, changed_vs_root(w))[:5]]
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
        lost = still_holds(s, a["file"], a["hunk"], before, now) if before else []
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
            if uncertain and (not added or placeable(lines, hunk, at)):
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


GAP = 10            # how far apart two consecutive hunk lines may sit in the file and still be the same place


def _bounds(lines, hunk, anchor):
    """The hunk's own place in the file, found by aligning its lines IN ORDER: the first specific hunk line
    (context or removed) is located from a little before the anchor, and every following specific line must
    appear within GAP lines of the previous match; the alignment stops at the first that does not.
    -> (lo, hi) inclusive, or None.

    Why in order: a long but common line (`/* stylelint-disable-next-line ... */`, `color: inherit;`) occurs
    all over a CSS file; taken out of order it pulled the span onto the wrong block (live run 12, h7/h11)."""
    counts = {}
    for l in lines:
        k = _key(l)
        counts[k] = counts.get(k, 0) + 1
    # a line that occurs a few times may anchor (navigator-toolbox.js holds the same `case` block in two handlers,
    # live run 16); one that occurs all over the file never does. A non-unique line may not EXTEND the span
    # across lines the hunk knows nothing about (`color: inherit;` two rules further down, h7).
    hunk_keys = {_key(l[1:]) for l in hunk["lines"] if l[:1] in (" ", "-")}
    seq = [_key(l[1:]) for l in hunk["lines"] if l[:1] in (" ", "-") and _specific(_key(l[1:]))]
    # the START needs a line that occurs a few times at most; later lines may occur more often (design-token
    # CSS repeats the same declaration in every colour scheme) because the walk only accepts them in order,
    # within GAP, and not across unknown lines
    while seq and counts.get(seq[0], 0) > 3:
        seq.pop(0)
    if not seq:
        return None
    limit = min(len(lines), anchor + len(hunk["lines"]) + 40)
    # the hunk may begin well before the anchor (the anchor can land inside a long removed block,
    # DesktopActorRegistry h1): take the occurrence nearest below the anchor within the hunk's own length,
    # else the first one after
    below = [i for i in range(max(0, anchor - len(hunk["lines"]) - 5), min(len(lines), anchor + 6)) if _key(lines[i]) == seq[0]]
    lo = below[-1] if below else next((i for i in range(anchor + 6, limit) if _key(lines[i]) == seq[0]), None)
    if lo is None:
        return None
    hi = lo
    for key in seq[1:]:
        # a line upstream dropped (a removed line already gone, a context line rewritten) is skipped, not a stop:
        # the next key is still looked for within GAP of the last match
        nxt = next((i for i in range(hi + 1, min(len(lines), hi + 1 + GAP)) if _key(lines[i]) == key), None)
        if nxt is None:
            continue
        if counts.get(key, 0) > 1 and sum(1 for i in range(hi + 1, nxt) if _key(lines[i]) not in hunk_keys) >= 3:
            continue                        # (two unknown lines: upstream added a pair inside the span, tokens-platform.css)
        hi = nxt
    # the hunk's trailing removals right after the last match (`}`, a blank) belong to the span when contiguous
    hl = [l for l in hunk["lines"] if l[:1] in (" ", "-")]
    pos = next((i for i, l in enumerate(hl) if _key(l[1:]) == _key(lines[hi])), None)
    if pos is not None:
        for l in hl[pos + 1:]:
            if l.startswith("-") and hi + 1 < len(lines) and _key(lines[hi + 1]) == _key(l[1:]):
                hi += 1
            else:
                break
    return lo, hi


def _span(lines, hunk, lo=None):
    """The region of `lines` this hunk is about -> (lo, hi) EXCLUSIVE, or None when it cannot be pinned.
    `lo` may be given (the span's start found in the file BEFORE the change: edits happen inside the span,
    so its start is the same afterwards, while re-anchoring on the changed text can drift)."""
    if lo is None:
        at = _anchor(lines, [l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")])
        if at is None:
            return None
        b = _bounds(lines, hunk, at)
        return (b[0], b[1] + 1) if b else None
    tail = [_key(l[1:]) for l in reversed(hunk["lines"]) if l.startswith(" ") and _specific(_key(l[1:]))]
    limit = min(len(lines), lo + len(hunk["lines"]) + 40)
    for key in tail:
        hit = next((i for i in range(lo, limit) if _key(lines[i]) == key), None)
        if hit is not None:
            return lo, hit + 1
    return None


def hunk_problems(before, after, hunk):
    """What is still wrong in `after` (lines) for the hunk's change, counted against `before`.

    For every meaningful line the hunk adds, the file must hold as many copies as before plus
    those added minus those removed; for every line it removes, the count must drop the same way.
    Braces and punctuation-only lines prove nothing and are ignored.

    Counted INSIDE the hunk's span when it can be pinned in both files, else file-wide. Live run 9
    (2026-10-01, firefox.js h33): upstream had already dropped the span's `#ifdef NIGHTLY_BUILD`, and the
    file holds fourteen other such lines, so a file-wide count could never 'drop' and a correct merge
    (and, before it, three of Gemma's attempts) were refused for a line that was never there."""
    removed, added, _ = hunk_sides(hunk)
    sb = _span(before, hunk)
    sa = _span(after, hunk, lo=sb[0]) if sb else None
    pinned = bool(sb and sa)
    if pinned:
        # the after-span must reach over the lines the hunk ADDS: when the hunk's trailing context is too short to
        # pin (`BackupUI: {`), the span ended before the inserted lines and called them missing (DesktopActorRegistry h1)
        add_keys = {_key(l[1:]) for l in hunk["lines"] if l.startswith("+") and _specific(_key(l[1:]))}
        reach = min(len(after), sa[0] + len(hunk["lines"]) + 40)
        last_added = max((i for i in range(sa[0], reach) if _key(after[i]) in add_keys), default=sa[1] - 1)
        sa = (sa[0], max(sa[1], last_added + 1))
        before, after = before[sb[0]:sb[1]], after[sa[0]:sa[1]]
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
        elif net < 0 and a.count(k) > want and pinned:
            # a removal is only ever demanded inside the hunk's own span: counted file-wide, a line that lives
            # elsewhere too (`color: inherit;`) refused a correct answer (live run 12, h7); collateral() still
            # catches removals that were not asked for
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
    hunk_idents = set().union(*(_idents(l) for l in removed + added)) if removed + added else set()
    gone, new = collections.Counter(), collections.Counter()
    sm = difflib.SequenceMatcher(None, [l.strip() for l in before], [l.strip() for l in after], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("delete", "replace"):
            # a removed run that starts and ends on the hunk's own '-' lines is the hunk's block, re-wrapped by
            # upstream in between (ActorManagerParent h1: `esModuleURI:` split over two lines inside `MLEngine: {`)
            run = [before[i] for i in range(i1, i2) if before[i].strip()]
            if len(run) >= 3 and _key(run[0]) in may_remove and _key(run[-1]) in may_remove:
                import collections as _c
                inner = _c.Counter(_key(l) for l in run[1:-1])
                for k, n in inner.items():
                    may_remove[k] = max(may_remove[k], n) if k in may_remove else n
            for i in range(i1, i2):
                if not before[i].strip():
                    continue
                # a line upstream added INSIDE a block the hunk removes (both neighbours are the hunk's '-' lines)
                # goes with the block: the port, not damage (SessionStore h6: `let activeIndex = this.historyIndex(tab);`)
                nb = [_key(before[j]) for j in (i - 1, i + 1) if 0 <= j < len(before) and before[j].strip()]
                if _key(before[i]) not in may_remove and len(nb) == 2 and all(k in may_remove for k in nb) \
                        and (TRIVIAL.match(before[i].strip()) or _idents(before[i]) & hunk_idents):
                    may_remove[_key(before[i])] += 1       # ...but `"ipc",` added by upstream inside the block is not
                gone[before[i].strip()] += 1
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
    k = f"{m.group(1)}:{m.group(2)}" if m else _code(line)
    # upstream turns `this._field` into the private `this.#field` (SessionStore 157: _windows -> #windows); the
    # same line under either spelling (live run 16, SessionStore h2)
    return k.replace("this.#", "this._") if "this.#" in k else k


# ── question form (run 6 countermeasure) ─────────────────────────────────────
# Gemma cannot compose line operations. So the harness identifies which lines
# in the target file match the hunk's '-' lines (auto-removes), which are new
# upstream text (uncertain), and asks the model only about the uncertain ones.

GENERIC = {"#endif", "#else", "#ifdef nightly_build", "#ifndef", "}", "{", "};", "});", "end", "fi", "done"}


def _specific(key):
    """A line that can pin a position on its own: long enough, not punctuation, not a preprocessor closer."""
    return bool(key) and len(key) >= 12 and not TRIVIAL.match(key) and key.lower() not in GENERIC


def identify_questions(lines, hunk, anchor):
    """Which target-file lines to auto-remove and which to ask about, by walking the hunk IN ORDER over the
    file (a tolerant transplant), never by key sets: `}` is in every hunk and in every file, and a key-set match
    removed a context brace next to a removed block (live run 16, SessionStore h6, DesktopActorRegistry h1).

    Returns (auto_remove, uncertain), both 1-indexed. A '-' line is removed where the walk finds it (a trivial
    one only immediately where the walk stands); a context line moves the walk on; a line the walk has to step
    over that is not in the hunk is new upstream text: a question, unless it sits inside a removed block."""
    b = _bounds(lines, hunk, anchor)
    if b is None:
        return [], []
    lo, hi = b
    hunk_keys = {_key(l[1:]) for l in hunk["lines"] if l[:1] in (" ", "-", "+")}
    p, auto_remove, consumed, uncertain = lo, [], set(), []
    limit = hi + 1                      # the walk never leaves the frame (h7: `color: inherit;` two rules further down)
    for hl in hunk["lines"]:
        tag, text = hl[:1], hl[1:]
        if tag == "+":
            continue
        k = _key(text)
        if p >= limit:
            break
        if not _specific(k):
            # blank, punctuation, `#endif`, `break;`: it says nothing on its own, so it matches only where the
            # walk stands (a context `break;` searched ahead jumped the walk over the very block to remove,
            # navigator-toolbox.js h3)
            if _key(lines[p]) == k:
                if tag == "-":
                    auto_remove.append(p + 1)
                consumed.add(p)
                p += 1
            continue
        j = next((i for i in range(p, min(limit, p + GAP + 1)) if _key(lines[i]) == k), None)
        if j is None:
            continue                                              # upstream dropped this line
        for i in range(p, j):
            if lines[i].strip() and _key(lines[i]) not in hunk_keys:
                uncertain.append(i + 1)
        if tag == "-":
            auto_remove.append(j + 1)
        consumed.add(j)
        p = j + 1
    rm = set(auto_remove)
    enclosed = [n for n in uncertain if (n - 1) in rm and (n + 1) in rm]      # inside a removed block: goes with it
    if enclosed:
        auto_remove = sorted(rm | set(enclosed))
        uncertain = [n for n in uncertain if n not in enclosed]
    return sorted(set(auto_remove)), sorted(set(uncertain))


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
        if not hl[i].startswith("-") and hl[i].strip():
            break                                       # ('-' lines are gone, blank lines say nothing: walk on)
    for i in range(first_plus, len(hl)):
        if hl[i].startswith(" ") and hl[i][1:].strip():
            at = find(_key(hl[i][1:]))
            return at - 1 if at is not None else None   # before this line
    return None


def _placements(body, hunk, lo, hi):
    """Where each '+' block of the hunk goes, decided block by block. -> [(index in `body`, mode, lines)] with
    mode 'at' (the block replaces its own '-' lines: insert where the first of them stands) or 'after' / 'before'
    a unique context neighbour in the span. Raises PlacementGone when a block's neighbours are no longer here,
    Ambiguous when a neighbour occurs more than once in the span (live run 13, h17: two '+' blocks in one hunk)."""
    hl = hunk["lines"]
    span_keys = [_key(body[i]) for i in range(lo, hi + 1)]
    have = {_key(l) for l in body}

    def find(text):
        hits = [i for i in range(lo, hi + 1) if _key(body[i]) == _key(text)]
        return hits[0] if len(hits) == 1 else None
    out = []
    i = 0
    while i < len(hl):
        if not hl[i].startswith("+"):
            i += 1
            continue
        j = i
        while j < len(hl) and hl[j][:1] in "+-":
            j += 1
        block_minus = [hl[k][1:] for k in range(i, j) if hl[k].startswith("-")]
        # a block may start with '-' lines before the first '+': include them
        k0 = i
        while k0 > 0 and hl[k0 - 1].startswith("-"):
            k0 -= 1
        block_minus = [hl[k][1:] for k in range(k0, j) if hl[k].startswith("-")]
        added = [hl[k][1:] for k in range(i, j) if hl[k].startswith("+")]
        before = next((hl[k][1:] for k in range(k0 - 1, -1, -1) if hl[k].startswith(" ") and hl[k][1:].strip()), None)
        after = next((hl[k][1:] for k in range(j, len(hl)) if hl[k].startswith(" ") and hl[k][1:].strip()), None)
        placed = None
        usable = [c for c in (before, after) if c and _specific(_key(c))]
        once = [c for c in usable if span_keys.count(_key(c)) == 1]
        if not usable or once:
            # the block's own '-' lines pin it best (a substitution lands where the old line stood), but only
            # when a neighbour agrees the place is still here (h7: the old block was here, its @media was not)
            for m in block_minus:
                if m.strip() and find(m) is not None:
                    placed = (find(m), "at", added)
                    break
        if placed is None:
            if before and before in once:
                placed = (find(before), "after", added)
            elif after and after in once:
                placed = (find(after), "before", added)
            elif usable and not once:
                if any(span_keys.count(_key(c)) > 1 for c in usable):
                    raise Ambiguous("the place for the added lines could not be fixed on one context line")
                gone_ctx = [c for c in usable if _key(c) not in have]
                if gone_ctx:
                    raise PlacementGone(f"the lines the added text belongs with no longer exist in this Firefox: {gone_ctx[0][:80]!r}")
                raise PlacementGone(f"the lines the added text belongs with are somewhere else in the file now: {usable[0][:80]!r}")
            else:
                raise Ambiguous("the place for the added lines could not be fixed on one context line")
        out.append(placed)
        i = j
    return out


def _apply(body, deletions, placements):
    """Delete the 1-indexed `deletions`, then insert each placement, with every index measured on the ORIGINAL body."""
    gone = sorted(set(deletions))
    new = list(body)
    for n in reversed(gone):
        del new[n - 1]

    def shifted(idx):
        return idx - sum(1 for n in gone if n - 1 < idx)
    for idx, mode, lines in sorted(placements, key=lambda p: p[0], reverse=True):
        at = shifted(idx) + (1 if mode == "after" else 0)
        new[at:at] = lines
    return new


_FUNC = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|(?:static\s+)?(?:get\s+|set\s+)?(#?\w+)\s*\([^)]*\)\s*\{\s*$"
                   r"|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>|(\w+)\s*:\s*(?:async\s+)?function\b"
                   r"|(?:const|let|var)\s+(\w+)\s*=\s*\{\s*$|(\w+)\s*:\s*\{\s*$)")


def enclosing_function(lines, idx):
    """The nearest function / method / object the line belongs to, by indentation (a heuristic for JS/C++)."""
    if idx is None or idx >= len(lines):
        return None
    indent = len(lines[idx]) - len(lines[idx].lstrip())
    for i in range(idx - 1, -1, -1):
        l = lines[i]
        if not l.strip():
            continue
        ind = len(l) - len(l.lstrip())
        if ind < indent:
            m = _FUNC.match(l)
            if m:
                return next(g for g in m.groups() if g)
            indent = ind
    return None


def moved_where(body, hunk, file):
    """For a hunk the harness could not place in a code file: where its old home was (the patch's own context
    header) and where its lines sit now, so a person knows what moved (PanelTestProvider h2: the ternary moved
    from getMessages() into tagMessageForTesting())."""
    if not file.endswith((".js", ".mjs", ".jsm", ".cpp", ".h", ".c", ".py", ".rs")):
        return None
    m = re.search(r"@@ .*? @@\s*(.*)$", hunk.get("header", ""))
    old_home = m.group(1).strip() if m and m.group(1).strip() else None
    _, _, _ = hunk_sides(hunk)
    specific = [_key(l[1:]) for l in hunk["lines"] if l.startswith("-") and _specific(_key(l[1:]))]
    hits = [i for i, l in enumerate(body) if _key(l) in specific]
    new_home = enclosing_function(body, hits[0]) if hits else None
    if not old_home and not new_home:
        return None
    return ("in the old tree this sat in: " + (old_home[:60] if old_home else "?") +
            ("; its lines now sit in: " + new_home + "()" if new_home else "; its lines were not found in this file"))


def placeable(body, hunk, at):
    """Can every '+' block of the hunk be placed without a model? (the question form needs that)"""
    b = _bounds(body, hunk, at)
    lo, hi = (b[0], min(len(body) - 1, b[1] + 5)) if b else (max(0, at - 5), min(len(body) - 1, at + len(hunk["lines"]) + 30))
    try:
        _placements(body, hunk, lo, hi)
        return True
    except Ambiguous:
        return False


def auto_merge(body, hunk, notes=None):
    """Tier 3 of the harness's own attempt: remove every file line whose key matches a '-' line, insert each '+'
    block at its own place. Refuses (Ambiguous) when the span holds lines it cannot account for: those are the
    REMOVE/KEEP questions for the model. Live run 8 (2026-10-01, firefox.js h32): upstream had collapsed a
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
    b = _bounds(body, hunk, at)
    lo, hi = (b[0], min(len(body) - 1, b[1] + 5)) if b else (max(0, at - 5), min(len(body) - 1, at + len(hunk["lines"]) + 30))
    placements = _placements(body, hunk, lo, hi) if added else []
    new = _apply(body, auto_rm, placements)
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

    # The '+' blocks are the harness's job, never the model's: each placed by its own lines (mixed hunks).
    removed_, added, _ = hunk_sides(hunk)
    placements = []
    if added:
        at = _anchor(file_lines, [l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")])
        if at is None:
            raise OSError("the added lines have no place: the hunk's context was not found")
        b = _bounds(file_lines, hunk, at)
        lo, hi = (b[0], min(len(file_lines) - 1, b[1] + 5)) if b else (max(0, at - 5), min(len(file_lines) - 1, at + len(hunk["lines"]) + 30))
        try:
            placements = _placements(file_lines, hunk, lo, hi)
        except Ambiguous as e:
            raise OSError(str(e))
    file_lines = _apply(file_lines, [n for n in all_removes if 1 <= n <= len(file_lines)], placements)

    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(nl.join(file_lines) + (nl if trailing else ""))

    summary = f"removed {len(all_removes)} line(s) ({len(auto_removes)} auto, {len(model_removes)} by model)" + \
        (f", inserted {len(added)} line(s) by the harness" if added else "")
    return len(all_removes) + len(added), summary, removed_texts


class Ambiguous(ValueError):
    """The transplant cannot be done without judgement: the job goes to the model."""


class PlacementGone(Ambiguous):
    """The context the added lines belong with no longer exists: a decision for the owner, not a model."""


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
PREF_FILES = {"firefox.js", "all.js", "mobile.js", "firefox-branding.js"}      # ported by pref name


_IDENT = re.compile(r"[A-Za-z_$#-][\w$-]{7,}|\"[^\"]{4,}\"|'[^']{4,}'")


def _idents(line):
    """The identifiers (8+ chars) and string literals of a line, with `_`/`#`/`$`/`-` prefixes dropped so that
    `this._allowTransparentBrowser`, `this.#documentGlobal` and `lazy.allowTransparentBrowser` agree. Hyphens stay
    inside a name: a CSS custom property is one identifier (`--border-color-deemphasized`), not its last word
    (the first cut matched `deemphasized` alone and called eight done CSS hunks 'renamed')."""
    return {t.lstrip("_#$-").rstrip("-") for t in _IDENT.findall(line) if t.lstrip("_#$-").rstrip("-")}


def _is_renamed(removed_toks, text):
    """Is `text` the removed line under new names? Every long token (12+) of the removed line must be there, at
    least half of all its tokens, and the text's own tokens must be mostly the removed line's (not a longer line
    that merely mentions them)."""
    mine = _idents(text)
    if not mine:
        return False
    shared = removed_toks & mine
    if any(len(t) >= 10 and t not in mine for t in removed_toks):
        return False                                    # `isAIWindow` missing: `.isPopup = true` is another line
    return len(shared) * 2 >= len(removed_toks) and len(shared) * 10 >= len(mine) * 6


def _judgeable_rename(removed):
    """A removed line may be judged 'present under new names' only when it carries enough identity: two or more
    8+ identifiers/strings, or one of 12+ characters (`remoteTypes` alone identifies nothing)."""
    toks = _idents(removed)
    names = {t for t in toks if t[:1] not in "\"'`"}             # a string literal is content, not identity
    return (len(toks) >= 2 and any(len(t) >= 10 for t in names)) or any(len(t) >= 12 for t in names)


REWRAP = 3            # a removed line may now be spread over up to this many lines


def renamed_candidates(lines, removed, lo=0, hi=None, exclude=()):
    """-> [(start, n)] runs of n lines in lines[lo:hi] that are `removed` under new names (never the exact line).
    Upstream also re-wraps: `if (A || B) {` became `if (` / `A ||` / `B` on three lines (Tabbrowser h4)."""
    if not _judgeable_rename(removed):
        return []
    toks = _idents(removed)
    key = removed.strip()
    skip = {l.strip() for l in exclude}
    added_toks = [_idents(x) for x in exclude if _judgeable_rename(x)]
    hi = len(lines) if hi is None else hi
    out, i = [], lo
    while i < hi:
        hit = None
        if not _idents(lines[i]):                       # a run starts on a line that says something
            i += 1
            continue
        for n in range(1, REWRAP + 1):
            if i + n > hi:
                break
            run = lines[i:i + n]
            if any(l.strip() == key or l.strip() in skip for l in run):
                continue                                # the exact line is presence, not a rename; an added line is the result
            if n > 1 and (not all(_idents(l) & toks for l in run) or any(_is_renamed(toks, l) for l in run)):
                continue                                # every line of a run carries part of it; runs are minimal
            text = " ".join(l.strip() for l in run)
            if any(_is_renamed(a, text) for a in added_toks):
                continue                                # the hunk's own added line, adapted by a person
            if _is_renamed(toks, text):
                hit = n
                break
        if hit:
            out.append((i, hit))
            i += hit
        else:
            i += 1
    return out


def renamed_form(lines, removed, lo=0, hi=None, exclude=()):
    """Index in lines[lo:hi] of the line that IS the removed line after upstream renamed things around it, or None.

    Live run 16 (2026-10-01, Tabbrowser.sys.mjs h3-h5): Firefox 157 turned `AIWindow.isAIWindowActive(window)` into
    `lazy.AIWindow.isAIWindowActive(this.documentGlobal)` and `this._allowTransparentBrowser` into
    `lazy.allowTransparentBrowser`. Judged by exact text the removed lines were "gone", so the port was recorded as
    done (h3) and obsolete (h4) with the code still there."""
    c = renamed_candidates(lines, removed, lo, hi, exclude)
    return c[0][0] if c else None


def renamed_pairs(lines, removed, lo=0, hi=None, exclude=()):
    """-> [(removed line, file text)] for the removed lines present only in renamed form; `exclude` is the hunk's
    added lines (a changed value is the hunk's result, not a rename)."""
    have = {l.strip() for l in lines[lo:hi]}
    out = []
    for r in removed:
        k = r.strip()
        if not k or TRIVIAL.match(k) or k in have:
            continue
        c = renamed_candidates(lines, r, lo, hi, exclude)
        if c:
            i, n = c[0]
            out.append((k, " ".join(l.strip() for l in lines[i:i + n])))
    return out


def rename_window(lines, hunk):
    """Where a hunk's removed lines may live: one hunk length and GAP either side of the context frame; the whole
    file when no frame can be pinned (the context itself may have been renamed)."""
    frame = _span(lines, {"lines": [l for l in hunk["lines"] if not l.startswith("-")]})
    reach = len(hunk["lines"]) + GAP
    return (max(0, frame[0] - reach), min(len(lines), frame[1] + reach)) if frame else None


def renamed_near(lines, hunk, removed, added=()):
    """renamed_pairs inside the hunk's window; [] when the hunk cannot be pinned (a file-wide search called other
    rules' declarations 'renamed', live run 16)."""
    win = rename_window(lines, hunk)
    return renamed_pairs(lines, removed, *win, exclude=added) if win else []


def renamed_removal(body, hunk, notes=None):
    """Remove a block whose lines upstream renamed inside: every specific removed line is matched, uniquely and in
    order, to its renamed (or exact) form near the hunk's frame; the run from the first to the last goes. Only for
    hunks that add nothing but comments: an added CODE line would carry the OLD names into the new file, which no
    tool may do (that case is deferred to a person with the renames listed)."""
    removed, added, _ = hunk_sides(hunk)
    if any(l.strip() and not l.strip().startswith(("//", "/*", "*", "#")) for l in added):
        raise Ambiguous("the hunk adds code lines, which would need the renaming applied to them")
    spec = [l for l in removed if _specific(_key(l))]
    if not spec:
        raise Ambiguous("no specific removed lines")
    # the frame comes from the context lines; the block may lie before them (trailing context only), so the
    # window reaches one hunk length either side, and every match must be unique inside it
    win = rename_window(body, hunk)
    if not win:
        raise Ambiguous("the hunk's context cannot be pinned in the file")
    lo, hi = win
    idx, pos = [], lo
    for l in spec:
        k = _key(l)
        exact = [(i, 1) for i in range(lo, hi) if _key(body[i]) == k]
        ren = renamed_candidates(body, l, lo, hi, added)
        if any(n > 1 for _, n in ren):
            raise Ambiguous(f"upstream re-wrapped `{l.strip()[:50]}` over several lines")
        cands = sorted(set(exact + ren))
        if len(cands) != 1:
            raise Ambiguous(f"{len(cands)} lines near the frame could be `{l.strip()[:50]}`")
        i, n = cands[0]
        if i < pos:
            raise Ambiguous("the removed lines are not in the hunk's order here")
        idx.append((i, n))
        pos = i + n
    if not any(renamed_candidates(body, l, lo, hi, added) for l in spec):
        raise Ambiguous("nothing is renamed here")
    first, last = idx[0][0], idx[-1][0] + idx[-1][1] - 1
    while removed and removed[0].strip().startswith("//") and first > 0 and body[first - 1].strip().startswith("//"):
        first -= 1
    # the hunk's trivial edge lines (`);`, `}`, blanks before and after the block) go with it, matched one by one:
    # specific lines alone would leave `);` and `}` dangling (h5: 5 of 8 lines) and the file would not parse
    spec_keys = {_key(l) for l in spec}
    lead = [l.strip() for l in removed[:next((i for i, l in enumerate(removed) if _key(l) in spec_keys), 0)]]
    tail = [l.strip() for l in removed[len(removed) - next((i for i, l in enumerate(reversed(removed)) if _key(l) in spec_keys), 0):]]
    for want in reversed(lead):
        if first > 0 and body[first - 1].strip() == want and (TRIVIAL.match(want) or not want):
            first -= 1
    for want in tail:
        if last + 1 < len(body) and body[last + 1].strip() == want and (TRIVIAL.match(want) or not want):
            last += 1
    region = body[first:last + 1]
    if len(region) > len(removed) + GAP:
        raise Ambiguous(f"the matched lines span {len(region)} lines for a {len(removed)}-line removal")
    toks = set().union(*(_idents(l) for l in removed))
    rkeys = {_key(r) for r in removed}
    for l in region:
        k = l.strip()
        if not k or TRIVIAL.match(k) or _key(l) in rkeys or _idents(l) & toks:
            continue
        raise Ambiguous(f"a line inside the block is not the hunk's: {k[:60]}")
    new = body[:first] + added + body[last + 1:]
    if notes is not None:
        pairs = renamed_pairs(body, spec, lo, hi)
        notes.append(f"removed the block in its renamed form ({len(region)} lines in the file, {len(removed)} in the hunk): "
                     + "; ".join(f"`{a[:50]}` is now `{b[:50]}`" for a, b in pairs[:3]))
    return new, region


_OPENER = re.compile(r"^\s*(?:[\w$.]+|\"[^\"]+\"|'[^']+')\s*[:=]\s*\{\s*$|^\s*[\w$.]+\(\s*$")
_CLOSER = re.compile(r"^\s*[\}\)][,;]?\s*$")
_PAIRS = (("{", "}"), ("(", ")"))


def block_removal(body, hunk, notes=None):
    """Remove one whole brace-balanced block the hunk removes, found in the new file by its opener line.

    Live run 16 (2026-10-01, ActorManagerParent h1): the hunk deletes the `MLEngine: { ... },` actor entry (a comment
    line, the opener, nine inner lines, the closer). Firefox 157 re-wrapped two inner lines (`esModuleURI:` on its own
    line, the URI below it, now moz-src://), so no line tier matched and Gemma, asked line by line, left `child: {`
    behind twice. The block is still one block: its opener occurs once, its braces balance; the whole of it goes,
    and the hunk's '+' lines take its place. Only the single-change-block, opener-to-closer shape is handled."""
    blocks = change_blocks(hunk)
    if len(blocks) != 1 or not blocks[0]["-"]:
        raise Ambiguous("not a single removed block")
    rem = [l for l in blocks[0]["-"]]
    lead = 0
    while lead < len(rem) and rem[lead].strip().startswith("//"):
        lead += 1
    core = rem[lead:]
    if len(core) < 3 or not _OPENER.match(core[0]) or not _CLOSER.match(core[-1]):
        raise Ambiguous("the removed lines are not one `name: { ... }` block")
    o, c = next((pair for pair in _PAIRS if core[0].rstrip().endswith(pair[0])), _PAIRS[0])
    if sum(l.count(o) - l.count(c) for l in core) != 0:
        raise Ambiguous("the removed block's brackets do not balance")
    opener = _key(core[0])
    at = [i for i, l in enumerate(body) if _key(l) == opener]
    if len(at) != 1:
        raise Ambiguous(f"the block's opener occurs {len(at)} times in the file")
    start = at[0]
    depth, end = 0, None
    for i in range(start, min(len(body), start + 2 * len(core) + 8)):
        depth += body[i].count(o) - body[i].count(c)
        if depth == 0:
            end = i
            break
    if end is None or not _CLOSER.match(body[end]):
        raise Ambiguous("the block in the file does not close where expected")
    # the comment lines directly above the opener belong to the block when the hunk removed comment lines too
    first = start
    while lead and first > 0 and body[first - 1].strip().startswith("//"):
        first -= 1
    inner_keys = {_key(l) for l in core[1:-1] if _specific(_key(l))}
    found = sum(1 for l in body[start + 1:end] if _key(l) in inner_keys)
    if inner_keys and found * 2 < len(inner_keys):
        raise Ambiguous(f"the block in the file shares only {found} of {len(inner_keys)} specific lines with the hunk's")
    new = body[:first] + blocks[0]["+"] + body[end + 1:]
    if notes is not None:
        notes.append(f"removed the whole `{core[0].strip()}` block ({end + 1 - first} lines in the file, "
                     f"{len(rem)} in the hunk) and put the hunk's {len(blocks[0]['+'])} added line(s) in its place")
    return new


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
    if renamed_near(body, hunk, removed, added):
        return None                                     # still there, renamed: a port, not an obsolete change
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

    # Fluent files are ported by MESSAGE, never by line (live run 11, browser.ftl h30)
    if file.endswith(".ftl"):
        from . import fluent
        try:
            new, fnotes, gone = fluent.port(body, hunk)
        except fluent.Ambiguous as e:
            fnotes, gone, new = [f"fluent: {e}"], [], None
        if gone:
            # the owner's wording carried onto the renamed message, under strict rules; the rest to the owner
            new2, mapping, tnotes, gone = fluent.transfer(body, hunk, gone)
            fnotes += tnotes
            if mapping:
                s["fluent_map"] = mapping
                new = new2
        if gone:
            why = "; ".join(f"message '{i}' no longer exists" + (f" (upstream may have renamed it: {', '.join(c[:3])})" if c else "")
                            for i, c in gone)
            return {"ok": False, "defer": True, "obsolete": not any(c for _, c in gone),
                    "why": [why + ". Not a job for a model; the owner decides where the wording goes"]}
        if new is not None:
            if new != body:
                target.write_text(nl.join(new) + (nl if trailing else ""), encoding="utf-8", newline="")
            return {"ok": True, "why": [], "notes": fnotes}
        notes += fnotes

    # Keyed files (.properties / .dtd / .ini) are ported by KEY
    from . import keyed
    if keyed.kind(file):
        new, knotes, gone = keyed.port(body, hunk, file)
        if gone and not gone[0][0].startswith("(new keys"):
            why = "; ".join(f"key '{k}' no longer exists" + (f" (upstream may have renamed it: {', '.join(c[:3])})" if c else "")
                            for k, c in gone)
            return {"ok": False, "defer": True, "obsolete": not any(c for _, c in gone),
                    "why": [why + ". Not a job for a model; the owner decides where the value goes"]}
        if not gone:
            if new != body:
                target.write_text(nl.join(new) + (nl if trailing else ""), encoding="utf-8", newline="")
            return {"ok": True, "why": [], "notes": knotes}
        notes += knotes                                           # new keys: the merge below places them

    # Preference files are ported by PREF NAME when the hunk only sets or drops prefs (new prefs need a place:
    # those go through the merge below)
    if Path(file).name in PREF_FILES:
        from . import prefs
        ch = prefs.changes(hunk)
        if (ch["set"] or ch["drop"]) and not ch["add"]:
            new, pnotes, gone = prefs.port(body, hunk)
            if gone:
                why = "; ".join(f"pref '{n}' " + (c[0] if c and c[0].startswith("defined") else "no longer exists" +
                                                   (f" (upstream may have renamed it: {', '.join(c[:3])})" if c else "")) for n, c in gone)
                return {"ok": False, "defer": True, "obsolete": not any(c for _, c in gone),
                        "why": [why + ". Not a job for a model; the owner decides"]}
            if new != body:
                target.write_text(nl.join(new) + (nl if trailing else ""), encoding="utf-8", newline="")
            return {"ok": True, "why": [], "notes": pnotes}

    # Tier 0: nothing to do (upstream already has it, or an earlier attempt landed and the record was reopened)
    if already_upstream(body, hunk):
        return {"ok": True, "why": [], "notes": ["already in place: the added lines are there and the removed ones gone"]}

    gone = obsolete_upstream(body, hunk)
    if gone:
        return {"ok": False, "defer": True, "obsolete": True, "why": [gone]}

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

    # Tier 2b: a whole brace-balanced block removed (upstream re-wrapped lines inside it)
    try:
        new = block_removal(body, hunk, notes)
        target.write_text(nl.join(new) + (nl if trailing else ""), encoding="utf-8", newline="")
        return {"ok": True, "why": [], "notes": notes}
    except Ambiguous as e:
        notes.append(f"block: {e}")

    # Tier 2c: the removed lines are there under names upstream changed
    try:
        new, region = renamed_removal(body, hunk, notes)
        s["renamed_removals"] = region
        target.write_text(nl.join(new) + (nl if trailing else ""), encoding="utf-8", newline="")
        return {"ok": True, "why": [], "notes": notes}
    except Ambiguous as e:
        notes.append(f"renamed: {e}")
    pairs = renamed_near(body, hunk, hunk_sides(hunk)[0], hunk_sides(hunk)[1])
    if pairs:
        return {"ok": False, "defer": True,
                "why": ["upstream renamed identifiers inside this hunk (" + "; ".join(f"`{a[:40]}` is now `{b[:40]}`" for a, b in pairs[:3])
                        + "); its added lines must be rewritten with the new names by a person, never by a model"]}

    # Tier 3: merge by key (removals matched line by line, the '+' block placed after its context line)
    try:
        new = auto_merge(body, hunk, notes)
        target.write_text(nl.join(new) + (nl if trailing else ""), encoding="utf-8", newline="")
        return {"ok": True, "why": [], "notes": notes}
    except PlacementGone as e:
        return {"ok": False, "defer": True, "why": [f"{e}. Not a job for a model; the owner decides whether the change still applies"]}
    except Ambiguous as e:
        notes.append(f"merge: {e}")

    where = moved_where(body, hunk, file)
    if where:
        notes.append(where)
    return {"ok": False, "why": ["transplant, auto-substitute and merge all failed: " + "; ".join(notes)]}


_TOKEN = re.compile(r"[A-Za-z_$][\w$]{5,}|\"[^\"]{4,}\"|'[^']{4,}'|`[^`]{4,}`")


def hand_keeps(note):
    """The removed lines a hand port keeps on purpose, declared in the submit note as `keeps: <line>` (one per
    line or `;`-separated). ml/moz.build h1 (live run 16): upstream added `"ipc",` inside the `if ... android`
    block the owner's hunk removes; the port must keep the `if` and drop only backends/llama."""
    out = []
    for chunk in re.split(r"[\n;]", note or ""):
        m = re.match(r"\s*keeps?:\s*(.+?)\s*$", chunk)
        if m:
            out.append(m.group(1).strip())
    return out


def hand_port_check(before, after, hunk, pristine=None, keeps=()):
    """A PERSON ported this hunk by hand (not a model): the shape may differ from the patch, the meaning may not.
    Required: every specific removed line is gone (from the frame when it can be pinned, else the file); every
    distinctive token of the added lines (identifiers of 6+ chars, quoted strings) is present near the change;
    nothing was removed outside the hunk. PanelTestProvider h2 (2026-10-01): the owner's change moved from an
    object literal in getMessages() into tagMessageForTesting(); a literal check can never accept that, a token
    check can, and collateral still guards the rest of the file."""
    removed, added, _ = hunk_sides(hunk)
    why = []
    # the whole file: a person may legitimately port the change into another function (the real case did)
    scope_after = after
    have = {l.strip() for l in scope_after}
    # a removed line is looked for file-wide only when it has identity of its own (a distinctive identifier, not a
    # comment): `color: inherit;` and a stylelint comment live in many rules, and a hand port of browser-shared.css
    # h7 was refused for copies in other rules (live run 16). Generic lines are judged inside the hunk's window.
    # the window sits round the hunk's specific context lines that occur once in the file (`}` and a `@media` that
    # the file has six times anchor nothing: browser-shared.css h7 was refused for copies 300 lines away)
    reach = len(hunk["lines"]) + GAP
    pins = [i for l in hunk["lines"] if l.startswith(" ") and _specific(_key(l[1:]))
            for i in [[j for j, x in enumerate(after) if _key(x) == _key(l[1:])]] if len(i) == 1]
    if pins:
        lo, hi = max(0, min(p[0] for p in pins) - reach), min(len(after), max(p[0] for p in pins) + reach)
    else:
        lo, hi = 0, len(after)
    near = {l.strip() for l in after[lo:hi]}
    import collections
    cb = collections.Counter(l.strip() for l in (pristine if pristine is not None else before))
    ca = collections.Counter(l.strip() for l in after)
    cn = collections.Counter(l.strip() for l in after[lo:hi])
    want_gone = collections.Counter(l.strip() for l in removed)
    kept = {x.strip() for x in keeps}
    moved = {_key(l) for l in added}                       # removed AND added back (a block moved): not a removal
    for k, n in want_gone.items():
        if _key(k) in moved:
            continue
        if not _specific(_key(k)) or k.startswith(("/*", "//", "*", "<!--")):
            continue                                        # a comment follows its block; it proves nothing alone
        if k in kept or any(d.startswith(k) for d in kept):
            continue                                        # declared (a reason may follow the line), journaled with the submit
        wide = _judgeable_rename(k)
        here = ca[k] if wide else cn[k]
        if not here:
            continue
        # a line other blocks also use (`GeneratedFile(` opens three blocks in addons/moz.build, the hunk removes one)
        # is no evidence either way: only a line whose every copy in `before` is the hunk's must be gone. What a
        # shared opener left dangling does is caught by the parse check (syntax_problems), not by text.
        if (pristine is not None or before is not after) and cb[k] != n:
            continue
        why.append(f"line should be gone: {k[:100]}")
    # a line that already existed before the edit cannot be the renamed form of a removed line (02 Oct: the
    # Remote Settings lock removed `: AppConstants.REMOTE_SETTINGS_SERVER_URLS[0];` and the check pointed at the
    # pre-existing `AppConstants.REMOTE_SETTINGS_SERVER_URLS.includes(...)` as its new name)
    pre = {l.strip() for l in (pristine if pristine is not None else before)}
    for a, b in [(a, b) for a, b in renamed_near(after, hunk, removed, added) if b.strip() not in pre][:3]:
        why.append(f"line still there under new names: `{a[:60]}` is now `{b[:60]}`")
    text_after = "\n".join(scope_after)
    norm = lambda tok: tok.strip("\"'`").lstrip("_#$")       # `this._x`, `this.#x` and `lazy.x` are one name (live run 16)
    for l in added:
        for tok in _TOKEN.findall(l):
            if tok not in text_after and norm(tok) not in text_after:
                why.append(f"the added text's {tok[:40]!r} is nowhere near the change")
                break
    # removals outside the hunk are collateral whatever the shape of the port: every new line is 'allowed', and
    # a removed line is allowed when it shares a distinctive token with the hunk (`message.targeting =` next to
    # the hunk's `targeting:`); a removed line with no such token (`other() {`) is damage
    hunk_tokens = {norm(tok) for l in removed + added for tok in _TOKEN.findall(l)}
    import difflib
    allowed = []
    sm = difflib.SequenceMatcher(None, [l.strip() for l in before], [l.strip() for l in after], autojunk=False)
    for op, i1, i2, _, _ in sm.get_opcodes():           # runs of removed lines, as the diff sees them: a token-less
        if op not in ("delete", "replace"):             # line (`if (`, `) {`) inside a run that carries the hunk's
            continue                                    # tokens goes with it (Tabbrowser h4, live run 16)
        run = [l for l in before[i1:i2] if l.strip()]
        if any({norm(t) for t in _TOKEN.findall(x)} & hunk_tokens for x in run):
            allowed += [x for x in run if {norm(t) for t in _TOKEN.findall(x)} & hunk_tokens or not _TOKEN.findall(x)]
    new_lines = [l for l in after if l.strip() and l.strip() not in {b.strip() for b in before}]
    extra = collateral(before, after, {"lines": [l for l in hunk["lines"] if not l.startswith("+")] + ["+" + l for l in new_lines]},
                       extra_removals=allowed)
    why += [w for w in extra if w.startswith("you removed")]
    return why


def hand_port_holds(now, hunk, pristine=None, keeps=()):
    """Does a hand port's result still stand in `now`? The meaning check (specific removed lines gone, the added
    text's tokens present) without the collateral part: later hunks touch the same file. Used by the verifier and
    the final re-check for steps done by hand (live run 16: the literal verifier reopened four hand ports, the tiers
    re-ran on them and one Fluent port removed the owner's moved message a second time). With `pristine` (the
    upstream file) removals are judged by count, pristine -> now."""
    return [w for w in hand_port_check(pristine if pristine is not None else now, now, hunk, pristine, keeps)
            if not w.startswith("you removed")]


def check_port(t, s, patch, file, hunk, **kw):
    target = Path(t["workdir"]) / file
    if not target.is_file():
        return {"ok": False, "why": [f"{file} does not exist; this hunk needs the owner"]}
    if isinstance(hunk, dict) and hunk.get("binary"):              # a binary hand edit is judged by its bytes
        import hashlib
        got = hashlib.sha256(target.read_bytes()).hexdigest()
        return {"ok": got == hunk.get("sha256"), "why": [] if got == hunk.get("sha256") else
                [f"{file} is not the recorded binary (sha256 {got[:12]} vs {str(hunk.get('sha256'))[:12]})"]}
    before = subprocess.run(["git", "-C", t["workdir"], "show", f"HEAD:{file}"], capture_output=True).stdout.decode(
        "utf-8", "replace").splitlines()
    after = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if s.get("hand_port"):
        root = subprocess.run(["git", "-C", t["workdir"], "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.split()
        pristine = subprocess.run(["git", "-C", t["workdir"], "show", f"{root[0]}:{file}"], capture_output=True).stdout.decode(
            "utf-8", "replace").splitlines() if root else None
        why = hand_port_check(before, after, hunk, pristine or None, keeps=hand_keeps(s.get("hand_note")))
        return {"ok": not why, "why": why[:8]}
    if file.endswith(".ftl"):
        from . import fluent
        try:
            why = fluent.check(before, after, hunk, {k: tuple(v) for k, v in (s.get("fluent_map") or {}).items()})
            return {"ok": not why, "why": why[:8]}
        except fluent.Ambiguous:
            pass                                            # fall through to the line-level check
    if Path(file).name in PREF_FILES:
        from . import prefs
        ch = prefs.changes(hunk)
        if (ch["set"] or ch["drop"]) and not ch["add"]:
            why = prefs.check(before, after, hunk)
            return {"ok": not why, "why": why[:8]}
    from . import keyed
    if keyed.kind(file):
        why = keyed.check(before, after, hunk, file)
        return {"ok": not why, "why": why[:8]}
    if before == after and already_upstream(after, hunk):
        # nothing was changed because nothing needed changing (tier 0 / a reopened step whose result had landed):
        # the net-count check below would read the present lines as 'missing' (live run 10, h39)
        return {"ok": True, "why": []}
    # No flag, answer key or other shortcut may skip these checks (the overnight run of 2026-10-01 did).
    why = hunk_problems(before, after, hunk)
    extra = list(s.get("question_removals") or []) + list(s.get("renamed_removals") or [])  # driver / tier 2c
    why += collateral(before, after, hunk, extra_removals=extra or None)
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
    # removals are judged inside the hunk's CONTEXT frame: `color: inherit;` living in another rule made a done
    # removal look undone and parked it (live run 15, h15/h16). One anchor only -> as far as the hunk reaches.
    frame = _span(file_lines, {"lines": [l for l in hunk["lines"] if not l.startswith("-")]}) if meaningful_removed else None
    if frame:
        lo, hi = frame
        if hi - lo <= 1:
            hi = min(len(file_lines), lo + len(hunk["lines"]) + 5)
        here = [l.strip() for l in file_lines[lo:hi]]
    else:
        here = a
    if renamed_near(file_lines, hunk, meaningful_removed, added):
        return False                                    # the removed lines are still there under new names
    if meaningful_added and misplaced(file_lines, hunk):
        return False                                    # the lines exist, but not where the hunk puts them (h5, live run 16)
    return bool(meaningful_added or meaningful_removed) and all(k in a for k in meaningful_added) and \
        not any(k in here for k in meaningful_removed)


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
