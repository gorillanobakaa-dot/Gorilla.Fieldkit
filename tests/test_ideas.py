"""fieldkit ideas (owner order 2026-10-29): suggestions parked, never added to the running plan; only a person at a
terminal accepts or forgets. Fixture stores only; the clock is injected; no cable."""
import json
from pathlib import Path

import pytest

from fieldkit import ideas, plan

NOW = 1798000000.0   # fixed clock


@pytest.fixture
def root(tmp_path):
    return tmp_path


@pytest.fixture
def person(monkeypatch):
    from fieldkit.buildh import task
    monkeypatch.setattr(task, "owner_terminal", lambda: True)


@pytest.fixture
def model(monkeypatch):
    from fieldkit.buildh import task
    monkeypatch.setattr(task, "owner_terminal", lambda: False)


def test_add_assigns_ids_writes_json_and_markdown(root):
    a = ideas.add("First", "does a thing", root=root, now=NOW)
    b = ideas.add("Second", "does b", what="Two plain sentences.", source="agent:claude", root=root, now=lambda: NOW + 60)
    assert (a["id"], b["id"]) == ("I-01", "I-02")
    assert a["status"] == "parked" and a["what"] == "does a thing" and b["what"] == "Two plain sentences."
    assert a["date"] != b["date"]
    d = ideas.store_dir(root)
    assert json.loads((d / "ideas.json").read_text())["ideas"][1]["source"] == "agent:claude"
    md = (d / "TODO.Features.Plan.md").read_text()
    assert "## I-01: First" in md and "Two plain sentences." in md


def test_add_refuses_empty_and_bad_source(root):
    with pytest.raises(ideas.Refused):
        ideas.add("", "x", root=root)
    with pytest.raises(ideas.Refused):
        ideas.add("t", "", root=root)
    with pytest.raises(ideas.Refused):
        ideas.add("t", "w", source="someone", root=root)


def test_a_read_never_creates_the_store(root):
    assert ideas.listing(root=root) == [] and ideas.line(root) == "" and ideas.hook_text("s1", root) == ""
    assert ideas.brief(root) == "No parked ideas."
    assert not ideas.store_dir(root).exists()


def test_list_filters_by_status(root, person):
    ideas.add("A", "a", root=root, now=NOW)
    ideas.add("B", "b", root=root, now=NOW)
    ideas.set_status(["I-02"], "forgotten", root, NOW)
    assert [e["id"] for e in ideas.listing("parked", root)] == ["I-01"]
    assert [e["id"] for e in ideas.listing("forgotten", root)] == ["I-02"]
    with pytest.raises(ideas.Refused):
        ideas.listing("bogus", root)


def test_a_model_cannot_accept_forget_or_review(root, model):
    ideas.add("A", "a", root=root, now=NOW)
    for st in ("accepted", "forgotten"):
        with pytest.raises(ideas.Refused, match="real terminal"):
            ideas.set_status(["I-01"], st, root, NOW)
    with pytest.raises(ideas.Refused, match="real terminal"):
        ideas.review(root, ask=lambda _: "a", say=lambda *_: None)
    assert ideas.listing(root=root)[0]["status"] == "parked"


def test_cli_review_refuses_without_a_terminal(root, model, monkeypatch, capsys):
    monkeypatch.setattr(ideas, "ROOT", root)
    ideas.add("A", "a", root=root, now=NOW)
    assert ideas.main(["review"]) == 1
    assert "real terminal" in capsys.readouterr().err


def test_accept_assigns_the_next_plan_id_and_writes_a_brief(root, person):
    (root / "docs").mkdir()
    (root / "docs" / "HARNESS-PLAN.md").write_text("| P1 | taken | PLANNED |\n", encoding="utf-8")
    ideas.add("Idea", "what it does", root=root, now=NOW)
    r = ideas.set_status(["I-01"], "accepted", root, NOW)[0]
    assert r["plan_id"] == "P2" and Path(r["brief"]).is_file()
    assert "P2" in Path(r["brief"]).read_text() and "what it does" in Path(r["brief"]).read_text()
    e = ideas.listing(root=root)[0]
    assert e["status"] == "accepted" and e["decided"]


def test_forgotten_stays_in_the_file_struck_through(root, person):
    ideas.add("Gone", "g", root=root, now=NOW)
    ideas.set_status(["I-01"], "forgotten", root, NOW)
    md = (ideas.store_dir(root) / "TODO.Features.Plan.md").read_text()
    assert "~~Gone~~" in md


def test_a_decided_idea_is_not_decided_again(root, person):
    ideas.add("A", "a", root=root, now=NOW)
    ideas.set_status(["I-01"], "forgotten", root, NOW)
    with pytest.raises(ideas.Refused, match="already"):
        ideas.set_status(["I-01"], "accepted", root, NOW)
    with pytest.raises(ideas.Refused, match="no such idea"):
        ideas.set_status(["I-09"], "accepted", root, NOW)


@pytest.mark.parametrize("text,n,want", [("", 3, ("p", None)), ("A", 3, ("a", None)), ("f", 3, ("f", None)),
                                         ("1,3", 3, ("pick", [1, 3])), ("1, 3, 1", 3, ("pick", [1, 3])),
                                         ("4", 3, None), ("0", 3, None), ("zzz", 3, None)])
def test_parse_choice(text, n, want):
    assert ideas.parse_choice(text, n) == want


def _seed(root):
    for t in ("One", "Two", "Three"):
        ideas.add(t, f"{t} does x", root=root, now=NOW)


def _answers(*a):
    it = iter(a)
    return lambda _prompt: next(it)


def test_review_accept_all(root, person):
    _seed(root)
    res = ideas.review(root, ask=_answers("a"), say=lambda *_: None, now=NOW)
    assert [r["status"] for r in res] == ["accepted"] * 3
    assert [r["plan_id"] for r in res] == ["P1", "P2", "P3"]


def test_review_subset_forget_leaves_the_rest_parked(root, person):
    _seed(root)
    ideas.review(root, ask=_answers("1,3", "f"), say=lambda *_: None, now=NOW)
    assert {e["id"]: e["status"] for e in ideas.listing(root=root)} == {"I-01": "forgotten", "I-02": "parked", "I-03": "forgotten"}


def test_review_enter_keeps_everything_parked(root, person):
    _seed(root)
    assert ideas.review(root, ask=_answers(""), say=lambda *_: None, now=NOW) == []
    assert len(ideas.listing("parked", root)) == 3


def test_review_shows_title_and_what_before_asking(root, person):
    _seed(root)
    said = []
    ideas.review(root, ask=_answers("p"), say=said.append, now=NOW)
    assert "Two does x" in said[0] and "I-02" in said[0]


def test_review_gives_up_after_unclear_answers(root, person):
    _seed(root)
    assert ideas.review(root, ask=_answers(*["?"] * 5), say=lambda *_: None, now=NOW) == []
    assert len(ideas.listing("parked", root)) == 3


def test_hook_line_then_brief_on_the_first_prompt_only(root):
    _seed(root)
    first = ideas.hook_text("sess-1", root)
    assert first.startswith("3 parked ideas - fieldkit ideas review") and "Would do: One does x" in first
    second = ideas.hook_text("sess-1", root)
    assert second == "3 parked ideas - fieldkit ideas review"
    assert "Would do" in ideas.hook_text("sess-2", root)


def test_plan_next_counts_parked_ideas_apart_from_the_plan(root, tmp_path):
    (root / "docs").mkdir()
    (root / "docs" / "HARNESS-PLAN.md").write_text(
        "| R1 | a | PLANNED |\n\n## Order (approved)\n\n| phase | items | why |\n|---|---|---|\n| 1. x | R1 | y |\n",
        encoding="utf-8")
    _seed(root)
    r = plan.next_(root, run=lambda *a, **k: type("R", (), {"stdout": "", "returncode": 1})())
    assert r["parked_ideas"] == 3 and "ideas" not in r["line"] and r["open"] == ["R1"]


def test_a_plan_row_with_a_p_id_is_read(root):
    assert plan.items("| P1 | idea | PLANNED |\n") == {"P1": "PLANNED"}
    assert plan.order("## Order\n\n| phase | items | why |\n|---|---|---|\n| 9. later | P1 | y |\n") == [("9. later", ["P1"])]


def test_the_markdown_is_rebuilt_from_the_json_when_missing_or_stale(root):
    ideas.add("A", "why a", root=root, now=NOW)
    md = ideas.store_dir(root) / ideas.MD_NAME
    md.unlink()
    assert [e["id"] for e in ideas.listing(root=root)] == ["I-01"] and "I-01: A" in md.read_text(encoding="utf-8")
    md.write_text("hand-edited", encoding="utf-8")
    assert ideas.ensure_md(root) is True and "I-01: A" in md.read_text(encoding="utf-8")
    assert ideas.ensure_md(root) is False                      # in step: nothing rewritten


def test_a_missing_json_is_never_invented_by_the_markdown_rebuild(root):
    assert ideas.ensure_md(root) is False and not ideas.store_dir(root).exists()


def test_the_room_hook_shows_the_ideas_line_then_the_brief_once(root, monkeypatch):
    from fieldkit.autopilot import room
    monkeypatch.undo()                                          # conftest silences _ideas; this test wants the real one
    ideas.add("A", "why a", root=root, now=NOW)
    monkeypatch.setattr(ideas, "ROOT", root)
    monkeypatch.setattr(ideas, "state_root", lambda r=None: Path(r or root))
    out = room._ideas({"session_id": "s9"}, "UserPromptSubmit", "")
    assert "1 parked ideas - fieldkit ideas review" in out and "I-01" in out
    assert room._ideas({"session_id": "s9"}, "UserPromptSubmit", "").count("I-01") == 0
    assert room._ideas({"session_id": "s9"}, "PreToolUse", "x") == "x"
    # and the hook's one context builder calls it (new session, so the brief shows again)
    for f in ("_net", "_hog", "_rotate", "_plan", "_relay"):
        monkeypatch.setattr(room, f, lambda call, event, ctx: ctx)
    monkeypatch.setattr(room, "unseen", lambda sid: [])
    assert "parked ideas - fieldkit ideas review" in room._context({"session_id": "s10"}, "UserPromptSubmit")


@pytest.mark.parametrize("text,want", [("1,2 a", ("a", [1, 2])), ("a 1,2", ("a", [1, 2])), ("1 f", ("f", [1])),
                                       ("f 3", ("f", [3])), ("1, 2  p", ("p", [1, 2])), ("a", ("a", None)),
                                       ("1,3", ("pick", [1, 3])), ("", ("p", None)),
                                       ("4 a", None), ("a 0", None), ("1 x", None), ("a b", None), ("1,2 a f", None)])
def test_parse_answer(text, want):
    assert ideas.parse_answer(text, 3) == want


def test_review_numbers_and_action_in_one_answer(root, person):
    _seed(root)
    res = ideas.review(root, ask=_answers("1,2 a"), say=lambda *_: None, now=NOW)
    assert [r["id"] for r in res] == ["I-01", "I-02"] and {r["status"] for r in res} == {"accepted"}
    assert [e["id"] for e in ideas.listing("parked", root)] == ["I-03"]


def test_review_bad_answers_are_still_refused(root, person):
    _seed(root)
    res = ideas.review(root, ask=_answers(*["9 a"] * 5), say=lambda *_: None, now=NOW)
    assert res == [] and len(ideas.listing("parked", root)) == 3
