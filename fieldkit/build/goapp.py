# Version: 1.1.0 · updated 26-10-25-18-40
"""Go applications built the same way every time: Gorilla OpenCode, and any other Go program.

    fieldkit pipeline run gorilla-opencode --var version=v0.1.146          (src from goapps.gorilla-opencode)
    fieldkit pipeline run go-app --var src=PATH --var name=myapp --var version_var=example.com/x/version.Version

Stages (pipelines/go-app.yaml, gorilla-opencode.yaml):
    toolchain   the `go` program found (PATH, then the usual install folders), and at least the version go.mod asks
                for. Too old or missing: the exact install line for this system, never a silent download.
    modules     go mod download + go mod verify: every dependency matches go.sum, or the build stops
    vet         go vet ./...                      a short report: the problems, not the whole log
    test        go test ./... -count=1 [-short]   the failing packages and tests, each with its first lines
    build       each target (GOOS/GOARCH), CGO off, -trimpath, stamped with the version (-ldflags -X), so the same
                commit gives the same bytes on any machine; Windows gets .exe and its committed resources (.syso)
    repro       each target built AGAIN from a copy of the source in another folder (own build cache and temp folder,
                another time zone and locale) and compared byte for byte: the binary is proven, not only the source;
                a difference is named, inside archives too (fieldkit/build/repro.py)
    published   (gorilla-opencode, --var compare_with=FOLDER|release) each binary compared byte for byte with the
                published file of the same name; a difference names the bytes and go's build info (vcs.*, flags)
    arch        (gorilla-opencode) the Arch .pkg.tar.zst (Linux only; not-this-platform elsewhere)
    packages    (gorilla-opencode) .deb/.rpm/tarball; every .deb/.xz made passes fieldkit xzcheck (--var xz_cap_mib)
    checksums   SHA256SUMS-<version>.txt for what was built

--var git_ref=COMMIT builds from a clean clone of that commit (<out>/source-<sha12>), so go records the same vcs.*
build info as the release build made in a git checkout; an export of a tag has no .git and records none.

A stage is up to date when its report matches the CURRENT source (a fingerprint of every Go file, go.mod and go.sum)
and, for builds, when every file still has the checksum and the version stamp it was built with. Changing one .go
file reruns vet, test and build; changing nothing reruns nothing. Reports live in <out>/.fieldkit/.

The version, when not given: `git describe --tags --always --dirty` in the source (v0.1.145-3-gabc1234-dirty), so a
build from uncommitted changes says so in its own --version.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..core import settings
from ..core.host import host

INSTALL = {
    "windows": "winget install --id GoLang.Go -e   (or the installer from https://go.dev/dl/), then open a new terminal",
    "debian": ("the packaged golang-go is usually too old: download go<version>.linux-amd64.tar.gz from https://go.dev/dl/, "
               "then: sudo rm -rf /usr/local/go && sudo tar -C /usr/local -xzf go<version>.linux-amd64.tar.gz "
               "&& export PATH=/usr/local/go/bin:$PATH"),
    "linux": ("download go<version>.linux-amd64.tar.gz from https://go.dev/dl/, then: sudo tar -C /usr/local -xzf "
              "go<version>.linux-amd64.tar.gz && export PATH=/usr/local/go/bin:$PATH"),
}
TEST_LINE = re.compile(r"^(ok|FAIL|---\s+FAIL:|\?)\s+(\S+)")


# -- where things are ---------------------------------------------------------------------------------------
def find_go():
    """The go program: PATH first, then the places the official installers put it."""
    exe = "go.exe" if os.name == "nt" else "go"
    found = shutil.which("go")
    if found:
        return found
    cands = [Path("/usr/local/go/bin") / exe, Path.home() / "go" / "bin" / exe, Path("/usr/lib/go/bin") / exe]
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                cands += [Path(base) / "Go" / "bin" / exe, Path(base) / "Programs" / "Go" / "bin" / exe]
    cands += sorted((Path.home() / "sdk").glob("go*/bin/" + exe), reverse=True)
    return next((str(c) for c in cands if c.is_file()), None)


def _src(ctx):
    v = ctx.vars
    src = v.get("src") or ((settings.local_settings().get("goapps") or {}).get(v.get("name", "")) or "")
    if not src:
        raise ValueError(f"no source folder: --var src=PATH, or add to fieldkit.local.json: {src_line(v.get('name'))}")
    src = Path(settings.expand(src)).expanduser()
    if not (src / "go.mod").is_file():
        raise ValueError(f"{src} has no go.mod: not a Go module")
    ref = (v.get("git_ref") or "").strip()
    if not ref:
        return src
    dest = checkout(src, ref, v.get("out") or "")
    copy_embeds(src, dest, v.get("embed_dirs") or "")
    return dest


# ---- build tags and the git-ignored files they embed (2026-10-27) -------------------------------------------------
# v0.1.155 of Gorilla OpenCode shipped without its toolbox: `go version -m` on the published exe listed no -tags. The
# release is `go build -tags toolbox`, and the helpers it embeds (internal/toolbox/bin/windows-amd64/*.gz, 162 MB,
# git-ignored on purpose) are not in a clean clone of the tag. This pipeline passed no -tags at all, and a git_ref
# build could not have embedded the helpers anyway. vars.tags names the tags per target; vars.embed_dirs are copied
# from the source checkout into the clone; vars.embed_sums pins every embedded file by sha256 before a byte is built.
def target_tags(ctx, goos, goarch):
    """vars.tags: entries split by ';', each `os/arch=tag1,tag2` for one target or `tag1,tag2` for every target.
    -> the comma-joined tags for this target ('' for none)."""
    out = []
    for entry in (x.strip() for x in (ctx.vars.get("tags") or "").split(";") if x.strip()):
        target, eq, tags = entry.partition("=")
        if not eq:
            target, tags = "", entry
        if target and target.strip() != f"{goos}/{goarch}":
            continue
        out += [t.strip() for t in tags.split(",") if t.strip() and t.strip() not in out]
    return ",".join(out)


def copy_embeds(repo, dest, dirs):
    """Copy each folder of `dirs` (comma-separated, relative to the module) from the source checkout into the clone.
    They must be git-ignored there (else the clone is no longer clean and go records vcs.modified=true: refused) and
    must hold files (an empty one is what made v0.1.155: refused). Raises ValueError."""
    for rel in (d.strip() for d in dirs.split(",") if d.strip()):
        a, b = Path(repo) / rel, Path(dest) / rel
        files = sorted(p for p in a.rglob("*") if p.is_file()) if a.is_dir() else []
        if not files:
            raise ValueError(f"embed_dirs: {a} holds no files; the build would embed nothing. Put the files there "
                             "(for Gorilla OpenCode: scripts/build-toolbox.ps1 -HelpersDir ...) and run again")
        ign = subprocess.run(["git", "-C", str(dest), "check-ignore", "-q", rel + "/x"], capture_output=True,
                             timeout=60)
        if ign.returncode != 0:
            raise ValueError(f"embed_dirs: {rel} is not git-ignored in the clone; copying it there would make the "
                             "build record vcs.modified=true")
        for f in files:
            t = b / f.relative_to(a)
            if not t.is_file() or _sha(t) != _sha(f):
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, t)


def embed_digest(src, dirs):
    """One hash of the files under vars.embed_dirs (git-ignored, so fingerprint() does not see them)."""
    h = hashlib.sha256()
    for rel in (d.strip() for d in (dirs or "").split(",") if d.strip()):
        base = Path(src) / rel
        for f in sorted(p for p in base.rglob("*") if p.is_file()) if base.is_dir() else []:
            h.update(f.relative_to(base).as_posix().encode() + b"\0" + _sha(f).encode())
    return h.hexdigest()[:20]


def check_embeds(ctx, src, goos, goarch):
    """vars.embed_sums (a SHA256SUMS file in the module, `<sha256>  <os>-<arch>/<file>`) and vars.embed_root (the
    folder those names are under): every line for this target must be there, as the file or <file>.gz, and its
    (unpacked) bytes must have that hash. -> (count checked, problems). Nothing set: (0, [])."""
    import gzip
    v = ctx.vars
    sums, root = (v.get("embed_sums") or "").strip(), (v.get("embed_root") or "").strip()
    if not sums or not target_tags(ctx, goos, goarch):
        return 0, []
    plat, n, problems = f"{goos}-{goarch}/", 0, []
    for line in (Path(src) / sums).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2 or line.lstrip().startswith("#") or not parts[1].startswith(plat):
            continue
        want, rel = parts
        f = Path(src) / root / rel
        gz = f.with_name(f.name + ".gz")
        if f.is_file():
            data = f.read_bytes()
        elif gz.is_file():
            data = gzip.decompress(gz.read_bytes())
        else:
            problems.append(f"{rel} missing")
            continue
        n += 1
        if hashlib.sha256(data).hexdigest() != want:
            problems.append(f"{rel} is not the pinned bytes")
    if n == 0 and not problems:
        problems.append(f"{sums} pins nothing for {goos}-{goarch}")
    return n, problems


def fetch_missing_embeds(ctx, src, goos, goarch, problems):
    """vars.embed_fetch (a script in the module: `bash SCRIPT OUTDIR` lays out <os>-<arch>/<file> from the official
    releases, each checked against the pins) fills the pinned helpers `problems` reports missing, gzipped into the
    embed folder (2026-11-01: the official-release helpers 7za, ffmpeg, ffprobe, cwebp, avifenc, oxipng are not built
    here, so a clean machine had only the seven built ones). Only "missing" is fetched; wrong bytes stay a failure.
    -> the problems that remain."""
    import gzip
    script = (ctx.vars.get("embed_fetch") or "").strip()
    root = (ctx.vars.get("embed_root") or "").strip()
    missing = [p.rsplit(" missing", 1)[0] for p in problems if p.endswith(" missing")]
    if not script or not root or not missing or len(missing) != len(problems):
        return problems
    tmp = Path(tempfile.mkdtemp(prefix="fk-embed-fetch-"))
    try:
        bash = shutil.which("bash") or "bash"
        env = dict(os.environ)
        if sys.platform == "win32":
            # Git for Windows: the script needs its unzip, tar (GNU, --wildcards), sha256sum, awk, not System32's bsdtar
            bd = Path(bash).resolve().parent
            env["PATH"] = os.pathsep.join([str(d) for d in (bd, bd.parent / "usr" / "bin", bd.parent.parent / "usr" / "bin")
                                           if d.is_dir()] + [env.get("PATH", "")])
        # OUTDIR is "." with cwd=tmp: GNU tar reads a "C:/..." -C/-f path as host "C" (tar: Cannot connect to C)
        r = subprocess.run([bash, (Path(src) / script).as_posix(), "."], capture_output=True, text=True,
                           timeout=1800, env=env, cwd=str(tmp))
        if r.returncode != 0:
            return [f"{script} failed ({r.returncode}): " + (r.stderr or r.stdout).strip()[-300:]] + problems
        for rel in missing:
            got = tmp / rel
            if not got.is_file():
                continue
            dest = Path(src) / root / (rel + ".gz")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(gzip.compress(got.read_bytes(), 9, mtime=0))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return check_embeds(ctx, src, goos, goarch)[1]


def _guess_clone(name, local=None):
    """A folder in sync.repos of fieldkit.local.json whose name is `name` (Gorilla.Opencode = gorilla-opencode)."""
    local = settings.local_settings() if local is None else local
    for r in (local.get("sync") or {}).get("repos") or []:
        path = r.get("path") if isinstance(r, dict) else r
        if path and re.sub(r"[^a-z0-9]+", "-", Path(str(path)).name.lower()).strip("-") == name:
            return str(path).replace("\\", "/")
    return None


def src_line(name, local=None):
    """The exact entry fieldkit.local.json lacks for `--var src` to be optional (2026-10-27: goapps.gorilla-opencode
    was missing and the run only said `--var src`)."""
    return json.dumps({"goapps": {name or "app": _guess_clone(name or "", local) or "FULL/PATH/TO/THE/CLONE"}})


def doctor_row(pipeline, local=None):
    """fieldkit doctor --for PIPELINE: a Go app pipeline whose src is neither a var default nor in goapps -> a MISS
    row with the entry to add; the entry present -> an ok row; not a Go app pipeline -> None."""
    import yaml
    f = Path(__file__).parent / "pipelines" / f"{pipeline}.yaml"
    try:
        text = f.read_text(encoding="utf-8")
        spec = yaml.safe_load(text) or {}
    except (OSError, ValueError):
        return None
    v = spec.get("vars") or {}
    if "fieldkit.build.goapp:" not in text or v.get("src") or not v.get("name"):
        return None
    local = settings.local_settings() if local is None else local
    have = (local.get("goapps") or {}).get(v["name"])
    return {"kind": "setting", "name": f"goapps.{v['name']}", "ok": bool(have),
            "detail": have or "not in fieldkit.local.json: every run needs --var src=PATH",
            "install": "" if have else f"add to fieldkit.local.json: {src_line(v['name'], local)}"}


def source_commit(src):
    """The commit the source folder is at (40 hex), or None outside git."""
    try:
        r = subprocess.run(["git", "-C", str(src), "rev-parse", "--verify", "--quiet", "HEAD"], capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = r.stdout.strip()
    return sha if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def stale(rec, src, what):
    """2026-10-27: run 2 (git_ref b9ef96e) kept run 1's Mac binary (vcs.revision 19fcb0b) in the same out folder; a
    release must never mix commits. A stage's artefact is fresh only when it was made from the commit the source is
    at now. -> the reason it is stale, or None."""
    cur = source_commit(src)
    if cur and (rec or {}).get("commit") != cur:
        was = ((rec or {}).get("commit") or "")[:12] or "an unrecorded commit"
        return f"{what} was made from {was}; the source is at {cur[:12]} (git_ref changed): run it again"
    return None


def vcs_revision(go, f):
    """vcs.revision of a Go binary (`go version -m`), or None when go recorded none."""
    for line in _buildinfo(go, f):
        m = re.search(r"\bvcs\.revision=([0-9a-f]{7,40})", line)
        if m:
            return m.group(1)
    return None


# ---- a build from a git checkout of one commit (2026-10-25) ------------------------------------------------------
# v0.1.152 rebuilt from an export of its tag differed from the published files only by the vcs.* build info
# (`go version -m`): an export has no .git, so go records no vcs.revision/vcs.time/vcs.modified. A clone of the
# commit gives the same build info as the checkout the release was built in. A clone, not `git worktree add`: it
# writes nothing into the source repository (a shared checkout other sessions use).
def checkout(repo, ref, out=""):
    """A clean clone of `ref` (any commit-ish) of the repository at `repo`, at <out>/source-<sha12> (out empty:
    <repo>/dist/git-<sha12>/source-<sha12>). Made once, then reused while its HEAD is that commit and it is
    clean; anything else there is refused, never deleted. Raises ValueError."""
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                       capture_output=True, text=True, timeout=60)
    sha = r.stdout.strip()
    if r.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError(f"git_ref {ref!r} is not a commit in {repo}")
    base = Path(settings.expand(out)).expanduser() if out else Path(repo) / "dist" / f"git-{sha[:12]}"
    dest = base / f"source-{sha[:12]}"
    if not dest.exists():
        base.mkdir(parents=True, exist_ok=True)
        c = subprocess.run(["git", "clone", "--quiet", "--no-checkout", str(repo), str(dest)], capture_output=True,
                           text=True, timeout=1800)
        if c.returncode == 0:
            c = subprocess.run(["git", "-C", str(dest), "-c", "advice.detachedHead=false", "checkout", "--quiet",
                                "--detach", sha], capture_output=True, text=True, timeout=1800)
        if c.returncode != 0:
            raise ValueError(f"cloning {sha[:12]} into {dest} failed: {(c.stderr or c.stdout).strip()[:300]}")
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=60)
    if head.stdout.strip() != sha:
        raise ValueError(f"{dest} exists and is not a checkout of {sha[:12]} (HEAD {head.stdout.strip()[:12]!r}): "
                         "move it away or choose another out")
    dirty = subprocess.run(["git", "-C", str(dest), "status", "--porcelain"], capture_output=True, text=True,
                           timeout=300)
    if dirty.returncode != 0 or dirty.stdout.strip():
        raise ValueError(f"{dest} is not clean (go would record vcs.modified=true): "
                         + (dirty.stdout.strip().splitlines() or [dirty.stderr.strip()])[0][:200])
    if not (dest / "go.mod").is_file():
        raise ValueError(f"{sha[:12]} has no go.mod at the top: not a Go module")
    return dest


def eol_drift(src):
    """[paths] whose working-tree file has CRLF line ends although .gitattributes asks for LF (`git ls-files --eol`:
    w/crlf with eol=lf). Measured 2026-10-25: Gorilla OpenCode v0.1.152 was built from a checkout with 574 such
    files; git normalizes them, so `git status` and go's vcs.modified=false call the tree clean, yet every embedded
    prompt, SQL migration and doc was compiled in with CRLF: the published binaries are reproducible only from that
    one folder, never from git. Not a git checkout: []."""
    try:
        r = subprocess.run(["git", "-C", str(src), "ls-files", "--eol"], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        meta, _, path = line.partition("\t")
        f = meta.split()
        if len(f) >= 3 and f[1] == "w/crlf" and "eol=lf" in meta:
            out.append(path)
    return out


def _out(ctx, src):
    o = ctx.vars.get("out") or ""
    if o:
        return Path(settings.expand(o)).expanduser()
    return src.parent if (ctx.vars.get("git_ref") or "").strip() else src / "dist"


def _reports(ctx, src):
    d = _out(ctx, src) / ".fieldkit"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save(ctx, src, name, data):
    (_reports(ctx, src) / f"{name}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def _load(ctx, src, name):
    try:
        return json.loads((_reports(ctx, src) / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def gomod(src):
    """-> {module, go, toolchain} from go.mod."""
    text = (Path(src) / "go.mod").read_text(encoding="utf-8")
    get = lambda rx: (re.search(rx, text, re.M) or [None, None])[1]
    return {"module": get(r"^module\s+(\S+)"), "go": get(r"^go\s+(\S+)"), "toolchain": get(r"^toolchain\s+go(\S+)")}


def _ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "0")[:3]) + (0,) * (3 - len(re.findall(r"\d+", s or "0")[:3]))


def go_version(go):
    r = subprocess.run([go, "env", "GOVERSION"], capture_output=True, text=True, timeout=60)
    return r.stdout.strip().removeprefix("go") if r.returncode == 0 else None


def fingerprint(src):
    """One hash of every file that decides the build: .go files, go.mod, go.sum, embedded assets tracked by git.
    Git's list when it is a git checkout (ignored files do not count), else every .go file under src."""
    src = Path(src)
    try:
        r = subprocess.run(["git", "-C", str(src), "ls-files", "-co", "--exclude-standard"], capture_output=True,
                           text=True, timeout=120)
        files = sorted(l for l in r.stdout.splitlines() if l) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        files = None
    if files is None:
        files = sorted(str(p.relative_to(src)).replace(os.sep, "/") for p in src.rglob("*")
                       if p.is_file() and (p.suffix == ".go" or p.name in ("go.mod", "go.sum")))
    h = hashlib.sha256()
    skip = ("dist/",)
    for rel in files:
        if rel.startswith(skip):
            continue
        p = src / rel
        if p.is_file():
            h.update(rel.encode() + b"\0")
            with open(p, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
    return h.hexdigest()[:20]


def describe_version(src):
    r = subprocess.run(["git", "-C", str(src), "describe", "--tags", "--always", "--dirty"], capture_output=True,
                       text=True, timeout=60)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "dev"


def _env(extra=None):
    env = {"GOFLAGS": "-mod=readonly", "GOTOOLCHAIN": "local"}  # never fetch a toolchain or edit go.mod silently
    env.update(extra or {})
    return env


def _run(ctx, cmd, src, name, env=None, timeout=3600):
    return ctx.runner.run(cmd, cwd=str(src), env=_env(env), timeout=timeout, name=name)


# -- stages ---------------------------------------------------------------------------------------------------
def stage_toolchain(ctx):
    src = _src(ctx)
    need = gomod(src)
    go = find_go()
    system = "windows" if os.name == "nt" else ("debian" if "debian" in str(host().get("distro", "")).lower() or
                                                 Path("/etc/debian_version").exists() else "linux")
    want = need.get("toolchain") or need.get("go")
    if not go:
        return {"ok": False, "detail": f"no `go` program on this machine; go.mod needs {want}. Install: "
                                       f"{INSTALL[system].replace('<version>', want or '')}"}
    have = go_version(go)
    if not have or _ver(have) < _ver(need.get("go")):
        return {"ok": False, "go": go, "have": have,
                "detail": f"{go} is go {have}; go.mod needs at least {need.get('go')}. Install a newer one: "
                          f"{INSTALL[system].replace('<version>', want or '')}"}
    rec = {"ok": True, "go": go, "have": have, "module": need["module"], "needs": need.get("go"),
           "detail": f"go {have} at {go}; {need['module']} needs go {need.get('go')}"}
    if need.get("toolchain") and _ver(have) < _ver(need["toolchain"]):
        rec["detail"] += (f" (go.mod suggests toolchain {need['toolchain']}; building with {have}, "
                          "GOTOOLCHAIN=local: nothing is downloaded)")
    return _save(ctx, src, "toolchain", rec)


def verify_toolchain(ctx):
    src = _src(ctx)
    rec = _load(ctx, src, "toolchain")
    go = find_go()
    ok = bool(rec and rec.get("ok") and go and go_version(go) == rec.get("have"))
    return {"ok": ok, "detail": rec.get("detail") if rec else "toolchain not checked yet"}


def _go(ctx, src):
    rec = _load(ctx, src, "toolchain") or {}
    go = rec.get("go") or find_go()
    if not go:
        raise ValueError("no go program: run the toolchain stage first")
    return go


def _gosum(src):
    p = Path(src) / "go.sum"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:20] if p.is_file() else "none"


def stage_modules(ctx):
    src = _src(ctx)
    go = _go(ctx, src)
    r = _run(ctx, [go, "mod", "download"], src, "go-mod-download", timeout=3600)
    if not r.ok:
        return {"ok": False, "detail": "go mod download failed (network? a proxy?): "
                                       + (r.stderr.strip().splitlines() or ["no output"])[-1][:200], "log": r.log}
    v = _run(ctx, [go, "mod", "verify"], src, "go-mod-verify", timeout=1800)
    ok = v.ok and "all modules verified" in (v.stdout + v.stderr)
    return _save(ctx, src, "modules", {"ok": ok, "gosum": _gosum(src), "log": v.log,
                                       "detail": "all modules verified against go.sum" if ok else
                                       "go mod verify FAILED: a downloaded module does not match go.sum: "
                                       + (v.stdout + v.stderr).strip()[-300:]})


def verify_modules(ctx):
    src = _src(ctx)
    rec = _load(ctx, src, "modules")
    ok = bool(rec and rec.get("ok") and rec.get("gosum") == _gosum(src))
    return {"ok": ok, "detail": "go.sum unchanged since the modules were verified" if ok else
            "modules not verified for the current go.sum"}


def summarize_tests(output, keep=12):
    """go test output -> {passed, failed, no_tests, failures: {package: [first lines]}} (short enough for a model)."""
    passed, failed, notest, tests = [], [], 0, []
    fail_lines, current = {}, None
    for line in output.splitlines():
        m = TEST_LINE.match(line)
        if m:
            kind, what = m.group(1), m.group(2)
            if kind == "ok":
                passed.append(what)
            elif kind == "FAIL":
                failed.append(what)
            elif kind == "?":
                notest += 1
            elif kind.startswith("---"):
                tests.append(what)
                current = what
                fail_lines.setdefault(current, [])
                continue
        if current and line.startswith("    ") and len(fail_lines[current]) < keep:
            fail_lines[current].append(line.strip()[:200])
    return {"passed": len(passed), "failed": sorted(set(failed)), "failed_tests": tests, "no_tests": notest,
            "failures": fail_lines}


def _checked(ctx, name, cmd, timeout):
    src = _src(ctx)
    go = _go(ctx, src)
    fp = fingerprint(src)
    r = _run(ctx, [go] + cmd, src, f"go-{name}", timeout=timeout)
    out = r.stdout + r.stderr
    rec = {"ok": r.ok, "fingerprint": fp, "log": r.log, "seconds": round(r.seconds, 1), "command": "go " + " ".join(cmd)}
    if name == "test":
        s = summarize_tests(out)
        rec.update(s)
        rec["detail"] = (f"{s['passed']} packages passed" if r.ok else
                         f"{len(s['failed'])} package(s) failed: {', '.join(s['failed'][:6])}; tests: "
                         f"{', '.join(s['failed_tests'][:8])}") + f"; full log {r.log}"
    else:
        lines = [l for l in out.splitlines() if l.strip() and not l.startswith("#")]
        rec["problems"] = lines[:40]
        rec["detail"] = "no problems" if r.ok else f"{len(lines)} problem line(s), first: {lines[0][:160] if lines else '?'}"
    return _save(ctx, src, name, rec)


def _fresh(ctx, name):
    src = _src(ctx)
    rec = _load(ctx, src, name)
    if not rec:
        return {"ok": False, "detail": f"{name} has not run"}
    if rec.get("fingerprint") != fingerprint(src):
        return {"ok": False, "detail": f"the source changed since {name} ran"}
    return {"ok": bool(rec.get("ok")), "detail": rec.get("detail")}


def stage_vet(ctx):
    return _checked(ctx, "vet", ["vet", "./..."], 3600)


def verify_vet(ctx):
    return _fresh(ctx, "vet")


def stage_test(ctx, mode="all"):
    cmd = ["test", "./...", "-count=1", "-timeout", "45m"] + (["-short"] if mode == "short" else [])
    return _checked(ctx, "test", cmd, 3 * 3600)


def verify_test(ctx, mode="all"):
    return _fresh(ctx, "test")


def _targets(ctx):
    raw = ctx.vars.get("targets") or "linux/amd64,windows/amd64"
    out = []
    for t in (x.strip() for x in raw.split(",") if x.strip()):
        goos, _, goarch = t.partition("/")
        if not goos or not goarch:
            raise ValueError(f"target {t!r}: write it os/arch, e.g. linux/amd64")
        out.append((goos, goarch))
    return out


def _file_name(ctx, version, goos, goarch):
    v = ctx.vars
    if goos == "windows" and v.get("windows_name"):
        return v["windows_name"]
    ext = ".exe" if goos == "windows" else ""
    return f"{v.get('name') or 'app'}-{version}-{goos}-{goarch}{ext}"


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stage_build(ctx):
    src = _src(ctx)
    go = _go(ctx, src)
    v = ctx.vars
    version = v.get("version") or describe_version(src)
    out = _out(ctx, src)
    out.mkdir(parents=True, exist_ok=True)
    ld = "-s -w" + (f" -X {v['version_var']}={version}" if v.get("version_var") else "")
    drift = eol_drift(src)
    if drift:
        return {"ok": False, "detail": f"{len(drift)} file(s) have CRLF in the working tree although .gitattributes "
                                       f"says LF (e.g. {', '.join(drift[:3])}): embedded files would go in with CRLF "
                                       "and git still calls the tree clean. Build with --var git_ref=HEAD (a clean "
                                       "clone) or re-check out the tree"}
    fp = fingerprint(src)
    built = []
    # every compile runs guarded: the fan must be cooling, and the governor keeps the CPU near thermal.build_target
    from ..thermal import buildguard
    say = getattr(ctx, "say", None) or (lambda m: print(m, file=sys.stderr))
    try:
        with buildguard.guard(say, label=f"go-{v.get('name') or 'app'}"):
            for goos, goarch in _targets(ctx):
                f = out / _file_name(ctx, version, goos, goarch)
                tags = target_tags(ctx, goos, goarch)
                embedded, problems = check_embeds(ctx, src, goos, goarch)
                if problems:
                    problems = fetch_missing_embeds(ctx, src, goos, goarch, problems)
                    embedded = check_embeds(ctx, src, goos, goarch)[0]
                if problems:
                    return {"ok": False, "detail": f"{goos}/{goarch} not built, embedded files wrong: "
                                                   + "; ".join(problems[:5])}
                cmd = [go, "build", "-trimpath"] + (["-tags", tags] if tags else []) + \
                      ["-ldflags", ld, "-o", str(f), v.get("main") or "."]
                r = _run(ctx, cmd, src, f"go-build-{goos}-{goarch}",
                         env={"GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": str(v.get("cgo") or "0")}, timeout=3600)
                if not r.ok or not f.is_file():
                    first = [l for l in (r.stderr + r.stdout).splitlines() if l.strip()][:6]
                    return {"ok": False, "detail": f"{goos}/{goarch} failed: " + " | ".join(first)[:400], "log": r.log}
                built.append({"target": f"{goos}/{goarch}", "file": str(f), "sha256": _sha(f), "bytes": f.stat().st_size,
                              "tags": tags, "embedded": embedded})
    except buildguard.BuildRefused as e:
        return {"ok": False, "detail": f"not built: {e}"}
    rec = {"ok": True, "version": version, "go": (_load(ctx, src, "toolchain") or {}).get("have"), "ldflags": ld,
           "fingerprint": fp, "built": built, "when": time.strftime("%Y-%m-%d %H:%M:%S"),
           "embed_digest": embed_digest(src, v.get("embed_dirs")),
           "git_ref": (v.get("git_ref") or "").strip() or None, "commit": source_commit(src),
           "detail": f"{version}: " + ", ".join(
               f"{Path(b['file']).name} ({b['bytes']:,} bytes" + (f", -tags {b['tags']}" if b["tags"] else "")
               + (f", {b['embedded']} pinned file(s) embedded" if b["embedded"] else "") + ")" for b in built)}
    return _save(ctx, src, "build", rec)


def verify_build(ctx):
    src = _src(ctx)
    rec = _load(ctx, src, "build")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "nothing built yet"}
    if rec.get("fingerprint") != fingerprint(src):
        return {"ok": False, "detail": "the source changed since the build"}
    why = stale(rec, src, "the build")
    if why:
        return {"ok": False, "detail": why}
    if rec.get("embed_digest", embed_digest(src, "")) != embed_digest(src, ctx.vars.get("embed_dirs")):
        return {"ok": False, "detail": "the embedded files (embed_dirs) changed since the build"}
    want = ctx.vars.get("version")
    if want and want != rec.get("version"):
        return {"ok": False, "detail": f"built {rec.get('version')}, asked for {want}"}
    if {b["target"] for b in rec["built"]} != {f"{o}/{a}" for o, a in _targets(ctx)}:
        return {"ok": False, "detail": "the targets changed since the build"}
    go = find_go()
    for b in rec["built"]:
        want = target_tags(ctx, *b["target"].split("/"))
        if (b.get("tags") or "") != want:
            return {"ok": False, "detail": f"{b['target']} was built with tags {b.get('tags')!r}, asked for {want!r}"}
        # what go itself recorded in the binary, not what this record says (v0.1.155: no -tags in `go version -m`)
        got = next((l.split("=", 1)[1] for l in _buildinfo(go, b["file"]) if l.startswith("build\t-tags=")
                    or l.startswith("build -tags=")), "") if go else None
        if go and got != want:
            return {"ok": False, "detail": f"`go version -m {Path(b['file']).name}` records -tags={got!r}, "
                                           f"asked for {want!r}"}
    stamp = rec["version"].encode()
    for b in rec["built"]:
        p = Path(b["file"])
        if not p.is_file() or _sha(p) != b["sha256"]:
            return {"ok": False, "detail": f"{p.name} is missing or changed since it was built"}
        if ctx.vars.get("version_var") and stamp not in p.read_bytes():
            return {"ok": False, "detail": f"{p.name} does not carry the version stamp {rec['version']}"}
    runs = _runs_here(rec)
    if runs and ctx.vars.get("version_flag"):
        r = subprocess.run([runs, ctx.vars["version_flag"]], capture_output=True, text=True, timeout=60)
        if r.stdout.strip() != rec["version"]:
            return {"ok": False, "detail": f"{Path(runs).name} {ctx.vars['version_flag']} says "
                                           f"{r.stdout.strip()[:60]!r}, not {rec['version']}"}
    return {"ok": True, "detail": rec["detail"] + (" (ran here, reports its version)" if runs else "")}


def stage_repro(ctx):
    """The binary proven, not only the source: build every target a second time from a copy of the source in another
    folder, with its own build cache and temp folder, another time zone and locale, and compare the bytes with the
    build stage's. Identical, or the report says which bytes differ and why (fieldkit/build/repro.py)."""
    import tempfile
    from . import repro
    src = _src(ctx)
    rec = _load(ctx, src, "build")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "run the build stage first"}
    go = _go(ctx, src)
    out = _out(ctx, src).resolve()
    base = Path(tempfile.mkdtemp(prefix="fieldkit-repro-"))
    other = base / ("elsewhere-" + src.name)

    def ignore(d, names):
        return [n for n in names if (Path(d) / n).resolve() == out]
    shutil.copytree(src, other, symlinks=True, ignore=ignore)
    for d in ("gocache", "tmp", "out"):
        (base / d).mkdir()
    modcache = subprocess.run([go, "env", "GOMODCACHE"], capture_output=True, text=True, timeout=60).stdout.strip()
    env = {"GOCACHE": str(base / "gocache"), "TMPDIR": str(base / "tmp"), "TEMP": str(base / "tmp"),
           "TMP": str(base / "tmp"), "TZ": "Pacific/Kiritimati", "LC_ALL": "C", "LANG": "C"}
    if modcache:
        env["GOMODCACHE"] = modcache          # the modules go.sum verified; the second build fetches nothing
    v = ctx.vars
    results, findings = [], []
    for b in rec["built"]:
        goos, _, goarch = b["target"].partition("/")
        f = base / "out" / Path(b["file"]).name
        cmd = [go, "build", "-trimpath"] + (["-tags", b["tags"]] if b.get("tags") else []) + \
              ["-ldflags", rec["ldflags"], "-o", str(f), v.get("main") or "."]
        r = ctx.runner.run(cmd, cwd=str(other), env=_env({**env, "GOOS": goos, "GOARCH": goarch,
                                                         "CGO_ENABLED": str(v.get("cgo") or "0")}),
                           timeout=3600, name=f"go-repro-{goos}-{goarch}")
        if not r.ok or not f.is_file():
            first = [l for l in (r.stderr + r.stdout).splitlines() if l.strip()][:6]
            return {"ok": False, "detail": f"second build of {b['target']} failed: " + " | ".join(first)[:400],
                    "log": r.log, "kept": str(base)}
        same = repro.sha256(f) == b["sha256"]
        results.append({"target": b["target"], "file": Path(b["file"]).name, "sha256": b["sha256"],
                        "second_sha256": repro.sha256(f), "identical": same})
        if not same:
            c = repro.compare(b["file"], f, roots=[src, other])
            findings += [x for d in c["differ"] for x in d["findings"]]
    ok = all(x["identical"] for x in results)
    if ok:
        shutil.rmtree(base, ignore_errors=True)
    data = {"ok": ok, "fingerprint": rec["fingerprint"], "version": rec["version"], "results": results,
            "second_build": {"folder": str(other), "env": {k: env[k] for k in ("TZ", "LC_ALL")},
                             "own_cache": True},
            "findings": findings[:20], "kept": None if ok else str(base),
            "detail": (f"reproduced: {len(results)} binary(ies) byte for byte identical when built again elsewhere"
                       if ok else f"NOT reproduced: {sum(not x['identical'] for x in results)} of {len(results)} differ; "
                       + "; ".join(f"{x['where']}: {x['what']}" for x in findings[:3])
                       + f" (second build kept in {base})")}
    return _save(ctx, src, "repro", data)


def verify_repro(ctx):
    src = _src(ctx)
    rec, rp = _load(ctx, src, "build"), _load(ctx, src, "repro")
    if not rp:
        return {"ok": False, "detail": "not checked yet"}
    if not rec or rp.get("fingerprint") != rec.get("fingerprint") or \
            {x["sha256"] for x in rp["results"]} != {b["sha256"] for b in rec["built"]}:
        return {"ok": False, "detail": "the build changed since it was reproduced"}
    return {"ok": bool(rp.get("ok")), "detail": rp["detail"]}


def _buildinfo(go, f):
    """`go version -m` of a binary, the file name line left out: what go recorded (toolchain, vcs.*, flags)."""
    r = subprocess.run([go, "version", "-m", str(f)], capture_output=True, text=True, timeout=120)
    return [l.strip() for l in r.stdout.splitlines()[1:] if l.strip()] if r.returncode == 0 else []


def _published_files(ctx, src, names):
    """vars.compare_with: a folder holding the published files, or `release`: the files of the GitHub release
    <version> of vars.repo, downloaded into <out>/.fieldkit/published-<version>/ and checked against that release's
    SHA256SUMS first. -> folder. Raises ValueError."""
    from . import relfiles
    v = ctx.vars
    want = (v.get("compare_with") or "").strip()
    if want != "release":
        folder = Path(settings.expand(want)).expanduser()
        if not folder.is_dir():
            raise ValueError(f"compare_with {folder} is not a folder")
        return folder
    rec = _load(ctx, src, "build") or {}
    if not v.get("repo"):
        raise ValueError("compare_with=release needs --var repo=OWNER/NAME")
    folder = _reports(ctx, src) / f"published-{rec['version']}"
    shutil.rmtree(folder, ignore_errors=True)       # this stage's own download folder, refilled every run
    folder.mkdir(parents=True)
    cmd = ["gh", "release", "download", rec["version"], "-R", v["repo"], "-D", str(folder), "-p", "SHA256SUMS-*.txt"]
    for n in names:
        cmd += ["-p", n]
    g = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if g.returncode != 0:
        raise ValueError(f"gh release download {rec['version']} failed: {(g.stderr or g.stdout).strip()[:200]}")
    sums = sorted(folder.glob("SHA256SUMS-*.txt"))
    if len(sums) != 1:
        raise ValueError(f"release {rec['version']} has {len(sums)} SHA256SUMS file(s), expected one")
    listed = relfiles.parse_sums(sums[0])
    for n in names:
        p = folder / n
        if p.is_file() and listed.get(n) != _sha(p):
            raise ValueError(f"the downloaded {n} does not match {sums[0].name}")
    return folder


def stage_published(ctx):
    """Byte equality with what was published (vars.compare_with; empty: skipped). Every binary the build stage made
    is compared with the published file of the same name: identical, or the report names what differs - the bytes
    (fieldkit/build/repro.py) and the build info go recorded (`go version -m`: toolchain, flags, vcs.*)."""
    from . import repro
    src = _src(ctx)
    if not (ctx.vars.get("compare_with") or "").strip():
        return _save(ctx, src, "published", {"ok": True, "skipped": True, "results": [],
                                             "detail": "no compare_with: not compared with published files"})
    rec = _load(ctx, src, "build")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "run the build stage first"}
    go = _go(ctx, src)
    try:
        folder = _published_files(ctx, src, [Path(b["file"]).name for b in rec["built"]])
    except ValueError as e:
        return {"ok": False, "detail": f"published files: {e}"}
    results = []
    for b in rec["built"]:
        mine, theirs = Path(b["file"]), folder / Path(b["file"]).name
        if not theirs.is_file():
            results.append({"file": mine.name, "identical": False, "problem": f"not in {folder}"})
            continue
        row = {"file": mine.name, "sha256": b["sha256"], "published_sha256": _sha(theirs),
               "bytes": [mine.stat().st_size, theirs.stat().st_size]}
        row["identical"] = row["sha256"] == row["published_sha256"]
        if not row["identical"]:
            a, p = _buildinfo(go, mine), _buildinfo(go, theirs)
            row["buildinfo_only_built"] = [l for l in a if l not in p]
            row["buildinfo_only_published"] = [l for l in p if l not in a]
            c = repro.compare(mine, theirs, roots=[src])
            row["findings"] = [x for d in c["differ"] for x in d["findings"]][:10]
            # v0.1.152: same build info, other bytes = embedded files with other line ends (see eol_drift)
            row["crlf"] = [mine.read_bytes().count(b"\r\n"), theirs.read_bytes().count(b"\r\n")]
        results.append(row)
    ok = all(r["identical"] for r in results)
    same = [r["file"] for r in results if r["identical"]]
    data = {"ok": ok, "version": rec["version"], "git_ref": rec.get("git_ref"), "published": str(folder),
            "built_sha256": sorted(b["sha256"] for b in rec["built"]), "results": results,
            "detail": (f"byte for byte the published files: {', '.join(same)}" if ok else
                       "NOT the published bytes: " + "; ".join(
                           f"{r['file']}: " + (r.get("problem") or ", ".join(
                               (r.get("buildinfo_only_built") or []) + ["published " + x for x in
                                                                        r.get("buildinfo_only_published") or []])
                               or ("same build info, bytes differ" + (
                                   f"; CRLF pairs {r['crlf'][0]:,} built vs {r['crlf'][1]:,} published: embedded files "
                                   "with other line ends" if r.get("crlf") and r["crlf"][0] != r["crlf"][1] else "")))
                           for r in results if not r["identical"])[:600])}
    return _save(ctx, src, "published", data)


def verify_published(ctx):
    src = _src(ctx)
    rec, pb = _load(ctx, src, "build"), _load(ctx, src, "published")
    if not pb:
        return {"ok": False, "detail": "not compared yet"}
    if pb.get("skipped"):
        if (ctx.vars.get("compare_with") or "").strip():
            return {"ok": False, "detail": "compare_with is set now: compare again"}
        return {"ok": True, "detail": pb["detail"]}
    if not rec or sorted(b["sha256"] for b in rec["built"]) != pb.get("built_sha256"):
        return {"ok": False, "detail": "the build changed since it was compared with the published files"}
    return {"ok": bool(pb.get("ok")), "detail": pb["detail"]}


def _runs_here(rec):
    import platform
    me = ("windows" if os.name == "nt" else "linux" if platform.system() == "Linux" else "darwin",
          {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower()))
    for b in rec["built"]:
        if b["target"] == f"{me[0]}/{me[1]}":
            return b["file"]
    return None



# ---- every target from one machine (owner, 2026-10-23): the Mac 10.13 binary and the packages --------------------
# "va trebui sa modificam comanda de build ca sa le putem crea intr-o singura etapa ca sa nu mai trebuiasca sa sar
# intre 3 calculatoare, si trei modele". Linux and Windows come from stage_build; these add the legacy Mac binary
# (the project's own script: a patched GOROOT, since stock Go needs macOS 11) and the packages the project's own
# scripts make from the Linux binary. Both are driven by vars, so a Go app without them skips them.
MACHO = (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")    # 64-bit Mach-O, either byte order


def _plain(version):
    """The scripts take 0.1.149 and check the binary prints v0.1.149."""
    return version[1:] if version.startswith("v") else version


def cached_toolchains(want, modcache=None):
    """Go toolchains the go command itself left in the module cache (GOTOOLCHAIN=go1.24.13 once fetched one):
    <GOMODCACHE>/golang.org/toolchain@v0.0.1-go<want>.*.<os>-<arch>/bin/go, newest first."""
    import platform
    goos = "windows" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux")
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(),
                                                                                         platform.machine().lower())
    base = Path(modcache) if modcache else Path.home() / "go" / "pkg" / "mod"
    exe = "go.exe" if goos == "windows" else "go"
    found = []
    for d in (base / "golang.org").glob(f"toolchain@v0.0.1-go{want}.*.{goos}-{arch}"):
        m = re.search(r"-go(\d+\.\d+(?:\.\d+)?)\.", d.name)
        if m and (d / "bin" / exe).is_file():
            found.append((_ver(m.group(1)), m.group(1), d / "bin" / exe))
    return [(ver, str(go)) for _, ver, go in sorted(found, reverse=True)]


def mac_go(ctx, src):
    """The go for the Mac 10.13 build. vars.mac_go (e.g. 1.24): the patch in build-darwin-legacy.sh is written for
    that series and the builds tested on the MacBook Air used it. The system go when it is that series, else the
    newest cached toolchain of it; none: refused with the line that fetches one (2026-10-25: the first Windows run
    built the Mac program with go 1.27, untested on the Air). -> (go, version) or raises ValueError."""
    want = str(ctx.vars.get("mac_go") or "").strip()
    go = _go(ctx, src)
    have = go_version(go) or ""
    if not want or have == want or have.startswith(want + "."):
        return go, have
    modcache = subprocess.run([go, "env", "GOMODCACHE"], capture_output=True, text=True, timeout=60).stdout.strip()
    for ver, cand in cached_toolchains(want, modcache or None):
        if go_version(cand) == ver:
            return cand, ver
    raise ValueError(f"the Mac build wants go {want}.x; the system go is {have} and no go{want}.x toolchain is "
                     f"cached under {modcache or '~/go/pkg/mod'}/golang.org. Fetch one (the go command downloads "
                     f"and checks it against sum.golang.org): GOTOOLCHAIN=go{want}.0 go version - then run again")


def stage_mac(ctx):
    """vars.mac_script: the project's legacy-Mac build script (empty: skipped). Its output is checked to be a
    64-bit Mach-O binary built by the go of vars.mac_go."""
    src = _src(ctx)
    v = ctx.vars
    script = (v.get("mac_script") or "").strip()
    if not script:
        return _save(ctx, src, "mac", {"ok": True, "skipped": True, "detail": "no mac_script: skipped"})
    rec = _load(ctx, src, "build")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "run the build stage first (the version comes from it)"}
    version = rec["version"]
    out = _out(ctx, src)
    f = out / f"{v.get('name') or 'app'}-{version}-darwin-amd64-{v.get('mac_suffix') or 'legacy'}"
    if not (src / script).is_file():
        return {"ok": False, "detail": f"{script} is not in {src}"}
    try:
        go, gover = mac_go(ctx, src)
    except ValueError as e:
        return {"ok": False, "detail": str(e)}
    # the script calls `go` from PATH: the chosen one goes first
    env = {"VERSION": _plain(version), "PATH": str(Path(go).parent) + os.pathsep + os.environ.get("PATH", "")}
    from ..thermal import buildguard
    say = getattr(ctx, "say", None) or (lambda m: print(m, file=sys.stderr))
    try:
        with buildguard.guard(say, label=f"go-{v.get('name') or 'app'}-mac"):
            r = _run(ctx, ["bash", script, str(f)], src, "mac-build", env=env, timeout=7200)
    except buildguard.BuildRefused as e:
        return {"ok": False, "detail": f"not built: {e}"}
    if not r.ok or not f.is_file():
        last = [l for l in (r.stderr + r.stdout).splitlines() if l.strip()][-4:]
        return {"ok": False, "detail": f"{script} failed: " + " | ".join(last)[:400], "log": r.log}
    if f.read_bytes()[:4] not in MACHO:
        return {"ok": False, "detail": f"{f.name} is not a 64-bit Mach-O binary"}
    if v.get("mac_go") and gover and f"go{gover}".encode() not in f.read_bytes():
        return {"ok": False, "detail": f"{f.name} does not carry go{gover} in its build info: built by another go"}
    rec = {"ok": True, "version": version, "file": str(f), "sha256": _sha(f), "bytes": f.stat().st_size,
           "go": gover, "detail": f"{f.name} ({f.stat().st_size:,} bytes, go {gover})", "package": [],
           "commit": source_commit(src)}
    # the installer's folder (Install/Undo .command, the .tar.gz it checks, SHA256SUMS, a .zip for the release page),
    # by the project's own packager (the Windows side's build_mac_legacy_package.py, 2026-10-23 PR #3)
    pkg = (v.get("mac_package") or "").strip()
    if pkg and (src / pkg).is_file():
        pdir = out / "mac-package"
        shutil.rmtree(pdir, ignore_errors=True)
        r = _run(ctx, [sys.executable, str(src / pkg), str(src), str(f), _plain(version), str(pdir)], src,
                 "mac-package", timeout=1800)
        made = sorted(p for p in pdir.rglob("*") if p.is_file() and p.suffix in (".zip", ".gz"))
        if not r.ok or not made:
            last = [l for l in (r.stderr + r.stdout).splitlines() if l.strip()][-4:]
            return {"ok": False, "detail": f"{pkg} failed: " + " | ".join(last)[:300], "log": r.log}
        # vars.mac_zip: the name the release page uses for the zip (v0.1.151 and v0.1.152 published
        # ...-mac-10.13-x86_64.zip; the packager writes ...-mac-10.13.zip). Set: only that zip is published, the
        # .tar.gz travels inside it and the bare binary is not a release file.
        zip_name = (v.get("mac_zip") or "").replace("<name>", v.get("name") or "app").replace("<version>",
                                                                                           _plain(version))
        if zip_name:
            zips = [m for m in made if m.suffix == ".zip"]
            if len(zips) != 1:
                return {"ok": False, "detail": f"{pkg} made {len(zips)} .zip file(s), expected one"}
            made = [zips[0]]
            rec["publish_binary"] = False
        for m in made:
            dst = out / (zip_name or m.name)
            shutil.copy2(m, dst)
            rec["package"].append({"file": str(dst), "sha256": _sha(dst), "bytes": dst.stat().st_size})
        rec["detail"] += "; package: " + ", ".join(Path(x["file"]).name for x in rec["package"])
    elif pkg:
        rec["detail"] += f"; no Mac package: {pkg} is not in this checkout"
    return _save(ctx, src, "mac", rec)


def verify_mac(ctx):
    src = _src(ctx)
    rec = _load(ctx, src, "mac")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "no Mac binary yet"}
    if rec.get("skipped"):
        return {"ok": True, "detail": rec["detail"]}
    f = Path(rec["file"])
    if not f.is_file() or _sha(f) != rec["sha256"]:
        return {"ok": False, "detail": f"{f.name} is missing or changed"}
    why = stale(rec, src, f.name)
    if why:
        return {"ok": False, "detail": why}
    cur = source_commit(src)
    if cur:
        try:
            got = vcs_revision(_go(ctx, src), f)
        except (ValueError, OSError, subprocess.TimeoutExpired):
            got = None
        if got and not cur.startswith(got):
            return {"ok": False, "detail": f"{f.name} says vcs.revision={got[:12]} (go version -m); the source is at "
                                           f"{cur[:12]}: a release never mixes commits, run the mac stage again"}
    return {"ok": True, "detail": rec["detail"]}


def stage_packages(ctx):
    """vars.packages: comma list of the project's package scripts (e.g. scripts/build-deb.sh), each run as
    `sh SCRIPT <version>` with the Linux binary at <src>/<name> (their documented input); every file a script leaves
    in vars.package_dir (default Compiled.Builds) while it ran is copied into the output folder. A new file, not a
    version in the name: the portable tarball keeps one fixed name on purpose (the install script fetches it), and
    the first run refused it for lacking the version (2026-10-23)."""
    src = _src(ctx)
    v = ctx.vars
    scripts = [x.strip() for x in (v.get("packages") or "").split(",") if x.strip()]
    if not scripts:
        return _save(ctx, src, "packages", {"ok": True, "skipped": True, "files": [], "detail": "no packages: skipped"})
    rec = _load(ctx, src, "build")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "run the build stage first"}
    version = rec["version"]
    linux = next((b["file"] for b in rec["built"] if b["target"].startswith("linux/")), None)
    if not linux:
        return {"ok": False, "detail": "no linux binary in the build: add linux/amd64 to targets"}
    if packager(v) == "nfpm":
        return _packages_nfpm(ctx, src, rec, linux)
    name = v.get("name") or "app"
    staged = src / name
    shutil.copy2(linux, staged)                         # the scripts package whatever sits at <src>/<name>
    pkgdir = src / (v.get("package_dir") or "Compiled.Builds")
    out = _out(ctx, src)
    files, start = [], time.time() - 1
    for script in scripts:
        if not (src / script).is_file():
            return {"ok": False, "detail": f"{script} is not in {src}"}
        r = _run(ctx, ["sh", script, _plain(version)], src, "pkg-" + Path(script).stem, timeout=1800)
        made = [p for p in pkgdir.glob("*") if p.is_file() and p.stat().st_mtime >= start
                and p.name not in {Path(f["file"]).name for f in files}] if pkgdir.is_dir() else []
        if not r.ok or not made:
            last = [l for l in (r.stderr + r.stdout).splitlines() if l.strip()][-4:]
            return {"ok": False, "detail": f"{script}: {'failed' if not r.ok else 'made no new file'}: "
                                           + " | ".join(last)[:300], "log": r.log}
        for m in made:
            dst = out / m.name
            shutil.copy2(m, dst)
            files.append({"script": script, "file": str(dst), "sha256": _sha(dst), "bytes": dst.stat().st_size})
    xz, bad = xz_checked(ctx, files)
    if bad:
        return bad
    return _save(ctx, src, "packages", {"ok": True, "version": version, "files": files, "xz": xz,
                                        "commit": source_commit(src),
                                        "detail": ", ".join(f"{Path(f['file']).name}" for f in files)
                                        + _xz_note(xz)})


def stage_arch(ctx):
    """vars.arch_script (the project's scripts/build-arch.sh, run as `sh SCRIPT <version>` with the Linux binary at
    <src>/<name>): the Arch/CachyOS .pkg.tar.zst, a stage of its own so a run with the nfpm packager gets it too when
    it runs on Linux. Elsewhere (the script runs the Linux binary, needs bsdtar and zstd): ok, status
    not-this-platform, no file; the release then adds the package from a Linux run (ship gh-release --add)."""
    src = _src(ctx)
    v = ctx.vars
    script = (v.get("arch_script") or "").strip()
    if not script:
        return _save(ctx, src, "arch", {"ok": True, "skipped": True, "files": [], "detail": "no arch_script: skipped"})
    if sys.platform != "linux":
        return _save(ctx, src, "arch", {"ok": True, "skipped": True, "status": "not-this-platform", "files": [],
                                        "detail": "not-this-platform: the Arch package is made on Linux"})
    rec = _load(ctx, src, "build")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "run the build stage first"}
    linux = next((b["file"] for b in rec["built"] if b["target"].startswith("linux/")), None)
    if not linux:
        return {"ok": False, "detail": "no linux binary in the build: add linux/amd64 to targets"}
    if not (src / script).is_file():
        return {"ok": False, "detail": f"{script} is not in {src}"}
    missing = [t for t in ("bsdtar", "zstd") if not shutil.which(t)]
    if missing:
        return {"ok": False, "detail": f"{script} needs {', '.join(missing)} on PATH"}
    shutil.copy2(linux, src / (v.get("name") or "app"))
    pkgdir = src / (v.get("package_dir") or "Compiled.Builds")
    start = time.time() - 1
    r = _run(ctx, ["sh", script, _plain(rec["version"])], src, "pkg-arch", timeout=1800)
    made = [p for p in pkgdir.glob("*.pkg.tar.zst") if p.stat().st_mtime >= start] if pkgdir.is_dir() else []
    if not r.ok or len(made) != 1:
        last = [l for l in (r.stderr + r.stdout).splitlines() if l.strip()][-4:]
        return {"ok": False, "detail": f"{script}: {'failed' if not r.ok else f'made {len(made)} .pkg.tar.zst, not one'}: "
                                       + " | ".join(last)[:300], "log": r.log}
    dst = _out(ctx, src) / made[0].name
    shutil.copy2(made[0], dst)
    files = [{"script": script, "file": str(dst), "sha256": _sha(dst), "bytes": dst.stat().st_size}]
    return _save(ctx, src, "arch", {"ok": True, "version": rec["version"], "files": files,
                                    "commit": source_commit(src), "detail": dst.name})


def verify_arch(ctx):
    src = _src(ctx)
    rec = _load(ctx, src, "arch")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "no arch stage yet"}
    if rec.get("skipped"):
        return {"ok": True, "detail": rec["detail"]}
    why = stale(rec, src, "the arch package")
    if why:
        return {"ok": False, "detail": why}
    for f in rec["files"]:
        p = Path(f["file"])
        if not p.is_file() or _sha(p) != f["sha256"]:
            return {"ok": False, "detail": f"{p.name} is missing or changed"}
        r = subprocess.run(["zstd", "-t", str(p)], capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return {"ok": False, "detail": f"{p.name}: zstd -t failed: {(r.stderr or r.stdout)[-200:]}"}
        if not p.name.endswith("-x86_64.pkg.tar.zst") or _plain(rec["version"]) not in p.name:
            return {"ok": False, "detail": f"{p.name} is not named for version {_plain(rec['version'])}"}
    return {"ok": True, "detail": rec["detail"]}


def xz_checked(ctx, files):
    """Every .deb/.xz/.txz among the made files through fieldkit xzcheck (the dictionary fits the content, not
    bigger, not smaller; unpacking proven within vars.xz_cap_mib, default 128). -> (results, None), or
    (results, a failed stage result naming each problem). Owner, 2026-10-25: maximum compression, not crazy."""
    from ..method import xzcheck
    cap = int(str(ctx.vars.get("xz_cap_mib") or 128))
    xz = [dict(xzcheck.check_file(f["file"], cap), sha256=f["sha256"]) for f in files
          if str(f["file"]).lower().endswith(xzcheck.CHECKED)]
    problems = [p for r in xz for p in r["problems"]]
    if problems:
        return xz, {"ok": False, "xz": xz, "detail": "fieldkit xzcheck: " + "; ".join(problems)[:600]}
    return xz, None


def _xz_note(xz):
    return f"; xzcheck passed: {', '.join(r['file'] for r in xz)}" if xz else ""


def verify_packages(ctx):
    src = _src(ctx)
    rec = _load(ctx, src, "packages")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "no packages yet"}
    why = None if rec.get("skipped") else stale(rec, src, "the packages")
    if why:
        return {"ok": False, "detail": why}
    for f in rec.get("files", []):
        p = Path(f["file"])
        if not p.is_file() or _sha(p) != f["sha256"]:
            return {"ok": False, "detail": f"{p.name} is missing or changed"}
    # every xz file made must carry a passing xzcheck of exactly these bytes (a record from before the check: rerun)
    from ..method import xzcheck
    checked = {(r["file"], r.get("sha256")) for r in rec.get("xz") or [] if r.get("ok")}
    for f in rec.get("files", []):
        p = Path(f["file"])
        if p.name.lower().endswith(xzcheck.CHECKED) and (p.name, f["sha256"]) not in checked:
            return {"ok": False, "detail": f"{p.name} has no passing fieldkit xzcheck: run the packages stage again"}
    tb = rec.get("tarball")
    if tb:
        problem = check_tarball(Path(tb["file"]), tb["member"], Path(tb["binary"]), tb["binary_sha256"], tb["stamp"])
        if problem:
            return {"ok": False, "detail": problem}
    return {"ok": True, "detail": rec["detail"]}


# ---- packages on a machine without dpkg-deb or rpmbuild (Windows, 2026-10-25) ------------------------------------
# v0.1.152 was released from the Windows laptop: build-deb.sh/build-rpm.sh need dpkg-deb and rpmbuild, and every
# package script runs the Linux binary to read its version, which Windows cannot. By hand it took nfpm (the
# project's scripts/build-packages-nfpm.py, checked against the last release's .deb), a tarball packed in Python and
# checked by bytes instead of by running it, and the Arch package from the arch-package workflow on GitHub.
def packager(v):
    """vars.packager: scripts (the project's shell scripts), nfpm, or auto: scripts where dpkg-deb and rpmbuild
    exist and this is not Windows, else nfpm."""
    want = (v.get("packager") or "auto").strip()
    if want != "auto":
        return want
    return "scripts" if os.name != "nt" and shutil.which("dpkg-deb") and shutil.which("rpmbuild") else "nfpm"


def _source_epoch(src):
    if os.environ.get("SOURCE_DATE_EPOCH", "").isdigit():
        return int(os.environ["SOURCE_DATE_EPOCH"])
    try:
        r = subprocess.run(["git", "-C", str(src), "log", "-1", "--format=%ct"], capture_output=True, text=True,
                           timeout=60)
        return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else 0
    except (OSError, subprocess.TimeoutExpired):
        return 0


def make_tarball(binary, dest, member, mtime=0):
    """The layout of scripts/build-tarball.sh without running the binary: ONE member at the root, a regular file,
    mode 0755, owner root, gzip level 9. mtime fixed (the commit time), gzip header without name or time: the same
    binary gives the same bytes."""
    import gzip
    import io
    import tarfile
    data = Path(binary).read_bytes()
    ti = tarfile.TarInfo(member)
    ti.size, ti.mode, ti.mtime, ti.type = len(data), 0o755, int(mtime), tarfile.REGTYPE
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = "root"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as t:
        t.addfile(ti, io.BytesIO(data))
    with open(dest, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as g:
        g.write(buf.getvalue())
    return Path(dest)


def check_tarball(tarball, member, binary, binary_sha, stamp):
    """-> None, or what is wrong. The packed file must be byte for byte the binary the build and repro stages
    proved, carry the version stamp, be the only member, and be 0755."""
    import tarfile
    try:
        with tarfile.open(tarball, "r:gz") as t:
            ms = t.getmembers()
            if [m.name for m in ms] != [member]:
                return f"{Path(tarball).name} holds {[m.name for m in ms]}, expected only {member!r}"
            m = ms[0]
            if not m.isfile() or m.mode & 0o7777 != 0o755:
                return f"{member} in {Path(tarball).name} is mode {oct(m.mode & 0o7777)}, expected 0o755"
            data = t.extractfile(m).read()
    except (OSError, tarfile.TarError, EOFError) as e:
        return f"{Path(tarball).name} is not a readable .tar.gz: {e}"
    if hashlib.sha256(data).hexdigest() != binary_sha:
        return f"{member} in {Path(tarball).name} is not byte for byte {Path(binary).name}"
    if stamp.encode() not in data:
        return f"{member} in {Path(tarball).name} does not carry the version stamp {stamp}"
    return None


def previous_release_deb(repo, version, dest, gh="gh"):
    """The .deb of the release before `version` on GitHub, downloaded into dest/<tag>/ and checked against that
    release's SHA256SUMS. -> (path, tag). Raises ValueError (no earlier release, no .deb, a checksum mismatch)."""
    from . import relfiles
    r = subprocess.run([gh, "release", "list", "-R", repo, "--limit", "40", "--json", "tagName,isDraft"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise ValueError(f"gh release list -R {repo} failed: {(r.stderr or r.stdout).strip()[:200]}")
    tags = [x["tagName"] for x in json.loads(r.stdout or "[]")
            if not x.get("isDraft") and re.fullmatch(r"v\d+\.\d+\.\d+", x["tagName"])
            and _ver(x["tagName"]) < _ver(version)]
    if not tags:
        raise ValueError(f"no release of {repo} older than {version}")
    tag = max(tags, key=_ver)
    d = Path(dest) / tag
    for attempt in (0, 1):
        debs = sorted(d.glob("*_amd64.deb")) if d.is_dir() else []
        sums = sorted(d.glob("SHA256SUMS-*.txt")) if d.is_dir() else []
        if len(debs) == 1 and len(sums) == 1:
            want = relfiles.parse_sums(sums[0]).get(debs[0].name)
            if want and _sha(debs[0]) == want:
                return debs[0], tag
            if attempt:
                raise ValueError(f"{debs[0].name} of {tag} does not match its SHA256SUMS")
        if attempt:
            raise ValueError(f"{tag} has {len(debs)} .deb file(s) and {len(sums)} SHA256SUMS file(s), expected one each")
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        g = subprocess.run([gh, "release", "download", tag, "-R", repo, "-D", str(d), "-p", "*_amd64.deb",
                            "-p", "SHA256SUMS-*.txt"], capture_output=True, text=True, timeout=1800)
        if g.returncode != 0:
            raise ValueError(f"gh release download {tag} failed: {(g.stderr or g.stdout).strip()[:200]}")
    raise ValueError("unreachable")


def _packages_nfpm(ctx, src, rec, linux):
    v = ctx.vars
    version = rec["version"]
    out = _out(ctx, src)
    rp = _load(ctx, src, "repro") or {}
    target = next(b for b in rec["built"] if b["file"] == linux)
    proved = next((x for x in rp.get("results", []) if x.get("target") == target["target"]), None)
    if not (rp.get("ok") and proved and proved["identical"] and proved["second_sha256"] == _sha(linux)):
        return {"ok": False, "detail": f"{Path(linux).name} is not the binary the repro stage reproduced: run repro"}
    files, notes = [], []
    # the tarball: bytes, not a run of the binary (Windows cannot run it)
    tname = (v.get("tarball") or "").strip()
    tb = None
    if tname:
        member = (v.get("tarball_member") or v.get("name") or "app").strip()
        dst = make_tarball(linux, out / tname, member, _source_epoch(src))
        tb = {"file": str(dst), "member": member, "binary": linux, "binary_sha256": target["sha256"], "stamp": version}
        problem = check_tarball(dst, member, Path(linux), target["sha256"], version)
        if problem:
            return {"ok": False, "detail": problem}
        files.append({"script": "fieldkit make_tarball", "file": str(dst), "sha256": _sha(dst),
                      "bytes": dst.stat().st_size})
    script = (v.get("nfpm_script") or "scripts/build-packages-nfpm.py").strip()
    if not (src / script).is_file():
        return {"ok": False, "detail": f"{script} is not in {src}: no way to make the .deb and .rpm here"}
    cmd = [sys.executable, "-I", str(src / script), _plain(version), linux]
    prev = (v.get("previous_deb") or "auto").strip()
    if prev == "auto":
        if not v.get("repo"):
            return {"ok": False, "detail": "previous_deb=auto needs --var repo=OWNER/NAME (or previous_deb=PATH|none)"}
        try:
            prev_path, prev_tag = previous_release_deb(v["repo"], version, _reports(ctx, src) / "previous")
        except ValueError as e:
            return {"ok": False, "detail": f"the last release's .deb to compare with: {e}"}
        notes.append(f"layout compared with {prev_tag}'s {prev_path.name}")
    elif prev == "none":
        prev_path = None
        notes.append("NOT compared with an earlier .deb (previous_deb=none)")
    else:
        prev_path = Path(settings.expand(prev)).expanduser()
        if not prev_path.is_file():
            return {"ok": False, "detail": f"previous_deb {prev_path} is not a file"}
        notes.append(f"layout compared with {prev_path.name}")
    stage = _reports(ctx, src) / "nfpm-out"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    cmd += [str(stage)] + (["--compare", str(prev_path)] if prev_path else [])
    r = _run(ctx, cmd, src, "pkg-nfpm", timeout=1800)
    made = sorted(p for p in stage.iterdir() if p.is_file() and p.suffix in (".deb", ".rpm"))
    if not r.ok or sorted(p.suffix for p in made) != [".deb", ".rpm"]:
        last = [l for l in (r.stderr + r.stdout).splitlines() if l.strip()][-4:]
        return {"ok": False, "detail": f"{script}: {'failed' if not r.ok else 'did not make one .deb and one .rpm'}: "
                                       + " | ".join(last)[:400], "log": r.log}
    said = [l.strip() for l in r.stdout.splitlines() if l.startswith(("deb:", "rpm:", "same layout"))]
    for m in made:
        dst = out / m.name
        shutil.copy2(m, dst)
        files.append({"script": script, "file": str(dst), "sha256": _sha(dst), "bytes": dst.stat().st_size})
    others = [s for s in (x.strip() for x in (v.get("packages") or "").split(",")) if s and
              not any(k in s for k in ("deb", "rpm", "tarball", "arch"))]
    if others:
        notes.append("not made here: " + ", ".join(others) + " (needs its own system)")
    xz, bad = xz_checked(ctx, files)
    if bad:
        return bad
    data = {"ok": True, "version": version, "packager": "nfpm", "files": files, "tarball": tb, "nfpm_said": said,
            "notes": notes, "log": r.log, "xz": xz,
            "detail": ", ".join(Path(f["file"]).name for f in files) + (f"; {'; '.join(notes)}" if notes else "")
            + _xz_note(xz)}
    data["commit"] = source_commit(src)
    return _save(ctx, src, "packages", data)


def published(ctx, src):
    """[(path, sha256)] of every file the release publishes, from the stage records: the builds, the Mac zip (or
    the Mac binary and its package files when no mac_zip name is set), the packages."""
    rec = _load(ctx, src, "build") or {}
    files = [(Path(b["file"]), b["sha256"]) for b in rec.get("built", [])]
    mac = _load(ctx, src, "mac") or {}
    if mac.get("ok") and mac.get("file"):
        if mac.get("publish_binary", True):
            files.append((Path(mac["file"]), mac["sha256"]))
        files += [(Path(x["file"]), x["sha256"]) for x in mac.get("package", [])]
    files += [(Path(f["file"]), f["sha256"]) for f in (_load(ctx, src, "packages") or {}).get("files", [])]
    files += [(Path(f["file"]), f["sha256"]) for f in (_load(ctx, src, "arch") or {}).get("files", [])]
    return files


def stage_checksums(ctx):
    from . import relfiles
    src = _src(ctx)
    rec = _load(ctx, src, "build")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "run the build stage first"}
    out = _out(ctx, src)
    f = out / f"SHA256SUMS-{rec['version']}.txt"
    files = published(ctx, src)
    for p, sha in files:
        if not p.is_file() or _sha(p) != sha:
            return {"ok": False, "detail": f"{p.name} is missing or changed since its stage made it"}
    f.write_text("".join(f"{sha}  {p.name}\n" for p, sha in files), encoding="utf-8", newline="\n")
    # the release page's table of files, sizes and checksums, measured here (v0.1.152's was typed by hand)
    t = out / f"RELEASE-FILES-{rec['version']}.md"
    t.write_text(relfiles.table(f, out), encoding="utf-8", newline="\n")
    return {"ok": True, "file": str(f), "table": str(t), "detail": f"{f.name}: {len(files)} file(s); table {t.name}"}


def verify_checksums(ctx):
    from . import relfiles
    src = _src(ctx)
    rec = _load(ctx, src, "build")
    if not rec:
        return {"ok": False, "detail": "nothing built"}
    for name in ("build", "mac", "packages", "arch"):
        r = _load(ctx, src, name) or {}
        why = None if (r.get("skipped") or not r.get("ok")) else stale(r, src, f"the {name} stage")
        if why:
            return {"ok": False, "detail": f"SHA256SUMS would mix commits: {why}"}
    out = _out(ctx, src)
    f = out / f"SHA256SUMS-{rec['version']}.txt"
    if not f.is_file():
        return {"ok": False, "detail": f"{f.name} missing"}
    sums = relfiles.parse_sums(f)
    want = {p.name for p, _ in published(ctx, src)}
    if set(sums) != want:
        return {"ok": False, "detail": f"{f.name} does not list exactly the published files: missing "
                                       f"{sorted(want - set(sums))}, extra {sorted(set(sums) - want)}"}
    for name, sha in sums.items():
        p = out / name
        if not p.is_file() or _sha(p) != sha:
            return {"ok": False, "detail": f"{name} does not match {f.name}"}
    t = out / f"RELEASE-FILES-{rec['version']}.md"
    bad = relfiles.check_table(t.read_text(encoding="utf-8") if t.is_file() else "", sums,
                               {n: (out / n).stat().st_size for n in sums})
    if bad:
        return {"ok": False, "detail": f"{t.name}: " + "; ".join(bad[:3])}
    return {"ok": True, "detail": f"{f.name} lists every published file and matches it; {t.name} agrees"}


def stage_align(ctx, run=subprocess.run):
    """After gh-release (release mode, once): bring both computers to the new version with `fieldkit parity align
    --apply`, here and on every peer over the cable (a `run` link job, no AI, no owner step). The release itself is
    the source: align downloads the asset and SHA256SUMS from it and checks them, so an unpublished tag fails here."""
    src = _src(ctx)
    rec = _load(ctx, src, "build")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "run the build stage first"}
    tag = rec["version"] if str(rec["version"]).startswith("v") else "v" + str(rec["version"])
    cmd = [sys.executable, "-m", "fieldkit", "parity", "align", "--apply", "--version", tag]
    mine = run(cmd, capture_output=True, text=True, timeout=1800)
    rows = [{"device": "this computer", "ok": mine.returncode == 0, "tail": (mine.stdout or mine.stderr or "")[-300:]}]
    from ..lifecycle import tasks
    from ..peers import link
    root = Path(tempfile.mkdtemp(prefix="fk-align-"))      # the job needs no files: an empty tree, not the repository
    for d in link.others(link.household() or {}):
        try:
            t = tasks.start_run(["fieldkit", "parity", "align", "--apply", "--version", tag], d["name"], root, wall=30,
                                again=True)
        except (ValueError, tasks.AlreadyStarted) as e:
            rows.append({"device": d["name"], "ok": False, "tail": str(e)[:300]})
            continue
        w = run([sys.executable, "-m", "fieldkit", "task", "watch", t["id"]], capture_output=True, text=True,
                timeout=1800)
        rows.append({"device": d["name"], "ok": t["status"] == "started" and w.returncode == 0, "task": t["id"],
                     "tail": (w.stdout or w.stderr or "")[-300:]})
    root.rmdir()
    ok = all(r["ok"] for r in rows)
    return _save(ctx, src, "align", {"ok": ok, "version": tag, "commit": rec.get("commit"), "rows": rows,
                                     "detail": "; ".join(f"{r['device']}: {'aligned' if r['ok'] else 'FAILED'}"
                                                         for r in rows)})


def verify_align(ctx):
    """Both computers print the new version (fieldkit parity installed, against this release's tag)."""
    from .. import parity, parity_goal
    src = _src(ctx)
    rec = _load(ctx, src, "align")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": (rec or {}).get("detail") or "align did not run"}
    r = parity_goal.installed(newest=parity.norm_version(rec["version"]))
    return {"ok": r["ok"], "detail": "; ".join(r["problems"]) if not r["ok"] else
            "installed " + ", ".join(f"{k} {v}" for k, v in r["versions"].items())}
