# Version: 1.0.0 · updated 26-10-29-14-04
"""TWO STRIKES (owner, 2026-10-29; limit 3 and unproven-never-a-strike 2026-11-16): a branch or PR that fails CI by its own
fault LIMIT (3) times is parked (here with the ladder off; tests/test_escalation.py climbs); ci-wait, push and task start
refuse it, the plan line shows it. Fake gh, fake inbox, a registry in tmp_path: no network, no host processes."""
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
    # these tests cover the flat LIMIT-strikes mechanics; the escalation ladder (tests/test_escalation.py) has its own
    from fieldkit.lifecycle import models
    monkeypatch.setattr(models, "ladder_cfg", lambda data=None: {"enabled": False})
    monkeypatch.delenv("FIELDKIT_ATTEMPT_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
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


def _rounds(heads, ib):
    r = None
    for h in heads:
        r = train.ci_wait(RedGH(h * 40), 166, sleep=lambda s: None, inbox=ib)
    return r


def test_the_first_and_second_failed_rounds_are_strikes_not_a_park():
    ib = Inbox()
    r = _rounds("a", ib)
    assert r["state"] == "red" and "2 own strike(s) left" in r["next"] and ib.items == []
    r = _rounds("b", ib)
    assert r["state"] == "red" and "1 own strike(s) left" in r["next"] and ib.items == []
    assert strikes.parked_for("feat/x") is None and strikes.LIMIT == 3


def test_the_third_failed_round_parks_with_one_inbox_item_naming_all_failures():
    ib = Inbox()
    r = _rounds("abc", ib)
    assert not r["ok"] and r["state"] == "parked" and r["next"] == "parked, go on with the plan"
    assert strikes.parked_for("feat/x")["pr"] == 166
    assert len(ib.items) == 1
    title, why, _ = ib.items[0]
    assert "feat/x" in title and "#166" in title and "aaaaaaaaaa" in why and "bbbbbbbbbb" in why and "cccccccccc" in why


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
    _rounds("abc", Inbox())
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


def test_check_reads_gh_run_list_and_parks_on_three_failed_heads():
    runs = [{"headSha": "a" * 40, "conclusion": "failure", "name": "ci"},
            {"headSha": "a" * 40, "conclusion": "failure", "name": "lint"},
            {"headSha": "b" * 40, "conclusion": "failure", "name": "ci"},
            {"headSha": "c" * 40, "conclusion": "failure", "name": "ci"},
            {"headSha": "d" * 40, "conclusion": "success", "name": "ci"}]
    log = lambda run: ["FAILED tests/a.py::t1"]       # noqa: E731
    r = strikes.check("feat/z", fetch=lambda b: runs, inbox=Inbox(), logger=log, base_of=lambda h: "m" * 40)
    assert r["parked"] and r["rounds"] == 3 and r["own"] == 3


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
    assert line == "PARKED (strikes): feat/x #166" and "\n" not in line
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
    for h in "ab":
        train.ci_wait(gh(h * 40), 166, sleep=lambda s: None, inbox=ib)
    r = train.ci_wait(gh("c" * 40), 166, sleep=lambda s: None, inbox=ib)
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
    for h in "ab":
        train.ci_wait(RedGH(h * 40), 166, sleep=lambda s: None, inbox=ib, runner=PASSES)
    r = train.ci_wait(RedGH("c" * 40), 166, sleep=lambda s: None, inbox=ib, runner=PASSES)
    assert r["state"] == "parked" and len(strikes.load()["strikes"]["feat/x"]["rounds"]) == 3


class NoLogGH(RedGH):
    def failed_log(self, sha):
        return ["(log unreadable)"]


def test_f4_an_unreadable_log_is_unproven_never_counted_never_parks():
    """Owner 2026-11-16 (PR 553): 'I do not know' is never a strike, however many times it repeats."""
    ib = Inbox()
    for head in "abcdef":
        r = train.ci_wait(NoLogGH(head * 40), 166, sleep=lambda s: None, inbox=ib, runner=PASSES)
        assert r["state"] == "unproven"
    d = strikes.load()
    assert len(d["strikes"]["feat/x"]["unproven"]) == 6 and d["strikes"]["feat/x"]["rounds"] == []
    assert strikes.parked_for("feat/x") is None


def test_record_unproven_is_never_counted_however_often_it_repeats():
    """Owner 2026-11-16 (PR 553): unlike a flake, an unproven red has no limit past which it counts."""
    for i, h in enumerate("abcdefgh", 1):
        r = strikes.record_unproven("feat/u", h * 40, "no ids", pr=553)
        assert r["unproven"] == i and r["counted"] is False
    assert strikes.parked_for("feat/u") is None


def test_unproven_without_main_evidence_is_escalated_and_filed_once_per_head(monkeypatch):
    ib = Inbox()
    monkeypatch.setattr(strikes, "escalate_for", lambda branch, p=None: "opus")
    boom = lambda b, i, j: 1 / 0                                      # noqa: E731
    r = strikes.count_red("feat/u", "h1", ["FAILED tests/a.py::t1"], "m" * 40, inbox=ib, runner=boom, proof=lambda i, b: None)
    assert r["kind"] == "unproven" and not r["counted"] and r["escalate_to"] == "opus"
    strikes.count_red("feat/u", "h1", ["FAILED tests/a.py::t1"], "m" * 40, inbox=ib, runner=boom, proof=lambda i, b: None)
    assert len(ib.items) == 1 and "opus judges it next" in ib.items[0][1]        # once per head
    assert strikes.parked_for("feat/u") is None


def test_pr_553_round_two_failed_only_on_mains_red_is_main_not_a_strike():
    """The real incident (2026-11-16): bh-recipe-documents was adopted without a crossos.yaml row; main's own CI and
    PR 553's round 2 failed the same tests: tests/test_crossos.py x2 and test_autonomy_loop[windows]. The merge base
    could not be run (the runner raises), so the direct verdict was unproven: main's own failed runs now prove it."""
    ids = ["tests/test_crossos.py::test_every_recipe_has_a_row", "tests/test_crossos.py::test_the_matrix_is_complete",
           "tests/test_autonomy_loop.py::test_autonomy_loop[windows]"]
    own_log = [f"FAILED {i} - AssertionError" for i in ids]
    main_runs = [{"headSha": "m" * 40, "conclusion": "failure", "name": "ci", "databaseId": 1},
                 {"headSha": "x" * 40, "conclusion": "success", "name": "ci", "databaseId": 2}]
    ib = Inbox()

    def boom(b, i, j):
        raise RuntimeError("cannot build a worktree of the merge base")

    proof = lambda i, base: strikes.prove_more(i, base, fetch=lambda: main_runs, logger=lambda r: own_log)  # noqa: E731
    for head in ("h1", "h2", "h3", "h4"):
        r = strikes.count_red("fix/emerge-x", head, own_log, "m" * 40, pr=553, inbox=ib, runner=boom, proof=proof)
        assert r["kind"] == "main" and not r["counted"] and "main's own CI run" in r["evidence"]
    d = strikes.load()
    assert strikes.parked_for("fix/emerge-x") is None and "fix/emerge-x" not in d["strikes"] and ib.items == []
    # a failed id main does NOT share stays unproven (never "own" without a direct run), and still never a strike
    r = strikes.count_red("fix/emerge-x", "h5", own_log + ["FAILED tests/other.py::t"], "m" * 40, pr=553, inbox=ib,
                          runner=boom, proof=proof)
    assert r["kind"] == "unproven" and not r["counted"] and strikes.parked_for("fix/emerge-x") is None


def test_unknown_model_counts_as_the_default_rung(monkeypatch):
    from fieldkit.lifecycle import models
    monkeypatch.setattr(models, "ladder_cfg", lambda data=None: {"enabled": True, "ladder": ["haiku", "sonnet", "opus", "fable"],
                                                                 "own_limit": 3})
    step = strikes.ladder_step([{"head": "a", "model": ""}])
    assert step["cur"] == strikes.DEFAULT_MODEL == "sonnet" and step["own"] == 1


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


def test_f4_recount_prints_own_main_unproven_and_reports_a_parked_branch_below_the_limit():
    for h in "abc":
        strikes.record_failure("feat/p", h * 40, "x", pr=295, inbox=Inbox())
    assert strikes.parked_for("feat/p")
    logs = {"a" * 40: ["FAILED tests/a.py::t1"], "b" * 40: [], "c" * 40: ["FAILED tests/a.py::t1"]}
    runs = [{"headSha": h, "conclusion": "failure", "name": "ci", "databaseId": i} for i, h in enumerate(logs, 1)]
    r = strikes.recount("feat/p", fetch=lambda b: runs, logger=lambda run: list(logs.values())[run["databaseId"] - 1],
                        base_of=lambda h: "m" * 40, runner=FAILS)
    assert [v["kind"] for v in r["heads"].values()] == ["main", "unproven", "main"]
    assert r["own"] == 0 and r["parked"] and r["would_unpark"]
    assert strikes.parked_for("feat/p")                               # never unparked by the tool
    r = strikes.recount("feat/p", fetch=lambda b: runs, logger=lambda run: ["FAILED tests/a.py::t1"],
                        base_of=lambda h: "m" * 40, runner=PASSES)
    assert r["own"] == 3 and not r["would_unpark"] and r["parked"]   # every head now names a failed test


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


def test_infra_classifier_reads_the_signature_data_and_fails_closed():
    from test_ci_wait import APT_DNS
    assert strikes.infra(APT_DNS).startswith("ci-runner-dns")
    assert strikes.infra(["x\tstep\tE: Failed to fetch http://archive.ubuntu.com/a.deb  404"]).startswith("ci-apt-fetch")
    assert strikes.infra(["curl: (7) Failed to connect to pypi.org port 443: Network is unreachable"])
    assert not strikes.infra([]) and not strikes.infra(["AssertionError: 1 != 2"])            # nothing known: a strike
    assert not strikes.infra(APT_DNS + ["FAILED tests/test_x.py::test_y"])                     # a failed test wins
    assert not strikes.infra(APT_DNS + ["===== 1 failed, 20 passed in 3.1s ====="])           # pytest summary wins
    assert not strikes.infra(APT_DNS + ["ERROR tests/test_x.py - ImportError"])               # collection error wins
    assert not strikes.infra(APT_DNS, set_name="no-such-set")                                 # no data: no verdict


def test_infra_backoff_doubles_and_is_capped():
    assert [strikes.infra_backoff(k) for k in (1, 2, 3, 4, 5)] == [60, 120, 240, 480, 600]


def test_unpark_sticks_the_old_red_heads_do_not_park_the_branch_again(tmp_path, monkeypatch):
    """2026-11-15: after `unpark`, the next ship push ran check(), which re-read the same two old red heads from
    `gh run list`, counted them again and parked the branch at once (PR 521, PR 496). Forgiven heads stay forgiven;
    a NEW red head still counts."""
    p = tmp_path / "strikes.json"
    monkeypatch.setattr(strikes, "whose_red", lambda *a, **k: ("own", "test: own failure"))
    for h in ("aaa", "bbb", "ccc"):
        strikes.record_failure("fix/x", h, "red", pr=9, inbox=lambda *a, **k: None, p=p)
    assert strikes.parked_for("fix/x", p) is not None
    assert strikes.unpark("9", p=p)["ok"]
    runs = [{"headSha": "aaa", "conclusion": "failure", "name": "t"},
            {"headSha": "bbb", "conclusion": "failure", "name": "t"},
            {"headSha": "ccc", "conclusion": "failure", "name": "t"}]
    r = strikes.check("fix/x", fetch=lambda b: runs, inbox=lambda *a, **k: None, p=p, logger=lambda run: [],
                      base_of=lambda h: "", runner=lambda *a, **k: ("own", "test"))
    assert strikes.parked_for("fix/x", p) is None and r["own"] == 0
    runs.append({"headSha": "ddd", "conclusion": "failure", "name": "t"})
    strikes.check("fix/x", fetch=lambda b: runs, inbox=lambda *a, **k: None, p=p, logger=lambda run: [],
                  base_of=lambda h: "", runner=lambda *a, **k: ("own", "test"))
    assert strikes.load(p)["strikes"]["fix/x"]["rounds"][-1]["head"] == "ddd"


def test_ship_counter_after_unpark_does_not_recount_forgiven_heads(tmp_path, monkeypatch):
    """2026-11-15: after the owner's unpark, `fieldkit ship push` (ship._own_reds -> count_red) re-recorded the same old
    red heads and refused auto-merge as TWO STRIKES again (PR 521)."""
    monkeypatch.setattr(strikes, "whose_red", lambda *a, **k: ("own", "test: own failure"))
    for h in ("aaa", "bbb", "ccc"):
        strikes.record_failure("fix/y", h, "red", pr=7, inbox=lambda *a, **k: None)
    assert strikes.unpark("7")["ok"]
    r = strikes.count_red("fix/y", "aaa", [], "", "red", inbox=lambda *a, **k: None)
    assert r["kind"] == "forgiven" and not r["counted"]
    assert strikes.parked_for("fix/y") is None
    assert len(strikes.load()["strikes"].get("fix/y", {}).get("rounds", [])) == 0
