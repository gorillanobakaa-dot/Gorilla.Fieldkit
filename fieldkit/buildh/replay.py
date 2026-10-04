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
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

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


def _targets(pset, groups):
    """Every path the enabled groups touch (patch targets, new, replaced, deleted)."""
    out = set()
    for g, spec in groups:
        ex = set(spec.get("exclude", []))
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
        r = subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "-p1", "--whitespace=nowarn", str(pf)],
                           cwd=str(scratch), capture_output=True,
                           env={**ENV, "GIT_CEILING_DIRECTORIES": str(Path(scratch).parent)})
    else:
        with open(pf, "rb") as fh:
            r = subprocess.run([firefox._patch_exe(), "-p1", "--forward", "--no-backup-if-mismatch", "--fuzz=0",
                                "-d", str(scratch)], stdin=fh, capture_output=True, timeout=600)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    return r.returncode == 0 and "previously applied" not in out, out


def replay(owner_root, workdir, say=print, keep=False):
    """-> {ok, replayed, built, pristine, failures, dirty, differ, patches, scratch}."""
    w = Path(workdir)
    pset, policy = firefox._policy(owner_root)
    groups = [(g, s) for g, s in policy.items() if s.get("status") == "enabled"]
    pristine = _git(w, "rev-list", "--max-parents=0", "HEAD").decode().split()[0]
    built = _git(w, "rev-parse", "HEAD^{tree}").decode().strip()
    dirty = [l for l in _git(w, "status", "--porcelain", "--untracked-files=no").decode("utf-8", "replace").splitlines() if l.strip()]
    scratch = Path(tempfile.mkdtemp(prefix="greplay_"))
    res = {"ok": False, "built": built, "pristine": pristine, "failures": [], "dirty": dirty[:20], "differ": [],
           "patches": 0, "scratch": str(scratch)}
    try:
        paths = _targets(pset, groups)
        modes = _materialise(w, pristine, paths, scratch / "t")
        say(f"  replay: {len(paths)} path(s) touched by {len(groups)} group(s); {len(modes)} taken from pristine {pristine[:10]}")
        t = scratch / "t"
        t.mkdir(exist_ok=True)
        for g, spec in groups:
            ex = set(spec.get("exclude", []))
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
        res["replayed"] = _git(w, "write-tree", env=env).decode().strip()
        if res["replayed"] != built:
            res["differ"] = _git(w, "diff-tree", "-r", "--name-status", res["replayed"], built).decode("utf-8", "replace").splitlines()[:40]
        res["ok"] = not res["failures"] and not dirty and res["replayed"] == built
    finally:
        if not keep:
            shutil.rmtree(scratch, ignore_errors=True)
    return res


def run(task_id, say=print):
    from . import buildrun
    t = task.load(task_id)
    owner = Path(buildrun._owner_root(t))
    r = replay(owner, t["workdir"], say=say)
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
