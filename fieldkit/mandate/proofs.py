# Version: 1.0.0 · updated 26-10-23-04-41
"""What a mandate's conditions mean, proven by the tool itself: never taken from the caller's word.

The Windows side's review of M1 (2026-10-23, point 4): allows(merge_main) took its proofs as keyword arguments, so
`fieldkit mandate allows ... full_tests_pass_here=1` let anyone assert them. Here each condition is a function that
looks for itself: the repository and branch come from git, a fast-forward from the refs, the test suite is RUN, the
other computer's review is a [DONE] note in the link inbox whose signature is checked again with the link key, CI is
read from GitHub. A condition with no prover is never proven.

    where(repo_path)                         -> (repo name from origin, current branch)
    fast_forward(repo_path, branch)          -> (ok, evidence)
    PROVERS[name](repo_path, commit, cfg)    -> (ok, evidence)   cfg: the repository's block of the sealed mandate
"""
import hashlib
import hmac
import json
import re
import shlex
import subprocess
from pathlib import Path


def _git(repo, *a, timeout=120):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=timeout)


def where(repo_path):
    url = _git(repo_path, "remote", "get-url", "origin").stdout.strip()
    name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")
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


def full_tests_pass_here(repo_path, commit, cfg, run=subprocess.run):
    """Run the suite the SEALED mandate names (repos.X.test: {cmd, expect}) on a clean checkout of exactly `commit`."""
    t = (cfg or {}).get("test")
    if not isinstance(t, dict) or not t.get("cmd"):
        return False, "the mandate names no test command for this repository (repos.X.test.cmd)"
    if head(repo_path) != commit:
        return False, f"HEAD is not {commit[:10]}: check out the commit to merge first"
    if _git(repo_path, "status", "--porcelain").stdout.strip():
        return False, "the checkout has uncommitted changes: the suite would not test the commit"
    cmd = t["cmd"] if isinstance(t["cmd"], list) else shlex.split(str(t["cmd"]))
    try:
        r = run(cmd, cwd=str(repo_path), capture_output=True, text=True, timeout=int(t.get("timeout", 3600)))
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"the suite did not run: {e}"
    out = (r.stdout or "") + (r.stderr or "")
    expect = str(t.get("expect", r"\b\d+ passed\b"))
    last = next((ln for ln in reversed(out.splitlines()) if ln.strip()), "")
    if r.returncode != 0:
        return False, f"the suite failed (exit {r.returncode}): {last[:160]}"
    if not re.search(expect, out) or re.search(r"\b\d+ (failed|errors?)\b", out):
        return False, f"exit 0 but the output does not show a pass ({expect}): {last[:160]}"    # an exit code lies
    return True, f"the suite passed on {commit[:10]}: {last[:160]}"


REVIEW_RE = re.compile(r"\breviewed\b[^\n]*?\b([0-9a-f]{7,40})\b", re.I)


def other_machine_reviewed(repo_path, commit, cfg, link_home=None, link_key=None, my_os=None):
    """A [DONE] note from the OTHER computer, its signature checked again, whose signed evidence says 'reviewed <sha>'
    for this commit. The inbox file alone proves nothing: a model can write a file, not the HMAC."""
    from ..peers import link
    lh = Path(link_home) if link_home else link.home()
    k = link_key if link_key is not None else link.key()
    if not k:
        return False, "no link key here: the review note cannot be checked"
    me = my_os or link.os_tag()
    d = lh / "inbox"
    for p in sorted(d.glob("*-message.json"), reverse=True) if d.is_dir() else []:
        try:
            req = json.loads(p.read_text(encoding="utf-8")).get("signed")
        except (OSError, ValueError):
            continue
        if not isinstance(req, dict) or not req.get("done") or req.get("os") in (None, me):
            continue
        want = hmac.new(k, link._canon(req), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, str(req.get("mac", ""))):
            continue
        m = REVIEW_RE.search(str(req.get("evidence") or ""))
        if m and commit.lower().startswith(m.group(1).lower()):
            return True, f"note {p.stem} from {req.get('os')} ({req.get('from_host')}): {req['evidence'][:160]}"
    return False, f"no signed [DONE] note from the other computer with evidence 'reviewed {commit[:10]}'"


def ci_green_both(repo_path, commit, cfg):
    from ..core import sync
    return sync.ci_green(sync._slug(repo_path), commit)


PROVERS = {"full_tests_pass_here": full_tests_pass_here, "other_machine_reviewed": other_machine_reviewed,
           "ci_green_both": ci_green_both}
