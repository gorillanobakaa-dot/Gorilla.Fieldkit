"""P18 stage 8: every open item gets a final state with evidence. Each rule can fail (mutations.yaml)."""
import pytest

from fieldkit.capture import classify as C, cli, episodes as ep, store as st
from tests.capture.test_distill import rec


@pytest.fixture
def s(tmp_path):
    return st.Store(tmp_path / "cap")


def test_items_in_an_episode_cite_it_and_the_rest_cite_no_signal(s):
    for i in range(3):
        s.record(rec(f"git add f{i}", session="s1", problem="ship it"))
    s.record(rec("lone", session="", problem="x", kind="script", origin="scan:docs"))
    ep.store_episodes(s)
    marks, rules = C.classify(s)
    by = {m[0]: m[2] for m in marks}
    assert rules == {"episode": 3, "failure-fix": 0, "same-path": 0, "no-signal": 1}
    assert sum(v.startswith("episode ep") for v in by.values()) == 3
    assert any(v.startswith("no-signal: script from scan:docs") for v in by.values())
    assert all(m[1] == "procedure" for m in marks)


def test_a_failure_fix_pair_is_cited(s):
    s.record(rec("make it", code=1, errors="Error: boom", session="s9", problem="a"))
    s.record(rec("make it now", session="s9", problem="a"))
    marks, rules = C.classify(s)
    assert rules["failure-fix"] == 2 and len(marks) == 2
    assert all(m[2].startswith("failure-fix pair with cap-") for m in marks)


def test_a_repeated_path_points_to_the_first_item(s):
    s.record(rec("v1", session="", kind="file", path="/x/a.py", problem="z"))
    s.record(rec("v2", session="", kind="file", path="/x/a.py", problem="z"))
    marks, rules = C.classify(s)
    assert rules["same-path"] == 1 and rules["no-signal"] == 1
    assert [m[2] for m in marks if m[2].startswith("same path as")]


def test_apply_closes_the_ledger_and_keeps_every_item(s, monkeypatch, tmp_path):
    for i in range(4):
        s.record(rec(f"cmd {i}", session="", problem="q"))
    assert len(s.ledger()["outstanding"]) == 4
    marks, _ = C.classify(s)
    assert C.apply(s, marks) == 4
    led = s.ledger()
    assert led["outstanding"] == [] and led["unaccounted"] == [] and len(led["items"]) == 4
    assert all(r["evidence"] for r in led["items"])
    assert C.classify(s)[0] == []


def test_closed_ledger_fails_while_items_are_open_and_passes_after(s, monkeypatch, tmp_path):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "cap"))
    s.record(rec("one", session="", problem="q"))
    assert cli.main(["ledger", "--strict"]) == 0
    assert cli.main(["ledger", "--closed"]) == 3
    assert cli.main(["classify", "--apply"]) == 0
    assert cli.main(["ledger", "--closed"]) == 0
