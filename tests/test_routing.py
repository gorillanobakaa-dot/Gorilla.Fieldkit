"""Solo / Multiplayer and the model router (data: fieldkit/lifecycle/routing.yaml; mutation plan:
tests/routing_mutations.yaml). Owner order 2026-11-02: the router goes DOWN from the owner's ceiling, never above it;
a job that needs Fable is parked; solo runs one job at a time. No network, no model, no clock but the ones passed in."""
import json
import time

import pytest

from fieldkit.lifecycle import routing, tasks


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.delenv("FIELDKIT_ROUTING_MODE", raising=False)


EASY = "fix a typo in the readme and rename one heading"
HARD = "redesign the architecture of the mandate signature protocol, a migration with rollback and a security review"
MID = "refactor the schema with a migration in one module"
VERY = HARD + " plus the credential and secret handling, concurrency and a schema change, privacy and data loss"


def job(i, status="started", on="here", pid=None, at=None, wall=120):
    return {"id": f"T{i}", "status": status, "on": on, "pid": pid, "wall": wall,
            "at": at or time.strftime("%Y-%m-%d %H:%M:%S")}


def test_assess_is_deterministic_and_ranked():
    a, b = routing.assess(EASY), routing.assess(HARD)
    assert a == routing.assess(EASY)
    assert a["rung"] == "haiku" and b["rung"] in ("opus", "fable") and b["score"] > a["score"]


def test_downgrade_from_the_ceiling_not_above():
    r = routing.route(EASY, "sonnet")
    assert (r["model"], r["action"]) == ("haiku", "downgrade")
    r = routing.route(MID, "haiku")
    assert r["model"] == "haiku"                       # the ceiling holds even if the risk wants more
    r = routing.route(MID, "sonnet")
    assert (r["model"], r["action"]) == ("sonnet", "capped")      # needs opus, the owner's ceiling is sonnet


def test_fable_need_is_parked_and_pin_overrides():
    r = routing.route(VERY, "sonnet")
    assert r["action"] == "park" and r["model"] == ""
    assert routing.route(VERY, "fable", pin=True)["model"] == "fable"


def test_cascade_climbs_one_rung_per_failure_up_to_the_ceiling():
    assert routing.cascade("haiku", 0, "sonnet") == "haiku"
    assert routing.cascade("haiku", 1, "sonnet") == "sonnet"
    assert routing.cascade("sonnet", 1, "sonnet") == ""          # at the ceiling the reviewer role repairs it
    assert routing.cascade("haiku", 5, "haiku") == ""


def test_default_choice_is_cheap_and_accept_is_per_day(monkeypatch):
    ch = routing.choice()
    assert (ch["mode"], ch["ceiling"], ch["fresh"]) == ("solo", "sonnet", False)
    routing.set_choice("multi", "opus")
    ch = routing.choice()
    assert (ch["mode"], ch["ceiling"], ch["fresh"]) == ("multi", "opus", True)
    assert routing.choice(today=time.time() + 5 * 86400)["ceiling"] == "sonnet"      # stale -> cheap again
    with pytest.raises(ValueError):
        routing.set_choice("duo", "sonnet")
    with pytest.raises(ValueError):
        routing.set_choice("solo", "gpt")


def test_solo_admits_one_job_and_no_peer():
    alive = lambda pid: True
    assert routing.admit({}, None)[0]
    ok, why = routing.admit({"T1": job(1, pid=7)}, None, alive=alive)
    assert not ok and "T1" in why
    assert routing.admit({"T1": job(1, pid=7)}, None, alive=lambda p: False)[0]       # its process is gone
    assert routing.admit({"T1": job(1, status="done")}, None)[0]
    ok, why = routing.admit({}, "vaio")
    assert not ok and "another device" in why
    old = job(1, on="peer", at="2026-01-01 00:00:00")
    assert routing.admit({"T1": old}, None)[0]                                         # past its wall time


def test_multi_caps_the_parallel_jobs():
    routing.set_choice("multi", "sonnet")
    live = {f"T{i}": job(i, pid=i) for i in range(3)}
    assert not routing.admit(live, None, alive=lambda p: True)[0]
    assert routing.admit(dict(list(live.items())[:2]), None, alive=lambda p: True)[0]
    assert routing.admit({}, "vaio")[0]


def brief(tmp_path, text):
    p = tmp_path / "b.md"
    p.write_text(text + "\nPLAN ITEM: R1\n", encoding="utf-8")
    return str(p)


def fake_start(tmp_path, text, **kw):
    return tasks.start(brief(tmp_path, text), plan="R1", launch=lambda a: {"pid": 99999, "log": "x"},
                       worktree=lambda repo, tid: (None, "test"), **kw)


def test_start_routes_down_and_logs(tmp_path):
    rec = fake_start(tmp_path, EASY, model="sonnet")
    assert rec["model"] == "haiku"
    assert json.loads((tmp_path / "lc" / "routing.jsonl").read_text().splitlines()[-1])["action"] == "downgrade"


def test_start_pin_keeps_the_model(tmp_path):
    assert fake_start(tmp_path, EASY, model="sonnet", pin=True)["model"] == "sonnet"


def test_start_parks_a_fable_job_in_the_inbox(tmp_path):
    parked = []
    with pytest.raises(routing.ModelParked):
        fake_start(tmp_path, VERY, model="sonnet", park=lambda t, w: parked.append((t, w)))
    assert parked and "fable" in parked[0][1]
    assert not (tmp_path / "lc" / "tasks.jsonl").exists()


def test_start_solo_refuses_a_second_job(tmp_path, monkeypatch):
    monkeypatch.setattr("fieldkit.peers.peers.pid_alive", lambda pid: True)
    fake_start(tmp_path, EASY, model="sonnet")
    with pytest.raises(routing.ModeRefused):
        fake_start(tmp_path, EASY + " again", model="sonnet", again=True)


def test_easy_words_lower_the_score():
    assert routing.assess("fix the typo")["score"] < routing.assess("fix the")["score"]


def test_the_cost_guard_checks_the_ceiling_before_the_router_lowers_it(tmp_path):
    with pytest.raises(ValueError, match="fable"):
        fake_start(tmp_path, EASY, model="fable")           # an easy brief would be lowered, but fable was asked for
    assert not (tmp_path / "lc" / "tasks.jsonl").exists()


# The brief that went to Haiku on 2026-11-03 (T-20261103-013221-61849b), its words kept: score 0 by the words alone.
HAIKU_BRIEF = ("Budget warnings at 50% and 90%, a session counter for names, no release; see docs/WHERE-THINGS-LIVE.md. "
               "Add tests and mutate the new checks; wire the room hook.")


def test_floor_keeps_a_tests_and_hooks_job_on_sonnet():
    a = routing.assess(HAIKU_BRIEF)
    assert a["floor"] == "sonnet" and a["rung"] == "sonnet"
    r = routing.route(HAIKU_BRIEF, "sonnet")
    assert (r["model"], r["action"]) == ("sonnet", "use")
    assert routing.route(EASY, "sonnet")["model"] == "haiku"      # no floor word: the router still goes down


def test_floor_holds_in_the_window_squeeze(monkeypatch):
    monkeypatch.setattr(routing, "window_state", lambda d=None: {"fraction": 0.99})
    assert routing.route(HAIKU_BRIEF, "sonnet")["model"] == "sonnet"
    assert routing.route(EASY, "sonnet")["model"] == "haiku"


def test_floor_above_the_ceiling_parks_instead_of_shrinking():
    r = routing.route(HAIKU_BRIEF, "haiku")
    assert r["action"] == "park" and r["model"] == "" and "floor sonnet" in r["why"]
