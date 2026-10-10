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
    checksums   SHA256SUMS-<version>.txt for what was built

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
        raise ValueError(f"no source folder: --var src=PATH, or set goapps.{v.get('name')} in fieldkit.local.json")
    src = Path(settings.expand(src)).expanduser()
    if not (src / "go.mod").is_file():
        raise ValueError(f"{src} has no go.mod: not a Go module")
    return src


def _out(ctx, src):
    o = ctx.vars.get("out") or ""
    return Path(settings.expand(o)).expanduser() if o else src / "dist"


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
    fp = fingerprint(src)
    built = []
    for goos, goarch in _targets(ctx):
        f = out / _file_name(ctx, version, goos, goarch)
        cmd = [go, "build", "-trimpath", "-ldflags", ld, "-o", str(f), v.get("main") or "."]
        r = _run(ctx, cmd, src, f"go-build-{goos}-{goarch}",
                 env={"GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": str(v.get("cgo") or "0")}, timeout=3600)
        if not r.ok or not f.is_file():
            first = [l for l in (r.stderr + r.stdout).splitlines() if l.strip()][:6]
            return {"ok": False, "detail": f"{goos}/{goarch} failed: " + " | ".join(first)[:400], "log": r.log}
        built.append({"target": f"{goos}/{goarch}", "file": str(f), "sha256": _sha(f), "bytes": f.stat().st_size})
    rec = {"ok": True, "version": version, "go": (_load(ctx, src, "toolchain") or {}).get("have"), "ldflags": ld,
           "fingerprint": fp, "built": built, "when": time.strftime("%Y-%m-%d %H:%M:%S"),
           "detail": f"{version}: " + ", ".join(f"{Path(b['file']).name} ({b['bytes']:,} bytes)" for b in built)}
    return _save(ctx, src, "build", rec)


def verify_build(ctx):
    src = _src(ctx)
    rec = _load(ctx, src, "build")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "nothing built yet"}
    if rec.get("fingerprint") != fingerprint(src):
        return {"ok": False, "detail": "the source changed since the build"}
    want = ctx.vars.get("version")
    if want and want != rec.get("version"):
        return {"ok": False, "detail": f"built {rec.get('version')}, asked for {want}"}
    if {b["target"] for b in rec["built"]} != {f"{o}/{a}" for o, a in _targets(ctx)}:
        return {"ok": False, "detail": "the targets changed since the build"}
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


def _runs_here(rec):
    import platform
    me = ("windows" if os.name == "nt" else "linux" if platform.system() == "Linux" else "darwin",
          {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower()))
    for b in rec["built"]:
        if b["target"] == f"{me[0]}/{me[1]}":
            return b["file"]
    return None


def stage_checksums(ctx):
    src = _src(ctx)
    rec = _load(ctx, src, "build")
    if not rec or not rec.get("ok"):
        return {"ok": False, "detail": "run the build stage first"}
    out = _out(ctx, src)
    f = out / f"SHA256SUMS-{rec['version']}.txt"
    lines = [f"{b['sha256']}  {Path(b['file']).name}" for b in rec["built"]]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "file": str(f), "detail": f"{f.name}: {len(lines)} file(s)"}


def verify_checksums(ctx):
    src = _src(ctx)
    rec = _load(ctx, src, "build")
    if not rec:
        return {"ok": False, "detail": "nothing built"}
    f = _out(ctx, src) / f"SHA256SUMS-{rec['version']}.txt"
    if not f.is_file():
        return {"ok": False, "detail": f"{f.name} missing"}
    for line in f.read_text(encoding="utf-8").splitlines():
        sha, _, name = line.partition("  ")
        p = _out(ctx, src) / name
        if not p.is_file() or _sha(p) != sha:
            return {"ok": False, "detail": f"{name} does not match {f.name}"}
    return {"ok": True, "detail": f"{f.name} matches every file"}
