"""`migrate landed`: an intent in the tree in another form, proven by a verbatim quote that is re-read every time."""
import pytest

from fieldkit.buildh import task
from fieldkit.migrate import landed


@pytest.fixture
def tid(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    (w / "browser").mkdir(parents=True)
    (w / "browser" / "firefox.js").write_text('// -- GORILLA: THIS BROWSER NEVER CALLS HOME --\npref("a.b", true, locked);\n',
                                              encoding="utf-8")
    task.start("t1", "firefox-upgrade", w, [], meta={})
    return "t1"


def test_record_needs_the_quote_in_the_tree(tid):
    with pytest.raises(task.Refused, match="not recorded"):
        landed.record(tid, "I-1", "browser/firefox.js", "pref(\"x.y\", false);", "rewritten by 23.REBASE.158")
    assert landed.load(tid) == {}


def test_record_needs_a_reason_and_a_real_line(tid):
    with pytest.raises(task.Refused, match="say where"):
        landed.record(tid, "I-1", "browser/firefox.js", "NEVER CALLS HOME", "x")
    with pytest.raises(task.Refused, match="verbatim"):
        landed.record(tid, "I-1", "browser/firefox.js", "pref", "rewritten by 23.REBASE.158")


def test_proof_is_rechecked_and_fails_when_the_line_goes(tid):
    r = landed.record(tid, "I-1", "browser/firefox.js", "THIS BROWSER NEVER CALLS HOME", "rewritten by 23.REBASE.158")
    assert "landed in another form" in r["evidence"]
    rec = landed.load(tid)["I-1"]
    w = task.load(tid)["workdir"]
    assert landed.proven(w, rec)[0]
    (task.load(tid)["workdir"] and __import__("pathlib").Path(w, "browser", "firefox.js")).write_text("gone\n", encoding="utf-8")
    ok, ev = landed.proven(w, rec)
    assert not ok and "no longer" in ev


def test_proven_hunks_maps_a_proven_record_to_its_hunks_and_drops_a_stale_one(tmp_path, monkeypatch):
    """verify accepts an obsolete port step of a hunk only while the intent's landed quote is in the tree now."""
    from types import SimpleNamespace
    from fieldkit.migrate import landed, ledger, plan
    (tmp_path / "a.js").write_text("// GORILLA LOCK: no fetch\n", encoding="utf-8")
    recs = {"I-1": {"file": "a.js", "quote": "// GORILLA LOCK: no fetch", "why": "reshaped upstream"},
            "I-2": {"file": "a.js", "quote": "a line that is gone", "why": "stale record"}}
    monkeypatch.setattr(landed, "load", lambda tid: recs)
    monkeypatch.setattr(plan, "Migration", lambda *a, **k: SimpleNamespace(owner=tmp_path))
    monkeypatch.setattr(ledger, "load", lambda owner: {"intents": [
        {"id": "I-1", "patch": "07/x.patch", "hunks": [{"file": "a.js", "n": 8}]},
        {"id": "I-2", "patch": "07/y.patch", "hunks": [{"file": "a.js", "n": 2}]}]})
    got = landed.proven_hunks({"id": "t", "workdir": str(tmp_path)})
    assert list(got) == [("07/x.patch", "a.js", 8)]
    (tmp_path / "a.js").write_text("changed\n", encoding="utf-8")
    assert landed.proven_hunks({"id": "t", "workdir": str(tmp_path)}) == {}
