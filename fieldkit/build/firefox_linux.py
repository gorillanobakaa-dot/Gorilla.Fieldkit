# Version: 1.0.0 · updated 26-10-15-17-56
"""Firefox on Linux (Debian): the pieces, as tested functions, plus pipeline stages
(build/pipelines/firefox-linux.yaml).

Written 2026-10-14 from what already existed and was never in the harness:
  - the public gorilla-firefox folder: recreate.sh (its preflight is the tool list and the disk/RAM
    rows below), scripts/run_build_and_capture.sh (the build wrapper), scripts/build_deb.sh (the
    package), patches/apply.sh (--check, then apply; NEW_FILES copied; user.js never copied);
  - the 154 build's 18 solved compile errors (signatures/firefox-linux.yaml), one of which is an
    environment trap this module avoids on every call: python/mozbuild's
    is_running_under_coding_agent() changes mach's behaviour when CLAUDECODE is set, so the
    stages run mach with that variable removed (ERROR 4, 2026-07-15);
  - the Windows harness's shape (stages, pinned revision, hashes recorded), without its
    orchestrator, which is Windows-only and not on this machine.

What this module does NOT do: port the patch set to a new Firefox. That is
`fieldkit build-harness start firefox` (buildh/firefox.py). This module builds a tree the patch
set applies to; patches-check says whether it does, and refuses otherwise.

Every stage returns {"ok": bool, "detail": str, ...}. Nothing here calls sudo.
"""
import hashlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

# recreate.sh's preflight, as rows. Stated in that source; the LLVM major is the mozconfig's.
TOOLS = ["git", "python3", "patch", "rsync", "curl", "unzip", "node", "rustc", "cargo", "cbindgen", "sccache",
         "clang-21", "clang++-21", "lld-21", "llvm-ar-21", "llvm-nm-21", "llvm-ranlib-21", "llvm-objcopy-21",
         "llvm-objdump-21"]
NEED_GB, MIN_GB = 25, 20          # recreate.sh: "Compiling Firefox needs ~25 GB"; refuses under 20
MIN_RAM_GB = 8                    # recreate.sh: under 8 GB with no swap, the link step dies
# ERROR 4: mozbuild util.py is_running_under_coding_agent() checks all four; Linux dropped only CLAUDECODE until the
# Windows preflight port (agent-env, 2026-10-14) showed the other three would silence mach the same way
MACH_ENV_DROP = ("CLAUDECODE", "CODEX_SANDBOX", "GEMINI_CLI", "OPENCODE")


def _v(ctx, key):
    return ctx.vars[key]


def _run(cmd, cwd=None, env=None, timeout=None):
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def mach_env(base=None):
    """The environment mach runs in: the caller's, minus the coding-agent markers (ERROR 4)."""
    env = dict(base if base is not None else os.environ)
    for k in MACH_ENV_DROP:
        env.pop(k, None)
    return env


def _free_gb(path):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / 1e9
    except OSError:
        return 0.0


def _ram_gb():
    try:
        for line in open("/proc/meminfo", encoding="utf-8"):
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1e6
    except OSError:
        pass
    return 0.0


# ---------------------------------------------------------------- deps
def stage_deps(ctx, which=shutil.which, free_gb=_free_gb, ram_gb=_ram_gb):
    """Is this machine fit to compile Firefox? Tools, disk, memory, each a row with evidence.
    apt packages are NOT listed here: `mach bootstrap` (the bootstrap stage) installs and names them."""
    work = Path(_v(ctx, "work"))
    missing = [t for t in TOOLS if not which(t)]
    rows = [{"check": f"tool {t}", "ok": t not in missing} for t in TOOLS]
    parent = work if work.exists() else work.parent if work.parent.exists() else Path.home()
    disk = free_gb(str(parent))
    rows.append({"check": f"disk >= {MIN_GB} GB free at {parent}", "ok": disk >= MIN_GB, "detail": f"{disk:.0f} GB"})
    ram = ram_gb()
    rows.append({"check": f"RAM >= {MIN_RAM_GB} GB", "ok": ram >= MIN_RAM_GB, "detail": f"{ram:.0f} GB"})
    ok = not missing and disk >= MIN_GB and ram >= MIN_RAM_GB
    detail = "fit to build" if ok else ("missing: " + ", ".join(missing) if missing else "") + \
        (f" disk {disk:.0f} GB < {MIN_GB}" if disk < MIN_GB else "") + (f" RAM {ram:.0f} GB < {MIN_RAM_GB}" if ram < MIN_RAM_GB else "")
    return {"ok": ok, "rows": rows, "missing": missing, "detail": detail.strip()}


# ---------------------------------------------------------------- source
def stage_source(ctx, restore=None):
    """A fresh working copy of the vaulted release. The vault is the only source: never a clone of main."""
    from ..buildh import vault
    version, src = _v(ctx, "version"), Path(_v(ctx, "src"))
    if src.exists():
        return verify_source(ctx) | {"detail": f"already present: {src}"}
    (restore or vault.restore)("firefox", str(src), version=version)
    return verify_source(ctx)


def verify_source(ctx):
    version, src = _v(ctx, "version"), Path(_v(ctx, "src"))
    vt = src / "browser" / "config" / "version.txt"
    if not vt.is_file():
        return {"ok": False, "detail": f"not a Firefox tree: {src} (no browser/config/version.txt)"}
    have = vt.read_text(encoding="utf-8").strip()
    return {"ok": have == version, "version": have, "detail": f"tree says {have}, pipeline wants {version}"}


# ---------------------------------------------------------------- mozconfig
def stage_mozconfig(ctx):
    """The tuned mozconfig from the harness folder, copied in and its hash recorded, so build-verify
    can say which mozconfig the binary came from."""
    harness, src = Path(_v(ctx, "harness")), Path(_v(ctx, "src"))
    m = harness / "mozconfig"
    if not m.is_file():
        return {"ok": False, "detail": f"no mozconfig at {m}"}
    shutil.copy2(m, src / "mozconfig")
    sha = hashlib.sha256((src / "mozconfig").read_bytes()).hexdigest()
    (src / "mozconfig.sha256").write_text(sha + "\n", encoding="utf-8")
    return {"ok": True, "sha256": sha, "detail": f"mozconfig installed ({sha[:12]})"}


# ---------------------------------------------------------------- patches
_COUNTS = re.compile(r"(\d+) clean, (\d+) applied-with-fuzz, (\d+) failed|patches\s*:\s*(\d+)")


def ported_commits(src):
    """Commits on top of the pristine root commit (Mozilla's release) -> int, or None when src is not a git tree."""
    r = subprocess.run(["git", "-C", str(src), "rev-list", "--count", "HEAD"], capture_output=True, text=True)
    return int(r.stdout.strip()) - 1 if r.returncode == 0 and r.stdout.strip().isdigit() else None


def task_work_trees(version, state=None):
    """Work trees of build-harness tasks that hold this Firefox version AND carry a port (commits on top of the
    pristine release) -> [(task id, path)]. 2026-10-15: the pipeline's default src was the pristine copy under
    Build.Work while the 158 port lived in the task's work tree; sessions passed --var src= by hand, from memory."""
    import json as _json
    if state is None:
        from ..buildh import task
        state = task.STATE
    out = []
    for f in sorted(Path(state).glob("*/task.json")):
        try:
            w = Path(_json.loads(f.read_text(encoding="utf-8")).get("workdir") or "")
        except (OSError, ValueError):
            continue
        vt = w / "browser" / "config" / "version.txt"
        if vt.is_file() and vt.read_text(encoding="utf-8").strip() == version and (ported_commits(w) or 0) > 0:
            out.append((f.parent.name, w))
    return out


def _apply(ctx, check_only, run=_run):
    harness, src = Path(_v(ctx, "harness")), Path(_v(ctx, "src"))
    n = ported_commits(src)
    if n:
        # a ported work tree already carries the port as commits; the public set (an older version) is not applied
        # over it. The set is rebuilt FROM this tree at publishing (S9 export).
        return {"ok": True, "failed": 0, "mode": "ported", "detail": f"ported work tree ({n} commit(s) on top of the "
                f"pristine release): the patch set is not re-applied; export (S9) rebuilds it from this tree"}
    script = harness / "patches" / "apply.sh"
    if not script.is_file():
        return {"ok": False, "detail": f"no apply.sh at {script}"}
    cmd = ["bash", str(script)] + (["--check"] if check_only else []) + [str(src)]
    rc, out = run(cmd, cwd=str(harness))
    failed = re.findall(r"^\s*(?:FAIL|failed|x)\s+(\S+)", out, re.M)
    m = re.search(r"ok\s*:\s*(\d+).*?fail\s*:\s*(\d+)", out, re.S | re.I)
    fail_n = int(m.group(2)) if m else len(failed)
    ok = rc == 0 and fail_n == 0
    if ok:
        detail = "every patch applies"
    elif fail_n:
        detail = f"{fail_n} patch(es) do not apply; refusing to build a partly patched tree" + \
            (": " + ", ".join(failed[:5]) if failed else "")
    else:   # 2026-10-14: apply.sh exited non-zero before counting (no source tree): say its last line, not "0 patches"
        detail = f"apply.sh exited {rc}: " + (out.strip().splitlines()[-1] if out.strip() else "(no output)")
    return {"ok": ok, "failed": fail_n, "failed_patches": failed[:50], "mode": "check" if check_only else "apply",
            "detail": detail, "output_tail": out[-2000:]}


def stage_patches(ctx, check_only=False, run=_run):
    """`apply.sh --check` (dry run) or `apply.sh` (apply, then NEW_FILES copied). A failed patch is a port
    job for buildh/firefox.py, not something to build over."""
    return _apply(ctx, check_only, run=run)


def verify_patches(ctx, run=_run):
    """After apply, --check must report every patch as already present (apply.sh counts a reverse-applying
    patch as skip) and nothing failing."""
    r = _apply(ctx, True, run=run)
    r["detail"] = "patch set is in the tree" if r["ok"] else r["detail"]
    return r


# ---------------------------------------------------------------- bootstrap
def stage_bootstrap(ctx, run=_run, env=None):
    """Mozilla's own toolchain helpers (cbindgen, nasm, dump_syms, ...) into ~/.mozbuild, and the apt
    packages mach names. Non-interactive; CLAUDECODE removed from the environment (ERROR 4)."""
    src = Path(_v(ctx, "src"))
    rc, out = run(["./mach", "--no-interactive", "bootstrap", "--application-choice=browser"], cwd=str(src),
                  env=mach_env(env), timeout=7200)
    return {"ok": rc == 0, "detail": "bootstrap finished" if rc == 0 else f"mach bootstrap exited {rc}",
            "output_tail": out[-3000:]}


def verify_bootstrap(ctx, home=None):
    mb = Path(home or Path.home()) / ".mozbuild"
    return {"ok": mb.is_dir(), "detail": f"{mb} {'present' if mb.is_dir() else 'missing: run the bootstrap stage'}"}


# ---------------------------------------------------------------- build
# THE BUILD-COST DECISION (owner's convention, brain lesson Unpackaged_Dev_Build_Inject_Dont_Full_Compile,
# 2026-08-02; taught to the harness 2026-10-15): the dev build is UNPACKED (no omni.ja; JS, CSS, FTL and prefs are
# loose files in dist/bin), so only compiled sources need the full `./mach build` (hours on the VAIO). Everything
# else needs `./mach build faster` (minutes). omni.ja is packed only for the artifact uploaded to GitHub.
COMPILED = re.compile(r"(\.(c|cc|cpp|cxx|h|hh|hpp|mm|m|s|S|asm|rs|toml|webidl|idl|ipdl|ipdlh|py|configure|build)|"
                      r"(^|/)(moz\.build|Cargo\.lock|StaticPrefList\.yaml|mozconfig|old-configure\.in|"
                      r"moz\.configure|Makefile\.in|jar\.mn|jar\.inc\.mn))$")
STAMP = ".gorilla-built-commit"


def build_mode(src, objdir=None):
    """-> ("full" | "faster" | "nothing", why). Compares the tree with the commit of the last successful build
    (recorded in the objdir); no record, a missing objdir or any compiled source changed means full."""
    src = Path(src)
    od = Path(objdir) if objdir else src / "obj-x86_64-pc-linux-gnu"
    stamp = od / STAMP
    if not (od / "dist" / "bin").is_dir() or not stamp.is_file():
        return "full", "no successful build recorded in the objdir"
    last = stamp.read_text().strip()
    r = subprocess.run(["git", "-C", str(src), "diff", "--name-only", last], capture_output=True, text=True)
    if r.returncode != 0:
        return "full", f"cannot compare with the last built commit {last[:12]}"
    changed = [f for f in r.stdout.split() if not f.startswith("build_logs/")]
    if not changed:
        return "nothing", f"no change since the last build ({last[:12]})"
    compiled = [f for f in changed if COMPILED.search(f)]
    if compiled:
        return "full", f"{len(compiled)} compiled source(s) changed, e.g. {compiled[0]}"
    return "faster", f"{len(changed)} file(s) changed, none compiled (e.g. {changed[0]}): mach build faster"


def _record_built(src, run=_run):
    rc, head = run(["git", "-C", str(src), "rev-parse", "HEAD"])
    od = Path(src) / "obj-x86_64-pc-linux-gnu"
    if rc == 0 and od.is_dir():
        (od / STAMP).write_text(head.strip() + "\n", encoding="utf-8")


def stage_build(ctx, run=_run, env=None):
    """`scripts/run_build_and_capture.sh SRC`: ./mach build with the whole output captured and summarised,
    CLAUDECODE removed (ERROR 4). Hours. Run it under `fieldkit guard` for the thermal check. When no compiled
    source changed since the last successful build, `./mach build faster` instead (build_mode)."""
    harness, src = Path(_v(ctx, "harness")), Path(_v(ctx, "src"))
    mode, why = build_mode(src)
    if mode == "nothing":
        return {"ok": True, "detail": f"nothing to build: {why}", "mode": mode}
    if mode == "faster":
        logs = src / "build_logs"
        logs.mkdir(exist_ok=True)
        rc, out = run(["./mach", "build", "faster"], cwd=str(src), env=mach_env(env), timeout=7200)
        (logs / "latest_faster.log").write_text(out, encoding="utf-8")
        if rc == 0:
            _record_built(src, run)
        return {"ok": rc == 0, "mode": mode, "detail": f"mach build faster exited {rc} ({why})",
                "output_tail": out[-3000:]}
    script = harness / "scripts" / "run_build_and_capture.sh"
    if not script.is_file():
        return {"ok": False, "detail": f"no build wrapper at {script}"}
    rc, out = run(["bash", str(script), str(src)], cwd=str(harness), env=mach_env(env), timeout=43200)
    if rc == 0:
        _record_built(src, run)
    detail = (f"build wrapper exited 0 (full build: {why})" if rc == 0 else f"build wrapper exited {rc}")
    if rc != 0:
        detail += "; " + failure_summary(src / "build_logs" / "latest_build.log")
    return {"ok": rc == 0, "detail": detail, "output_tail": out[-3000:]}


def failure_summary(log):
    """The failed build named in one line: the first compiler error and what the signatures know about it.
    Added 2026-10-15: the first 158 build failed and the pipeline log said only "build wrapper exited 2"."""
    from .triage import triage_file
    if not Path(log).is_file():
        return f"no mach log at {log}"
    r = triage_file(log, "firefox-linux")
    first = r["errors"][0]["line"][:200] if r["errors"] else "no error line"
    if r["matches"]:
        m = r["matches"][0]
        return f"known failure {m['id']}: {first}. Fix: {' '.join(str(m.get('fix', '')).split())}"
    return f"UNRECOGNISED failure, add a signature to signatures/firefox-linux.yaml: {first} (log {log})"


def dist_bin(ctx):
    return Path(_v(ctx, "src")) / "obj-x86_64-pc-linux-gnu" / "dist" / "bin"


def verify_build(ctx, run=_run):
    """The binary that came out says the pinned version. An exit code is not a browser."""
    version = _v(ctx, "version")
    fx = dist_bin(ctx) / "firefox"
    if not fx.is_file():
        return {"ok": False, "detail": f"no binary at {fx}"}
    rc, out = run([str(fx), "--version"], env=mach_env())
    said = out.strip().splitlines()[-1] if out.strip() else ""
    return {"ok": rc == 0 and version in said, "said": said, "detail": f"{fx.name} --version: {said or '(nothing)'}"}


# ---------------------------------------------------------------- package
def dangling_links(dist, src):
    """Symlinks in the unpacked dist/bin whose target is gone -> (removed-from-the-tree, other).

    D-158-11 keeps the dev build unpacked: dist/bin holds SYMLINKS to the source files. A file deleted from the tree
    (2026-10-15, D-158-08: seven AI texts and stylesheets) leaves its link behind, pointing at nothing; the build does
    not prune it, and build_deb.sh's rsync stops with code 23 ("symlink has no referent"). A link whose target lies
    inside the source tree and no longer exists is safe to drop: nothing can ship from it."""
    gone, other = [], []
    if not Path(dist).is_dir():
        return gone, other
    src = Path(src).resolve()
    for p in Path(dist).rglob("*"):
        if p.is_symlink() and not p.exists():
            target = Path(os.readlink(p))
            target = target if target.is_absolute() else (p.parent / target)
            (gone if str(target.resolve()).startswith(str(src) + os.sep) else other).append((p, target))
    return gone, other


def prune_dangling(dist, src, trash=None):
    """Move dangling links of files removed from the tree to the trash (house rule: never delete) -> (moved, refused)."""
    gone, other = dangling_links(dist, src)
    moved = []
    if other:                       # refuse BEFORE touching anything: a person looks at the whole set first
        return moved, [f"{p} -> {t}" for p, t in other]
    if gone:
        trash = Path(trash or Path.home() / "Agents.Work.Trash") / ("dangling-dist-links-" + time.strftime("%y-%m-%d-%H-%M"))
        for p, target in gone:
            dest = trash / p.relative_to(dist)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))      # moves the link itself; os.rename fails across filesystems
            moved.append(f"{p.relative_to(dist)} -> {target}")
    return moved, []


def stage_package(ctx, run=_run):
    """`scripts/build_deb.sh "" DIST_BIN OUT_DIR deb_template BRANDING`, exactly as recreate.sh calls it."""
    harness, src, release = Path(_v(ctx, "harness")), Path(_v(ctx, "src")), Path(_v(ctx, "release"))
    script = harness / "scripts" / "build_deb.sh"
    if not script.is_file():
        return {"ok": False, "detail": f"no package script at {script}"}
    moved, refused = prune_dangling(dist_bin(ctx), src)
    if refused:
        return {"ok": False, "detail": "dist/bin has links to nothing that are NOT files removed from the tree "
                f"(look before packaging): {'; '.join(refused[:5])}"}
    release.mkdir(parents=True, exist_ok=True)
    rc, out = run(["bash", str(script), "", str(dist_bin(ctx)), str(release), str(harness / "deb_template"),
                   str(src / "browser" / "branding" / "gorilla")], cwd=str(harness))
    detail = "build_deb.sh exited 0" + (f"; {len(moved)} dangling dist/bin link(s) of removed files moved to the "
                                         f"trash first: {', '.join(m.split(' -> ')[0] for m in moved[:4])}" if moved else "")
    if rc != 0:
        # 2026-10-15: the gate refused FONT-001 and the pipeline log said only "build_deb.sh exited 1"
        why = [l.strip() for l in out.splitlines() if re.match(r"\s*(FAIL|FATAL|ERROR)\b", l)]
        detail = f"build_deb.sh exited {rc}" + (": " + " | ".join(why[:6]) if why else "")
    return {"ok": rc == 0, "detail": detail, "output_tail": out[-3000:]}


def verify_package(ctx, run=_run):
    """The newest .deb in the release folder carries the pinned version in its control file, and its sha256 is recorded."""
    version, release = _v(ctx, "version"), Path(_v(ctx, "release"))
    debs = sorted(release.glob("*.deb"), key=lambda p: p.stat().st_mtime)
    if not debs:
        return {"ok": False, "detail": f"no .deb in {release}"}
    deb = debs[-1]
    # 2026-10-15: build_deb.sh failed (rsync code 23) and this check read the OLD .deb from the morning and said OK.
    # A .deb older than the browser it should contain is not this build's package.
    lib = dist_bin(ctx) / "libxul.so"
    if lib.is_file() and deb.stat().st_mtime < lib.stat().st_mtime:
        return {"ok": False, "deb": str(deb), "detail": f"{deb.name} is OLDER than the build it should hold "
                f"({time.strftime('%H:%M', time.localtime(deb.stat().st_mtime))} vs libxul.so "
                f"{time.strftime('%H:%M', time.localtime(lib.stat().st_mtime))}): the package stage did not produce it"}
    rc, out = run(["dpkg-deb", "-f", str(deb), "Version"])
    said = out.strip()
    sha = hashlib.sha256(deb.read_bytes()).hexdigest()
    (release / (deb.name + ".sha256")).write_text(f"{sha}  {deb.name}\n", encoding="utf-8")
    return {"ok": rc == 0 and said.startswith(version), "deb": str(deb), "version": said, "sha256": sha,
            "detail": f"{deb.name}: Version {said or '(unreadable)'}"}


def verify_deb(ctx, run=_run):
    """The newest .deb gives a person the right menu name, a launcher that exists, crisp icons and a refreshed menu
    (fieldkit.build.deb_check). Added 2026-10-15: 158 packaged as "Gorilla Unleashed 154"."""
    from . import deb_check
    release = Path(_v(ctx, "release"))
    debs = sorted(release.glob("*.deb"), key=lambda p: p.stat().st_mtime)
    if not debs:
        return {"ok": False, "detail": f"no .deb in {release}"}
    r = deb_check.check(debs[-1], Path(_v(ctx, "src")) / "browser" / "branding" / "gorilla")
    return {"ok": r["ok"], "detail": " | ".join(deb_check.lines(r))}
