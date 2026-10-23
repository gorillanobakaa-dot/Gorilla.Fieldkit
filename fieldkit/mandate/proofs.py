# Version: 1.1.0 · updated 26-10-23-12-12
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
import json
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


def other_machine_reviewed(repo_path, commit, cfg, link_home=None, reviewers=None, own_key=None):
    """An "APPROVE <repo> <sha>" from the OTHER computer: its Ed25519 signature checked with a review key the owner
    pinned in the guarded store, never this computer's own (M1.2: the link key is shared by both computers, so it
    proves nothing about which one spoke). Found in the link inbox, which only carries it."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from ..peers import link
    from . import mandate as M
    keys = reviewers if reviewers is not None else M.reviewer_keys()
    mine = own_key if own_key is not None else M.review_key()[1]
    keys = {fp: raw for fp, raw in keys.items() if fp != mine}
    if not keys:
        return False, "no review key of the other computer is pinned here (fieldkit mandate trust-reviewer)"
    repo, _ = where(repo_path)
    want = M.approval_text(repo, commit).encode()
    d = (Path(link_home) if link_home else link.home()) / "inbox"
    for p in sorted(d.glob("*-message.json"), reverse=True) if d.is_dir() else []:
        try:
            a = (json.loads(p.read_text(encoding="utf-8")).get("signed") or {}).get("approval")
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(a, dict) or a.get("repo") != repo or a.get("commit") != commit or a.get("key") not in keys:
            continue
        try:
            Ed25519PublicKey.from_public_bytes(keys[a["key"]]).verify(bytes.fromhex(str(a.get("sig", ""))), want)
        except (InvalidSignature, ValueError):
            continue
        return True, f"note {p.stem}: {want.decode()} signed by review key {a['key']}"
    return False, f"no signed '{want.decode()}' from the other computer"


def ci_green_both(repo_path, commit, cfg):
    from ..core import sync
    return sync.ci_green(sync._slug(repo_path), commit)


PROVERS = {"full_tests_pass_here": full_tests_pass_here, "other_machine_reviewed": other_machine_reviewed,
           "ci_green_both": ci_green_both}
