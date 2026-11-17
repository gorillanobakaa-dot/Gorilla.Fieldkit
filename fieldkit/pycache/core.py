"""Bytecode facts, shared by every `fieldkit pycache` verb. No import-time work; stdlib + yaml only.

Why (owner, 2026-11-12): the environment Claude Code gives every hook has PYTHONDONTWRITEBYTECODE=1, so Python read
.pyc but never wrote one: a job worktree had none, the main checkout went stale after every pull, and each hook
process (2-4 per tool call) recompiled 50-300 ms of source. The fix is a .pth file (runs at every interpreter
start, after the environment is read) that turns writing back on; `warm` fills the cache once.
"""
import importlib.util
import os
import shutil
import site
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def scratch_dir():
    """The scratch root (fieldkit scratch): the one place a tool's temporary copies may be deleted directly."""
    try:
        from ..scratch import root
        d = Path(root())
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
    except Exception:  # noqa: BLE001 - no scratch root readable: the system temp dir
        return None


def hotpath():
    import yaml
    return yaml.safe_load((HERE / "hotpath.yaml").read_text(encoding="utf-8"))


def repo_of(arg=None):
    p = Path(arg or os.getcwd()).resolve()
    for q in [p, *p.parents]:
        if (q / "fieldkit" / "__init__.py").is_file():
            return q
    return REPO


def sources(repo):
    return sorted(p for p in (Path(repo) / "fieldkit").rglob("*.py") if "__pycache__" not in p.parts)


def cache_file(src):
    """The .pyc this interpreter would read for `src` (PYTHONPYCACHEPREFIX and sys.pycache_prefix aware)."""
    return importlib.util.cache_from_source(str(src))


def state(src):
    """'fresh' | 'stale' | 'missing': the .pyc header against the source (timestamp or hash based)."""
    try:
        head = Path(cache_file(src)).open("rb").read(16)
    except OSError:
        return "missing"
    if len(head) < 16 or head[:4] != importlib.util.MAGIC_NUMBER:
        return "stale"
    flags = int.from_bytes(head[4:8], "little")
    if flags & 1:                                    # hash-based: valid until the source hash changes
        return "fresh"
    try:
        st = Path(src).stat()
    except OSError:
        return "missing"
    mtime = int.from_bytes(head[8:12], "little")
    size = int.from_bytes(head[12:16], "little")
    return "fresh" if mtime == (int(st.st_mtime) & 0xFFFFFFFF) and size == (st.st_size & 0xFFFFFFFF) else "stale"


def count(files):
    out = {"fresh": 0, "stale": 0, "missing": 0}
    for f in files:
        out[state(f)] += 1
    return out


def closure(repo, modules, python=None):
    """-> (source files of every fieldkit.* module the hot modules load, {module: error}). One subprocess."""
    code = ("import sys,json,importlib;sys.dont_write_bytecode=True;sys.path.insert(0,sys.argv[1]);bad={}\n"
            "for m in sys.argv[2:]:\n"
            "    try: importlib.import_module(m)\n"
            "    except BaseException as e: bad[m]=type(e).__name__+': '+str(e)[:120]\n"
            "files=sorted({getattr(v,'__file__',None) for k,v in list(sys.modules.items()) if k=='fieldkit' or k.startswith('fieldkit.')}-{None})\n"
            "print(json.dumps({'files':files,'bad':bad}))")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([python or sys.executable, "-c", code, str(repo), *modules], capture_output=True, text=True,
                       cwd=tempfile.gettempdir(), env=env, timeout=120)
    import json
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [], {m: f"import probe failed: {(r.stderr or r.stdout)[-120:]}" for m in modules}
    root = str(Path(repo).resolve())
    return [Path(f) for f in d["files"] if str(Path(f).resolve()).startswith(root)], d["bad"]


# ---------------------------------------------------------------- the .pth that keeps writing on

def pth_dir():
    try:
        d = sysconfig.get_paths().get("purelib")
    except Exception:  # noqa: BLE001
        d = None
    return Path(d or site.getsitepackages()[0])


def pth_status(cfg=None, directory=None):
    """-> (ok, path). ok: the file exists in this interpreter's site-packages and still sets writing on."""
    cfg = (cfg or hotpath())["pth"]
    p = Path(directory or pth_dir()) / cfg["file"]
    try:
        return cfg["marker"] in p.read_text(encoding="utf-8"), p
    except OSError:
        return False, p


def pth_install(cfg=None, directory=None):
    cfg = (cfg or hotpath())["pth"]
    p = Path(directory or pth_dir()) / cfg["file"]
    p.write_text(cfg["line"] + "\n", encoding="utf-8")
    return p


def ensure_pth():
    """Fail-soft: write the .pth when this interpreter has none (after a venv rebuild). -> True when it is there."""
    try:
        if pth_status()[0]:
            return True
        pth_install()
        return True
    except Exception:  # noqa: BLE001 - read-only site-packages (a system Python): the audit says so
        return False


# ---------------------------------------------------------------- warm

def warm(repo, workers=0):
    """Compile every fieldkit/*.py into the cache this interpreter reads. Idempotent: fresh files are skipped."""
    import compileall
    before = count(sources(repo))
    ok = compileall.compile_dir(str(Path(repo) / "fieldkit"), quiet=2, workers=workers, legacy=False)
    return before, count(sources(repo)), bool(ok)


def warm_background(path):
    """Fail-soft warm of a new worktree, in a detached process so the job starts at once. Never raises."""
    try:
        p = Path(path)
        if not (p / "fieldkit" / "__init__.py").is_file():
            return False
        env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
        kw = {"creationflags": 0x08000000 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
        subprocess.Popen([sys.executable, "-m", "compileall", "-q", "-j", "0", str(p / "fieldkit")], env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=str(p), **kw)
        return True
    except Exception:  # noqa: BLE001 - a warm is an optimization, never a reason to fail a job
        return False


# ---------------------------------------------------------------- where the variable comes from

def env_origin(var="PYTHONDONTWRITEBYTECODE"):
    """-> None (not set here) or 'pid NAME' of the topmost ancestor process that holds `var` while its own parent does
    not: where it entered the process tree (the `claude` process on the VAIO, not any profile file)."""
    if var not in os.environ:
        return None
    try:
        import psutil
        chain, p = [], psutil.Process()
        while p is not None:
            try:
                has = var in p.environ()
            except (psutil.Error, OSError):
                has = None
            chain.append((p.pid, p.name(), has))
            p = p.parent()
        holders = [c for c in chain if c[2]]
        for pid, name, has in holders:               # first holder whose parent does not hold it
            i = chain.index((pid, name, has))
            if i + 1 >= len(chain) or not chain[i + 1][2]:
                return f"first seen in pid {pid} ({name}), inherited by {len(holders) - 1} child process(es); its parent does not show it (set by the app at run time)"
        return "set in this process tree (the setting process is not readable)"
    except ImportError:
        return "set in this process's environment (psutil is missing: ancestors not walked)"


# ---------------------------------------------------------------- timing, cold vs warm

def import_ms(python, root, module, runs, env):
    best = None
    for _ in range(max(1, runs)):
        t = time.perf_counter()
        r = subprocess.run([python, "-c", f"import sys;sys.dont_write_bytecode=True;sys.path.insert(0,{str(root)!r});import {module}"],
                           capture_output=True, env=env, cwd=tempfile.gettempdir(), timeout=120)
        ms = (time.perf_counter() - t) * 1000
        if r.returncode != 0:
            return None
        best = ms if best is None else min(best, ms)
    return best


def timing(repo, modules, runs, python=None, tmp_root=None):
    """-> {module: (cold_ms, warm_ms)}. Cold = a copy of the package with no bytecode, run with writing off (so every
    run recompiles, as a worker worktree does); warm = the same copy compiled once. stdlib and site-packages are
    cached the same way in both, so the difference is Fieldkit's own source."""
    python = python or sys.executable
    out = {}
    tmp = Path(tempfile.mkdtemp(prefix="pycache-timing-", dir=tmp_root or scratch_dir()))
    try:
        shutil.copytree(Path(repo) / "fieldkit", tmp / "fieldkit",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPYCACHEPREFIX", "PYTHONPATH")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        cold = {m: import_ms(python, tmp, m, runs, env) for m in modules}
        wenv = {k: v for k, v in env.items() if k != "PYTHONDONTWRITEBYTECODE"}
        subprocess.run([python, "-m", "compileall", "-q", str(tmp / "fieldkit")], env=wenv, capture_output=True,
                       timeout=300)
        warm_ = {m: import_ms(python, tmp, m, runs, env) for m in modules}
        for m in modules:
            out[m] = (cold[m], warm_[m])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out
