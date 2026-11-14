"""fieldkit compileonce: ahead-of-time Python bytecode pre-compilation (.pyc in __pycache__) for Fieldkit and kits.

Owner order, 2026-11-14:
  "MAKE SURE THE PYTHON CODE COMPILES SO WE DON'T HAVE TO RECOMPILE IT ON THE FLY EVERYTIME THE TOOLS START and make
   us OVERLOAD THE PROCESSOR and LOSE TIME."

Both [L15-WIN] (Windows) and [VAIO-LNX] (Linux) run this deterministically on `fieldkit sync --apply`, on
`fieldkit peers link restart`, and on demand:

    fieldkit compileonce [build] [--root DIR] [--force] [--json]   compile every .py in fieldkit/ and kits/ to .pyc
    fieldkit compileonce check   [--root DIR] [--json]             verify every .py has a current .pyc (exit 3 if stale)
"""
from __future__ import annotations

import compileall
import importlib.util
import json
import py_compile
import struct
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRS = ("fieldkit", "kits")
DEFAULT_FILES = ("install_skills.py",)
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", "node_modules", "state", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def iter_py_files(root: Path = ROOT, dirs: tuple[str, ...] = DEFAULT_DIRS) -> list[Path]:
    """Return all tracked/source .py files under root/fieldkit, root/kits, and top-level scripts."""
    root = Path(root).resolve()
    out: list[Path] = []
    for name in DEFAULT_FILES:
        f = root / name
        if f.is_file():
            out.append(f)
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if any(part in SKIP_PARTS for part in p.parts):
                continue
            if p.is_file():
                out.append(p)
    return out


def pyc_is_fresh(py_file: Path) -> tuple[bool, Path, str]:
    """Check whether `py_file` has an up-to-date PEP 3147 `.pyc` file with matching magic, source mtime, and size."""
    pyc_path = Path(importlib.util.cache_from_source(str(py_file)))
    if not pyc_path.is_file():
        return False, pyc_path, "missing"
    try:
        st = py_file.stat()
        header = pyc_path.read_bytes()[:16]
    except OSError as e:
        return False, pyc_path, f"unreadable: {e}"
    if len(header) < 16:
        return False, pyc_path, "truncated header"
    magic, flags, mtime_u32, size_u32 = struct.unpack("<4sIII", header)
    if magic != importlib.util.MAGIC_NUMBER:
        return False, pyc_path, "magic mismatch"
    if flags == 0:
        exp_mtime = int(st.st_mtime) & 0xFFFFFFFF
        exp_size = st.st_size & 0xFFFFFFFF
        if mtime_u32 != exp_mtime or size_u32 != exp_size:
            return False, pyc_path, f"stale header (pyc={mtime_u32}/{size_u32}, src={exp_mtime}/{exp_size})"
    return True, pyc_path, "ok"


def check(root: Path = ROOT, dirs: tuple[str, ...] = DEFAULT_DIRS) -> dict:
    """Verify every .py file under root has a fresh .pyc file."""
    root = Path(root).resolve()
    files = iter_py_files(root, dirs=dirs)
    stale: list[dict] = []
    fresh = 0
    for f in files:
        ok, pyc, reason = pyc_is_fresh(f)
        if ok:
            fresh += 1
        else:
            stale.append({"file": str(f.relative_to(root)), "pyc": str(pyc), "why": reason})
    return {
        "ok": len(stale) == 0 and len(files) > 0,
        "root": str(root),
        "total": len(files),
        "fresh": fresh,
        "stale_count": len(stale),
        "stale": stale,
    }


def build(root: Path = ROOT, dirs: tuple[str, ...] = DEFAULT_DIRS, force: bool = False) -> dict:
    """Compile all .py files under root/fieldkit and root/kits into .pyc bytecode ahead-of-time."""
    t0 = time.monotonic()
    root = Path(root).resolve()
    files = iter_py_files(root, dirs=dirs)
    stale_before = {f for f in files if force or not pyc_is_fresh(f)[0]}
    for d in dirs:
        target = root / d
        if target.is_dir():
            compileall.compile_dir(str(target), quiet=1, force=force, workers=0)
    errors: list[dict] = []
    for f in files:
        ok, _, _ = pyc_is_fresh(f)
        if force or not ok:
            try:
                py_compile.compile(str(f), doraise=True)
                stale_before.add(f)
            except (py_compile.PyCompileError, OSError) as e:
                # If __pycache__ or its .pyc was left root-owned inside a user-writable package directory,
                # move the stale __pycache__ out of the way and retry once.
                if isinstance(e, PermissionError):
                    pyc_dir = f.parent / "__pycache__"
                    backup = f.parent / f".pycache-stale-{int(time.time())}"
                    try:
                        if pyc_dir.is_dir():
                            pyc_dir.rename(backup)
                        py_compile.compile(str(f), doraise=True)
                        stale_before.add(f)
                        continue
                    except (py_compile.PyCompileError, OSError) as e2:
                        e = e2
                errors.append({"file": str(f.relative_to(root)), "error": str(e)})
    chk = check(root, dirs=dirs)
    compiled = max(0, len(stale_before) - len(errors))
    elapsed_ms = round((time.monotonic() - t0) * 1000.0, 1)
    return {
        "ok": len(errors) == 0 and chk["ok"],
        "root": str(root),
        "total": chk["total"],
        "fresh": chk["fresh"],
        "compiled_now": compiled,
        "stale_count": chk["stale_count"],
        "errors": errors,
        "elapsed_ms": elapsed_ms,
    }


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="fieldkit compileonce",
        description="Ahead-of-time Python bytecode pre-compilation (.pyc) so tools never recompile on the fly.",
    )
    ap.add_argument("action", nargs="?", default="build", choices=("build", "check"), help="build (default) or check")
    ap.add_argument("--root", default=str(ROOT), help="repository root (default: Fieldkit root)")
    ap.add_argument("--force", action="store_true", help="recompile all .pyc files even if already fresh")
    ap.add_argument("--json", action="store_true", help="output JSON")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    if a.action == "check":
        res = check(root)
        if a.json:
            print(json.dumps(res, indent=2))
        elif res["ok"]:
            print(f"COMPILEONCE CHECK: OK ({res['fresh']}/{res['total']} .pyc files fresh)")
        else:
            for s in res["stale"][:20]:
                print(f"  [STALE] {s['file']}: {s['why']}")
            print(f"COMPILEONCE CHECK: STALE ({res['stale_count']} of {res['total']} missing or stale; exit 3)")
            print("NEXT: fieldkit compileonce build")
        return 0 if res["ok"] else 3

    res = build(root, force=a.force)
    if a.json:
        print(json.dumps(res, indent=2))
    elif res["ok"]:
        print(
            f"COMPILEONCE: OK ({res['fresh']}/{res['total']} .pyc files ready, "
            f"{res['compiled_now']} compiled in {res['elapsed_ms']} ms, 0 errors)"
        )
    else:
        for err in res["errors"]:
            print(f"  [ERROR] {err['file']}: {err['error']}")
        print(f"COMPILEONCE: FAILED ({len(res['errors'])} syntax/compile error(s), {res['stale_count']} stale)")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
