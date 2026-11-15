"""fieldkit ship ci-local (fieldkit/method/cilocal.py; owner rule 2026-11-11: GitHub's runner down -> the same CI runs
locally and counts). Mutation plan: tests/cilocal_mutations.yaml."""
import json
import sys

import pytest

from fieldkit.method import cilocal as C

HEAD = "a" * 40
OTHER = "b" * 40
DNS = ["##[group]Run sudo apt-get install", "Temporary failure resolving 'azure.archive.ubuntu.com'"]
TESTRED = ["FAILED tests/test_x.py::test_y - assert 1 == 2", "Temporary failure resolving 'x'"]
WF = """
name: tests
on: [pull_request]
jobs:
  test:
    strategy:
      matrix:
        os: [windows-latest, ubuntu-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: python -m pip install -e ".[test]"
      - name: linux only
        if: runner.os == 'Linux'
        run: echo linux
      - name: pr only
        if: github.event_name == 'pull_request'
        run: echo "origin/${{ github.base_ref }}"
      - name: tests
        shell: bash
        timeout-minutes: 5
        run: python -m pytest -q
"""


def done(concl):
    return {"id": 7, "status": "completed", "conclusion": concl}


# -- the classifier: infra vs test ------------------------------------------------------------------------------------
def test_runner_dns_red_is_infra():
    v, why = C.classify(done("failure"), DNS)
    assert v == "infra" and why.startswith("ci-runner-dns")


def test_a_failed_test_is_never_infra_even_with_a_dns_line():
    assert C.classify(done("failure"), TESTRED)[0] == "test"


def test_an_unknown_red_is_a_test_failure():
    assert C.classify(done("failure"), ["Error: something else broke"])[0] == "test"
    assert C.classify(done("failure"), [])[0] == "test"


def test_green_and_pending_are_not_eligible():
    assert C.classify(done("success"), DNS)[0] == "green"
    assert C.classify({"status": "in_progress"}, [], age_s=10 ** 6)[0] == "pending"


def test_never_started_only_past_the_limit():
    assert C.classify({"status": "queued"}, [], age_s=29 * 60)[0] == "pending"
    assert C.classify({"status": "queued"}, [], age_s=31 * 60)[0] == "never-started"
    assert C.classify(None, [], age_s=60)[0] == "none"
    assert C.classify(None, [], age_s=31 * 60)[0] == "never-started"


# -- the sha match -----------------------------------------------------------------------------------------------------
def test_sha_matches_only_the_exact_full_head():
    assert C.sha_matches(HEAD, HEAD)
    assert C.sha_matches(HEAD, HEAD.upper())
    assert not C.sha_matches(HEAD, OTHER)
    assert not C.sha_matches(HEAD[:12], HEAD[:12])
    assert not C.sha_matches("", "")


# -- the workflow ------------------------------------------------------------------------------------------------------
def test_the_job_and_its_steps_for_each_os():
    ctx, steps = C.load_job(WF, "linux")
    assert ctx == "test (ubuntu-latest)" and len(steps) == 6
    p = C.plan(steps, C.context_for("linux", "main"))
    assert [s["kind"] for s in p] == ["checkout", "python", "run", "run", "run", "run"]
    assert p[4]["cmd"].strip() == 'echo "origin/main"'
    assert [s["setup"] for s in p] == [True, True, True, False, False, False]
    assert p[5]["timeout"] == 5 and p[5]["shell"] == "bash"
    ctx, steps = C.load_job(WF, "windows")
    assert ctx == "test (windows-latest)"
    assert C.plan(steps, C.context_for("windows", "main"))[3]["kind"] == "skipped"


def test_an_unknown_expression_condition_or_action_refuses():
    with pytest.raises(C.Unsupported):
        C.expand("${{ secrets.TOKEN }}", C.context_for("linux", "main"))
    with pytest.raises(C.Unsupported):
        C.condition("contains(github.ref, 'x')", C.context_for("linux", "main"))
    with pytest.raises(C.Unsupported):
        C.plan([{"uses": "someone/action@v1"}], C.context_for("linux", "main"))


def test_steps_run_in_order_and_stop_at_the_first_red(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    steps = [{"name": "one", "kind": "run", "cmd": "exit 0", "shell": "bash", "timeout": 1, "setup": False},
             {"name": "two", "kind": "run", "cmd": "exit 4", "shell": "bash", "timeout": 1, "setup": False},
             {"name": "three", "kind": "run", "cmd": "exit 0", "shell": "bash", "timeout": 1, "setup": False}]
    calls = []

    class R:
        def __init__(self, rc):
            self.returncode = rc

    def run(cmd, **kw):
        calls.append(cmd)
        return R(4 if len(calls) == 2 else 0)
    res = C.run_steps(tree, steps, "linux", tmp_path / "log", run=run)
    assert [r["rc"] for r in res] == [0, 4] and len(calls) == 2
    assert C.steps_verdict(res, steps) == "red-check"
    assert C.steps_verdict(res[:1], steps[:1]) == "green"
    assert C.steps_verdict([dict(res[1], setup=True)], steps) == "red-setup"


@pytest.mark.skipif(sys.platform == "win32", reason="the bash step runner's real run is proven on Linux")
def test_a_real_bash_step_runs_in_the_tree(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    steps = [{"name": "pwd", "kind": "run", "cmd": "test -f marker\necho ok-$RUNNER_OS", "shell": "bash",
              "timeout": 1, "setup": False}]
    (tree / "marker").write_text("x")
    res = C.run_steps(tree, steps, "linux", tmp_path / "log")
    assert res[0]["rc"] == 0 and "ok-Linux" in (tmp_path / "log").read_text()


# -- the whole rule, with a fake GitHub ------------------------------------------------------------------------------
class Hub:
    slug = "o/r"

    def __init__(self, check, log=(), heads=(HEAD, HEAD), pinned=None):
        self._check, self._log, self.heads, self._pinned = check, list(log), list(heads), pinned
        self.posted = []

    def pr(self, n):
        return {"number": n, "headRefName": "feat/x", "baseRefName": "main", "headRefOid": self.heads.pop(0),
                "state": "OPEN"}

    def check(self, sha, context):
        return self._check

    def job_log(self, job_id):
        return self._log

    def pushed_at(self, sha):
        return 0.0

    def post_status(self, sha, context, desc, state="success"):
        self.posted.append((sha, context, state))
        return True, ""

    def pinned(self, context, base="main"):
        return self._pinned


def green_run(repo, n, head, base, os_word, d):
    return {"context": "test (ubuntu-latest)", "workflow": C.WORKFLOW, "event": "pull_request", "base": base,
            "os": os_word, "steps": [{"name": "tests", "kind": "run", "rc": 0, "seconds": 1.0, "setup": False}],
            "verdict": "green", "log": "", "log_sha256": "c" * 64, "python": "3.13", "host": "h"}


def red_run(verdict):
    def run(*a):
        r = green_run(*a)
        r["steps"][0].update(rc=1, setup=verdict == "red-setup")
        return dict(r, verdict=verdict)
    return run


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "proof_dir", lambda: tmp_path / "proofs")
    monkeypatch.setattr(C, "_drop", lambda d: None)
    from fieldkit import scratch
    monkeypatch.setattr(scratch, "new", lambda **kw: (tmp_path / "scratch", {}))

    class K:
        def sign(self, b):
            return b"sig"
    from fieldkit.mandate import mandate as M
    monkeypatch.setattr(M, "review_key", lambda: (K(), "fp"))
    return tmp_path


def test_infra_red_green_local_run_posts_a_signed_proof(env):
    hub = Hub(done("failure"), DNS)
    r = C.ci_local(5, "linux", hub=hub, runner=green_run, strike=pytest.fail)
    assert r["ok"] and r["state"] == "proven", r
    assert hub.posted == [(HEAD, "test (ubuntu-latest)", "success")]
    proof = json.loads((env / "proofs" / f"{HEAD}-linux.json").read_text())
    assert proof["sha"] == HEAD and proof["sig"] == b"sig".hex() and proof["log_sha256"] == "c" * 64


def test_a_test_failure_on_github_is_never_run_locally(env):
    hub = Hub(done("failure"), TESTRED)
    r = C.ci_local(5, "linux", hub=hub, runner=pytest.fail)
    assert not r["ok"] and r["state"] == "refused" and r["github"] == "test" and hub.posted == []


def test_no_proof_when_the_head_moved_during_the_run(env):
    hub = Hub(done("failure"), DNS, heads=(HEAD, OTHER))
    r = C.ci_local(5, "linux", hub=hub, runner=green_run)
    assert not r["ok"] and r["state"] == "moved" and hub.posted == []


def test_a_red_check_step_is_a_strike_a_red_setup_is_not(env):
    hub, struck = Hub(done("failure"), DNS), []
    r = C.ci_local(5, "linux", hub=hub, runner=red_run("red-check"),
                   strike=lambda *a, **k: struck.append(a) or {"parked": False})
    assert r["state"] == "red" and len(struck) == 1 and struck[0][1] == HEAD and hub.posted == []
    hub = Hub(done("failure"), DNS)
    r = C.ci_local(5, "linux", hub=hub, runner=red_run("red-setup"), strike=pytest.fail)
    assert r["state"] == "red-setup" and hub.posted == []


def test_a_pinned_ruleset_is_said_and_goes_to_the_inbox(env):
    hub, items = Hub(done("failure"), DNS, pinned=15368), []
    r = C.ci_local(5, "linux", hub=hub, runner=green_run, inbox=lambda *a: items.append(a))
    assert r["ok"] and r["state"] == "proven-not-counted" and len(items) == 1 and "15368" in r["why"]


def test_dry_run_runs_nothing(env):
    r = C.ci_local(5, "linux", hub=Hub(done("failure"), DNS), runner=pytest.fail, dry_run=True)
    assert r["ok"] and r["state"] == "eligible"


def test_an_unsigned_proof_is_never_posted(env, monkeypatch):
    from fieldkit.mandate import mandate as M

    def no_key():
        raise OSError("no key")
    monkeypatch.setattr(M, "review_key", no_key)
    hub = Hub(done("failure"), DNS)
    r = C.ci_local(5, "linux", hub=hub, runner=green_run)
    assert r["state"] == "unsigned" and hub.posted == []


def test_the_peer_tree_is_a_git_repo_at_the_head_with_origin_base(tmp_path):
    # the workflow diffs against origin/BASE (integration check, ci_tests.py): a tarball without .git would fail its
    # check steps over the cable for that alone, a false strike on Windows
    import subprocess

    def g(cwd, *a):
        return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                              capture_output=True, text=True, check=True).stdout.strip()
    src = tmp_path / "src"
    g(tmp_path, "init", "-q", "-b", "main", str(src))
    (src / "a.txt").write_text("1", encoding="utf-8")
    g(src, "add", "a.txt")
    g(src, "commit", "-q", "-m", "base")
    g(src, "update-ref", "refs/remotes/origin/main", "HEAD")
    (src / "a.txt").write_text("2", encoding="utf-8")
    g(src, "commit", "-q", "-am", "pr")
    head = g(src, "rev-parse", "HEAD")
    g(src, "update-ref", "refs/fieldkit/ci-local/5", head)
    g(src, "reset", "-q", "--hard", "HEAD~1")                       # the PR head is not checked out at the source
    tree = C.peer_tree(src, "refs/fieldkit/ci-local/5", head, "main", tmp_path / "tree")
    assert g(tree, "rev-parse", "HEAD") == head and (tree / "a.txt").read_text(encoding="utf-8") == "2"
    assert g(tree, "diff", "--name-only", "origin/main") == "a.txt"
