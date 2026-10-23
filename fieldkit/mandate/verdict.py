# Version: 1.0.0 · updated 26-10-23-14-36
"""The other computer's verdict on a commit: its own OS runs the suite, a tool signs the result. No model approves.

    fieldkit mandate verdict REPO SHA [--send]     REPO: the repository's name (Gorilla.Opencode) or its folder here

Why (2026-10-23): a merge under a mandate needs the other computer's "APPROVE <repo> <sha>". Asked to send one, the
Windows session's own permission layer refused, rightly: one Claude approving another's merge gate is exactly what
it guards against. So the merge waited for the owner every time (bottleneck 2, local/PROJECT-MANDATE-PLAN.md section
11). Now nobody approves. The merging computer asks over the cable (verb verdict-request); the listener here, not a
session, checks that the repository is one of this computer's own (sync.repos) and that the commit is on origin, and
starts this tool. It checks the commit out in a temporary worktree, runs the suite, measures it against origin/main
(proofs.full_tests_pass_here: no fewer passed, no more skipped, no deleted test file), and only on a pass signs
"APPROVE <repo> <sha>" with this computer's review key. On a fail it sends the failure, never an approval.

What the signature proves, and what it does not (doors() says the same to the owner): that THIS computer's OS ran the
full suite on that exact commit and it passed against main. Not that a person read the diff.

The suite is this computer's to choose, never the requester's: fieldkit.local.json `mandate.tests.<repo>` = {cmd,
expect, count}, else a fixed default for the language the repository is written in.

The Windows review of the plan (link note #49): a verdict-request makes this computer RUN code from a commit, so it is
a remote code execution door, gated like `ask`: only repositories and branches this computer's owner listed in
link.yaml `allow_verdict: {Repo: [branch patterns]}` (sha256-pinned by `fieldkit peers link approve`); one verdict at
a time; a wall-clock cap (`verdict_minutes`, default 60); the suite runs with no git or GitHub credential (the
autopilot environment) and, on Windows, through `fieldkit guard` (the thermal rule). And a commit controls its own test
run (conftest.py, pytest settings, TestMain): a commit that changes how its tests run gets "needs a person", never a
signature.
"""
import fnmatch
import os
import time
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TESTS = [                                   # the first whose marker file the repository has
    ("go.mod", {"cmd": "go test ./...", "expect": r"(?m)^ok\s", "count": r"(?m)^ok\s"}),
    ("pyproject.toml", {"cmd": f"{Path(sys.executable).name} -m pytest -q"}),
    ("pytest.ini", {"cmd": f"{Path(sys.executable).name} -m pytest -q"}),
]
NAME = re.compile(r"[\w.-]{1,100}")
# files that decide how a suite runs, or what it counts: a commit touching them could fake its own pass
CONTROL = re.compile(r"(^|/)(conftest\.py|pytest\.ini|tox\.ini|noxfile\.py|\.coveragerc)$|(^|/)testdata/.*\.go$", re.I)
SHA = re.compile(r"[0-9a-f]{40}")


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def find_repo(name, repos=None):
    """-> this computer's checkout of the repository named `name`, from sync.repos only (never a path asked for)."""
    from . import proofs
    if repos is None:
        from ..core import sync
        repos = sync.repos()
    for p in repos:
        try:
            if proofs.where(p)[0] == name:
                return Path(p)
        except OSError:
            continue
    return None


def test_cfg(repo_path, name, local=None):
    if local is None:
        try:
            from ..core.settings import local_settings
            local = local_settings()
        except Exception:  # noqa: BLE001 - no local settings: the defaults
            local = {}
    mine = ((local.get("mandate") or {}).get("tests") or {}).get(name)
    if isinstance(mine, dict) and mine.get("cmd"):
        return mine
    return next((cfg for marker, cfg in DEFAULT_TESTS if (Path(repo_path) / marker).is_file()), None)


def _section(text, kind):
    """The pytest settings in a pyproject.toml or setup.cfg text, as a comparable value (None when absent)."""
    try:
        if kind == "toml":
            import tomllib
            return ((tomllib.loads(text).get("tool") or {}).get("pytest"))
        import configparser
        c = configparser.ConfigParser()
        c.read_string(text)
        return dict(c["tool:pytest"]) if c.has_section("tool:pytest") else None
    except Exception:  # noqa: BLE001 - unreadable settings: treat as changed
        return "unreadable"


def test_control_changes(repo_path, commit, base="main", cmd=None):
    """-> the files in origin/<base>...commit that change how the tests run (the Windows review, point 2)."""
    out = []
    for f in _git(repo_path, "diff", "--name-only", f"origin/{base}...{commit}").stdout.split():
        show = lambda ref: _git(repo_path, "show", f"{ref}:{f}").stdout
        if CONTROL.search(f):
            out.append(f)
        elif f.endswith(("pyproject.toml", "setup.cfg")):
            kind = "toml" if f.endswith(".toml") else "cfg"
            if _section(show(f"origin/{base}"), kind) != _section(show(commit), kind):
                out.append(f"{f} (its pytest settings)")
        elif f.endswith("_test.go") and "func TestMain(" in show(commit) + show(f"origin/{base}"):
            out.append(f"{f} (TestMain)")
    for word in (cmd or []):                              # the test command's own files
        if "/" in word or word.endswith((".sh", ".py", ".ps1")):
            changed = _git(repo_path, "diff", "--name-only", f"origin/{base}...{commit}", "--", word).stdout.strip()
            if changed:
                out.append(f"{changed} (the test command itself)")
    return out


def _allowed(name, approvals):
    pats = ((approvals or {}).get("allow_verdict") or {}).get(name)
    return [str(x) for x in pats] if isinstance(pats, list) else None


def _guarded_run(env, cap):
    """The suite as an autopilot job: no credential; on Windows through fieldkit guard; never past the cap."""
    def run(cmd, **kw):
        if sys.platform == "win32":
            cmd = [sys.executable, "-m", "fieldkit", "guard", "--"] + list(cmd)
        kw["timeout"] = min(int(kw.get("timeout") or cap), cap)
        return subprocess.run(cmd, env=env, **kw)
    return run


def verdict(repo_path, commit, name=None, run=None, local=None, approvals=None, branches=None):
    """-> (passed, evidence). The commit must be on an allowed origin branch; the suite runs in a temporary worktree
    of it, measured against main; a commit that changes how its tests run gets "needs a person"."""
    from . import proofs
    name = name or proofs.where(repo_path)[0]
    if not SHA.fullmatch(commit or ""):
        return False, "a verdict is only given on a full 40-character sha"
    if approvals is None:
        from ..peers import link
        approvals = link.approvals()[0]
    pats = branches if branches is not None else _allowed(name, approvals)
    if not pats:
        return False, (f"{name} is not in this computer's link.yaml allow_verdict (the owner writes it and runs "
                       "fieldkit peers link approve)")
    _git(repo_path, "fetch", "--quiet", "origin")
    on = [b.strip() for b in _git(repo_path, "branch", "-r", "--contains", commit).stdout.splitlines() if b.strip()]
    if not on:
        return False, f"{commit[:10]} is on no branch of origin: only pushed work gets a verdict"
    if not any(fnmatch.fnmatch(b.removeprefix("origin/"), p) for b in on for p in pats):
        return False, f"{commit[:10]} is on {', '.join(on[:3])}, none of the branches allowed here ({', '.join(pats)})"
    cfg = test_cfg(repo_path, name, local)
    if not cfg:
        return False, f"no suite known for {name} here (fieldkit.local.json mandate.tests.{name})"
    import shlex
    control = test_control_changes(repo_path, commit, cmd=shlex.split(str(cfg["cmd"])) if isinstance(cfg["cmd"], str)
                                   else cfg["cmd"])
    if control:
        return False, ("NEEDS A PERSON: the commit changes how its own tests run, so a pass would prove nothing: "
                       + ", ".join(control[:6]))
    if run is None:
        from ..autopilot.run import isolated_env
        run = _guarded_run(isolated_env(), 60 * int(approvals.get("verdict_minutes") or 60))
    tmp = Path(tempfile.mkdtemp(prefix="mandate-verdict-"))
    wt = tmp / "commit"
    try:
        if _git(repo_path, "worktree", "add", "--detach", "--quiet", str(wt), commit).returncode != 0:
            return False, f"could not check out {commit[:10]}"
        return proofs.full_tests_pass_here(wt, commit, {"test": cfg}, run=run)
    finally:
        _git(repo_path, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)            # the temporary folder this function made


def send(name, commit, passed, evidence, call=None):
    """The verdict over the cable: a signed APPROVE on a pass, the failure on a fail."""
    from . import mandate as M
    if call is None:
        from ..peers import link
        call = link.call
    if passed and not str(evidence).startswith("NEEDS A PERSON"):
        a = M.sign_review(name, commit)
        return call({"verb": "message", "done": True, "evidence": f"{M.approval_text(name, commit)}: {evidence}"[:1900],
                     "approval": a, "text": f"VERDICT PASS {M.approval_text(name, commit)} (review key {a['key']})"})
    return call({"verb": "message", "text": f"VERDICT FAIL {name} {commit}: {evidence}"[:4000]})


def _lock(approvals):
    from ..peers import link
    return link.home() / "verdict.lock", 60 * int((approvals or {}).get("verdict_minutes") or 60)


def busy(lock, cap):
    """One verdict at a time: a lock younger than the cap means one is running."""
    try:
        return time.time() - lock.stat().st_mtime < cap + 60
    except OSError:
        return False


def handle_request(req, popen=subprocess.Popen, repos=None, approvals=None, lock=None):
    """The listener's side of verb verdict-request: refuse unless this computer's owner allowed it, else start the
    verdict tool, detached; no session is asked. Every refusal is an answer the caller logs (link.handle)."""
    name, commit = str(req.get("repo") or ""), str(req.get("commit") or "")
    if not NAME.fullmatch(name) or not SHA.fullmatch(commit):
        return {"ok": False, "error": "verdict-request needs repo (a name) and commit (40 hex)"}
    if approvals is None:
        from ..peers import link
        approvals = link.approvals()[0]
    if not _allowed(name, approvals):
        return {"ok": False, "error": f"{name} is not in this computer's approved link.yaml allow_verdict"}
    repo = find_repo(name, repos)
    if repo is None:
        return {"ok": False, "error": f"{name} is not one of this computer's repositories (sync.repos)"}
    lk, cap = (lock, 3600) if lock is not None else _lock(approvals)
    if busy(Path(lk), cap):
        return {"ok": False, "error": "a verdict is running here already: one at a time"}
    Path(lk).parent.mkdir(parents=True, exist_ok=True)
    Path(lk).write_text(f"{name} {commit} {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    popen([sys.executable, "-m", "fieldkit", "mandate", "verdict", str(repo), commit, "--send", "--lock", str(lk)],
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
          **({"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}))
    return {"ok": True, "started": True, "repo": str(repo), "commit": commit}


def request(name, commit, call=None):
    """The merging side: ask the other computer for its verdict, once."""
    if call is None:
        from ..peers import link
        call = link.call
    return call({"verb": "verdict-request", "repo": name, "commit": commit})
