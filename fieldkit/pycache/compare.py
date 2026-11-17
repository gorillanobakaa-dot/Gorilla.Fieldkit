"""The hook-timing test in the main checkout against a fresh job worktree (Zero's probe of 2026-11-12, saved).

    fieldkit pycache compare --worktree [--repo PATH] [--json]

Runs `pytest tests/test_zero_command.py -k loads_no_yaml -s` in the checkout and in a fresh detached worktree of its
HEAD (made in the scratch root, removed after), and quotes the "hook process" line of each: the difference is what a
worker pays for having no bytecode. Exit: 0 fine, 1 error.
"""
import argparse
import os
import shutil
import subprocess
import sys

from . import core
from .out import emit

TEST = ["tests/test_zero_command.py", "-k", "loads_no_yaml", "-s", "-q", "-p", "no:cacheprovider"]


def hook_line(tree):
    r = subprocess.run([sys.executable, "-m", "pytest", *TEST], capture_output=True, text=True, cwd=str(tree), timeout=600)
    for ln in (r.stdout + r.stderr).splitlines():
        if "hook process" in ln:
            return ln.strip()
    return f"no 'hook process' line (pytest exit {r.returncode}): {(r.stdout + r.stderr).strip()[-160:]}"


def main(argv):
    ap = argparse.ArgumentParser(prog="fieldkit pycache compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo")
    ap.add_argument("--worktree", action="store_true", help="required: compare against a fresh worktree")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.worktree:
        emit(a.json, "ERROR", "nothing to compare: pass --worktree", "fieldkit pycache compare --worktree")
        return 1
    repo = core.repo_of(a.repo)
    from ..lifecycle import worktrees
    path, why = worktrees.make(repo, "pycache-compare", fetch=False)
    if path is None:
        emit(a.json, "ERROR", why, "run it inside a git checkout")
        return 1
    try:
        main_line, wt_line = hook_line(repo), hook_line(path)
    finally:
        main_git = worktrees.top(repo)
        subprocess.run(["git", "-C", str(main_git), "worktree", "remove", "--force", str(path)], capture_output=True)
        shutil.rmtree(path, ignore_errors=True)
        try:
            os.unlink(str(path) + ".json")
        except OSError:
            pass
    emit(a.json, "OK", "main checkout vs fresh worktree", "fieldkit pycache audit", {"main": main_line, "worktree": wt_line},
         [f"main checkout: {main_line}", f"fresh worktree: {wt_line}"])
    return 0
