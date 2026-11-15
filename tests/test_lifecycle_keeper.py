"""P9: the keeper moves open work on with no model: fake gh, fake task store, one tick at a time."""
import json
import time
from pathlib import Path

import pytest

from fieldkit.lifecycle import idle, keeper, routing, tasks

NOW = time.mktime(time.strptime("2026-11-12 12:00:00", "%Y-%m-%d %H:%M:%S"))
CFG = {"every_min": 5, "hours": 24, "rules": {
    "behind_update": {"enabled": True}, "red_fix": {"enabled": True}, "died_restart": {"enabled": True, "max_restarts": 1},
    "queue_start": {"enabled": True, "file": "queue.yaml"},
    "wake": {"enabled": True, "headless_after_min": 45, "plan": "P9", "model": "sonnet"}}}


class Fake:
    """The ports of keeper.Ports, in memory."""

    def __init__(self, tmp):
        self.repo = str(tmp)
        self.led, self.pr_list, self.ci, self.updated, self.started, self.queue_items = {}, [], {}, [], [], []
        self.parked_set, self.own, self.work, self.slot, self.rooms, self.timer_calls, self.acted = set(), {}, {}, True, [], [], []
        self.counted = []

    def ledger(self): return self.led
    def prs(self): return self.pr_list
    def ci_state(self, sha): return self.ci.get(sha, ("none", ""))
    def update_branch(self, n, head): self.updated.append((n, head)); return True, "ok"
    def failed_log(self, sha): return ["FAILED tests/test_a.py::test_one - assert 1 == 2"]
    def parked(self, b): return b in self.parked_set
    def own_red_heads(self, b): return self.own.get(b, [])
    def count_red(self, b): self.counted.append(b)
    def queue(self, rel): return self.queue_items
    def admit(self, led, on=None): return (True, "") if self.slot else (False, "multi mode runs 2 job(s) at once")
    def work_left(self, t): return self.work.get(t["id"], "")
    def handoff_of(self, t): return "/h/HANDOFF.md"
    def room(self, obj, detail): self.rooms.append(detail)
    def watch_findings(self, led): return []
    def watch_act(self, found, led): self.acted.append(found)
    def timer_off(self): self.timer_calls.append("off"); return "removed"
    def timer_on(self): self.timer_calls.append("on"); return "installed"

    def start(self, brief, **kw):
        if not self.slot:
            raise routing.ModeRefused("multi mode runs 2 job(s) at once")
        self.started.append((Path(brief), kw))
        return {"id": f"T-new{len(self.started)}", "status": "started"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_KEEPER_DIR", str(tmp_path / "k"))
    monkeypatch.setenv("FIELDKIT_KEEPER_NO_SCHEDULE", "1")
    return Fake(tmp_path)


def pr(n, head="abc12345", branch="feat/x", state="CLEAN", auto=True, draft=False):
    return {"number": n, "headRefName": branch, "headRefOid": head, "isDraft": draft, "mergeStateStatus": state,
            "autoMergeRequest": {"mergeMethod": "MERGE"} if auto else None}


def ledger_row(tid, status, brief, wt, **kw):
    return dict({"id": tid, "title": "T " + tid, "status": status, "on": "here", "hash": "h-" + tid[:3], "brief": str(brief),
                 "worktree": str(wt), "at": "2026-11-12 10:00:00", "reported": "2026-11-12 11:00:00", "plan": "P9"}, **kw)


def test_behind_green_automerge_gets_update_branch_once(env):
    env.pr_list = [pr(7, "h1", state="BEHIND")]
    env.ci = {"h1": ("green", "")}
    r = keeper.tick(env, CFG, NOW)
    assert env.updated == [(7, "h1")] and any(a["rule"] == "behind_update" for a in r["actions"])
    keeper.tick(env, CFG, NOW + 300)
    assert env.updated == [(7, "h1")], "the same head is never updated twice"


@pytest.mark.parametrize("ci,auto,state", [("red", True, "BEHIND"), ("green", False, "BEHIND"), ("green", True, "CLEAN")])
def test_behind_rule_leaves_the_others_alone(env, ci, auto, state):
    env.pr_list = [pr(7, "h1", state=state, auto=auto)]
    env.ci = {"h1": (ci, "")}
    keeper.tick(env, CFG, NOW)
    assert env.updated == []


def test_red_own_fault_starts_one_fix_job_from_a_generated_brief(env):
    env.pr_list = [pr(9, "r1", "fix/y", state="UNSTABLE")]
    env.ci = {"r1": ("red", "")}
    env.own = {"fix/y": ["r1"]}
    env.led = {"T-1": ledger_row("T-1", "done", "/b", "/w", branch="fix/y")}
    keeper.tick(env, CFG, NOW)
    assert len(env.started) == 1
    text = env.started[0][0].read_text(encoding="utf-8")
    assert "tests/test_a.py::test_one" in text and "PLAN ITEM: P9" in text
    keeper.tick(env, CFG, NOW + 300)
    assert len(env.started) == 1, "one fix job per (branch, head)"


def test_red_parked_or_not_own_starts_nothing(env):
    env.pr_list = [pr(9, "r1", "fix/y"), pr(10, "r2", "fix/z")]
    env.ci = {"r1": ("red", ""), "r2": ("red", "")}
    env.parked_set = {"fix/y"}
    env.led = {"T-1": ledger_row("T-1", "done", "/b", "/w", branch="fix/z")}
    r = keeper.tick(env, CFG, NOW)
    assert env.started == [] and env.counted == ["fix/z"]
    assert any("red-unproven:10" in a["key"] for a in r["actions"]) or env.acted, "an unattributed red goes to the wake rule"


def test_died_with_work_restarts_once_in_its_own_worktree_then_parks(env, tmp_path):
    brief, wt = tmp_path / "brief.md", tmp_path / "wt"
    brief.write_text("# job\nPLAN ITEM: P9\n", encoding="utf-8")
    wt.mkdir()
    env.led = {"T-a": ledger_row("T-a", "died", brief, wt)}
    env.work = {"T-a": "HANDOFF /h/HANDOFF.md"}
    keeper.tick(env, CFG, NOW)
    assert len(env.started) == 1
    bp, kw = env.started[0]
    assert kw["again"] is True and kw["worktree"](None, "x")[0] == wt, "the restart runs in the dead job's worktree"
    assert "RESUME" in bp.read_text(encoding="utf-8") and str(wt) in bp.read_text(encoding="utf-8")
    env.led["T-new1"] = ledger_row("T-new1", "died", bp, wt, title="other", hash="h-new")
    env.work["T-new1"] = "uncommitted work in the worktree"
    r = keeper.tick(env, CFG, NOW + 300)
    assert len(env.started) == 1, "second death: no third try"
    assert any("parked" in a["what"] for a in r["actions"])


def test_died_without_work_is_not_restarted(env, tmp_path):
    env.led = {"T-a": ledger_row("T-a", "died", tmp_path / "b.md", tmp_path)}
    keeper.tick(env, CFG, NOW)
    assert env.started == []


def test_queue_starts_one_brief_when_a_slot_is_free_and_waits_when_not(env):
    env.queue_items = [{"brief": "state/briefs/a.md"}, {"brief": "state/briefs/b.md"}]
    env.slot = False
    r = keeper.tick(env, CFG, NOW)
    assert env.started == [] and any("no free slot" in a["what"] for a in r["actions"])
    env.slot = True
    keeper.tick(env, CFG, NOW + 300)
    assert [b.name for b, _ in env.started] == ["a.md"], "one per tick, in order"
    keeper.tick(env, CFG, NOW + 600)
    assert [b.name for b, _ in env.started] == ["a.md", "b.md"], "the started one is not started again"


def test_wake_headless_job_only_when_old_and_only_once(env, tmp_path):
    env.watch_findings = lambda led: [{"state": "UNHANDLED", "id": "T-b", "line": "UNHANDLED T-b blocked", "next": "x"}]
    env.led = {"T-b": ledger_row("T-b", "blocked", tmp_path / "b.md", tmp_path)}
    keeper.tick(env, CFG, NOW)
    assert env.acted and env.started == [], "said at once, no job yet"
    keeper.tick(env, CFG, NOW + 46 * 60)
    assert len(env.started) == 1 and env.started[0][1]["model"] == "sonnet"
    keeper.tick(env, CFG, NOW + 60 * 60)
    assert len(env.started) == 1


def test_nothing_open_switches_the_timer_off_and_open_work_keeps_it(env):
    r = keeper.tick(env, CFG, NOW)
    assert env.timer_calls == ["off"] and not r["open"]
    env.timer_calls.clear()
    env.pr_list = [pr(3, "p1", state="CLEAN")]
    r = keeper.tick(env, CFG, NOW + 300)
    assert env.timer_calls == [] and r["open"]


def test_every_action_is_journaled_in_the_keeper_log_and_the_room(env):
    env.pr_list = [pr(7, "h1", state="BEHIND")]
    env.ci = {"h1": ("green", "")}
    keeper.tick(env, CFG, NOW)
    assert any("keeper did behind_update" in x for x in env.rooms)
    log = keeper.last_actions()
    assert log and log[-1]["rule"] == "behind_update" and log[-1]["at"].startswith("2026-11-12 12:00")


def test_a_second_tick_while_one_runs_does_nothing(env):
    with keeper.lock(NOW) as held:
        assert held
        r = keeper.tick(env, CFG, NOW)
        assert r["held"] is False and env.started == [] and env.updated == []


def test_stop_guard_warns_only_with_jobs_in_flight_and_no_timer():
    led = {"T-1": {"id": "T-1", "status": "started"}}
    assert "KEEPER WARN" in keeper.stop_guard(led, timer=False)
    assert keeper.stop_guard(led, timer=True) == ""
    assert keeper.stop_guard({"T-1": {"id": "T-1", "status": "done"}}, timer=False) == ""


def test_the_shipped_rule_table_names_every_rule_function():
    cfg = keeper.load()
    assert set(cfg["rules"]) == set(keeper.RULES) and all(r.get("enabled") for r in cfg["rules"].values())


def test_timer_plan_uses_the_main_checkout_not_this_tree(monkeypatch, tmp_path):
    from fieldkit.core import settings
    monkeypatch.delenv("FIELDKIT_KEEPER_NO_SCHEDULE", raising=False)
    steps = keeper._plan()
    assert any(str(settings.state_root()) in str(s[2]) for s in steps if s[0].startswith("write"))  # Windows: "write-utf16" (the schtasks XML)
    assert any("keeper" in str(s) for s in steps)


# ---- the idle report, against the recorded 2026-11-12 night ----------------------------------------------------------

def test_idle_report_shows_the_2026_11_12_night_as_one_gap_of_about_5_5_hours():
    fx = json.loads((Path(__file__).parent / "fixtures" / "idle_2026_11_12.json").read_text(encoding="utf-8"))
    now = idle._t(fx["now"])
    r = idle.report(fx["room"], fx["tasks"], fx["prs"], since="12h", min_minutes=60, now=now)
    assert len(r["gaps"]) == 1
    assert 325 <= r["gaps"][0]["minutes"] <= 340
    assert "05:41" in idle.lines(r)[0] and "11:18" in idle.lines(r)[0]


def test_idle_report_is_empty_when_a_station_acts_often_and_when_nothing_is_open():
    t0 = idle._t("2026-11-12 10:00:00")
    room = [f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t0 + i * 600))}\tx@Fieldkit\ts\tnet\to" for i in range(30)]
    tasks_ = [{"at": "2026-11-12 10:00:00"}]
    assert idle.report(room, tasks_, [], "12h", 30, now=t0 + 3 * 3600)["gaps"] == []
    assert idle.report(room, [], [], "12h", 30, now=t0 + 3 * 3600)["gaps"] == []
    only_worker = [r.replace("x@Fieldkit", "x@wt-job-1") for r in room]
    assert len(idle.report(only_worker, tasks_, [], "12h", 30, now=t0 + 3 * 3600)["gaps"]) == 1


def test_headless_wake_jobs_are_capped_per_day(env, tmp_path):
    cfg = json.loads(json.dumps(CFG))
    cfg["rules"]["wake"]["max_per_day"] = 1
    env.watch_findings = lambda led: [{"state": "UNHANDLED", "id": f"T-{i}", "line": f"UNHANDLED T-{i}", "next": "x"} for i in range(3)]
    keeper.tick(env, cfg, NOW)
    r = keeper.tick(env, cfg, NOW + 46 * 60)
    assert len(env.started) == 1 and sum("headless wake jobs" in a["what"] for a in r["actions"]) == 2
