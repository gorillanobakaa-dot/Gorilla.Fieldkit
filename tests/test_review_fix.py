"""I8: a review fix becomes a signature that selects its own failing line; the next similar failure needs no reviewer."""
import shutil

import pytest

from fieldkit.build import review_fix as R
from fieldkit.build import triage

LINE = "src/widget.c:9:1: error: frobnicator 'zork' exploded unexpectedly"
LOG = "compiling widget\n" + LINE + "\nmake: *** [all] Error 1\n"
KW = dict(sig_id="frobnicator-exploded", pattern=r"frobnicator '\w+' exploded", log_line=LINE,
          cause="The frobnicator library aborts on a config it cannot parse.",
          fix="Run frobnicator --reset, then build again.")


@pytest.fixture
def sigs(tmp_path, monkeypatch):
    d = tmp_path / "signatures"
    shutil.copytree(triage.SIG_DIR, d)
    monkeypatch.setattr(triage, "SIG_DIR", d)
    return d


def learn(**over):
    kw = dict(KW, **over)
    return R.learn("go", kw.pop("sig_id"), kw.pop("pattern"), kw.pop("cause"), kw.pop("fix"), kw.pop("log_line"), **kw)


def test_a_review_fix_is_recorded_and_selects_its_failure(sigs):
    assert triage.triage_text(LOG, "go")["verdict"] == "unrecognised"
    r = learn()
    assert r["ok"], r
    got = triage.triage_text(LOG, "go")
    assert got["verdict"] == "known" and got["matches"][0]["id"] == "frobnicator-exploded"
    assert R.audit("go") == [{"id": "frobnicator-exploded", "set": "go", "ok": True, "why": "selects its log line"}]


def test_advice_only_still_needs_a_reviewer(sigs):
    learn()
    n = R.needs_reviewer(LOG, "go")
    assert n["needs_reviewer"] and "no stored repair" in n["why"]


def test_a_stored_repair_means_no_reviewer(sigs):
    assert learn(repair=["frobnicator", "--reset"])["ok"]
    n = R.needs_reviewer(LOG, "go")
    assert n == {"needs_reviewer": False, "why": "known as frobnicator-exploded with a stored repair",
                 "id": "frobnicator-exploded", "repair": ["frobnicator", "--reset"]}
    assert R.needs_reviewer("configure...\nbuilding...\n", "go")["needs_reviewer"]


def test_refusals(sigs):
    assert "does not match the log line" in learn(pattern="nothing like it")["why"]
    assert "does not compile" in learn(pattern="(")["why"]
    assert "already exists" in learn(sig_id="go-too-old")["why"]
    assert "already known as" in learn(log_line="SECURITY ERROR: checksum mismatch", pattern="SECURITY ERROR")["why"]
    assert "3 words" in learn(fix="reset")["why"]
    assert "argv" in learn(repair="frobnicator --reset")["why"]
    assert "id" in learn(sig_id="Bad ID")["why"]
    assert "no signature set" in R.learn("nope", "abc-def", "x", "a b c", "a b c", "x")["why"]
    assert "(config)" not in str(triage.available_sets())


def test_a_refused_fix_leaves_the_file_untouched(sigs):
    before = (sigs / "go.yaml").read_text(encoding="utf-8")
    learn(pattern="nothing like it")
    assert (sigs / "go.yaml").read_text(encoding="utf-8") == before


def test_audit_catches_a_pattern_edited_to_miss_its_line(sigs):
    learn()
    p = sigs / "go.yaml"
    import re
    p.write_text(re.sub(r"(?m)^  pattern: 'frobnicator.*$", "  pattern: 'something else entirely'", p.read_text(encoding="utf-8")), encoding="utf-8")
    assert [x["ok"] for x in R.audit("go")] == [False]


def test_a_line_with_no_error_word_is_kept_by_whole_log(sigs):
    assert learn(sig_id="quiet-stall", pattern="stalled at step 4", log_line="build stalled at step 4 for 600 s")["ok"]
    assert triage.triage_text("a\nbuild stalled at step 4 for 600 s\nb\n", "go")["verdict"] == "known"


def test_the_shipped_sets_audit_clean():
    assert all(x["ok"] for x in R.audit("auto"))
