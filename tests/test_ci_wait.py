"""fieldkit ship ci-wait (O4): one blocking call per PR, the train's CI verdict, the failed log tail, a proven merge."""
import pytest

from fieldkit.method import strikes, train


@pytest.fixture(autouse=True)
def _own_registry(tmp_path, monkeypatch):
    monkeypatch.setenv(strikes.ENV, str(tmp_path / "parked.json"))     # a red test must not park a real branch


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


def test_the_cli_exit_code_follows_the_verdict(monkeypatch, capsys):
    monkeypatch.setattr(train, "GH", lambda repo: GH(["red"]))
    assert train.main(["ci-wait", "166"]) == 3
    assert "RED" in capsys.readouterr().out
    monkeypatch.setattr(train, "GH", lambda repo: GH(["green"]))
    assert train.main(["ci-wait", "166"]) == 0
