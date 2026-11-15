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
