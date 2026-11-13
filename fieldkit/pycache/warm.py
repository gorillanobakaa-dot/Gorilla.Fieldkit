"""Compile fieldkit/*.py into the bytecode cache (idempotent, seconds), and keep the cache writable.

    fieldkit pycache warm [--repo PATH] [--install-pth] [--json]

--install-pth also writes zz-fieldkit-bytecode.pth into this interpreter's site-packages (Windows: Lib\\site-packages):
a .pth runs at every interpreter start AFTER the environment is read, so PYTHONDONTWRITEBYTECODE=1 (which Claude Code
gives every tool call and hook) no longer stops .pyc files from being written. Run it again after a venv rebuild.
Exit: 0 fine, 1 error (a file did not compile, the .pth could not be written).
"""
import argparse

from . import core
from .out import emit


def main(argv):
    ap = argparse.ArgumentParser(prog="fieldkit pycache warm", description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo")
    ap.add_argument("--install-pth", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    repo = core.repo_of(a.repo)
    extra = {}
    try:
        if a.install_pth:
            extra["pth"] = str(core.pth_install())
    except OSError as e:
        emit(a.json, "ERROR", f"could not write the .pth: {e}", "fieldkit pycache audit")
        return 1
    before, after, ok = core.warm(repo)
    msg = (f"before: ok {before['fresh']} stale {before['stale']} no-cache {before['missing']}; "
           f"after: ok {after['fresh']} stale {after['stale']} no-cache {after['missing']}"
           + (f"; .pth {extra['pth']}" if extra else ""))
    emit(a.json, "OK" if ok else "ERROR", msg, "fieldkit pycache audit",
         {"before": before, "after": after, **extra})
    return 0 if ok else 1
