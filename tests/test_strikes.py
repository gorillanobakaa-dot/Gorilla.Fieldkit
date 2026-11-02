# Version: 1.0.0 · updated 26-10-29-14-04
"""TWO STRIKES (owner, 2026-10-29): a branch or PR that fails CI twice is parked; ci-wait, push and task start refuse
it, the plan line shows it. Fake gh, fake inbox, a registry in tmp_path: no network, no host processes."""
import json

import pytest

from fieldkit import plan
from fieldkit.lifecycle import tasks
from fieldkit.method import ship, strikes, train
from test_ci_wait import GH


def PASSES(base_sha, ids, job=""):
    return {i: "pass" for i in ids}


def FAILS(base_sha, ids, job=""):
    return {i: "fail" for i in ids}


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(strikes.ENV, str(tmp_path / "parked.json"))
    # F4: a failed test passes on the merge base unless a test says otherwise (a real run builds a worktree)
    monkeypatch.setattr(strikes, "default_runner", PASSES)
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
    log = lambda run: ["FAILED tests/a.py::t1"]       # noqa: E731
    r = strikes.check("feat/z", fetch=lambda b: runs, inbox=Inbox(), logger=log, base_of=lambda h: "m" * 40)
    assert r["parked"] and r["rounds"] == 2 and r["own"] == 2


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


class MainRedGH(RedGH):
    """B2: the branch is red; main's head is red too, failing `main_fails`."""

    def __init__(self, head, main_fails, own_fails=("tests/test_x.py::test_y",)):
        super().__init__(head)
        self.main_fails, self.own_fails = list(main_fails), list(own_fails)

    def base_head(self, base="main"):
        return "m" * 40

    def ci_state(self, sha):
        return "red", "fake red"

    def failed_log(self, sha):
        fails = self.main_fails if sha == "m" * 40 else self.own_fails
        return ["-- run 7"] + [f"FAILED {t} - boom" for t in fails]

    def failed_checks(self, sha):
        return {"test (windows)"}


def test_b2_a_red_that_main_shares_is_not_a_strike():
    ib = Inbox()
    for head in ("a" * 40, "b" * 40, "c" * 40):
        r = train.ci_wait(MainRedGH(head, ["tests/test_x.py::test_y"]), 166, sleep=lambda s: None, inbox=ib,
                          runner=FAILS)
        assert r["state"] == "main-red" and "main's failure" in r["why"] and "fix main first" in r["next"]
    assert strikes.parked_for("feat/x") is None and ib.items == []
    assert "feat/x" not in strikes.load()["strikes"]


def test_b2_a_failure_main_does_not_share_still_counts_and_parks():
    ib = Inbox()
    gh = lambda h: MainRedGH(h, ["tests/test_other.py::test_z"])      # noqa: E731
    train.ci_wait(gh("a" * 40), 166, sleep=lambda s: None, inbox=ib)
    r = train.ci_wait(gh("b" * 40), 166, sleep=lambda s: None, inbox=ib)
    assert r["state"] == "parked" and len(ib.items) == 1


def test_f4_main_red_and_pr_green_on_its_own_files_is_no_strike():
    """Plan P9: main red now (or earlier), the failed id fails on the merge base too: three heads, no strike."""
    ib = Inbox()
    for head in ("a" * 40, "b" * 40, "c" * 40):
        gh = RedGH(head)
        r = train.ci_wait(gh, 166, sleep=lambda s: None, inbox=ib, runner=FAILS)
        assert r["state"] == "main-red" and "also fails on the merge base" in r["why"]
    assert strikes.parked_for("feat/x") is None and "feat/x" not in strikes.load()["strikes"] and ib.items == []


def test_f4_a_failed_id_that_passes_on_the_base_is_a_strike():
    ib = Inbox()
    train.ci_wait(RedGH("a" * 40), 166, sleep=lambda s: None, inbox=ib, runner=PASSES)
    r = train.ci_wait(RedGH("b" * 40), 166, sleep=lambda s: None, inbox=ib, runner=PASSES)
    assert r["state"] == "parked" and len(strikes.load()["strikes"]["feat/x"]["rounds"]) == 2


class NoLogGH(RedGH):
    def failed_log(self, sha):
        return ["(log unreadable)"]


def test_f4_an_unreadable_log_is_unproven_and_the_third_unproven_counts():
    ib = Inbox()
    states = []
    for head in ("a" * 40, "b" * 40, "c" * 40):
        r = train.ci_wait(NoLogGH(head), 166, sleep=lambda s: None, inbox=ib, runner=PASSES)
        states.append(r["state"])
    assert states == ["unproven", "unproven", "red"]
    d = strikes.load()
    assert len(d["strikes"]["feat/x"]["unproven"]) == 3 and len(d["strikes"]["feat/x"]["rounds"]) == 1
    assert strikes.parked_for("feat/x") is None


def test_f4_whose_red_rules():
    ids = ["tests/a.py::t1", "tests/b.py::t2"]
    assert strikes.whose_red("h", ids, "m", lambda b, i, j: {"tests/a.py::t1": "fail", "tests/b.py::t2": "pass"})[0] == "own"
    assert strikes.whose_red("h", ids, "m", FAILS)[0] == "main"
    assert strikes.whose_red("h", [r"tests\a.py::t1"], "m", lambda b, i, j: {"tests/a.py::t1": "pass"})[0] == "own"
    assert strikes.whose_red("h", [], "m", PASSES)[0] == "unproven"                       # no ids
    assert strikes.whose_red("h", ids, "", PASSES)[0] == "unproven"                       # no base
    assert strikes.whose_red("h", ids, "m", lambda b, i, j: {})[0] == "unproven"          # no answer
    assert strikes.whose_red("h", ids, "m", lambda b, i, j: {"tests/a.py::t1": "fail"})[0] == "unproven"   # partial
    boom = lambda b, i, j: 1 / 0                                                          # noqa: E731
    assert strikes.whose_red("h", ids, "m", boom)[0] == "unproven"
    seen = []
    strikes.whose_red("h", ids, "base1", lambda b, i, j: seen.append((b, i, j)) or {}, job="test (ubuntu)")
    assert seen == [("base1", ids, "test (ubuntu)")]                  # exactly the failed ids, on the base, with the job


def test_f4_recount_prints_own_main_unproven_and_reports_a_parked_branch_below_two():
    strikes.record_failure("feat/p", "a" * 40, "x", pr=295, inbox=Inbox())
    strikes.record_failure("feat/p", "b" * 40, "x", pr=295, inbox=Inbox())
    assert strikes.parked_for("feat/p")
    logs = {"a" * 40: ["FAILED tests/a.py::t1"], "b" * 40: []}
    runs = [{"headSha": h, "conclusion": "failure", "name": "ci", "databaseId": i} for i, h in enumerate(logs, 1)]
    r = strikes.recount("feat/p", fetch=lambda b: runs, logger=lambda run: logs["a" * 40 if run["databaseId"] == 1 else "b" * 40],
                        base_of=lambda h: "m" * 40, runner=FAILS)
    assert [v["kind"] for v in r["heads"].values()] == ["main", "unproven"]
    assert r["own"] == 0 and r["parked"] and r["would_unpark"]
    assert strikes.parked_for("feat/p")                               # never unparked by the tool
    r = strikes.recount("feat/p", fetch=lambda b: runs, logger=lambda run: ["FAILED tests/a.py::t1"],
                        base_of=lambda h: "m" * 40, runner=PASSES)
    assert r["own"] == 2 and not r["would_unpark"]


def test_b2_mains_failure_rules():
    own = ["FAILED tests/a.py::t1 - x"]
    assert strikes.mains_failure(own, ["FAILED tests/a.py::t1", "FAILED tests/b.py::t2"])
    assert not strikes.mains_failure(own + ["FAILED tests/c.py::t3"], ["FAILED tests/a.py::t1"])
    assert not strikes.mains_failure(own, [])
    assert strikes.mains_failure(["no test names"], [], {"lint"}, {"lint", "test"})        # by check name
    assert not strikes.mains_failure(["no test names"], [], {"lint", "docs"}, {"lint"})
    assert not strikes.mains_failure([], [], set(), set())                               # nothing known: counts


class FlakeGH(MainRedGH):
    """Owner 2026-10-31: main green, the branch red only in listed timing-sensitive tests (Windows backslashes)."""

    def ci_state(self, sha):
        return ("green", "main green") if sha == "m" * 40 else ("red", "fake red")


HOOK_SPEED = r"tests\test_zero_command.py::test_the_whole_hook_process_measured_and_loads_no_yaml"


def test_a_red_made_only_of_timing_tests_is_a_flake_until_the_third():
    ib = Inbox()
    for head in ("a" * 40, "b" * 40):
        r = train.ci_wait(FlakeGH(head, [], own_fails=[HOOK_SPEED]), 166, sleep=lambda s: None, inbox=ib)
        assert r["state"] == "flake" and "not counted" in r["why"] and "rerun" in r["next"]
    assert strikes.load()["strikes"]["feat/x"]["rounds"] == [] and strikes.parked_for("feat/x") is None
    r = train.ci_wait(FlakeGH("c" * 40, [], own_fails=[HOOK_SPEED]), 166, sleep=lambda s: None, inbox=ib)
    assert r["state"] != "flake" and len(strikes.load()["strikes"]["feat/x"]["rounds"]) == 1    # third: counted


def test_a_timing_test_next_to_a_real_failure_still_counts():
    r = train.ci_wait(FlakeGH("a" * 40, [], own_fails=[HOOK_SPEED, "tests/test_x.py::test_y"]), 166,
                      sleep=lambda s: None, inbox=Inbox())
    assert r["state"] != "flake" and len(strikes.load()["strikes"]["feat/x"]["rounds"]) == 1


def test_flake_rules_and_the_list():
    assert "tests/test_liveness.py::test_a_lone_silent_spinner_is_stopped_as_busy_no_children" in strikes.timing_tests()
    assert strikes.flake([f"FAILED {HOOK_SPEED} - slow"])
    assert not strikes.flake(["no test names"]) and not strikes.flake([])           # nothing known: never a flake
