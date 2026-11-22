"""The build environment a recipe asks for, worked out on this machine (Linux and macOS; Windows: nothing).

A recipe says it in data (`toolenv:`); this reads it:

    toolenv:
      path_if_missing: {cargo: ["~/.cargo/bin"]}     # a tool not on PATH is looked for in these folders
      tmpdir: {min_free_gb: 2, fallback: "~/.cache/fieldkit-scratch/tmp"}   # /tmp too small or noexec

-> the variables to add to the environment of every command stage and toolchain probe ({} when nothing is needed).
A build that needs a hand-set PATH or TMPDIR (job T-20261101-161350-424c2c, plan W6) now needs neither."""
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _which(tool, path):
    return shutil.which(tool, path=path)


def _can_exec(folder):
    """True when a program in `folder` may run (the folder is not mounted noexec)."""
    try:
        with tempfile.NamedTemporaryFile("w", dir=folder, suffix=".sh", delete=False) as f:
            f.write("#!/bin/sh\nexit 0\n")
            name = f.name
        try:
            os.chmod(name, 0o700)
            return os.system(f"'{name}' >/dev/null 2>&1") == 0
        finally:
            os.unlink(name)
    except OSError:
        return False


def tmp_ok(folder, min_free_gb, free=None, can_exec=None):
    folder = str(folder)
    try:
        left = (free or (lambda p: shutil.disk_usage(p).free))(folder)
    except OSError:
        return False
    return left >= min_free_gb * (1 << 30) and (can_exec or _can_exec)(folder)


def resolve(spec, environ=None, platform=None, free=None, can_exec=None):
    """-> {VAR: value} to add; `free`, `can_exec`, `environ` and `platform` are for tests."""
    if not spec or (platform or sys.platform) == "win32":
        return {}
    environ = os.environ if environ is None else environ
    out = {}
    path = environ.get("PATH", os.defpath)
    add = []
    for tool, folders in (spec.get("path_if_missing") or {}).items():
        if _which(tool, path):
            continue
        for d in folders:
            d = os.path.expanduser(d)
            if os.path.isdir(d) and d not in add and _which(tool, d):
                add.append(d)
                break
    if add:
        out["PATH"] = os.pathsep.join(add + ([path] if path else []))
    t = spec.get("tmpdir")
    if t:
        cur = environ.get("TMPDIR") or "/tmp"
        need = float(t.get("min_free_gb", 2))
        if not (os.path.isdir(cur) and tmp_ok(cur, need, free, can_exec)):
            fb = Path(os.path.expanduser(t["fallback"]))
            fb.mkdir(parents=True, exist_ok=True)
            out["TMPDIR"] = str(fb)
    return out


def _find_cache_tool(name, path, home, platform, which):
    """Locate a compiler cache binary (`ccache` or `sccache`) on PATH or in standard local tool folders."""
    found = which(name, path=path)
    if found:
        return found
    exe = f"{name}.exe" if platform == "win32" else name
    home_p = Path(home)
    candidates = [
        home_p / ".mozbuild" / name / exe,
        home_p / ".cargo" / "bin" / exe,
        home_p / ".local" / "bin" / exe,
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def cache_env(environ=None, platform=None, which=None, home=None, ccache_symlink_dir="/usr/lib/ccache"):
    """Compiler-cache environment for non-repro emerge stages (Linux, macOS, and Windows).

    Every Fieldkit project built through `fieldkit emerge` automatically uses installed compiler caches:
    - Rust: sets RUSTC_WRAPPER=<sccache> when sccache is installed.
    - C/C++ (CMake/Ninja/Make): sets CMAKE_C_COMPILER_LAUNCHER and CMAKE_CXX_COMPILER_LAUNCHER to ccache (or sccache),
      and on POSIX inserts /usr/lib/ccache onto PATH ahead of system compilers when present.
    - Go: preserves Go's native persistent GOCACHE (no C compiler involved when CGO_ENABLED=0).
    Explicit user/stage settings in `environ` are never overwritten.
    """
    plat = platform or sys.platform
    env = os.environ if environ is None else environ
    path = env.get("PATH", os.defpath)
    which_fn = which or shutil.which
    home_dir = home if home is not None else os.path.expanduser("~")

    sccache = _find_cache_tool("sccache", path, home_dir, plat, which_fn)
    ccache = _find_cache_tool("ccache", path, home_dir, plat, which_fn)
    out = {}

    if sccache and not env.get("RUSTC_WRAPPER"):
        out["RUSTC_WRAPPER"] = sccache

    c_launcher = ccache or sccache
    if c_launcher:
        if not env.get("CMAKE_C_COMPILER_LAUNCHER"):
            out["CMAKE_C_COMPILER_LAUNCHER"] = c_launcher
        if not env.get("CMAKE_CXX_COMPILER_LAUNCHER"):
            out["CMAKE_CXX_COMPILER_LAUNCHER"] = c_launcher

    if plat != "win32" and ccache and ccache_symlink_dir and os.path.isdir(ccache_symlink_dir):
        parts = [p for p in path.split(os.pathsep) if p]
        if ccache_symlink_dir not in parts:
            # Keep user tool dirs (~/.cargo/bin, etc.) ahead of /usr/lib/ccache while placing ccache ahead of /usr/bin.
            idx = next((i for i, p in enumerate(parts) if p in ("/usr/bin", "/bin", "/usr/local/bin")), len(parts))
            parts.insert(idx, ccache_symlink_dir)
            out["PATH"] = os.pathsep.join(parts)

    return out
