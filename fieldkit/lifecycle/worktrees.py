# Version: 1.0.0 · updated 26-10-28-19-02
"""Every job in its own git worktree, made by itself and removed once its work is on origin (HARNESS-PLAN I8,
lesson 2026-10-28: Zero started 4 jobs in the cable listener's own checkout; only a line appended to each brief kept
them from switching branches under each other).

    make(repo, name)   a detached worktree at origin/main in the scratch root (wt-job-NAME); -> (path, why). Outside
                       git: (None, why), the job runs in place. In git but no tree: Refused (fail closed: running in
                       the shared checkout is the mistake this prevents)
    own(path, pid)     records the job's process next to it (wt-job-NAME.json), so reap never takes a live job's tree
    reap(repo)         removes each job worktree whose process is gone, whose tree is clean and whose HEAD origin has
                       (the branch was shipped, or nothing was committed); a tree with unshipped work stays, said

Only folders named wt-job-* directly in the scratch root that git lists as worktrees of `repo` are ever removed, and
only with `git worktree remove` (no --force): a tree with changes is refused by git as well.
"""
import json
import re
import subprocess
from pathlib import Path

PREFIX = "wt-job-"


class Refused(ValueError):
    """A git repository, but no worktree of its own for the job: the job is not started."""
SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _git(repo, *a, run=subprocess.run):
    try:
        r = run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return r.returncode, (r.stdout if r.returncode == 0 else (r.stderr or r.stdout)).strip()


def scratch_root():
    from ..scratch import root
    return Path(root())


def top(repo, run=subprocess.run):
    """-> the main checkout of the repository holding `repo`, or None when it is not in git."""
    rc, out = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir", run=run)
    return Path(out).parent if rc == 0 and out else None


def make(repo, name, run=subprocess.run, root=None, fetch=True):
    """-> (path or None, why). None: `repo` is not in git, the job runs there and why says so. Raises Refused."""
    main = top(repo, run=run)
    if main is None:
        return None, f"{repo} is not in a git repository: the job runs there, in place"
    root = Path(root or scratch_root())
    path = root / (PREFIX + SAFE.sub("-", name)[:60])
    if path.exists():
        raise Refused(f"{path} already exists: a second job never shares a tree")
    if fetch:
        _git(main, "fetch", "--quiet", "origin", run=run)
    base = "origin/main" if _git(main, "rev-parse", "--verify", "-q", "origin/main", run=run)[0] == 0 else "HEAD"
    root.mkdir(parents=True, exist_ok=True)
    rc, out = _git(main, "worktree", "add", "--detach", str(path), base, run=run)
    if rc != 0:
        raise Refused(f"git worktree add {path} failed: {out[:200]}")
    return path, f"own worktree {path} at {base} (detached: the job makes its branch)"


def own(path, pid, task=""):
    if path:
        Path(str(path) + ".json").write_text(json.dumps({"pid": pid, "task": task}), encoding="utf-8")


def _owner(path):
    try:
        return json.loads(Path(str(path) + ".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def listed(main, run=subprocess.run):
    """-> [(path, head sha)] of the worktrees git has for this repository."""
    rc, out = _git(main, "worktree", "list", "--porcelain", run=run)
    rows, path = [], None
    for line in out.splitlines() if rc == 0 else []:
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):])
        elif line.startswith("HEAD ") and path is not None:
            rows.append((path, line[5:].strip()))
    return rows


def reap(repo, run=subprocess.run, root=None, alive=None):
    """-> {"removed": [paths], "kept": [(path, why)]}."""
    if alive is None:
        from ..peers.peers import pid_alive as alive
    out = {"removed": [], "kept": []}
    main = top(repo, run=run)
    if main is None:
        return out
    root = Path(root or scratch_root()).resolve()
    for path, sha in listed(main, run=run):
        if path.parent.resolve() != root or not path.name.startswith(PREFIX):
            continue
        pid = _owner(path).get("pid")
        if not pid:
            out["kept"].append((str(path), "no process recorded for it"))
            continue
        try:
            running = alive(int(pid))
        except (OSError, ValueError):
            running = True
        if running:
            out["kept"].append((str(path), f"its job (PID {pid}) is running"))
            continue
        rc, dirty = _git(path, "status", "--porcelain", run=run)
        if rc != 0 or dirty:
            out["kept"].append((str(path), "uncommitted changes in it"))
            continue
        rc, on = _git(main, "branch", "-r", "--contains", sha, run=run)
        if rc != 0 or not on.strip():
            out["kept"].append((str(path), f"HEAD {sha[:10]} is not on origin yet (not shipped)"))
            continue
        rc, why = _git(main, "worktree", "remove", str(path), run=run)
        if rc == 0:
            Path(str(path) + ".json").unlink(missing_ok=True)
            out["removed"].append(str(path))
        else:
            out["kept"].append((str(path), why[:200]))
    return out
