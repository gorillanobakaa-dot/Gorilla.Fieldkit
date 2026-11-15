"""P18 stages 0-1: the clean request, episodes in the journal, coverage. Each check can fail (mutations.yaml)."""
import pytest

from fieldkit.capture import episodes as ep, store as st


@pytest.fixture
def s(tmp_path):
    return st.Store(tmp_path / "cap")


def rec(cmd, code=0, session="s1", problem="fix the build", desc="", **kw):
    return dict({"kind": "command", "source": cmd, "command": cmd, "cwd": ".", "exit_code": code, "agent": "t",
                 "session": session, "path": None, "problem": problem, "description": desc,
                 "timestamp": kw.pop("ts", "2026-11-03T00:00:00+00:00")}, **kw)


@pytest.mark.parametrize("problem,request_,source", [
    ("fix the build\nmore text", "fix the build", "user"),
    ("Tool loaded.", "", "noise:tool-loaded"),
    ("Another Claude session sent a message:\n<cross-session-message from=\"x\">\nrelease it now\n</cross-session-message>",
     "release it now", "cross-session"),
    ("This session is being continued from a previous conversation", "", "noise:continuation"),
    ("left in scratch-root (somewhere)", "", "scan-note"),
    ("Try again", "", "noise:retry"),
    ("You are the VERIFIER of goal x", "You are the VERIFIER of goal x", "brief"),
    ("", "", "none"),
])
def test_clean_request(problem, request_, source):
    assert ep.clean_request(problem) == (request_, source)


def test_view_keeps_the_noisy_problem_and_adds_the_request(s):
    iid, _ = s.record(rec("ls", problem="Tool loaded."))
    v = s.view(s.state(), iid)
    assert v["problem"] == "Tool loaded." and v["request"] == "" and v["request_source"] == "noise:tool-loaded"


def test_episode_orders_steps_and_counts_failure_and_recovery(s):
    s.record(rec("pytest a", code=1, ts="2026-11-03T00:00:01+00:00"))
    s.record(rec("echo b", ts="2026-11-03T00:00:00+00:00"))
    s.record(rec("pytest a --fix", code=0, ts="2026-11-03T00:00:02+00:00"))
    res = ep.store_episodes(s)
    assert res["episodes_stored"] == 1 and res["items_in_episodes"] == 3
    e = next(iter(s.state()["episodes"].values()))
    steps = ep.steps_of(s, e)
    assert [x[3] for x in steps] == [0, 1, 0] and e["n_failed"] == 1 and e["n_recovered"] == 1
    assert all(x[0] in s.state()["items"] for x in steps)


def test_two_requests_in_one_session_are_two_episodes_and_noisy_ones_are_kept(s):
    s.record(rec("a", problem="build x"))
    s.record(rec("b", problem="test y"))
    s.record(rec("c", problem="Try again"))
    s.record(rec("d", problem="Tool loaded."))
    res = ep.store_episodes(s)
    assert res["episodes_stored"] == 4 and res["with_real_request"] == 2


def test_storing_twice_writes_nothing_new_and_a_changed_episode_is_rewritten(s):
    s.record(rec("a"))
    assert ep.store_episodes(s)["episodes_written"] == 1
    assert ep.store_episodes(s)["episodes_written"] == 0
    s.record(rec("b"))
    assert ep.store_episodes(s)["episodes_written"] == 1
    assert not s.verify()[0]


def test_items_without_a_session_are_counted_not_dropped(s):
    s.record(rec("a", session=""))
    res = ep.store_episodes(s)
    assert res["episodes_stored"] == 0 and res["items_without_session"] == 1


def test_coverage_lists_every_transcript_with_a_reason(s, tmp_path):
    proj = tmp_path / ".claude" / "projects" / "p"
    proj.mkdir(parents=True)
    (proj / "s1.jsonl").write_text('{"type":"tool_use","name":"Bash"}\n')
    (proj / "s2.jsonl").write_text('{"type":"tool_use","name":"Bash"}\n')
    (proj / "s3.jsonl").write_text('{"type":"text"}\n')
    s.record(rec("a", session="s1"))
    cov = ep.coverage(s.state(), home=tmp_path)["agents"]["claude"]
    assert cov["on_disk"] == 3 and cov["captured"] == 1
    reasons = {u["session"]: u["reason"] for u in cov["uncaptured"]}
    assert reasons["s2"].startswith("GAP") and reasons["s3"].startswith("no work call")
