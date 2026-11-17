"""The replay proof: the PUBLIC patch set, applied to pristine upstream, must give exactly the tree that was compiled.

Until 2026-10-03 this was a hand procedure (patches/BASELINE.txt, "scratch git index"), and the S9 gate said so. A
hand procedure is skipped under pressure, and that day export-hand had left two stale copies of renamed patches
beside the new ones: a replay would have applied both. So it is a command now, and it records its result.

    fieldkit build-harness replay <task>

How, exactly as the port applied the set (fieldkit.buildh.firefox), group by group in policy order:
  1. every *.patch of the group, sorted by path, except the policy's excludes: GNU patch -p1 --forward
     --no-backup-if-mismatch --fuzz=0; a patch carrying a git binary hunk (a logo) goes through `git apply`,
     which has no fuzz at all
  2. NEW_FILES/<path> copied, never over a file pristine upstream already has (the port leaves those to the owner)
  3. REPLACE_FILES/<path> written byte-exact
  4. DELETED_FILES.manifest.txt: every listed path removed
Only the files the set touches are materialised (from the pristine commit, in a scratch folder); the result goes
into a scratch git index built from the pristine tree, and `git write-tree` gives the replayed tree hash. The
built tree is HEAD's tree, and the working copy must have no uncommitted change, or the comparison means nothing.
Fail closed: any patch failure, a dirty working copy or a different hash is a FAIL, with the differing paths.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..core import childrun
from . import firefox, task

ENV = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"}


def _git(w, *a, env=None, inp=None):
    r = subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(w), *a], capture_output=True, input=inp,
                       env=env or ENV)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a[:3])}: {r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return r.stdout


def _materialise(w, commit, paths, dest):
    """Write each path that exists in `commit` under `dest`. -> {path: mode} of the pristine files."""
    listing = _git(w, "ls-tree", "-r", "-z", commit).split(b"\0")
    modes = {}
    want = set(paths)
    for e in listing:
        if not e:
            continue
        meta, path = e.split(b"\t", 1)
        p = path.decode("utf-8", "surrogateescape")
        if p in want:
            mode, _, sha = meta.decode().split()
            modes[p] = (mode, sha)
    if modes:
        batch = "".join(f"{sha}\n" for _, sha in modes.values()).encode()
        out = _git(w, "cat-file", "--batch", inp=batch)
        i = 0
        for p, (mode, sha) in modes.items():
            nl = out.index(b"\n", i)
            size = int(out[i:nl].split()[2])
            data = out[nl + 1:nl + 1 + size]
            i = nl + 1 + size + 1
            f = Path(dest) / p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
    return {p: m for p, (m, _) in modes.items()}


def _targets(pset, groups, extra_exclude=None, include=None):
    """Every path the enabled groups touch (patch targets, new, replaced, deleted)."""
    out = set()
    for g, spec in groups:
        ex = (set(spec.get("exclude", [])) - set((include or {}).get(g, ()))) | set((extra_exclude or {}).get(g, ()))
        for pf in sorted((pset / g).rglob("*.patch")):
            if pf.name in ex:
                continue
            for f in firefox.parse_patch(pf.read_text(encoding="utf-8", errors="replace")):
                out.add(f["file"])
        out.update(rel for _, rel in firefox.new_files(pset, g))
        rep = pset / g / "REPLACE_FILES"
        out.update(p.relative_to(rep).as_posix() for p in rep.rglob("*") if p.is_file()) if rep.is_dir() else None
        man = pset / g / "DELETED_FILES.manifest.txt"
        if man.is_file():
            out.update(l.strip() for l in man.read_text(encoding="utf-8").splitlines() if l.strip())
    return out


def _apply_patch(pf, scratch):
    """-> (ok, output)."""
    text = pf.read_bytes()
    if b"GIT binary patch" in text:
        # outside any repository (the ceiling stops git finding one above the scratch folder), so paths are
        # relative to the scratch tree
        r = childrun.run(["git", "-c", "core.autocrlf=false", "apply", "-p1", "--whitespace=nowarn", str(pf)],
                         f"git apply {pf.name}", cwd=str(scratch),
                         env={**ENV, "GIT_CEILING_DIRECTORIES": str(Path(scratch).parent)})
    else:
        # --binary: Strawberry's patch.exe 2.5.9 aborts on LF patches read in text mode ("Expression: hunk",
        # Windows CI 2026-10-16); the bytes are compared exactly anyway. No effect on Linux.
        # --batch + childrun: never a question on a terminal, a deadline and a stall stop (P29: 67 min hang, 160)
        r = childrun.run([firefox._patch_exe(), "-p1", "--forward", "--batch", "--no-backup-if-mismatch", "--fuzz=0",
                          "--binary", "-d", str(scratch)], f"patch {pf.name}", stdin_path=pf)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    return r.returncode == 0 and "previously applied" not in out, out


def refreshed_lists(task_id, workdir):
    """{path: blob} of the security-list files whose built form is lists-refresh's output, proven from the record:
    the last commit touching the file is a recorded hand step whose reason is lists-refresh's (export.DERIVED) and
    which touches list files only. Those files are regenerated before every build and are in no patch (2026-10-18),
    so replay takes them from the built tree; any other difference still fails."""
    import json as _json
    from . import export
    from .timebombs import LIST_FILES
    notes = []
    jp = task.STATE / task_id / "journal.jsonl"
    for line in jp.read_text(encoding="utf-8").splitlines() if jp.is_file() else []:
        try:
            e = _json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "hand-edit" and export.DERIVED.match(" / ".join(e.get("why") or [])):
            notes.append(set(e.get("files") or []))
    out = {}
    for f in LIST_FILES:
        c = _git(workdir, "log", "-1", "--format=%H", "HEAD", "--", f).decode().strip()
        if not c:
            continue
        subj = _git(workdir, "show", "-s", "--format=%s", c).decode().strip()
        files = set(_git(workdir, "show", "--name-only", "--format=", c).decode().split())
        if subj.startswith("checkpoint: hand edit") and files <= set(LIST_FILES) and files in notes:
            out[f] = _git(workdir, "rev-parse", f"HEAD:{f}").decode().strip()
    return out


def unregistered_groups(owner_root):
    """-> [(group folder, patch count)] for every folder of the patch set holding *.patch files that the policy does
    not name at all (a disabled group is named, so it is a decision; an unnamed one is a loss nobody chose).

    2026-11-16 (Firefox 160): export-hand wrote 21.PORT.FIXES.160 and 22.EGRESS.LOCKDOWN.160 and only PRINTED "register
    new groups". Nobody did; for five days the build, the replay and the Windows recipe skipped them without a word -
    a privacy cut (the on-device LLM excision) and two port fixes among them. Now the replay fails and names them."""
    try:
        pol = json.loads((Path(owner_root) / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    root, named = Path(owner_root) / pol.get("patchset_root", "patches"), set(pol.get("groups") or {})
    if not root.is_dir():
        return []
    return [(d.name, n) for d in sorted(root.iterdir())
            if d.is_dir() and d.name not in named and (n := len(list(d.rglob("*.patch"))))]


def register_groups(owner_root, say=print):
    """Name every unregistered group of the set in the policy, enabled, after the groups already there, with the
    first paragraph of its README as the reason. -> the names added. export-hand made these folders from the tree that
    was built, so leaving them out is the loss; replay then proves the order (2026-11-16, Firefox 160)."""
    lost = unregistered_groups(owner_root)
    if not lost:
        return []
    f = Path(owner_root) / "config" / "patch_policy.json"
    pol = json.loads(f.read_text(encoding="utf-8"))
    root = Path(owner_root) / pol.get("patchset_root", "patches")
    for g, n in lost:
        readme = root / g / "README.md"
        text = readme.read_text(encoding="utf-8", errors="replace") if readme.is_file() else ""
        para = next((b.strip().replace("\n", " ") for b in text.split("\n\n") if b.strip() and not b.lstrip().startswith("#")), "")
        pol["groups"][g] = {"status": "enabled", "reason": (para or f"exported by build-harness export-hand ({n} patches)")[:600]
                            + " (registered by export-hand; replay proves the order)"}
        say(f"  registered {g} in config/patch_policy.json ({n} patch(es), enabled, applied after the groups before it)")
    f.write_text(json.dumps(pol, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return [g for g, _ in lost]


def replay(owner_root, workdir, say=print, keep=False, extra_exclude=None, skip=(), include=None, refreshed=None):
    """-> {ok, replayed, built, pristine, failures, dirty, differ, patches, scratch}.
    `extra_exclude` {group: {patch name}} and `skip` (group names) are for rebase.py: a replay as if the policy
    already excluded those patches and had no such group; `include` {group: {patch name}} applies patches the policy
    excludes (rebase.py recomputes what is superseded from scratch on every run)."""
    w = Path(workdir)
    pset, policy = firefox._policy(owner_root)
    groups = [(g, s) for g, s in policy.items() if s.get("status") == "enabled" and g not in skip]
    lost = unregistered_groups(owner_root)
    pristine = _git(w, "rev-list", "--max-parents=0", "HEAD").decode().split()[0]
    built = _git(w, "rev-parse", "HEAD^{tree}").decode().strip()
    dirty = [l for l in _git(w, "status", "--porcelain", "--untracked-files=no").decode("utf-8", "replace").splitlines() if l.strip()]
    scratch = Path(tempfile.mkdtemp(prefix="greplay_"))
    res = {"ok": False, "built": built, "pristine": pristine, "failures": [], "dirty": dirty[:20], "differ": [],
           "patches": 0, "scratch": str(scratch)}
    try:
        paths = _targets(pset, groups, extra_exclude, include)
        modes = _materialise(w, pristine, paths, scratch / "t")
        say(f"  replay: {len(paths)} path(s) touched by {len(groups)} group(s); {len(modes)} taken from pristine {pristine[:10]}")
        t = scratch / "t"
        t.mkdir(exist_ok=True)
        for g, spec in groups:
            ex = (set(spec.get("exclude", [])) - set((include or {}).get(g, ()))) | set((extra_exclude or {}).get(g, ()))
            n = 0
            for pf in sorted((pset / g).rglob("*.patch")):
                if pf.name in ex:
                    continue
                ok, out = _apply_patch(pf, t)
                n += 1
                if not ok:
                    res["failures"].append(f"{pf.relative_to(pset).as_posix()}: {out.strip().splitlines()[-1][:200] if out.strip() else 'failed'}")
            for src, rel in firefox.new_files(pset, g):
                if rel in modes:
                    continue                       # the port never overwrites a pristine file with a new file
                d = t / rel
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_bytes(src.read_bytes())
            rep = pset / g / "REPLACE_FILES"
            for p in sorted(rep.rglob("*")) if rep.is_dir() else []:
                if p.is_file():
                    d = t / p.relative_to(rep)
                    d.parent.mkdir(parents=True, exist_ok=True)
                    d.write_bytes(p.read_bytes())
            man = pset / g / "DELETED_FILES.manifest.txt"
            for rel in (man.read_text(encoding="utf-8").splitlines() if man.is_file() else []):
                if rel.strip() and (t / rel.strip()).is_file():
                    (t / rel.strip()).unlink()
            res["patches"] += n
            say(f"  replay: {g}: {n} patch(es){', ' + str(len(res['failures'])) + ' failure(s) so far' if res['failures'] else ''}")
        # the scratch index: pristine tree, then every touched path as the replay left it
        env = {**ENV, "GIT_INDEX_FILE": str(scratch / "index")}
        _git(w, "read-tree", pristine, env=env)
        present = sorted(p for p in paths if (t / p).is_file())
        gone = sorted(p for p in paths if not (t / p).is_file() and p in modes)
        if present:
            shas = _git(w, "hash-object", "-w", "--no-filters", "--stdin-paths",
                        inp="".join(str(t / p) + "\n" for p in present).encode("utf-8")).decode().split()
            info = "".join(f"{(modes.get(p) or '100644')} {sha}\t{p}\n" for p, sha in zip(present, shas))
            _git(w, "update-index", "--add", "--index-info", env=env, inp=info.encode("utf-8"))
        if gone:
            _git(w, "update-index", "--force-remove", "-z", "--stdin", env=env, inp="\0".join(gone).encode("utf-8") + b"\0")
        if refreshed:
            info = "".join(f"{(modes.get(p) or '100644')} {sha}\t{p}\n" for p, sha in sorted(refreshed.items()))
            _git(w, "update-index", "--add", "--index-info", env=env, inp=info.encode("utf-8"))
            res["refreshed"] = sorted(refreshed)
        res["touched"] = sorted(paths)
        res["replayed"] = _git(w, "write-tree", env=env).decode().strip()
        if res["replayed"] != built:
            res["differ"] = _git(w, "diff-tree", "-r", "--name-status", res["replayed"], built).decode("utf-8", "replace").splitlines()[:40]
        res["failures"] += [f"UNREGISTERED GROUP {g}: {n} patch(es) on disk that config/patch_policy.json does not name, so "
                            "no build or replay ever applies them; register the group (or move it out of the set)" for g, n in lost]
        res["ok"] = not res["failures"] and not dirty and res["replayed"] == built
    finally:
        if not keep:
            shutil.rmtree(scratch, ignore_errors=True)
    return res


def run(task_id, say=print):
    from .ownercheck import patchset_owner
    t = task.load(task_id)
    owner = Path(patchset_owner(t))              # the patch-set repo, Linux included (2026-10-15)
    r = replay(owner, t["workdir"], say=say, refreshed=refreshed_lists(task_id, t["workdir"]))
    for f in r.get("refreshed", []):
        say(f"  lists-refresh output (from the record, in no patch): {f}")
    task.journal(t, "replay-proof", ok=r["ok"], tree=r["built"], replayed=r.get("replayed"), pristine=r["pristine"],
                 patches=r["patches"], failures=r["failures"][:20], dirty=r["dirty"][:10], differ=r["differ"][:20])
    say(f"  replayed tree : {r.get('replayed')}")
    say(f"  built tree    : {r['built']}   (HEAD of the tree that was compiled)")
    for f in r["failures"][:20]:
        say(f"  FAILED: {f}")
    for d in r["dirty"][:10]:
        say(f"  uncommitted: {d}")
    for d in r["differ"][:20]:
        say(f"  differs: {d}")
    say(f"REPLAY {'OK' if r['ok'] else 'FAILED'}: {r['patches']} patch(es), {len(r['failures'])} failure(s)")
    return r


def materialize(owner_root, workdir, say=print):
    """Put the public patch set onto a pristine upstream work tree and commit it as one checkpoint (2026-11-16: the
    160 tree was a fresh clone of the tag, so preflight saw no branding, no stubs, no owner prefs). Same patch
    order and rules as replay. A tree that already has commits on top of the tag is left alone. -> {ok, detail}."""
    w = Path(workdir)
    if int(_git(w, "rev-list", "--count", "HEAD").decode().strip()) > 1:
        return {"ok": True, "detail": "work tree already carries checkpoints; nothing to apply"}
    r = replay(owner_root, workdir, say=say, keep=True)
    try:
        if r["failures"]:
            return {"ok": False, "detail": f"{len(r['failures'])} patch failure(s): {r['failures'][:5]}"}
        # leftovers of an earlier, half-finished run are paths this run rewrites anyway; anything else is foreign
        touched = set(r["touched"])
        dirty = [l for l in _git(w, "status", "--porcelain", "--untracked-files=no").decode("utf-8", "replace").splitlines()
                 if l.strip() and l[3:].strip().strip('"') not in touched]
        if dirty:
            return {"ok": False, "detail": f"work tree has uncommitted changes: {dirty[:5]}"}
        t = Path(r["scratch"]) / "t"
        for p in r["touched"]:
            d = w / p
            if (t / p).is_file():
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_bytes((t / p).read_bytes())
            elif d.is_file():
                d.unlink()
        # a path one patch creates and a later one deletes is in neither tree: git add fails on a pathspec that
        # matches nothing (160: browser/branding/gorilla/Assets.car), so stage only what exists or was tracked
        known = _git(w, "cat-file", "--batch-check=%(objecttype)",
                     inp="".join(f"HEAD:{p}\n" for p in r["touched"]).encode("utf-8")).decode("utf-8").splitlines()
        stage = [p for p, k in zip(r["touched"], known) if (w / p).is_file() or not k.endswith("missing")]
        if stage:
            _git(w, "add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul", inp="\0".join(stage).encode("utf-8"))
        _git(w, "-c", "user.name=fieldkit", "-c", "user.email=fieldkit@localhost", "commit", "-q", "--no-verify",
             "-m", f"checkpoint: public patch set replayed onto pristine {r['pristine'][:10]} ({r['patches']} patches)")
        return {"ok": True, "detail": f"{r['patches']} patch(es) applied over {len(r['touched'])} path(s) and committed"}
    finally:
        shutil.rmtree(r["scratch"], ignore_errors=True)


if __name__ == "__main__":
    import sys
    out = materialize(sys.argv[1], sys.argv[2])
    print(("OK: " if out["ok"] else "FAILED: ") + out["detail"])
    sys.exit(0 if out["ok"] else 1)
