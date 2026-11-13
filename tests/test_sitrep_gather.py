"""P34: `fieldkit sitrep gather` collects the facts with no model and renders a report that passes its own check."""
import json
import subprocess
import time
from types import SimpleNamespace

import pytest

from fieldkit import sitrep
from fieldkit.sitrep import gather

REAL_COLLECT = gather.collect
NOW = time.mktime(time.strptime("2026-11-12 12:00:00", "%Y-%m-%d %H:%M:%S"))
LEDGER = {
    "T-1": {"id": "T-1", "status": "started", "on": "here", "pid": 99, "title": "[VAIO-LNX] # Build the thing",
            "at": "2026-11-12 11:00:00", "log": ""},
    "T-2": {"id": "T-2", "status": "done", "title": "Fix the gate", "at": "2026-11-12 08:00:00",
            "pr": "https://github.com/x/y/pull/12", "branch": "fix/gate"},
    "T-3": {"id": "T-3", "status": "blocked", "title": "Wait on CI", "at": "2026-11-12 09:00:00"},
    "T-4": {"id": "T-4", "status": "done", "title": "Ancient job", "at": "2026-10-01 09:00:00"},
}
GH = json.dumps([{"number": 7, "title": "Add P35", "headRefName": "feat/x",
                  "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"status": "IN_PROGRESS"}]}])


def facts(**kw):
    return REAL_COLLECT(now=NOW, ledger=LEDGER, alive=lambda pid: pid == 99, plan_file="/nonexistent",
                          run=kw.get("run") or (lambda *a, **k: SimpleNamespace(returncode=0, stdout=GH, stderr="")),
                          builds_root="/nonexistent")


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))


def test_tasks_are_split_by_status_old_history_is_left_out_and_the_ledger_is_not_changed():
    t = facts()["tasks"]
    assert [x["id"] for x in t["running"]] == ["T-1"] and t["running"][0]["title"] == "Build the thing"
    assert [x["id"] for x in t["done"]] == ["T-2"] and [x["id"] for x in t["blocked"]] == ["T-3"]
    assert LEDGER["T-1"]["status"] == "started"


def test_a_started_task_whose_process_is_gone_is_died_not_running():
    f = gather.collect(now=NOW, ledger=LEDGER, alive=lambda pid: False, run=lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="[]", stderr=""), builds_root="/nonexistent")
    assert [x["id"] for x in f["tasks"]["died"]] == ["T-1"] and not f["tasks"]["running"]


def test_pull_requests_carry_their_ci_state():
    assert facts()["prs"] == [{"number": 7, "title": "Add P35", "branch": "feat/x", "ci": "pending"}]
    assert gather.ci_state([{"conclusion": "FAILURE"}]) == "failing"
    assert gather.ci_state([{"conclusion": "SUCCESS"}]) == "passing" and gather.ci_state([]) == "none"


def test_one_source_failing_is_an_error_in_its_slot_and_the_rest_still_come_back():
    f = facts(run=lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="gh auth login\nmore"))
    assert "error" in f["prs"] and "gh" in f["prs"]["error"]
    assert f["tasks"]["running"]
    text, chk = gather.render(f, "en")
    assert chk["ok"] and "could not be read" in text and "open pull requests ?" in text


@pytest.mark.parametrize("lang", ["en", "ro", "es"])
@pytest.mark.parametrize("order", ["ask-first", "classic"])
def test_the_rendered_report_passes_the_sitrep_check(lang, order):
    text, chk = gather.render(facts(), lang, order)
    assert chk["ok"], chk["problems"]
    assert sitrep.heading("One line", lang) in text


def test_a_render_of_a_report_that_breaks_a_rule_is_reported_failed(monkeypatch):
    monkeypatch.setattr(gather, "build_text", lambda *a, **k: "# One line\n\nIt is etc. and — odd.\n")
    _, chk = gather.render(facts(), "en")
    assert not chk["ok"]


def test_the_gather_makes_no_model_call(monkeypatch):
    from fieldkit.lifecycle import counsel
    monkeypatch.setattr(counsel, "prose_ask", lambda *a, **k: pytest.fail("gather and render never call a model"))
    gather.render(facts(), "en")


def test_cli_render_json_and_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(gather, "collect", lambda **k: facts())
    a = SimpleNamespace(hours=24, words=False, render=True, lang="en", order="ask-first", md=False, json=True)
    assert gather.main(a) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["check"]["ok"] and out["facts"]["prs"][0]["number"] == 7


def test_words_uses_prose_and_falls_back_to_the_render(monkeypatch, capsys):
    from fieldkit.lifecycle import counsel
    monkeypatch.setattr(gather, "collect", lambda **k: facts())
    a = SimpleNamespace(hours=24, words=True, render=False, lang="en", order="ask-first", md=False, json=False)
    monkeypatch.setattr(counsel, "prose_ask", lambda p, **k: {"ok": True, "text": "All quiet.", "why": "", "model": "haiku"})
    gather.main(a)
    assert "All quiet." in capsys.readouterr().out
    monkeypatch.setattr(counsel, "prose_ask", lambda p, **k: {"ok": False, "text": "", "why": "haiku unavailable"})
    gather.main(a)
    o = capsys.readouterr().out
    assert "no prose: haiku unavailable" in o and "One line" in o
