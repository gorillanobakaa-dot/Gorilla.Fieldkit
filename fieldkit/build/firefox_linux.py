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


def _apply(ctx, check_only, run=_run):
    harness, src = Path(_v(ctx, "harness")), Path(_v(ctx, "src"))
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
def stage_build(ctx, run=_run, env=None):
    """`scripts/run_build_and_capture.sh SRC`: ./mach build with the whole output captured and summarised,
    CLAUDECODE removed (ERROR 4). Hours. Run it under `fieldkit guard` for the thermal check."""
    harness, src = Path(_v(ctx, "harness")), Path(_v(ctx, "src"))
    script = harness / "scripts" / "run_build_and_capture.sh"
    if not script.is_file():
        return {"ok": False, "detail": f"no build wrapper at {script}"}
    rc, out = run(["bash", str(script), str(src)], cwd=str(harness), env=mach_env(env), timeout=43200)
    return {"ok": rc == 0, "detail": "build wrapper exited 0" if rc == 0 else f"build wrapper exited {rc}",
            "output_tail": out[-3000:]}


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
def stage_package(ctx, run=_run):
    """`scripts/build_deb.sh "" DIST_BIN OUT_DIR deb_template BRANDING`, exactly as recreate.sh calls it."""
    harness, src, release = Path(_v(ctx, "harness")), Path(_v(ctx, "src")), Path(_v(ctx, "release"))
    script = harness / "scripts" / "build_deb.sh"
    if not script.is_file():
        return {"ok": False, "detail": f"no package script at {script}"}
    release.mkdir(parents=True, exist_ok=True)
    rc, out = run(["bash", str(script), "", str(dist_bin(ctx)), str(release), str(harness / "deb_template"),
                   str(src / "browser" / "branding" / "gorilla")], cwd=str(harness))
    return {"ok": rc == 0, "detail": "build_deb.sh exited 0" if rc == 0 else f"build_deb.sh exited {rc}",
            "output_tail": out[-3000:]}


def verify_package(ctx, run=_run):
    """The newest .deb in the release folder carries the pinned version in its control file, and its sha256 is recorded."""
    version, release = _v(ctx, "version"), Path(_v(ctx, "release"))
    debs = sorted(release.glob("*.deb"), key=lambda p: p.stat().st_mtime)
    if not debs:
        return {"ok": False, "detail": f"no .deb in {release}"}
    deb = debs[-1]
    rc, out = run(["dpkg-deb", "-f", str(deb), "Version"])
    said = out.strip()
    sha = hashlib.sha256(deb.read_bytes()).hexdigest()
    (release / (deb.name + ".sha256")).write_text(f"{sha}  {deb.name}\n", encoding="utf-8")
    return {"ok": rc == 0 and said.startswith(version), "deb": str(deb), "version": said, "sha256": sha,
            "detail": f"{deb.name}: Version {said or '(unreadable)'}"}
