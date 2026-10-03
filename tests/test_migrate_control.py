"""Migration control: stage gates with fakes, `advance` refusing a red gate, adoption of an in-flight migration, the
SITREP and its one next action, and the drift guard (work items, parked tickets, refusals, one writer)."""
import json
import os
from types import SimpleNamespace

import pytest

from fieldkit import cli as fcli
from fieldkit.buildh import cli as bcli
from fieldkit.buildh import install as binstall
from fieldkit.buildh import task
from fieldkit.migrate import guard, plan, sitrep


@pytest.fixture
def mig(tmp_path, monkeypatch):
    """An in-flight task at S6: ported, built from tree T1, installed (marker = build zip), post-install with a red row."""
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    monkeypatch.setattr(binstall, "find_install", lambda: None)
    o = tmp_path / "owner"
    (o / "harness").mkdir(parents=True)
    (o / "harness" / "gorilla_build.py").write_text("", encoding="utf-8")
    (o / "state").mkdir()
    w = tmp_path / "work"
    w.mkdir()
    steps = [{"id": "apply-05.PREFS", "kind": "script", "status": "done", "args": {"group": "05.PREFS"}},
             {"id": "port-05.PREFS-x-h1", "kind": "model", "status": "done", "args": {"patch": "05.PREFS/x.patch"}}]
    t = task.start("t1", "firefox-upgrade", w, steps, meta={"upstream": {"version": "157.0"}, "harness_root": str(o)})
    task.approve("t1", "test")
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / "application.ini").write_text("[App]\nBuildID=20261003000001\n", encoding="utf-8")
    (inst / "gorilla-install.json").write_text(json.dumps({"zip_sha256": "Z1"}), encoding="utf-8")
    (task.STATE / "t1" / "build-result.json").write_text(json.dumps({"tree": "T1", "verified_at": "2026-10-03 06:19:16",
                                                                       "artifacts": {"zip": {"sha256": "Z1"}}}), encoding="utf-8")
    t = task.load("t1")
    task.journal(t, "build-start", head="h")
    task.journal(t, "build-verified", ok=True)
    task.journal(t, "install", target=str(inst), ok=True)
    task.journal(t, "post_install", target=str(inst), ok=False,
                 results=[["prefs", 0, "ok"], ["visual", 1, "FAIL"], ["verify_address_bar", None, "SKIPPED"]])
    (task.STATE / "CURRENT").write_text("t1", encoding="utf-8")

    def make(tree="T1"):
        return plan.Migration("t1", owner=o, install=inst, tree=tree)
    return SimpleNamespace(owner=o, work=w, inst=inst, make=make)


def ids(g):
    return {i["id"]: i for i in g["items"]}


def ok_gate(*_):
    return [plan.item("X.ok", True, "fake", "green")]


def red_gate(*_):
    return [plan.item("X.red", False, "fake", "red", "fake command")]


# -- gates ----------------------------------------------------------------------------------------------------------
def test_s5_gate_follows_the_tree(mig):
    assert plan.gate(mig.make(), "S5")["ok"]
    g = plan.gate(mig.make("T2"), "S5")
    assert not g["ok"] and "the tree is now T2" in ids(g)["S5.build"]["evidence"]
    assert ids(g)["S5.build"]["command"].endswith("build-run t1")


def test_a_measurement_of_another_tree_or_build_is_never_green(mig):
    m = mig.make()
    m.put("intents", {"rows": {"I-1": ["VERIFIED-IN-TREE", "applied", [], "05.PREFS", "05.PREFS/x.patch"]},
                      "counts": {}, "by_group": {}})
    assert ids(plan.gate(m, "S2"))["S2.hunks"]["ok"]
    stale = ids(plan.gate(mig.make("T2"), "S2"))["S2.hunks"]
    assert not stale["ok"] and stale["evidence"].startswith("STALE")


def test_s2_s3_list_the_intents_that_are_not_in_the_tree(mig):
    m = mig.make()
    m.put("intents", {"rows": {"I-a": ["NOT-IN-TREE", "applied", ["NOT-APPLIED: old line still there"], "07.TOOLKIT", "07.TOOLKIT/a.patch"],
                               "I-b": ["NOT-IN-TREE", "ported", ["NOT-APPLIED"], "07.TOOLKIT", "07.TOOLKIT/b.patch"]},
                      "counts": {}, "by_group": {}})
    s2 = ids(plan.gate(m, "S2"))["S2.hunks"]
    assert not s2["ok"] and "I-a" in s2["evidence"] and "I-b" not in s2["evidence"]       # I-b is queued (has a port step)
    s3 = ids(plan.gate(m, "S3"))
    assert not s3["S3.I-a"]["ok"] and s3["S3.I-a"]["brief"] == "B-PATCH-07-TOOLKIT-a-patch"


def test_s6_rows_name_their_command(mig):
    g = ids(plan.gate(mig.make(), "S6"))
    assert g["S6.installed"]["ok"] and g["S6.post-install:prefs"]["ok"]
    assert not g["S6.post-install:visual"]["ok"] and g["S6.post-install:visual"]["command"].endswith("--only visual")
    assert g["S6.post-install:verify_address_bar"]["command"].endswith("--drive")
    assert not g["S6.intents-build"]["ok"] and g["S6.intents-build"]["evidence"] == "not measured"


def test_s1_dispositions_come_from_recorded_decisions_only(mig):
    m = mig.make()
    m.put("intake", {"old": "a" * 40, "new": "b" * 40, "old_version": "155.0.1", "new_version": "157.0",
                     "counts": {"pref": {"new": 2, "auto": 0, "needs_disposition": 2}},
                     "items": [{"id": "INTAKE-pref-1", "category": "pref", "name": "browser.ml.x", "where": "all.js", "detail": "x",
                                "network": False, "ai": True, "auto": None, "hosts": []}],
                     "clusters": [{"id": "INTAKE-AI-ML-browser-ml", "brief": "B-INTAKE-AI-ML-browser-ml", "category": "AI-ML",
                                   "component": "browser.ml", "items": ["INTAKE-pref-1"]}]})
    g = ids(plan.gate(m, "S1"))["S1.INTAKE-AI-ML-browser-ml"]
    assert not g["ok"] and g["brief"] == "B-INTAKE-AI-ML-browser-ml" and g["needs"] == "human"
    snap = task.STATE / "t1" / "briefs" / "B-INTAKE-AI-ML-browser-ml.abc.json"
    snap.parent.mkdir(parents=True)
    snap.write_text(json.dumps({"target": {"intake": ["INTAKE-pref-1"]}}), encoding="utf-8")
    task.journal(task.load("t1"), "decide", brief="B-INTAKE-AI-ML-browser-ml", option="hold", words="later", snapshot=str(snap))
    assert not ids(plan.gate(mig.make(), "S1"))["S1.INTAKE-AI-ML-browser-ml"]["ok"]           # hold disposes nothing
    task.journal(task.load("t1"), "decide", brief="B-INTAKE-AI-ML-browser-ml", option="cut", words="cut it", snapshot=str(snap))
    assert ids(plan.gate(mig.make(), "S1"))["S1.INTAKE-AI-ML-browser-ml"]["ok"]


def test_s7_needs_a_release_pass_of_the_installed_build(mig):
    p = mig.owner / "state" / "leakgate_result.json"
    p.write_text(json.dumps({"BUILD": "20261002000000", "release_run": True, "FINAL_RESULT": "PASS", "policies": {}}), encoding="utf-8")
    g = plan.gate(mig.make(), "S7")
    assert not g["ok"] and "this build has no run" in g["items"][0]["evidence"]
    p.write_text(json.dumps({"BUILD": "20261003000001", "release_run": False, "FINAL_RESULT": "PASS", "policies": {}}), encoding="utf-8")
    assert not plan.gate(mig.make(), "S7")["ok"]
    p.write_text(json.dumps({"BUILD": "20261003000001", "release_run": True, "FINAL_RESULT": "PASS", "policies": {}}), encoding="utf-8")
    assert plan.gate(mig.make(), "S7")["ok"]


# -- adoption and advance -------------------------------------------------------------------------------------------
def test_an_in_flight_migration_is_adopted_where_its_evidence_is(mig):
    m = mig.make()
    assert plan.position(m) == "S6"
    p = plan.init(m)
    assert p["stage"] == "S6" and p["history"][0]["adopted"] and p["guard"] == "on"
    assert {c["stage"] for c in p["carried"]} >= {"S0", "S1"}
    ev = [e for e in plan.journal_events("t1") if e["event"] == "migrate-init"][-1]
    assert ev["sitrep"]["stage"] == "S6" and "S6.post-install:visual" in ev["sitrep"]["red"]
    with pytest.raises(task.Refused, match="already under migration control"):
        plan.init(mig.make())


def test_advance_refuses_a_red_gate_with_the_next_command(mig):
    plan.init(mig.make())
    with pytest.raises(task.Refused) as e:
        plan.advance(mig.make())
    msg = str(e.value)
    assert "S6 Install and proof gate is RED" in msg and "NEXT:" in msg and "cannot be entered" in msg
    assert plan.load_plan("t1")["stage"] == "S6"


def test_advance_moves_one_stage_and_writes_a_sitrep_block(mig, monkeypatch):
    plan.init(mig.make())
    (plan.state_dir("t1") / "work.json").write_text(json.dumps({"item": "S6.x", "stage": "S6"}), encoding="utf-8")
    monkeypatch.setitem(plan.GATES, "S6", ok_gate)
    p = plan.advance(mig.make())
    assert p["stage"] == "S7" and [h["stage"] for h in p["history"]] == ["S6", "S7"]
    ev = [e for e in plan.journal_events("t1") if e["event"] == "migrate-stage"][-1]
    assert ev["stage"] == "S7" and ev["left"] == "S6" and ev["sitrep"]["stage"] == "S7" and ev["sitrep"]["next"]
    assert guard.active("t1") is None                       # an item of the stage just left is never carried over


def test_s9_needs_every_earlier_gate_green_including_carried_ones(mig, monkeypatch):
    plan.init(mig.make(), at="S8")
    for s in ("S0", "S2", "S3", "S4", "S5", "S6", "S7", "S8"):
        monkeypatch.setitem(plan.GATES, s, ok_gate)
    monkeypatch.setitem(plan.GATES, "S1", red_gate)
    with pytest.raises(task.Refused, match=r"S9 \(publish\) needs every gate S0-S8 green; red: S1"):
        plan.advance(mig.make())
    monkeypatch.setitem(plan.GATES, "S1", ok_gate)
    assert plan.advance(mig.make())["stage"] == "S9"


# -- SITREP ---------------------------------------------------------------------------------------------------------
def test_sitrep_uncontrolled_says_so_and_the_next_action_is_init(mig):
    rep = sitrep.build(mig.make())
    assert not rep["controlled"] and rep["stage"] == "S6" and rep["level"] == "6 of 9"
    assert rep["next"]["command"] == "fieldkit build-harness migrate init t1"
    text = "\n".join(sitrep.lines(rep))
    for head in ("SITREP t1", "STAGE S6 Install and proof", "GATE S6: RED", "CARRIED from earlier stages", "PROGRESS",
                 "OPEN BRIEFS", "PARKED TICKETS: 0", "ACTIVE WORK ITEM: none", "LAST 10 JOURNAL EVENTS:", "NEXT: "):
        assert head in text


def test_sitrep_next_action_takes_a_work_item_then_follows_it(mig):
    plan.init(mig.make())
    rep = sitrep.build(mig.make())
    assert rep["next"]["command"].startswith("fieldkit build-harness migrate work t1 S6.")
    item = rep["next"]["command"].rsplit(" ", 1)[1]
    guard.work(mig.make(), item)
    rep = sitrep.build(mig.make())
    assert rep["work"]["item"] == item
    assert rep["next"]["command"] == next(i for i in rep["gate"]["red"] if i["id"] == item)["command"]


def test_sitrep_progress_is_in_the_plans_group_order(mig):
    m = mig.make()
    by = {g: {"total": 1, "applied": 1, "ported": 0, "authored": 0, "open": 0, "in_tree": 1, "proven": 0, "contradicted": 0,
              "explained": 0, "not_in_tree": 0, "out_of_scope": 0} for g in ("13.TELEMETRY.KILL", "05.PREFS", "08.Look")}
    m.put("intents", {"rows": {}, "counts": {"VERIFIED-IN-TREE": 3}, "by_group": by,
                      "coverage": {"with_check_pct": 80.0, "in_scope": 3, "with_build_check_pct": 10.0, "purpose_unknown": 0}})
    text = "\n".join(sitrep.lines(sitrep.build(mig.make())))
    assert text.index("13.TELEMETRY.KILL") < text.index("05.PREFS") < text.index("08.Look")
    assert "TOTAL" in text and "behaviour checks: 80.0%" in text


def test_sitrep_and_plan_from_the_command_line(mig, capsys):
    assert fcli.main(["build-harness", "migrate", "sitrep", "t1", "--json"]) == 3            # red gate: exit 3
    rep = json.loads(capsys.readouterr().out)
    assert rep["stage"] in plan.IDS and rep["next"]["command"].startswith("fieldkit build-harness migrate init t1")
    assert fcli.main(["build-harness", "migrate", "plan", "t1"]) == 0
    out = capsys.readouterr().out
    assert all(f"{s} " in out for s in plan.IDS) and "<== current stage" in out and "one writer" in out


# -- the drift guard ------------------------------------------------------------------------------------------------
def _args(action, *args, park=None, task_id=None):
    return SimpleNamespace(action=action, args=list(args), task=task_id, park=park, json=False, note="", words=None)


def test_no_plan_means_no_guard(mig):
    assert guard.enforce("record", _args("record", "t1", "a.js"), bcli.current_id) is None
    with pytest.raises(task.Refused, match="--park needs migration control"):    # a parked action never runs
        guard.enforce("record", _args("record", "t1", "a.js", park="later"), bcli.current_id)


def test_an_action_without_a_work_item_is_refused_with_a_next_hint(mig):
    plan.init(mig.make())
    with pytest.raises(task.Refused) as e:
        guard.enforce("record", _args("record", "t1", "a.js"), bcli.current_id)
    assert "no active work item" in str(e.value) and "migrate work t1" in str(e.value) and "--park" in str(e.value)


def test_a_work_item_outside_the_current_stage_is_refused(mig):
    plan.init(mig.make())
    with pytest.raises(task.Refused) as e:
        guard.work(mig.make(), "S5.build")
    assert "belongs to S5 Build" in str(e.value) and "park it" in str(e.value)
    with pytest.raises(task.Refused, match="is not an item of"):
        guard.work(mig.make(), "S6.nothing-like-this")


def test_an_allowed_action_is_journalled_against_its_item(mig):
    plan.init(mig.make())
    guard.work(mig.make(), "S6.post-install:visual")
    w = guard.enforce("repair", _args("repair", "t1"), bcli.current_id)
    assert w["item"] == "S6.post-install:visual"
    ev = [e for e in plan.journal_events("t1") if e["event"] == "migrate-action"][-1]
    assert ev["action"] == "repair" and ev["item"] == "S6.post-install:visual" and ev["stage"] == "S6"
    guard.release_writer("t1")


def test_an_item_of_a_stage_left_behind_is_refused(mig):
    plan.init(mig.make())
    (plan.state_dir("t1") / "work.json").write_text(json.dumps({"item": "S3.I-old", "stage": "S3"}), encoding="utf-8")
    with pytest.raises(task.Refused, match="belongs to S3, but the migration is at S6"):
        guard.enforce("build-run", _args("build-run", "t1"), bcli.current_id)


def test_park_records_a_ticket_and_the_action_does_not_run(mig, monkeypatch, capsys):
    plan.init(mig.make())
    from fieldkit.buildh import handedit

    def boom(*a, **k):
        raise AssertionError("the parked action ran")
    monkeypatch.setattr(handedit, "record", boom)
    rc = fcli.main(["build-harness", "record", "t1", "a.js", "--park", "the about: page icon is soft; not an S6 blocker"])
    assert rc == 0 and "PARKED P-001" in capsys.readouterr().out
    t = guard.open_tickets("t1")
    assert [x["id"] for x in t] == ["P-001"] and t[0]["action"] == "record" and t[0]["stage"] == "S6"
    assert "P-001" in "\n".join(sitrep.lines(sitrep.build(mig.make())))
    guard.unpark("t1", "P-001", "fixed in S4 of the next release")
    assert guard.open_tickets("t1") == []
    with pytest.raises(task.Refused, match="say why"):
        guard.park("t1", " ")


def test_one_writer_at_a_time(mig, monkeypatch):
    plan.init(mig.make())
    lock = plan.state_dir("t1") / "writer.lock"
    lock.write_text(json.dumps({"pid": 999999, "action": "build-run", "t": "x"}), encoding="utf-8")
    monkeypatch.setattr(guard, "_alive", lambda pid: pid != 999999 or ALIVE["v"])
    ALIVE["v"] = True
    with pytest.raises(task.Refused, match="one writer at a time"):
        guard.enforce("install", _args("install", "t1"), bcli.current_id)
    ALIVE["v"] = False                                      # its process is gone: the stale lock is taken over
    guard.enforce("install", _args("install", "t1"), bcli.current_id)
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    guard.release_writer("t1")
    assert not lock.exists()


ALIVE = {"v": True}
