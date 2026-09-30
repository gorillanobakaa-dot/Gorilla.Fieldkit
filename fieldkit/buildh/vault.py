"""vault - an untouched copy of every upstream source, to verify against and restore from.

Same idea as the Debian box's Kernel.Vault.Do.Not.Delete: the source exactly as fetched,
never worked in, set read-only, and a fresh working copy can be made from it at any time.

  Firefox: a git copy of the release tag (shallow, one commit). Verifying is instant and
           exact: HEAD must be the recorded commit and `git status` must show nothing.
  Kernel:  the kernel.org tarball, checked against kernel.org's sha256sums.asc.

    fieldkit build-harness vault measure firefox          size of the download, before downloading
    fieldkit build-harness vault fetch firefox|kernel     latest stable -> vault (read-only)
    fieldkit build-harness vault verify firefox|kernel [--version V]
    fieldkit build-harness vault restore firefox|kernel WORKDIR [--version V]
    fieldkit build-harness vault list

Layout: <vault root>/<product>/<version>/ plus vault.json (what, from where, when, commit/sha256).
The vault root is ${LOCAL:vault.root} or <Fieldkit>/vault; it is never inside a working copy.
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path

from ..core import settings
from . import upstream


def root():
    try:
        raw = settings.expand("${LOCAL:vault.root}")
    except (KeyError, ValueError):
        return settings.ROOT / "vault"
    p = Path(settings.expand(raw))                   # the setting may itself use ${DOCUMENTS} etc.
    if "${" in str(p) or not p.is_absolute():
        raise ValueError(f"vault.root must expand to an absolute path, got {p}")
    return p


def _git(*args, cwd=None, timeout=7200):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}...: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def _dir(product, version, base=None):
    return Path(base or root()) / product / version


def _meta(d):
    f = d / "vault.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def _write_meta(d, meta):
    (d / "vault.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


def _readonly(path):
    """Mark every file read-only. Directories stay listable; git's own index is left writable
    so `git status` can refresh its cache, which changes no source file."""
    n = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            p = os.path.join(dirpath, f)
            if os.path.basename(p) == "index" and ".git" in Path(p).parts:
                continue
            os.chmod(p, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            n += 1
    return n


# -- Firefox (git) -------------------------------------------------------------------

ARCHIVE = "https://archive.mozilla.org/pub/firefox/releases/{v}/source/firefox-{v}.source.tar.xz"


def measure_firefox(info=None, opener=urllib.request.urlopen):
    """Size of the release's source, read from Mozilla's archive with a HEAD request: no download.

    (2026-09-30: an earlier version listed file sizes from a partial git clone; git fetched
    every file to learn its size, so "measuring" quietly downloaded the whole source.)"""
    info = info or upstream.latest_firefox()
    url = ARCHIVE.format(v=info["version"])
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "fieldkit-build-harness"})
    with opener(req, timeout=60) as r:
        size = int(r.headers.get("Content-Length") or 0)
    return {**info, "archive": url, "bytes": size,
            "note": "the compressed source archive; a shallow git fetch of the tag is of the same order"}


def fetch_firefox(info=None, base=None):
    info = info or upstream.latest_firefox()
    d = _dir("firefox", info["version"], base)
    if _meta(d):
        return {"vault": str(d), "status": "already in the vault", **_meta(d)}
    tmp = d.with_name(d.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    _git("clone", "--quiet", "--depth", "1", "--branch", info["tag"], info["source"], str(tmp))
    head = _git("rev-parse", "HEAD", cwd=tmp).strip()
    if info.get("commit") and head != info["commit"]:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"fetched {head[:12]}, but the tag {info['tag']} should be {info['commit'][:12]}")
    tree = _git("rev-parse", "HEAD^{tree}", cwd=tmp).strip()
    tmp.rename(d)
    meta = {**info, "commit": head, "tree": tree, "fetched": time.strftime("%Y-%m-%d %H:%M:%S"),
            "seconds": round(time.time() - t0), "kind": "git"}
    _write_meta(d, meta)
    meta["read_only_files"] = _readonly(d)
    _write_meta_ro(d, meta)
    return {"vault": str(d), "status": "fetched", **meta}


def _write_meta_ro(d, meta):
    f = d / "vault.json"
    os.chmod(f, stat.S_IREAD | stat.S_IWRITE)
    _write_meta(d, meta)
    os.chmod(f, stat.S_IREAD)


# -- Kernel (tarball) ----------------------------------------------------------------

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_kernel(info=None, base=None, opener=urllib.request.urlopen):
    from ..build.kernel import expected_sha256
    info = info or upstream.latest_kernel()
    d = _dir("kernel", info["version"], base)
    if _meta(d):
        return {"vault": str(d), "status": "already in the vault", **_meta(d)}
    tmp = d.with_name(d.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    name = info["tarball"].rsplit("/", 1)[1]
    with opener(info["tarball"], timeout=600) as r, open(tmp / name, "wb") as out:
        shutil.copyfileobj(r, out)
    with opener(info["sums"], timeout=60) as r:
        sums = r.read().decode("utf-8", "replace")
    want, got = expected_sha256(sums, name), _sha256(tmp / name)
    if want != got:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"{name}: sha256 {got[:12]} does not match kernel.org's {str(want)[:12]}")
    tmp.rename(d)
    meta = {**info, "file": name, "sha256": got, "fetched": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "tarball"}
    _write_meta(d, meta)
    _readonly(d)
    return {"vault": str(d), "status": "fetched", **meta}


# -- verify / restore / list ----------------------------------------------------------

def _pick(product, version=None, base=None):
    pd = Path(base or root()) / product
    versions = sorted((p.name for p in pd.iterdir() if (p / "vault.json").is_file()), key=_vkey) if pd.is_dir() else []
    if not versions:
        raise FileNotFoundError(f"no {product} in the vault ({pd}); run: fieldkit build-harness vault fetch {product}")
    v = version or versions[-1]
    if v not in versions:
        raise FileNotFoundError(f"{product} {v} is not in the vault; it has {versions}")
    return _dir(product, v, base)


def _vkey(v):
    return [int(x) if x.isdigit() else 0 for x in v.replace("-", ".").split(".")]


def verify(product, version=None, base=None):
    d = _pick(product, version, base)
    m = _meta(d)
    problems = []
    if m["kind"] == "git":
        head = _git("rev-parse", "HEAD", cwd=d).strip()
        if head != m["commit"]:
            problems.append(f"HEAD is {head[:12]}, the vault recorded {m['commit'][:12]}")
        dirty = [l for l in _git("status", "--porcelain", "--untracked-files=all", cwd=d).splitlines()
                 if l[3:] != "vault.json"]
        if dirty:
            problems.append(f"{len(dirty)} file(s) changed or added, e.g. {dirty[0][3:]}")
    else:
        got = _sha256(d / m["file"])
        if got != m["sha256"]:
            problems.append(f"{m['file']} sha256 {got[:12]} is not the recorded {m['sha256'][:12]}")
    return {"vault": str(d), "product": product, "version": m["version"], "intact": not problems,
            "problems": problems}


def restore(product, workdir, version=None, base=None):
    """A fresh working copy from the vault. Refuses to overwrite an existing folder."""
    d = _pick(product, version, base)
    m = _meta(d)
    workdir = Path(workdir)
    if workdir.exists() and any(workdir.iterdir()):
        raise FileExistsError(f"{workdir} is not empty; restore makes a FRESH copy - move or remove it first")
    if Path(os.path.abspath(workdir)).is_relative_to(os.path.abspath(Path(base or root()))):
        raise ValueError("a working copy must never be inside the vault")
    check = verify(product, m["version"], base)
    if not check["intact"]:
        raise RuntimeError(f"the vault itself is damaged, refusing to copy it: {check['problems']}")
    workdir.parent.mkdir(parents=True, exist_ok=True)
    if m["kind"] == "git":
        _git("clone", "--quiet", "--no-hardlinks", str(d), str(workdir))
        for f in workdir.rglob("*"):
            if f.is_file() and not os.access(f, os.W_OK):
                os.chmod(f, stat.S_IREAD | stat.S_IWRITE)
    else:
        workdir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(d / m["file"]) as t:
            t.extractall(workdir, filter="data")
    return {"workdir": str(workdir), "from": str(d), "product": product, "version": m["version"],
            "commit": m.get("commit"), "sha256": m.get("sha256")}


def listing(base=None):
    out = []
    r = Path(base or root())
    for m in sorted(r.glob("*/*/vault.json")):
        meta = json.loads(m.read_text(encoding="utf-8"))
        out.append({"product": meta["product"], "version": meta["version"], "fetched": meta.get("fetched"),
                    "id": meta.get("commit") or meta.get("sha256"), "path": str(m.parent)})
    return out
