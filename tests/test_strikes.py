# Version: 1.0.0 · updated 26-10-29-14-04
"""TWO STRIKES (owner, 2026-10-29): a branch or PR that fails CI twice is parked; ci-wait, push and task start refuse
it, the plan line shows it. Fake gh, fake inbox, a registry in tmp_path: no network, no host processes."""
import json

import pytest

from fieldkit import plan
from fieldkit.lifecycle import tasks
from fieldkit.method import ship, strikes, train
from test_ci_wait import GH


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(strikes.ENV, str(tmp_path / "parked.json"))
    return tmp_path / "parked.json"


class Inbox:
    def __init__(self):
        self.items = []

    def __call__(self, title, why, command=""):
        self.items.append((title, why, command))


class RedGH(GH):
    """Every head is red; the head changes per round like a pushed fix."""

    def __init__(self, head):
        super().__init__(["red"])
        self.head = head

    def pr(self, n):
        return dict(super().pr(n), headRefOid=self.head)


def test_the_first_failed_round_is_a_strike_not_a_park():
    ib = Inbox()
    r = train.ci_wait(RedGH("a" * 40), 166, sleep=lambda s: None, inbox=ib)
    assert r["state"] == "red" and "one strike left" in r["next"] and ib.items == []
    assert strikes.parked_for("feat/x") is None


def test_the_second_failed_round_parks_with_one_inbox_item_naming_both_failures():
    ib = Inbox()
    train.ci_wait(RedGH("a" * 40), 166, sleep=lambda s: None, inbox=ib)
    r = train.ci_wait(RedGH("b" * 40), 166, sleep=lambda s: None, inbox=ib)
    assert not r["ok"] and r["state"] == "parked" and r["next"] == "parked, go on with the plan"
    assert strikes.parked_for("feat/x")["pr"] == 166
    assert len(ib.items) == 1
    title, why, _ = ib.items[0]
    assert "feat/x" in title and "#166" in title and "aaaaaaaaaa" in why and "bbbbbbbbbb" in why


def test_the_same_red_head_read_twice_is_one_round():
    for _ in range(3):
        train.ci_wait(RedGH("a" * 40), 166, sleep=lambda s: None, inbox=Inbox())
    assert strikes.parked_for("feat/x") is None


def test_a_parked_branch_is_not_waited_on_a_third_time():
    strikes.park("feat/x", inbox=Inbox())
    gh = GH(["green"])
    r = train.ci_wait(gh, 166, merge=True, sleep=lambda s: None)
    assert r["state"] == "parked" and gh.merges == [] and gh.waits == 0


def test_the_cli_exits_with_the_distinct_parked_code(monkeypatch, capsys):
    strikes.park("feat/x", inbox=Inbox())
    monkeypatch.setattr(train, "GH", lambda repo: GH(["green"]))
    assert train.main(["ci-wait", "166", "--merge"]) == strikes.PARKED_EXIT == 4
    assert "PARKED" in capsys.readouterr().out


def test_push_to_a_parked_branch_is_refused_and_nothing_is_run(monkeypatch):
    strikes.park("feat/x", inbox=Inbox())
    monkeypatch.setattr(ship, "sync", lambda repo, base: {"ok": True, "state": "ahead", "branch": "feat/x"})
    monkeypatch.setattr(ship, "git", lambda *a, **k: pytest.fail("git push ran for a parked branch"))
    r = ship.push(".")
    assert not r["ok"] and not r["pushed"] and r["state"] == "parked" and "PARKED" in r["error"]


def test_push_to_another_branch_is_not_refused(monkeypatch):
    strikes.park("feat/x", inbox=Inbox())
    monkeypatch.setattr(ship, "sync", lambda repo, base: {"ok": True, "state": "in-step", "branch": "feat/y"})
    assert ship.push(".")["pushed"] is False and "state" in ship.push(".") and ship.push(".")["state"] == "in-step"


def _brief(tmp_path, text):
    p = tmp_path / "brief.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_task_start_refuses_a_brief_naming_the_parked_branch(tmp_path):
    strikes.park("feat/x", inbox=Inbox())
    with pytest.raises(tasks.Parked) as e:
        tasks.start(_brief(tmp_path, "Repair feat/x and push it."), launch=lambda a: pytest.fail("launched"))
    assert "feat/x" in str(e.value) and "PARKED" in str(e.value)


def test_task_start_refuses_a_brief_naming_the_parked_pr_number(tmp_path):
    train.ci_wait(RedGH("a" * 40), 166, sleep=lambda s: None, inbox=Inbox())
    train.ci_wait(RedGH("b" * 40), 166, sleep=lambda s: None, inbox=Inbox())
    with pytest.raises(tasks.Parked):
        tasks.start(_brief(tmp_path, "Look at #166 again."), launch=lambda a: pytest.fail("launched"))
    assert strikes.named_in("PR #1660 is another one") == []        # #1660 is not #166


def test_the_cli_task_start_exits_3_with_a_plain_message(tmp_path, capsys):
    from fieldkit import lifecycle
    strikes.park("feat/x", inbox=Inbox())
    assert lifecycle.main(["task", "start", str(_brief(tmp_path, "fix feat/x"))]) == 3
    assert "NOT STARTED" in capsys.readouterr().out


def test_unpark_lets_the_brief_through_and_is_logged(tmp_path, store):
    strikes.park("feat/x", inbox=Inbox())
    started = []
    try:
        tasks.start(_brief(tmp_path, "Repair feat/x"), unpark="feat/x", launch=lambda a: started.append(a) or {},
                    worktree=lambda repo, tid: (None, "test"))
    except Exception:                                        # noqa: BLE001 - only the park check is under test
        pass
    assert strikes.parked_for("feat/x") is None
    assert json.loads(store.read_text())["log"][0]["action"] == "unpark"


def test_a_registry_that_is_not_json_is_an_error_not_a_fresh_one(store):
    store.write_text("{not json")
    with pytest.raises(strikes.StoreError):
        strikes.parked_for("feat/x")
    assert store.read_text() == "{not json"


def test_a_missing_state_folder_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv(strikes.ENV, str(tmp_path / "nope" / "parked.json"))
    with pytest.raises(strikes.StoreError):
        strikes.record_failure("feat/x", "a" * 40, inbox=Inbox())


def test_check_reads_gh_run_list_and_parks_on_two_failed_heads():
    runs = [{"headSha": "a" * 40, "conclusion": "failure", "name": "ci"},
            {"headSha": "a" * 40, "conclusion": "failure", "name": "lint"},
            {"headSha": "b" * 40, "conclusion": "failure", "name": "ci"},
            {"headSha": "c" * 40, "conclusion": "success", "name": "ci"}]
    r = strikes.check("feat/z", fetch=lambda b: runs, inbox=Inbox())
    assert r["parked"] and r["rounds"] == 2


def test_list_park_unpark_cli(capsys):
    strikes.park("feat/q", inbox=Inbox())
    assert strikes.main(["list"]) == 0 and "PARKED" in capsys.readouterr().out
    assert strikes.main(["unpark", "feat/nothing"]) == 3
    assert strikes.main(["unpark", "feat/q"]) == 0 and strikes.parked_for("feat/q") is None


def test_plan_next_prints_the_parked_items_on_one_line(tmp_path, capsys):
    strikes.park("feat/x", inbox=Inbox())
    d = strikes.load()
    d["parked"]["feat/x"]["pr"] = 166
    strikes.save(d)
    line = strikes.parked_line()
    assert line == "PARKED (two strikes): feat/x #166" and "\n" not in line
    assert plan.main(["next", "--repo", str(tmp_path)]) == 1         # no plan file in tmp_path: its own line
