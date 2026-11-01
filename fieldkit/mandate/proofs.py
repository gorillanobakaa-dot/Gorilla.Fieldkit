# Version: 1.1.0 · updated 26-10-23-15-10
"""What a mandate's conditions mean, proven by the tool itself: never taken from the caller's word.

The Windows side's review of M1 (2026-10-23, point 4): allows(merge_main) took its proofs as keyword arguments, so
`fieldkit mandate allows ... full_tests_pass_here=1` let anyone assert them. Here each condition is a function that
looks for itself: the repository and branch come from git, a fast-forward from the refs, the test suite is RUN (and
measured against main), the other computer's review is "APPROVE <repo> <sha>" signed with ITS review key (pinned by
the owner in the guarded store), CI is read from GitHub. A condition with no prover is never proven.

    where(repo_path)                         -> (repo name from origin, current branch)
    fast_forward(repo_path, branch)          -> (ok, evidence)
    PROVERS[name](repo_path, commit, cfg)    -> (ok, evidence)   cfg: the repository's block of the sealed mandate
"""
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path


def _git(repo, *a, timeout=120):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=timeout)


def where(repo_path):
    url = _git(repo_path, "remote", "get-url", "origin").stdout.strip()
    # a Windows path origin (C:\\work\\X.git) has backslashes: the Windows run of M3's merge test, link note #68
    name = url.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")
    return name, _git(repo_path, "branch", "--show-current").stdout.strip()


def head(repo_path):
    return _git(repo_path, "rev-parse", "HEAD").stdout.strip()


def fast_forward(repo_path, branch):
    """The push would only add commits: origin/BRANCH (if it exists) is an ancestor of HEAD."""
    remote = _git(repo_path, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}").stdout.strip()
    if not remote:
        return True, f"{branch} is new on origin"
    if _git(repo_path, "merge-base", "--is-ancestor", remote, "HEAD").returncode == 0:
        return True, f"origin/{branch} {remote[:10]} is an ancestor of HEAD"
    return False, f"not a fast-forward: origin/{branch} {remote[:10]} is not an ancestor of HEAD"


PASSED = re.compile(r"\b(\d+) passed\b")
SKIPPED = re.compile(r"\b(\d+) skipped\b")
TESTFILE = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*$|_test\.[a-z]+$", re.I)


def _suite(cwd, cmd, t, run):
    """-> (ok, passed, skipped, last line)."""
    try:
        r = run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=int(t.get("timeout", 3600)))
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, 0, 0, f"did not run: {e}"
    out = (r.stdout or "") + (r.stderr or "")
    last = next((ln for ln in reversed(out.splitlines()) if ln.strip()), "")[:160]
    expect = str(t.get("expect", PASSED.pattern))
    if r.returncode != 0:
        return False, 0, 0, f"exit {r.returncode}: {last}"
    if not re.search(expect, out) or re.search(r"\b\d+ (failed|errors?)\b", out):
        return False, 0, 0, f"exit 0 but no pass shown ({expect}): {last}"              # an exit code lies
    if t.get("count"):                                    # a runner that prints no "N passed" (go test: one ok per package)
        return True, len(re.findall(t["count"], out)), 0, last
    p, k = PASSED.findall(out), SKIPPED.findall(out)
    return True, int(p[-1]) if p else 0, int(k[-1]) if k else 0, last


def full_tests_pass_here(repo_path, commit, cfg, run=subprocess.run):
    """Run the suite the SEALED mandate names (repos.X.test: {cmd, expect, base}) on a clean checkout of exactly
    `commit`, AND on origin/<base> in a temporary worktree: a commit that guts the tests passes its own suite (the
    Windows review of M1.1, point C). Proven only with no fewer passed, no more skipped, and no test file deleted."""
    t = (cfg or {}).get("test")
    if not isinstance(t, dict) or not t.get("cmd"):
        return False, "the mandate names no test command for this repository (repos.X.test.cmd)"
    if head(repo_path) != commit:
        return False, f"HEAD is not {commit[:10]}: check out the commit to merge first"
    if _git(repo_path, "status", "--porcelain").stdout.strip():
        return False, "the checkout has uncommitted changes: the suite would not test the commit"
    base = f"origin/{t.get('base', 'main')}"
    gone = [f for f in _git(repo_path, "diff", "--name-only", "--diff-filter=D", f"{base}...{commit}").stdout.split()
            if TESTFILE.search(f)]
    if gone:
        return False, f"test files deleted since {base}: {', '.join(gone[:5])} (the owner decides that)"
    cmd = t["cmd"] if isinstance(t["cmd"], list) else shlex.split(str(t["cmd"]))
    ok, passed, skipped, last = _suite(repo_path, cmd, t, run)
    if not ok:
        return False, f"the suite failed on {commit[:10]}: {last}"
    tmp = Path(tempfile.mkdtemp(prefix="mandate-base-"))
    wt = tmp / "base"
    try:
        if _git(repo_path, "worktree", "add", "--detach", "--quiet", str(wt), base).returncode != 0:
            return False, f"could not check out {base} to measure the suite against it"
        bok, bpassed, bskipped, blast = _suite(wt, cmd, t, run)
    finally:
        _git(repo_path, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)            # a temporary folder this function made, nothing else
    if not bok:
        return False, f"the suite does not pass on {base}, so there is nothing to measure against: {blast}"
    if passed < bpassed or skipped > bskipped:
        return False, (f"fewer tests than {base}: {passed} passed / {skipped} skipped on {commit[:10]}, "
                       f"{bpassed} / {bskipped} on {base}")
    return True, f"the suite passed on {commit[:10]}: {passed} passed, {skipped} skipped ({base}: {bpassed} / {bskipped})"


def tests_on(repo_path, commit, cfg, os_name=None, workers=None, own_key=None):
    """A worker's RESULT for this commit (jobs.py), its Ed25519 signature checked with a worker key pinned at pairing,
    status pass; from a worker of `os_name` when given. Provenance, not approval (plan section 16): the orchestrator's
    mandate decides; the worker's concerns travel in the evidence ("CONCERNS: ...")."""
    from . import jobs
    repo, _ = where(repo_path)
    rs = jobs.verified_results(repo, commit, workers=workers, own_key=own_key)
    if os_name:
        rs = [r for r in rs if str(r.get("os", "")).upper() == os_name.upper()]
    passed = [r for r in rs if r.get("status") == "pass"]
    if not passed:
        said = "; ".join(f"{r.get('host')}: {r.get('status')}: {str(r.get('evidence'))[:120]}" for r in rs)
        return False, (f"no passing RESULT from a worker{' on ' + os_name if os_name else ''} for {commit[:10]}"
                       + (f" ({said})" if said else ""))
    r = passed[-1]
    concerns = [c.get("text", "") for c in r.get("concerns") or []]
    return True, (f"{r.get('host')} ({r.get('os')}) ran it: {str(r.get('evidence'))[:160]}"
                  + (f"; CONCERNS: {' | '.join(concerns)}" if concerns else ""))


def other_machine_reviewed(repo_path, commit, cfg, **kw):
    """The old name of the condition (M1 to M3): any worker's passing RESULT (tests_on)."""
    return tests_on(repo_path, commit, cfg, **kw)


def ci_green_both(repo_path, commit, cfg):
    from ..core import sync
    return sync.ci_green(sync._slug(repo_path), commit)


def ci_green(repo_path, commit, cfg, run=subprocess.run):
    """Every check run GitHub has for `commit` completed with success (or skipped), at least one ran, and every system
    in the repository's `ci_os:` (none by default: a repository with one CI job) had a job. Read with gh."""
    import json
    from ..core import sync
    slug = sync._slug(repo_path)
    r = run(["gh", "api", f"repos/{slug}/commits/{commit}/check-runs", "--jq",
             "[.check_runs[] | {name, status, conclusion}]"], capture_output=True, text=True)
    if r.returncode:
        return False, f"CI unreadable (gh): {(r.stderr or '').strip()[:160]}"
    runs = json.loads(r.stdout or "[]")
    if not runs:
        return False, f"no CI run for {commit[:10]}: a repository without CI never merges or releases unattended"
    bad = [x["name"] for x in runs if x["status"] != "completed" or x["conclusion"] not in ("success", "skipped")]
    if bad:
        return False, f"CI not green for {commit[:10]}: {', '.join(bad[:4])}"
    missing = sync.missing_os(runs, (cfg or {}).get("ci_os") or [], slug)     # job names as data (P14)
    if missing:
        return False, f"CI for {commit[:10]} has no {', '.join(missing)} job"
    return True, f"CI green for {commit[:10]} ({len(runs)} check runs)"


def _origin_has(repo_path, commit):
    """The commit is on a branch GitHub already has (refs/remotes/origin/*, fetched)."""
    r = _git(repo_path, "branch", "-r", "--contains", commit)
    return r.returncode == 0 and any(ln.strip().startswith("origin/") for ln in r.stdout.splitlines())


def tag_new_on_pushed_commit(repo_path, tag):
    """A tag to push: it exists here, GitHub does not have it yet (a tag is never moved), and its commit is already on
    a branch GitHub has (so the tag publishes nothing the branch gate has not seen)."""
    commit = _git(repo_path, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}").stdout.strip()
    if not commit:
        return False, f"no local tag {tag}"
    remote = _git(repo_path, "ls-remote", "--tags", "origin", f"refs/tags/{tag}", timeout=120)
    if remote.returncode != 0:
        return False, f"origin unreadable: {(remote.stderr or '').strip()[:160]}"
    if remote.stdout.strip():
        return False, f"{tag} already exists on origin: a tag is never moved or replaced"
    _git(repo_path, "fetch", "--quiet", "origin")
    if not _origin_has(repo_path, commit):
        return False, f"{tag}'s commit {commit[:10]} is on no branch of origin: push the branch first"
    return True, f"{tag} is new on origin; its commit {commit[:10]} is on origin"


def tag_on_origin(repo_path, tag):
    """-> (ok, '... <commit>'): the tag as GitHub has it (ls-remote, peeled), its commit the last word."""
    r = _git(repo_path, "ls-remote", "--tags", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}", timeout=120)
    if r.returncode != 0:
        return False, f"origin unreadable: {(r.stderr or '').strip()[:160]}"
    rows = dict((ln.split()[1], ln.split()[0]) for ln in r.stdout.splitlines() if len(ln.split()) == 2)
    commit = rows.get(f"refs/tags/{tag}^{{}}") or rows.get(f"refs/tags/{tag}")
    if not commit:
        return False, f"{tag} is not on origin: push it first (fieldkit ship push --tag {tag})"
    return True, f"{tag} on origin at {commit}"


PROVERS = {"full_tests_pass_here": full_tests_pass_here, "other_machine_reviewed": other_machine_reviewed,
           "tests_on": tests_on,
           "ci_green_both": ci_green_both, "ci_green": ci_green}
