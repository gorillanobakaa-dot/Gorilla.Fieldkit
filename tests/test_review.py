"""fieldkit review (owner 2026-10-31): parked ideas and inbox items in one list, one answer. Fixture stores only."""
import pytest

from fieldkit import ideas, review
from fieldkit.autopilot import inbox

NOW = 1798000000.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    from fieldkit.buildh import task
    monkeypatch.setattr(inbox, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(review, "ROOT", tmp_path)
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    return tmp_path


def _seed(root):
    ideas.add("Idea one", "does one", root=root, now=NOW)
    inbox.add("Publish v1", "release is green", "fieldkit ship gh-release v1")
    inbox.add("Which pack?", "pick one", kind="choice")


def _ask(*a):
    it = iter(a)
    return lambda _p: next(it)


PLAN = """# plan
| P5 | Gorilla OpenCode can do everyday jobs on Windows and Linux: (a) a suite | x | y | PLANNED |
| W10 | Fieldkit is baked into Gorilla OpenCode | x | y | FINAL GOAL |

## Order

| phase | items | why |
|---|---|---|
| 7d. Basic work first | P5 | basics |
| 8. One system | W10 | last |
"""


def test_page_in_three_groups_in_plain_words(env):
    (env / "docs").mkdir()
    (env / "docs" / "HARNESS-PLAN.md").write_text(PLAN, encoding="utf-8")
    _seed(env)
    inbox.add("Toolbox size?", "why", plan="P5", what="Which tools ship inside the program.",
              if_approve="Zero builds the core pack.", if_leave="The build waits.")
    inbox.add("Fan tray gone", "text mentions P9 only", what="w", if_approve="a", if_leave="l")
    es = review.entries(env)
    assert [e["ref"] for e in es] == ["#3", "#1", "#2", "#4", "I-01"]       # main goal, everyday, ideas
    text = "\n".join(review.render(es, env))
    heads = [text.index(h) for _, h in review.GROUPS]
    assert heads == sorted(heads)
    assert "THE MAIN GOAL" in text and "Portage" in text and "W10" in text
    assert "P5 - Gorilla OpenCode can do everyday jobs on Windows and Linux" in text
    assert text.count("If you approve:") == len(es) and text.count("If you leave it:") == len(es)
    assert "1. Toolbox size?  [#3, plan row P5]" in text
    assert "(no plain explanation was written for this item)" in text
    assert text.index("fieldkit ship gh-release v1") > text.index("2. Publish v1")
    assert "Plan row: P5 - Gorilla" in text


def test_plan_is_inferred_from_text_when_no_field(env):
    (env / "docs").mkdir()
    (env / "docs" / "HARNESS-PLAN.md").write_text(PLAN, encoding="utf-8")
    inbox.add("P5b toolbox", "grows it")
    assert review.entries(env)[0]["plan"] == "P5"


def test_pick_idea_and_inbox_item_approved_once(env):
    _seed(env)
    res = review.review(env, ask=_ask("1,3 a"), say=lambda *_: None, now=NOW)
    assert [r["status"] for r in res] == ["accepted", "approved"]
    assert [i["n"] for i in inbox.items("open")] == [2]
    done = inbox.items("done")[0]
    assert done["approved"] is True and done["note"] == "approved by the owner at a terminal"
    assert ideas.listing("accepted", env)[0]["plan_id"] == "P1"


def test_words_recorded_and_a_choice_needs_them(env):
    _seed(env)
    res = review.review(env, ask=_ask("2 a"), say=lambda *_: None, now=NOW)
    assert res == [] and len(inbox.items("open")) == 2          # left open: no words
    review.review(env, ask=_ask("2 a: core embedded + media pack"), say=lambda *_: None, now=NOW)
    it = [i for i in inbox.items("done") if i["n"] == 2][0]
    assert it["note"].endswith("core embedded + media pack") and it["answer"] == "core embedded + media pack"


def test_forget_dismisses_inbox_and_keeps_note_on_idea(env):
    _seed(env)
    review.review(env, ask=_ask("f: not now"), say=lambda *_: None, now=NOW)
    assert not inbox.items("open") and not ideas.listing("parked", env)
    assert inbox.items("done")[0]["note"].startswith("dismissed by the owner")
    assert inbox.items("done")[0]["approved"] is False
    assert ideas.listing("forgotten", env)[0]["note"] == "not now"


def test_enter_keeps_everything_waiting(env):
    _seed(env)
    assert review.review(env, ask=_ask(""), say=lambda *_: None, now=NOW) == []
    assert len(inbox.items("open")) == 2


def test_refused_when_not_an_owner_terminal(env, monkeypatch):
    from fieldkit.buildh import task
    _seed(env)
    monkeypatch.setattr(task, "owner_terminal", lambda: False)
    with pytest.raises(review.Refused):
        review.review(env, ask=_ask("a"), say=lambda *_: None)
    assert len(inbox.items("open")) == 2
    with pytest.raises(ideas.Refused):
        ideas.review(env, ask=_ask("a"), say=lambda *_: None)
    assert review.main([]) == 1


def test_the_page_is_rewritten_after_inbox_add_and_done(env):
    inbox.add("Need a yes", "because", "do it")
    page = review.page_path(env)
    assert "Need a yes" in page.read_text(encoding="utf-8")
    inbox.done(1, "ok")
    assert "Nothing waits" in page.read_text(encoding="utf-8")


def test_open_writes_the_page_and_hands_it_to_the_viewer(env):
    _seed(env)
    got = []
    p = review.open_page(env, run=got.append)
    assert got == [str(p)] and "I-01" in p.read_text(encoding="utf-8")
