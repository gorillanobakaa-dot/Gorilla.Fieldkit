"""Where does the time of a Fieldkit function or import go? (Saved probe: Zero's profile of the PreToolUse room step.)

    fieldkit pycache profile MODULE[:FUNC] [--arg JSON] [--top N] [--json]

MODULE alone: the import time of MODULE and what it loads, from `python -X importtime`, cumulative, slowest first.
MODULE:FUNC: cProfile of FUNC(arg), cumulative, slowest first. FUNC is called with --arg (a JSON value) or none.
Preset: fieldkit.autopilot.toolrouter:_room needs no --arg: it builds a hook call in a scratch folder with its own
coordination dir, runs the decision once (so the import is not measured) and profiles the room step.
Exit: 0 fine, 1 error.
"""
import argparse
import cProfile
import importlib
import io
import json
import os
import pstats
import subprocess
import sys
import tempfile

from . import core
from .out import emit


def import_profile(module, top):
    r = subprocess.run([sys.executable, "-X", "importtime", "-c", f"import sys;sys.dont_write_bytecode=True;sys.path.insert(0,{str(core.REPO)!r});import {module}"],
                       capture_output=True, text=True, cwd=tempfile.gettempdir(), timeout=120)
    rows = []
    for ln in r.stderr.splitlines():
        parts = ln.split("|")
        if len(parts) == 3 and parts[1].strip().isdigit():
            rows.append((int(parts[1]), parts[2].rstrip()))
    rows.sort(reverse=True)
    return r.returncode, [f"{us / 1000:8.1f} ms cumulative  {name.strip()}" for us, name in rows[:top]]


def call_profile(module, func, arg, top):
    sys.path.insert(0, str(core.REPO))
    mod = importlib.import_module(module)
    tmp = tempfile.mkdtemp(prefix="pycache-profile-", dir=core.scratch_dir())
    try:
        if (module, func) == ("fieldkit.autopilot.toolrouter", "_room") and arg is None:
            os.environ["FIELDKIT_COORDINATION_DIR"] = os.path.join(tmp, "coord")
            arg = {"session_id": "zzzzzzzz-1", "tool_name": "Edit", "tool_input": {"file_path": os.path.join(tmp, "free.py")},
                   "cwd": tmp}
            mod.verdict(arg)
        fn = getattr(mod, func)
        pr = cProfile.Profile()
        pr.enable()
        fn() if arg is None else fn(arg)
        pr.disable()
        buf = io.StringIO()
        pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(top)
        return buf.getvalue().strip().splitlines()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    ap = argparse.ArgumentParser(prog="fieldkit pycache profile", description=__doc__.split("\n\n")[0])
    ap.add_argument("target", help="MODULE or MODULE:FUNC")
    ap.add_argument("--arg", help="JSON value passed to FUNC")
    ap.add_argument("--top", type=int, default=18)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    module, _, func = a.target.partition(":")
    try:
        if func:
            lines = call_profile(module, func, json.loads(a.arg) if a.arg else None, a.top)
        else:
            rc, lines = import_profile(module, a.top)
            if rc:
                raise ImportError(f"{module} did not import")
    except Exception as e:  # noqa: BLE001 - report, never trace
        emit(a.json, "ERROR", f"{type(e).__name__}: {str(e)[:200]}", "fieldkit pycache profile --help")
        return 1
    emit(a.json, "OK", f"profile of {a.target}", "fieldkit pycache audit --timing", {"lines": lines}, lines)
    return 0
