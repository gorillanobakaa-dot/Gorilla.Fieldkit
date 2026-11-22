"""fieldkit ship ci-wait (O4): one blocking call per PR, the train's CI verdict, the failed log tail, a proven merge."""
import json

import pytest

from fieldkit.method import strikes, train

DEFAULT_RUNNER = strikes.default_runner

@pytest.fixture(autouse=True)
def _own_registry(tmp_path, monkeypatch):
    monkeypatch.setenv(strikes.ENV, str(tmp_path / "parked.json"))     # a red test must not park a real branch
    # F4: the failed test passes on the merge base unless a test says otherwise (a real run builds a worktree)
    monkeypatch.setattr(strikes, "default_runner", lambda base_sha, ids, job="": {i: "pass" for i in ids})


class GH:
    """GitHub for one PR: ci_state answers from a script of states, one per read."""

    def __init__(self, states, state="OPEN", merge_ok=True, merged_reads="MERGED"):
        self.states, self.state, self.merge_ok, self.merged_reads = list(states), state, merge_ok, merged_reads
        self.merges, self.waits = [], 0

    def pr(self, n):
        return {"number": n, "headRefName": "feat/x", "headRefOid": "a" * 40, "state": self.state}

    def ci_state(self, sha):
        s = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return s, f"fake {s}"

    def wait_checks(self, n, timeout):
        self.waits += 1

    def merge_base(self, base, head):
        return "m" * 40

    def failed_log(self, sha):
        return ["-- run 7", "FAILED tests/test_x.py::test_y"]

    def merge(self, n, head):
        self.merges.append(head)
        if self.merge_ok:
            self.state = self.merged_reads
        return self.merge_ok, "" if self.merge_ok else "refused"


def test_green_without_merge_waits_once_and_does_not_merge():
    gh = GH(["pending", "green"])
    r = train.ci_wait(gh, 166, sleep=lambda s: None)
    assert r["ok"] and r["state"] == "green" and gh.merges == [] and gh.waits == 1


def test_red_carries_the_failed_log_and_never_merges():
    gh = GH(["pending", "red"])
    r = train.ci_wait(gh, 166, merge=True, sleep=lambda s: None)
    assert not r["ok"] and r["state"] == "red" and gh.merges == []
    assert "FAILED tests/test_x.py::test_y" in r["log"]


def test_green_with_merge_merges_the_head_it_waited_on():
    gh = GH(["green"])
    r = train.ci_wait(gh, 166, merge=True, sleep=lambda s: None)
    assert r["ok"] and r["state"] == "merged" and gh.merges == ["a" * 40]


def test_a_merge_gh_reports_ok_but_the_pr_still_open_is_not_merged():
    gh = GH(["green"], merged_reads="OPEN")
    r = train.ci_wait(gh, 166, merge=True, sleep=lambda s: None)
    assert not r["ok"] and r["state"] == "not-merged"


def test_the_deadline_is_a_timeout_not_green():
    t = iter(range(0, 10_000, 100))
    gh = GH(["pending"])
    r = train.ci_wait(gh, 166, max_wait_s=250, sleep=lambda s: None, clock=lambda: next(t))
    assert not r["ok"] and r["state"] == "timeout"


def test_a_pr_already_merged_is_ok_without_waiting():
    gh = GH(["pending"], state="MERGED")
    r = train.ci_wait(gh, 166, merge=True, sleep=lambda s: None)
    assert r["ok"] and r["state"] == "merged" and gh.waits == 0 and gh.merges == []


@pytest.mark.parametrize("auto_merge", [False, True])
def test_wait_follows_the_latest_head_without_repairing_the_old_head(auto_merge):
    class UpdatedGH(GH):
        head = "a" * 40

        def pr(self, n):
            return dict(super().pr(n), headRefOid=self.head)

        def ci_state(self, sha):
            if sha == "b" * 40:
                return "green", "updated head passed CI"
            return ("red", "superseded check failed") if self.waits else ("pending", "initial checks")

        def failed_log(self, sha):
            pytest.fail(f"read failure for a superseded head {sha}")

        def wait_checks(self, n, timeout):
            assert timeout <= train.WATCH_CHUNK_S
            self.waits += 1
            self.head = "b" * 40
            if auto_merge:
                self.state = "MERGED"

    gh = UpdatedGH(["pending"])
    r = train.ci_wait(gh, 166, merge=True, sleep=lambda s: None)
    assert r["ok"] and r["state"] == "merged" and r["head"] == "b" * 40
    assert gh.waits == 1 and gh.merges == ([] if auto_merge else ["b" * 40])


def test_checks_after_the_first_api_page_can_block_green():
    gh = train.GH.__new__(train.GH)
    gh.slug = "o/r"
    checks = [{"name": f"earlier test {i}", "status": "completed", "conclusion": "success"} for i in range(30)]
    checks.append({"name": "late Linux check", "status": "completed", "conclusion": "failure"})

    def api(command, paginate, endpoint, jq, selector):
        assert (command, paginate, jq) == ("api", "--paginate", "--jq")
        if endpoint.endswith("check-runs"):
            assert selector.startswith(".check_runs[]")
            return 0, "\n".join(map(json.dumps, checks)), ""
        assert selector.startswith(".workflow_runs[]")
        return 0, "", ""

    gh._gh = api
    assert gh.ci_state("a" * 40)[0] == "red"
    assert gh.failed_checks("a" * 40) == {"late Linux check"}


def test_failure_ids_survive_aggregate_job_logs():
    gh = train.GH.__new__(train.GH)
    gh.slug = "o/r"
    log = "shard 4\tFAILED tests/test_x.py::test_y\n" + "\n".join(["aggregator: shard result failed"] * 40)
    gh._gh = lambda command, *args, **kw: (0, "[7]" if command == "api" else log, "")
    assert strikes.failed_tests(gh.failed_log("a" * 40)) == {"tests/test_x.py::test_y"}


def test_ci_failure_evidence_does_not_start_local_tests_without_the_owner(monkeypatch):
    monkeypatch.delenv("FIELDKIT_LOCAL_TESTS", raising=False)
    monkeypatch.setattr(strikes.subprocess, "run", lambda *a, **kw: pytest.fail("started local verification"))
    with pytest.raises(RuntimeError, match="local verification is disabled"):
        DEFAULT_RUNNER("b" * 40, ["tests/test_x.py::test_y"])


def test_the_cli_exit_code_follows_the_verdict(monkeypatch, capsys):
    monkeypatch.setattr(train, "GH", lambda repo: GH(["red"]))
    assert train.main(["ci-wait", "166"]) == 3
    assert "RED" in capsys.readouterr().out
    monkeypatch.setattr(train, "GH", lambda repo: GH(["green"]))
    assert train.main(["ci-wait", "166"]) == 0


class BehindGH(GH):
    """R17 (#315): head green, GitHub reads the PR BEHIND; the merge is refused until the branch is updated."""

    def __init__(self):
        super().__init__(["green"])
        self.head, self.updates = "a" * 40, []
        self.is_behind = True

    def pr(self, n):
        return dict(super().pr(n), headRefOid=self.head, baseRefName="main")

    def behind(self, n):
        return self.is_behind

    def update_branch(self, n, head):
        self.updates.append(head)
        self.head, self.is_behind = "b" * 40, False
        return True, "Updating"

    def merge(self, n, head):
        if self.is_behind:
            self.merges.append("REFUSED-BEHIND")
            return False, "the head branch is not up to date"
        return super().merge(n, head)


def test_behind_means_update_first_never_a_merge_attempt():
    gh = BehindGH()
    r = train.ci_wait(gh, 315, merge=True, sleep=lambda s: None)
    assert r["state"] == "merged" and gh.updates == ["a" * 40] and gh.merges == ["b" * 40], r


def test_ci_wait_merge_waits_for_required_checks_on_the_head():
    gh = GH(["green"])
    gh.polls = 0
    gh.required_checks = lambda base: {"ubuntu"}

    def runs(sha):
        gh.polls += 1
        return [] if gh.polls < 3 else [{"name": "ubuntu", "status": "completed", "conclusion": "success"}]
    gh.check_runs = runs
    r = train.ci_wait(gh, 315, merge=True, sleep=lambda s: None)
    assert r["state"] == "merged" and gh.polls == 3, r


# -- P9 (2026-11-11, PR 461): the runner's DNS / apt outage is re-run by ci-wait itself, never a strike --------------
APT_DNS = ["-- run 9",
           "test (ubuntu-latest)\tdesktop-file-validate\t2026-11-11T14:12:03Z Err:1 http://archive.ubuntu.com/ubuntu "
           "noble InRelease",
           "test (ubuntu-latest)\tdesktop-file-validate\t2026-11-11T14:12:03Z   Temporary failure resolving "
           "'archive.ubuntu.com'",
           "test (ubuntu-latest)\tdesktop-file-validate\t2026-11-11T14:12:04Z E: Failed to fetch "
           "https://security.ubuntu.com/ubuntu/pool/main/d/desktop-file-utils.deb",
           "test (ubuntu-latest)\tdesktop-file-validate\t2026-11-11T14:12:04Z ##[error]Process completed with exit "
           "code 100."]


class InfraGH(GH):
    """Red from the runner's network `outages` times, then the given state after each re-run."""

    def __init__(self, states, log=APT_DNS, rerun_ok=True):
        super().__init__(states)
        self.log, self.rerun_ok, self.reruns = log, rerun_ok, []

    def failed_log(self, sha):
        return list(self.log)

    def rerun_failed(self, sha):
        self.reruns.append(sha)
        assert len(self.reruns) <= 10, "unbounded automatic re-runs"     # a lost bound fails, never hangs
        return self.rerun_ok, [9], "" if self.rerun_ok else "HTTP 403"


def test_an_apt_dns_red_is_re_run_by_itself_with_backoff_and_merges_when_green():
    slept = []
    gh = InfraGH(["red", "red", "pending", "green"])
    r = train.ci_wait(gh, 461, merge=True, sleep=slept.append)
    assert r["state"] == "merged" and gh.reruns == ["a" * 40], r
    assert r["reruns"][0]["why"].startswith("ci-runner-dns") and slept[0] == strikes.infra_backoff(1)
    rec = strikes.load()["strikes"]["feat/x"]
    assert rec["rounds"] == [] and len(rec["infra"]) == 1 and rec["infra"][0]["runs"] == ["9"]   # journaled, no strike


def test_infra_re_runs_are_bounded_and_never_a_strike():
    gh = InfraGH(["red"])
    r = train.ci_wait(gh, 461, sleep=lambda s: None)
    assert r["state"] == "infra" and len(gh.reruns) == strikes.INFRA_RERUNS and "not counted" in r["why"], r
    assert "fieldkit ship ci-local" in r["next"], r   # owner 2026-11-11: the runner still down -> the same CI locally
    rec = strikes.load()["strikes"]["feat/x"]
    assert rec["rounds"] == [] and strikes.parked_for("feat/x") is None and rec["infra"][-1]["gave_up"]
    assert train.ci_wait(InfraGH(["red"]), 461, sleep=lambda s: None)["state"] == "infra"   # the bound holds per head
    assert len(strikes.load()["strikes"]["feat/x"]["rounds"]) == 0


def test_a_refused_infra_re_run_says_so_and_is_not_a_strike():
    r = train.ci_wait(InfraGH(["red"], rerun_ok=False), 461, sleep=lambda s: None)
    assert r["state"] == "infra" and "HTTP 403" in r["why"] and strikes.load()["strikes"]["feat/x"]["rounds"] == []


def test_a_failed_test_next_to_a_dns_line_stays_a_strike():
    gh = InfraGH(["red"], log=APT_DNS + ["FAILED tests/test_x.py::test_y - Temporary failure resolving 'x'"])
    r = train.ci_wait(gh, 461, sleep=lambda s: None)
    assert gh.reruns == [] and r["state"] == "red" and len(strikes.load()["strikes"]["feat/x"]["rounds"]) == 1


def test_one_gh_watch_covers_minutes_not_a_minute():
    """2026-11-22: 60 s chunks restarted four gh processes (four console hosts) every ~90 s per waiting PR."""
    seen = []

    class Slow(GH):
        def wait_checks(self, n, timeout):
            seen.append(timeout)
            super().wait_checks(n, timeout)
    t = iter(range(0, 100_000, 10))
    train.ci_wait(Slow(["pending", "green"]), 166, max_wait_s=3600, sleep=lambda s: None, clock=lambda: next(t))
    assert seen and seen[0] >= 300
