# Version: 1.0.0 · updated 26-11-22-17-00
"""fieldkit installed: the copy of Fieldkit the `fieldkit` command runs follows origin/main, deterministically.

Measured 2026-11-22: the `fieldkit` on PATH imported from Fieldkit-installed, a git worktree left on a detached HEAD
30 commits behind main. `pip install -e` was run once; nothing moved the folder afterwards, so every fix merged to
main stayed dead on this machine (`fieldkit agent-models audit` answered "invalid choice" although main has it).
`fieldkit sync` does not cover it: it follows the branch a checkout tracks, and a detached worktree tracks none.

    fieldkit installed check [--json]    PASS / FAIL: installed HEAD against origin/main (last fetched ref; says so when
                                         it could not fetch). Exit 0 level, 3 behind / dirty / diverged / not found.
    fieldkit installed sync [--json]     fetch origin main, move the installed tree to it, verify, print ONE line.
    fieldkit installed where             the installed tree this command found.

Door statement (every channel is a door). The channel is GitHub through `git fetch`, as for fieldkit sync. Let
through: only on command (the SessionStart hook only REPORTS, never moves), only a FAST-FORWARD to origin/main (main
moves only by a PR merged on green CI), never a dirty tree, never `reset --hard`, never a delete: the move is
`git checkout --detach SHA` after `merge-base --is-ancestor HEAD SHA`, which refuses when files would be overwritten.
`pip install --no-deps -e` is re-run only when pyproject.toml changed between the two commits. Afterwards HEAD must
equal origin/main and the installed `fieldkit --version` must load, else FAIL.

Finding the installed tree: FIELDKIT_INSTALLED_DIR, else where a fresh `python -I` imports `fieldkit` from (the
editable install), else nothing: NOT-FOUND (a machine that runs from a checkout has no separate installed copy).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRANCH = "main"


def _git(repo, *a, timeout=60):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def locate(env=None, run=subprocess.run):
    """-> the installed Fieldkit tree (the git top level `fieldkit` imports from), or None."""
    env = os.environ if env is None else env
    if env.get("FIELDKIT_INSTALLED_DIR"):
        return Path(env["FIELDKIT_INSTALLED_DIR"])
    try:
        r = run([sys.executable, "-I", "-c", "import importlib.util as u;s=u.find_spec('fieldkit');print(s.origin if s else '')"],
                capture_output=True, text=True, cwd=str(Path.home()), timeout=30)
        origin = (r.stdout or "").strip().splitlines()[-1] if r.returncode == 0 and (r.stdout or "").strip() else ""
    except (OSError, subprocess.SubprocessError):
        return None
    if not origin:
        return None
    root = Path(origin).resolve().parents[1]
    return root if (root / ".git").exists() else None


def inspect(tree, fetch=False):
    """-> a dict: tree, head, main (origin/main sha), behind, ahead, dirty, ff, fetched (True / False / None when not
    asked), age_h (hours since the last fetch), verdict OK / BEHIND / DIVERGED / DIRTY / NOT-FOUND / ERROR."""
    row = {"tree": str(tree) if tree else "", "verdict": "NOT-FOUND", "fetched": None}
    if not tree or not (Path(tree) / ".git").exists():
        row["why"] = "no separate installed Fieldkit tree (the command runs from a checkout, or FIELDKIT_INSTALLED_DIR is wrong)"
        return row
    if fetch:
        code, _, err = _git(tree, "fetch", "--quiet", "origin", BRANCH, timeout=120)
        row["fetched"] = code == 0
        if code:
            row["fetch_error"] = err[:160]
    code, head, err = _git(tree, "rev-parse", "HEAD")
    code2, main, _ = _git(tree, "rev-parse", f"refs/remotes/origin/{BRANCH}")
    if code or code2:
        row.update(verdict="ERROR", why=f"cannot read HEAD or origin/{BRANCH}: {err or 'no origin/' + BRANCH}"[:200])
        return row
    row.update(head=head, main=main)
    _, cnt, _ = _git(tree, "rev-list", "--left-right", "--count", f"HEAD...origin/{BRANCH}")
    ahead, behind = (int(x) for x in cnt.split())
    row.update(ahead=ahead, behind=behind, ff=ahead == 0,
               dirty=bool(_git(tree, "status", "--porcelain", "--untracked-files=no")[1]))
    fh = Path(subprocess.run(["git", "-C", str(tree), "rev-parse", "--git-common-dir"], capture_output=True, text=True).stdout.strip()
              or ".")
    fh = fh if fh.is_absolute() else Path(tree) / fh
    try:
        row["age_h"] = round((time.time() - (fh / "FETCH_HEAD").stat().st_mtime) / 3600, 1)
    except OSError:
        row["age_h"] = None
    if ahead and behind:
        row.update(verdict="DIVERGED", why=f"installed HEAD has {ahead} commit(s) origin/{BRANCH} lacks and misses {behind}: a person looks")
    elif ahead:
        row.update(verdict="DIVERGED", why=f"installed HEAD is {ahead} commit(s) AHEAD of origin/{BRANCH}: not main's history")
    elif behind:
        row.update(verdict="BEHIND", why=f"installed HEAD {head[:10]} is {behind} commit(s) behind origin/{BRANCH} {main[:10]}")
    elif row["dirty"]:
        row.update(verdict="DIRTY", why=f"level with origin/{BRANCH} but the tree has uncommitted changes")
    else:
        row.update(verdict="OK", why=f"installed HEAD {head[:10]} = origin/{BRANCH}")
    if row["fetched"] is False:
        row["why"] += f"; OFFLINE: could not fetch ({row['fetch_error']}), using the last fetched ref"
    elif fetch is False and row.get("age_h") is not None:
        row["why"] += f" (last fetched ref, {row['age_h']} h old)"
    return row


def line(row):
    ok = row["verdict"] == "OK"
    return f"installed copy: {'PASS' if ok else 'FAIL'} {row['verdict']}: {row['why']}"


def _smoke(tree, run=subprocess.run):
    """The installed `fieldkit --version` loads from a neutral folder; -> (ok, text)."""
    try:
        r = run([sys.executable, "-I", "-m", "fieldkit", "--version"], capture_output=True, text=True,
                cwd=str(Path.home()), timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)[:160]
    out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    return r.returncode == 0, (out[-1] if out else "no output")[:160]


def sync(tree=None, fetch=True, run=subprocess.run):
    """Fetch, fast-forward the installed tree to origin/main, verify. Never resets, never deletes."""
    tree = tree if tree is not None else locate()
    row = inspect(tree, fetch=fetch)
    row["action"] = "none"
    if row["verdict"] in ("NOT-FOUND", "ERROR", "DIVERGED"):
        return _fail(row, "refused: " + row["why"])
    if row["verdict"] == "DIRTY" or row.get("dirty"):
        return _fail(row, "refused: uncommitted changes in the installed tree (left alone)")
    if row.get("fetched") is False:
        return _fail(row, f"refused: could not fetch origin ({row.get('fetch_error', '')})")
    if row["verdict"] == "OK":
        ok, ver = _smoke(tree, run)
        return _done(row, ok, f"already level at {row['head'][:10]}; {ver}" if ok else f"level but fieldkit does not load: {ver}")
    from . import checkout_hold               # a running build reads this tree
    held = checkout_hold.busy(str(tree))
    if held:
        return _fail(row, f"refused: {held}; the build reads it, sync again when it ends")
    old, new = row["head"], row["main"]
    code, _, err = _git(tree, "checkout", "--detach", "--quiet", new)
    if code:
        return _fail(row, f"refused: checkout of {new[:10]} failed: {err[:160]}")
    row["action"] = "moved"
    _, changed, _ = _git(tree, "diff", "--name-only", old, new, "--", "pyproject.toml")
    pip = ""
    if changed:
        r = run([sys.executable, "-m", "pip", "install", "--no-deps", "-q", "-e", str(tree)], capture_output=True,
                text=True, timeout=600)
        pip = "; pip install -e re-run (pyproject changed)" + ("" if r.returncode == 0 else f" FAILED: {(r.stderr or '').strip()[-120:]}")
        if r.returncode:
            return _fail(row, f"moved {old[:10]} -> {new[:10]}{pip}")
    _, head, _ = _git(tree, "rev-parse", "HEAD")
    ok, ver = _smoke(tree, run)
    if head != new:
        return _fail(row, f"HEAD {head[:10]} != origin/{BRANCH} {new[:10]} after the move")
    row["head"], row["behind"], row["verdict"] = head, 0, "OK"
    return _done(row, ok, f"moved {old[:10]} -> {new[:10]} ({row.pop('moved_n', '')}HEAD = origin/{BRANCH}){pip}; {ver}"
                 if ok else f"moved to {new[:10]} but fieldkit does not load: {ver}")


def _fail(row, why):
    row.update(ok=False, why=why)
    return row


def _done(row, ok, why):
    row.update(ok=ok, why=why)
    return row


def sync_line(row):
    return f"installed copy sync: {'PASS' if row.get('ok') else 'FAIL'}: {row['why']}"


def hook_line(call, event):
    """SessionStart only, report only (no fetch, no rewrite): one line when the installed copy is behind, dirty,
    diverged; silent when level or when this machine has no separate installed tree. Never raises."""
    try:
        if event != "SessionStart":
            return ""
        row = inspect(locate(), fetch=False)
        if row["verdict"] in ("OK", "NOT-FOUND"):
            return ""
        return "FIELDKIT INSTALLED COPY " + line(row) + ". Fix: fieldkit installed sync"
    except Exception:  # noqa: BLE001 - a courtesy line must never break the session's hook
        return ""


def after_merge(repo, base=BRANCH):
    """After a merge into main landed (ship): sync the installed copy when it belongs to the same repository.
    -> the one PASS/FAIL line, or "" for another repository or no installed copy. Never raises."""
    try:
        tree = locate()
        if not tree or not (Path(tree) / ".git").exists():
            return ""
        a = _git(repo, "rev-parse", "--git-common-dir")[1]
        b = _git(tree, "rev-parse", "--git-common-dir")[1]
        same = lambda p, d: os.path.normcase(str((Path(d) if Path(d).is_absolute() else Path(p) / d).resolve()))  # noqa: E731
        if same(repo, a) != same(tree, b):
            return ""
        return sync_line(sync(tree))
    except Exception as e:  # noqa: BLE001
        return f"installed copy sync: FAIL: {str(e)[:120]}"


def main(argv=None):
    p = argparse.ArgumentParser(prog="fieldkit installed", description="The installed Fieldkit follows origin/main.",
                                epilog=__doc__.split("Door statement")[0])
    sub = p.add_subparsers(dest="cmd")
    for name in ("check", "sync"):
        sub.add_parser(name).add_argument("--json", action="store_true")
    sub.add_parser("where")
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "where":
        t = locate()
        print(t or "NOT-FOUND")
        return 0 if t else 3
    if args.cmd == "sync":
        row = sync()
        print(json.dumps(row, indent=1) if args.json else sync_line(row))
        return 0 if row.get("ok") else 3
    if args.cmd == "check":
        row = inspect(locate(), fetch=False)
        print(json.dumps(row, indent=1) if args.json else line(row))
        return 0 if row["verdict"] == "OK" else 3
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
