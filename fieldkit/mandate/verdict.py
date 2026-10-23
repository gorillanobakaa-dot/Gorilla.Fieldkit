# Version: 2.0.0 · updated 26-10-23-15-10
"""The worker's `test` job: this computer's OS runs the suite on a pushed commit and reports what it found.

    fieldkit mandate verdict REPO SHA        the same run by hand, printed here (nothing sent, nothing signed)

2026-10-23. First the merge needed the other computer's "APPROVE", which a careful model rightly refuses to give
(one Claude approving another's work); then the owner corrected the whole direction: "ONE COMPUTER IS THE BOSS, THE
ORCHESTRATOR ... the other ones ... execute it, they report their concerns ... they offer to fix the problem and
continue working ... they do not object" (local/PROJECT-MANDATE-PLAN.md section 16). So a worker approves nothing and
refuses nothing on judgement: it runs the suite and returns a RESULT (jobs.py signs it for provenance: which device
ran what). What looks wrong is a CONCERN with a fix offered; the orchestrator's mandate decides what a concern does.

Device hygiene, which costs the owner nothing and protects the worker (kept from the Windows review, #49): the job's
code runs only in a temporary worktree of a commit that is on an origin branch the mandate lets sessions push, with no
git or GitHub credential, under a wall-clock cap, one job at a time, and on Windows through `fieldkit guard`. The suite
is this computer's to choose (fieldkit.local.json mandate.tests.<repo>, else a fixed default per language), never the
orchestrator's.
"""
import fnmatch
import re
import shlex
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
SHA = re.compile(r"[0-9a-f]{40}")
# files that decide how a suite runs or what it counts: a commit touching them could make its own pass
CONTROL = re.compile(r"(^|/)(conftest\.py|pytest\.ini|tox\.ini|noxfile\.py|\.coveragerc)$|(^|/)testdata/.*\.go$", re.I)


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
            return (tomllib.loads(text).get("tool") or {}).get("pytest")
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


def guarded_run(env, cap):
    """The suite as an autopilot job: no credential; on Windows through fieldkit guard; never past the cap."""
    def run(cmd, **kw):
        if sys.platform == "win32":
            cmd = [sys.executable, "-m", "fieldkit", "guard", "--"] + list(cmd)
        kw["timeout"] = min(int(kw.get("timeout") or cap), cap)
        return subprocess.run(cmd, env=env, **kw)
    return run


def verdict(repo_path, commit, name=None, branches=None, run=None, local=None, cap=3600):
    """-> {status: pass | fail | not-run, evidence, concerns: [{text, fix_offered}]}.
    not-run: the commit is not a full sha, is on no origin branch the mandate lets sessions push, or no suite is
    known; the reason is the evidence. Otherwise the suite runs, measured against main (proofs.full_tests_pass_here),
    and a commit that changes how its tests run is a concern, not a refusal."""
    from . import proofs
    name = name or proofs.where(repo_path)[0]
    out = lambda status, ev, concerns=(): {"status": status, "evidence": ev, "concerns": list(concerns)}
    if not SHA.fullmatch(commit or ""):
        return out("not-run", "a job names a full 40-character sha")
    _git(repo_path, "fetch", "--quiet", "origin")
    on = [b.strip() for b in _git(repo_path, "branch", "-r", "--contains", commit).stdout.splitlines() if b.strip()]
    if not on:
        return out("not-run", f"{commit[:10]} is on no branch of origin: only pushed work is run here")
    pats = list(branches or [])
    if not any(fnmatch.fnmatch(b.removeprefix("origin/"), p) for b in on for p in pats + ["main"]):
        return out("not-run", f"{commit[:10]} is on {', '.join(on[:3])}, none of the branches the mandate lets "
                              f"sessions push ({', '.join(pats) or 'none'})")
    cfg = test_cfg(repo_path, name, local)
    if not cfg:
        return out("not-run", f"no suite known for {name} here",
                   [{"text": f"this computer has no test command for {name}",
                     "fix_offered": f"add fieldkit.local.json mandate.tests.{name} = {{cmd: ...}} on this computer"}])
    words = shlex.split(str(cfg["cmd"])) if isinstance(cfg["cmd"], str) else list(cfg["cmd"])
    concerns = [{"text": f"the commit changes how its own tests run, so a pass proves less: {', '.join(c)}",
                 "fix_offered": "a person reads the diff of those files, or the orchestrator runs the suite with "
                                "main's copy of them"}
                for c in [test_control_changes(repo_path, commit, cmd=words)] if c]
    if run is None:
        from ..autopilot.run import isolated_env
        run = guarded_run(isolated_env(), cap)
    tmp = Path(tempfile.mkdtemp(prefix="mandate-verdict-"))
    wt = tmp / "commit"
    try:
        if _git(repo_path, "worktree", "add", "--detach", "--quiet", str(wt), commit).returncode != 0:
            return out("not-run", f"could not check out {commit[:10]}", concerns)
        ok, ev = proofs.full_tests_pass_here(wt, commit, {"test": cfg}, run=run)
        return out("pass" if ok else "fail", ev, concerns)
    finally:
        _git(repo_path, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)            # the temporary folder this function made
