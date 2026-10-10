"""Debian kernel build: the pieces, as tested functions, plus pipeline stages.

Imported from the owner's debian-kernel project (kernel_build_namer.py,
kernel_config_injector.py, the CI workflow) and made reusable:

- localversion(): the 64-character uname guard. Debian and uname cap the
  release string at 64; the running kernel was once 63/64. Tags are dropped
  from the end, never the brand prefix or the timestamp.
- apply_fragment() / verify_fragment(): set CONFIG_ options, then prove they
  survived `make olddefconfig`. CI once built a different kernel from every
  local build because the injector was skipped; olddefconfig also silently
  drops options whose dependencies are off. Verify the artefact.
- fragment_from_injector(): read MANDATORY_FLAGS out of an existing
  kernel_config_injector.py with the ast module (never executing it), so the
  audited change-set is imported, not retyped.
- sha256 check of the tarball against kernel.org's sha256sums.asc.
- Pipeline stages (deps, fetch, extract, patch, config, build, collect) for
  build/pipelines/debian-kernel.yaml. Stages that need Debian say so; on
  Windows the pipeline can still be planned and dry-run.

The change-set itself (which options) is the project's data, not Fieldkit's.
"""
import ast
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from ..core import settings

UNAME_LIMIT = 64
DEFAULT_PREFIX = ("unleashed", "gorilla")
# Build-Depends of `make bindeb-pkg` on current kernels, plus what the owner's
# CI needed in practice (libdw-dev was missing and failed every run).
BUILD_DEPS = ["build-essential", "bc", "bison", "flex", "libssl-dev", "libelf-dev", "libdw-dev",
              "libncurses-dev", "dwarves", "cpio", "rsync", "kmod", "dpkg-dev", "debhelper",
              "python3", "zstd", "libzstd-dev", "xz-utils", "curl",
              "rpm"]          # rpmbuild, for the RPM packages made from the same build (stage_rpm, 2026-10-10)


# -- naming ------------------------------------------------------------------
def slug(s):
    s = s.lower().replace("_", "-").replace(" ", "-")
    s = re.sub(r"[^a-z0-9.-]", "", s)
    return re.sub(r"-{2,}", "-", s).strip("-")


def localversion(base, tags, prefix=DEFAULT_PREFIX, year_digits=2, now=None, limit=UNAME_LIMIT):
    """-> {'localversion', 'uname', 'length', 'dropped'}; raises ValueError if nothing fits."""
    now = now or datetime.datetime.now()
    ts = now.strftime("%y.%m.%d--%H.%M" if year_digits == 2 else "%Y.%m.%d--%H.%M")
    base = slug(base)
    tags = [slug(t) for t in tags if slug(t) and slug(t) != base]
    pre = [t for t in tags if t in prefix] or list(prefix)
    feats = [t for t in tags if t not in prefix]
    dropped = []

    def assemble(fs):
        return "-" + ".".join(pre) + (("-" + "-".join(fs)) if fs else "") + "-" + ts

    lv = assemble(feats)
    while len(base) + len(lv) > limit and feats:
        dropped.append(feats.pop())
        lv = assemble(feats)
    if len(base) + len(lv) > limit:
        raise ValueError(f"cannot fit prefix+timestamp in {limit} chars ({len(base) + len(lv)})")
    return {"localversion": lv, "uname": base + lv, "length": len(base) + len(lv), "dropped": dropped}


# -- config fragments ----------------------------------------------------------
def _line(flag, value):
    return f"# {flag} is not set\n" if value == "n" else f"{flag}={value}\n"


def load_fragment(path):
    """A fragment is YAML/JSON {flags: {CONFIG_X: y|n|m|"str"|123}} or a .config-style file."""
    path = Path(path)
    if path.suffix.lower() in (".yaml", ".yml", ".json"):
        data = settings.read_file(path) or {}
        flags = data.get("flags", data)
    else:
        flags = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(CONFIG_\w+)=(.*)$", line.strip())
            n = re.match(r"^# (CONFIG_\w+) is not set$", line.strip())
            if m:
                flags[m.group(1)] = m.group(2)
            elif n:
                flags[n.group(1)] = "n"
    bad = [k for k in flags if not re.fullmatch(r"CONFIG_[A-Za-z0-9_]+", str(k))]   # e.g. CONFIG_DVB_STV090x
    if bad:
        raise ValueError(f"{path}: not CONFIG_ symbols: {bad[:5]}")
    return {k: (v if isinstance(v, str) else ("y" if v is True else "n" if v is False else str(v)))
            for k, v in flags.items()}


def fragment_from_injector(py_path):
    """Extract MANDATORY_FLAGS from a kernel_config_injector.py without running it."""
    tree = ast.parse(Path(py_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "MANDATORY_FLAGS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"{py_path}: no MANDATORY_FLAGS assignment found")


def apply_fragment(config_path, flags):
    """Set every flag in a .config. Idempotent. -> {'changed', 'added', 'total'}."""
    config_path = Path(config_path)
    if not config_path.is_file():
        raise FileNotFoundError(f"{config_path}: configure the tree first (no .config)")
    changed, added, out, seen = [], [], [], set()
    for line in config_path.read_text(encoding="utf-8").splitlines(keepends=True):
        m = re.match(r"^(?:# )?(CONFIG_\w+)(?:=| is not set)", line)
        flag = m.group(1) if m else None
        if flag in flags:
            new = _line(flag, flags[flag])
            if new != line:
                changed.append(flag)
            out.append(new)
            seen.add(flag)
        else:
            out.append(line)
    for flag, value in flags.items():
        if flag not in seen:
            out.append(_line(flag, value))
            added.append(flag)
    config_path.write_text("".join(out), encoding="utf-8")
    return {"changed": changed, "added": added, "total": len(flags)}


def verify_fragment(config_path, flags):
    """After olddefconfig: which requested flags did not survive? -> list of (flag, wanted, got)."""
    have = {}
    for line in Path(config_path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(CONFIG_\w+)=(.*)$", line)
        n = re.match(r"^# (CONFIG_\w+) is not set$", line)
        if m:
            have[m.group(1)] = m.group(2)
        elif n:
            have[n.group(1)] = "n"
    missing = []
    for flag, want in flags.items():
        got = have.get(flag, "n")          # absent = not set
        if got != want:
            missing.append((flag, want, got))
    return missing


# -- source ----------------------------------------------------------------------
def tarball_url(version):
    major = version.split(".")[0]
    return f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/linux-{version}.tar.xz"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_sha256(sums_text, filename):
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == filename and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            return parts[0]
    return None


# -- pipeline stages (called by the engine with a Context) ----------------------
def _v(ctx, key):
    return ctx.vars[key]


def stage_deps(ctx, packages=None):
    packages = packages or BUILD_DEPS
    if not shutil.which("dpkg-query"):
        return {"ok": False, "detail": "dpkg-query not found: this stage needs Debian/Ubuntu"}
    missing = []
    for p in packages:
        r = subprocess.run(["dpkg-query", "-W", "-f=${Status}", p], capture_output=True, text=True)
        if "install ok installed" not in r.stdout:
            missing.append(p)
    return {"ok": not missing, "missing": missing,
            "detail": ("all present" if not missing else "sudo apt-get install -y " + " ".join(missing))}


PROVENANCE = "linux-{version}.provenance.json"


def _provenance(work, version, data):
    p = Path(work) / PROVENANCE.format(version=version)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return p


def provenance(work, version):
    """Where the source of `version` came from, as fetch recorded it, or None."""
    p = Path(work) / PROVENANCE.format(version=version)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def stage_fetch(ctx, mirror="", opener=None):
    """The pristine source: kernel.org's tarball checked against kernel.org's sha256sums.asc, or, ONLY when asked with
    --var mirror=<git URL> because kernel.org cannot be reached, the release tag from a git mirror.

    2026-10-10: the cloud machine that migrated 7.2.9 could not reach kernel.org; the source came from the git mirror
    github.com/gregkh/linux by hand. This makes that path a step: the mirror is never used silently, the tag's commit
    is recorded in linux-<version>.provenance.json with "sha256": "not verified", and the stage says so. A later run
    that can reach kernel.org verifies the tarball and overwrites the record. The signature on sha256sums.asc is not
    checked (no keyring is assumed); the checksum list is fetched over HTTPS from kernel.org."""
    version, work = _v(ctx, "version"), Path(_v(ctx, "workdir"))
    work.mkdir(parents=True, exist_ok=True)
    name = f"linux-{version}.tar.xz"
    dest = work / name
    url = tarball_url(version)
    sums_url = url.rsplit("/", 1)[0] + "/sha256sums.asc"
    opener = opener or (lambda u: urllib.request.urlopen(u, timeout=60))
    try:
        sums = opener(sums_url).read().decode("utf-8", "replace")
        if not dest.is_file():
            with opener(url) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f)
    except OSError as e:                                   # URLError, HTTPError, a proxy's 403: kernel.org unreachable
        if not mirror:
            return {"ok": False, "detail": f"kernel.org unreachable ({str(e)[:120]}). Allow cdn.kernel.org, or take the "
                                           f"release tag from a git mirror: --var mirror=https://github.com/gregkh/linux "
                                           f"(the sha256 then stays unverified until kernel.org is reachable)"}
        return _fetch_mirror(ctx, mirror, version, work, f"kernel.org unreachable: {str(e)[:120]}")
    want = expected_sha256(sums, name)
    got = sha256_file(dest)
    if want is None:
        return {"ok": False, "detail": f"{name} not listed in {sums_url}"}
    if want != got:
        dest.rename(dest.with_suffix(".xz.bad"))
        return {"ok": False, "detail": f"sha256 mismatch: expected {want}, got {got}; file moved aside"}
    _provenance(work, version, {"source": "kernel.org", "url": url, "sha256": got, "sums": sums_url})
    return {"ok": True, "detail": f"{name} sha256 verified", "sha256": got}


def _fetch_mirror(ctx, mirror, version, work, why):
    """git fetch --depth 1 <mirror> tag v<version>; git archive into <workdir>/linux-<version>."""
    repo, tag, src = work / "mirror.git", f"v{version}", work / f"linux-{version}"
    git = lambda *a: subprocess.run(["git", "-c", "core.autocrlf=false", *a], capture_output=True, text=True)
    if not (repo / "HEAD").is_file():
        r = git("init", "-q", "--bare", str(repo))
        if r.returncode:
            return {"ok": False, "detail": f"git init failed: {r.stderr.strip()[:200]}"}
    r = git("-C", str(repo), "fetch", "-q", "--depth", "1", mirror, f"refs/tags/{tag}:refs/tags/{tag}")
    if r.returncode:
        return {"ok": False, "detail": f"{why}; the mirror has no tag {tag} or cannot be reached: {r.stderr.strip()[:200]}"}
    commit = git("-C", str(repo), "rev-parse", f"{tag}^{{commit}}").stdout.strip()
    if not (src / "Makefile").is_file():
        src.mkdir(parents=True, exist_ok=True)
        arc = subprocess.run(["git", "-C", str(repo), "archive", tag], capture_output=True)
        if arc.returncode:
            return {"ok": False, "detail": f"git archive {tag} failed: {arc.stderr.decode('utf-8', 'replace')[:200]}"}
        import io
        with tarfile.open(fileobj=io.BytesIO(arc.stdout)) as t:
            t.extractall(src, filter="data")
    _provenance(work, version, {"source": "git mirror", "mirror": mirror, "tag": tag, "commit": commit,
                                "sha256": "not verified", "why": why})
    return {"ok": (src / "Makefile").is_file(), "unverified": True, "commit": commit,
            "detail": f"UNVERIFIED: {tag} ({commit[:12]}) from {mirror}; {why}. The kernel.org sha256 is not checked "
                      f"until a run can reach kernel.org"}


def verify_fetch(ctx):
    """fetch is done when the source is there AND its provenance is recorded (a kernel.org tarball, or a mirror tag
    that says it is unverified)."""
    version, work = _v(ctx, "version"), Path(_v(ctx, "workdir"))
    pv = provenance(work, version)
    if not pv:
        return {"ok": False, "detail": "no provenance record: run the fetch stage"}
    if pv["source"] == "kernel.org":
        ok = (work / f"linux-{version}.tar.xz").is_file()
        return {"ok": ok, "detail": f"kernel.org tarball, sha256 {pv['sha256'][:16]}..."}
    ok = (work / f"linux-{version}" / "Makefile").is_file()
    return {"ok": ok, "detail": f"git mirror {pv['tag']} ({pv['commit'][:12]}), sha256 NOT verified"}


def stage_extract(ctx):
    version, work = _v(ctx, "version"), Path(_v(ctx, "workdir"))
    src = work / f"linux-{version}"
    if (src / "Makefile").is_file():
        pv = provenance(work, version) or {}
        return {"ok": True, "detail": "already extracted" + (" (from the git mirror, sha256 not verified)"
                                                             if pv.get("source") == "git mirror" else "")}
    with tarfile.open(work / f"linux-{version}.tar.xz") as t:
        t.extractall(work, filter="data")
    return {"ok": (src / "Makefile").is_file(), "detail": str(src)}


def stage_config(ctx, base_config=None, fragment=None, injector=None):
    src = Path(_v(ctx, "src"))
    if base_config:
        shutil.copyfile(base_config, src / ".config")
    elif not (src / ".config").is_file():
        return {"ok": False, "detail": "no base_config given and no .config in the tree"}
    flags = {}
    if fragment:
        flags.update(load_fragment(fragment))
    if injector:
        flags.update(fragment_from_injector(injector))
    applied = apply_fragment(src / ".config", flags)
    r = ctx.runner.run(["make", "-C", str(src), "olddefconfig"], name="olddefconfig")
    if not r.ok:
        return {"ok": False, "detail": "olddefconfig failed", "log": r.log}
    missing = verify_fragment(src / ".config", flags)
    return {"ok": not missing, "applied": applied, "log": r.log,
            "missing_after_olddefconfig": missing[:50],
            "detail": ("config change-set verified" if not missing else
                       f"{len(missing)} option(s) missing after olddefconfig: config change-set not present")}


def verify_config(ctx, fragment=None, injector=None):
    flags = {}
    if fragment:
        flags.update(load_fragment(fragment))
    if injector:
        flags.update(fragment_from_injector(injector))
    missing = verify_fragment(Path(_v(ctx, "src")) / ".config", flags)
    return {"ok": not missing, "detail": f"{len(flags) - len(missing)}/{len(flags)} options as requested"}


def stage_build(ctx, tags=("gorilla",), jobs=None, kcflags="-O3 -pipe"):
    src, version = Path(_v(ctx, "src")), _v(ctx, "version")
    name = localversion(version, list(tags))
    cmd = ["make", "-C", str(src), f"LOCALVERSION={name['localversion']}", f"-j{jobs or ctx.host['cpus']}",
           "bindeb-pkg", f"KDEB_PKGVERSION={version}"]
    r = ctx.runner.run(cmd, env={"KCFLAGS": kcflags}, name="bindeb-pkg")
    return {"ok": r.ok, "uname": name["uname"], "log": r.log, "detail": f"uname {name['uname']} ({name['length']}/64)"}


def stage_rpm(ctx, jobs=None, kcflags="-O3 -pipe"):
    """RPM packages of the kernel the build stage just compiled (2026-10-10: "a final binary nicely packaged for
    debian, rpm and all that"). The release string is read from the built tree, never made again: a second
    localversion() call carries a new timestamp, and make would rebuild everything under another name. The stage
    passes only when the RPMs carry exactly the release of the .deb packages."""
    src, version = Path(_v(ctx, "src")), _v(ctx, "version")
    if not shutil.which("rpmbuild"):
        return {"ok": False, "detail": "rpmbuild not found: sudo apt-get install -y rpm (Debian/Ubuntu), "
                                       "or dnf install rpm-build (Fedora)"}
    rel_file = src / "include" / "config" / "kernel.release"
    if not rel_file.is_file():
        return {"ok": False, "detail": f"no {rel_file}: run the build stage first"}
    release = rel_file.read_text(encoding="utf-8").strip()
    if not release.startswith(version):
        return {"ok": False, "detail": f"the built tree is {release}, not {version}"}
    cmd = ["make", "-C", str(src), f"LOCALVERSION={release[len(version):]}", f"-j{jobs or ctx.host['cpus']}",
           "binrpm-pkg"]
    r = ctx.runner.run(cmd, env={"KCFLAGS": kcflags}, name="binrpm-pkg")
    after = rel_file.read_text(encoding="utf-8").strip()
    rpms = sorted(p.name for p in (src / "rpmbuild" / "RPMS").rglob("*.rpm"))
    kernel_rpms = [n for n in rpms if n.startswith("kernel-") and release.replace("-", "_") in n]
    ok = r.ok and after == release and bool(kernel_rpms)
    return {"ok": ok, "rpms": rpms, "log": r.log, "release": after,
            "detail": (f"{len(rpms)} RPM(s) for {release}" if ok else
                       f"binrpm-pkg {'failed' if not r.ok else 'made no kernel RPM for ' + release}"
                       + (f"; the release changed to {after}" if after != release else ""))}


def stage_collect(ctx):
    work, out = Path(_v(ctx, "workdir")), Path(_v(ctx, "output"))
    out.mkdir(parents=True, exist_ok=True)
    moved = []
    for p in work.glob("*.deb"):
        shutil.move(str(p), out / p.name)
        moved.append(p.name)
    for p in (Path(_v(ctx, "src")) / "rpmbuild" / "RPMS").rglob("*.rpm"):
        shutil.move(str(p), out / p.name)
        moved.append(p.name)
    images = [p.name for p in out.glob("linux-image*.deb")]           # what is in the output, not only this run's move
    return {"ok": bool(images), "moved": moved, "rpms": sorted(p.name for p in out.glob("*.rpm")),
            "detail": f"{len(moved)} package(s); install only linux-image and linux-headers, never linux-libc-dev"}
