"""Gather the owner's tools from GitHub repos and local folders into toolbox/.

Driven entirely by imports.yaml. Deterministic and re-runnable:

    fieldkit gather                 fetch/copy everything, write provenance, scan
    fieldkit gather --only NAME...  just these sources
    fieldkit gather --check         offline check: compare toolbox/ with the sources as they
                                    already are on disk; no network, nothing written
    fieldkit gather --offline       use the clones already in _sources/repos

For each source it writes toolbox/<name>/PROVENANCE.json: source, commit (for
git sources), the time, and a sha256 per file. After copying it checks every
.py file compiles and privacy-scans the new files. A finding is reported, not
hidden; nothing is ever pushed anywhere. A failed `git pull` or clone is an
error for that source (gather exits non-zero): the old copy is never used
silently as if it were current.
"""
import fnmatch
import hashlib
import json
import py_compile
import shutil
import subprocess
import time
from pathlib import Path

from .core import privacy, settings

ROOT = settings.ROOT
MANIFEST = ROOT / "imports.yaml"
LOCAL_MANIFEST = ROOT / "local" / "imports.yaml"      # private sources, git-ignored
TOOLBOX = ROOT / "toolbox"
CLONES = ROOT / "_sources" / "repos"


def load_manifest():
    data = settings.read_file(MANIFEST)
    defaults = data.get("defaults", {})
    sources = list(data["sources"])
    if LOCAL_MANIFEST.is_file():
        sources += settings.read_file(LOCAL_MANIFEST).get("sources") or []
    out = []
    for s in sources:
        s = settings.expand(s)
        s["exclude"] = list(defaults.get("exclude", [])) + list(s.get("exclude", []))
        if bool(s.get("github")) == bool(s.get("local")):
            raise ValueError(f"source {s.get('name')}: needs exactly one of github/local")
        out.append(s)
    names = [s["name"] for s in out]
    if len(set(names)) != len(names):
        raise ValueError("duplicate source names in imports.yaml / local/imports.yaml")
    return out


def _match(rel, patterns):
    """fnmatch, where '*' crosses folders; './pat' matches the top level only."""
    for p in patterns:
        if p.startswith("./"):
            if "/" not in rel and fnmatch.fnmatch(rel, p[2:]):
                return True
        elif fnmatch.fnmatch(rel, p) or (p.startswith("**/") and fnmatch.fnmatch(rel, p[3:])):
            return True
    return False


def select(root, include, exclude):
    """Files under root matching include and not exclude, as sorted relative posix paths."""
    root = Path(root)
    picked = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if _match(rel, include) and not _match(rel, exclude):
            picked.append(rel)
    return sorted(picked)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _commit(root):
    """Read-only: the short HEAD commit of a git folder, or None."""
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    except OSError:
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def existing_root(src):
    """-> (root path, commit or None) for what is ALREADY on disk. No network, nothing written."""
    root = Path(src["local"]) if src.get("local") else CLONES / src["name"]
    if not root.is_dir():
        if src.get("local"):
            raise FileNotFoundError(f"{src['name']}: {root} not found")
        raise FileNotFoundError(f"{src['name']}: no clone at {root} yet (run gather, online, to fetch it)")
    return root, _commit(root)


def source_root(src, offline=False):
    """-> (root path, commit or None). Clones/updates GitHub sources shallowly.

    A failed pull or clone raises RuntimeError: the old clone is never passed off as current."""
    if src.get("local") or offline:
        return existing_root(src)
    root = CLONES / src["name"]
    CLONES.mkdir(parents=True, exist_ok=True)
    if (root / ".git").is_dir():
        r = subprocess.run(["git", "-C", str(root), "pull", "-q", "--ff-only"], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"{src['name']}: git pull failed (exit {r.returncode}): "
                               f"{(r.stderr or r.stdout or '').strip()[:300]}; the old copy was NOT used")
    else:
        r = subprocess.run(["gh", "repo", "clone", src["github"], str(root), "--", "--depth", "1", "-q"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"{src['name']}: clone failed: {r.stderr.strip()}")
    return existing_root(src)


def gather_one(src, offline=False):
    root, commit = source_root(src, offline)
    files = select(root, src.get("include", ["**/*"]), src["exclude"])
    dest = TOOLBOX / src["name"]
    if dest.exists():
        shutil.rmtree(dest)                     # toolbox/<name> is fully generated; no hand edits live here
    dest.mkdir(parents=True)
    records, bad_py = {}, []
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target)
        records[rel] = _sha(target)
        if rel.endswith(".py"):
            try:
                py_compile.compile(str(target), doraise=True, cfile=str(target) + "c.tmp")
            except py_compile.PyCompileError as e:
                bad_py.append(f"{rel}: {e.msg.splitlines()[-1] if e.msg else e}")
            finally:
                Path(str(target) + "c.tmp").unlink(missing_ok=True)
    prov = {"name": src["name"], "what": src.get("what", ""),
            "source": src.get("github") or str(src["local"]), "kind": "github" if src.get("github") else "local",
            "commit": commit, "gathered": time.strftime("%Y-%m-%d %H:%M:%S"),
            "include": src.get("include"), "files": records}
    (dest / "PROVENANCE.json").write_text(json.dumps(prov, indent=1), encoding="utf-8")
    findings = privacy.scan_path(dest)
    findings.pop(str(dest / "PROVENANCE.json"), None)   # it records the local source path by design
    return {"name": src["name"], "files": len(files), "commit": commit, "compile_errors": bad_py,
            "privacy_findings": {str(Path(k).relative_to(dest)): len(v) for k, v in findings.items()}}


def check_one(src, offline=True):
    """Drift between toolbox/<name> and its source as it already is on disk (an offline check).

    Never touches the network and writes nothing: no pull, no clone, no folder created.
    `offline` is accepted for old callers and ignored - a check is always offline."""
    dest = TOOLBOX / src["name"]
    prov_file = dest / "PROVENANCE.json"
    if not prov_file.is_file():
        return {"name": src["name"], "status": "not-gathered", "mode": "offline check"}
    prov = json.loads(prov_file.read_text(encoding="utf-8"))
    try:
        root, commit = existing_root(src)
    except FileNotFoundError as e:
        return {"name": src["name"], "status": "source-unavailable", "detail": str(e), "mode": "offline check"}
    now = set(select(root, src.get("include", ["**/*"]), src["exclude"]))
    had = set(prov["files"])
    changed = sorted(r for r in now & had if _sha(root / r) != prov["files"][r])
    edited_here = sorted(r for r in had if (dest / r).is_file() and _sha(dest / r) != prov["files"][r])
    res = {"name": src["name"], "mode": "offline check",
           "added_at_source": sorted(now - had), "removed_at_source": sorted(had - now),
           "changed_at_source": changed, "edited_in_toolbox": edited_here,
           "commit_then": prov.get("commit"), "commit_now": commit}
    res["status"] = "in-sync" if not (res["added_at_source"] or res["removed_at_source"] or changed or edited_here) \
        else "drift"
    return res


def test_one(src, timeout=900):
    """Run a source's test commands. -> {'name', 'runs': [{cmd, ok, tail}], 'ok'}."""
    import sys
    cmds = src.get("tests") or []
    if not cmds:
        return {"name": src["name"], "ok": None, "runs": [], "detail": "no tests"}
    if src.get("test_in") == "source":
        cwd = Path(src["local"]) if src.get("local") else CLONES / src["name"]
    else:
        cwd = TOOLBOX / src["name"]
    runs = []
    for cmd in cmds:
        py = src.get("python") if src.get("python") and Path(src["python"]).is_file() else sys.executable
        cmd = [py if c in ("python", "python3") else c for c in cmd]
        try:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout)
            ok, tail = r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-2:]
        except subprocess.TimeoutExpired:
            ok, tail = False, [f"timed out after {timeout} s"]
        runs.append({"cmd": " ".join(cmd[1:]), "ok": ok, "tail": tail})
    return {"name": src["name"], "ok": all(r["ok"] for r in runs), "runs": runs, "where": str(cwd)}


def gather(only=None, offline=False, check=False, test=False):
    out = []
    for src in load_manifest():
        if only and src["name"] not in only:
            continue
        try:
            if test:
                out.append(test_one(src))
            else:
                out.append(check_one(src, offline=offline) if check else gather_one(src, offline=offline))
        except (FileNotFoundError, RuntimeError, ValueError) as e:
            out.append({"name": src["name"], "error": str(e)})
    return out
