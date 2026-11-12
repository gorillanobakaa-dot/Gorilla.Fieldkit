"""fieldkit review (owner 2026-10-31): parked ideas and inbox items in one list, one answer. Fixture stores only."""
import pytest

from fieldkit import ideas, review
from fieldkit.autopilot import inbox

NOW = 1798000000.0


class RAN:
    """Stands in for subprocess.run: the review runs an approved item's command, a test never does."""
    calls = []

    @classmethod
    def record(cls, cmd, **kw):
        import subprocess
        cls.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "done\n", "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    from fieldkit.buildh import task
    monkeypatch.setattr(inbox, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(review, "ROOT", tmp_path)
    monkeypatch.setattr(review, "running_rows", lambda root=None: [])      # never the real task ledger
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    monkeypatch.setattr(review, "RUNNER", RAN.record)
    RAN.calls.clear()
    return tmp_path


def _seed(root):
    ideas.add("Idea one", "does one", root=root, now=NOW)
    inbox.add("Publish v1", "release is green", "fieldkit ship gh-release v1", kind="hardline")
    inbox.add("Which pack?", "pick one", kind="hardline")


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
              if_approve="Zero builds the core pack.", if_leave="The build waits.", kind="hardline")
    inbox.add("Fan tray gone", "text mentions P9 only", what="w", if_approve="a", if_leave="l", kind="hardline")
    es = review.entries(env)
    assert [e["ref"] for e in es] == ["#3", "#1", "#2", "#4", "I-01"]       # main goal, everyday, ideas
    text = "\n".join(review.render(es, env))
    heads = [text.index(h) for _, h in review.GROUPS]
    assert heads == sorted(heads)
    assert "THE MAIN GOAL" in text and "Portage" in text and "W10" in text
    assert "P5 - Gorilla OpenCode can do everyday jobs on Windows and Linux" in text
    assert text.count("If you approve:") == len(es) and text.count("If you leave it:") == len(es)
    assert "1. Toolbox size?  [#3, plan row P5]" in text
    assert "(no plain explanation was written for this item)" not in text        # 2026-11-10: always plain words
    assert "It is closed as approved" in text
    assert text.index("fieldkit ship gh-release v1") > text.index("2. Publish v1")
    assert "Plan row: P5 - Gorilla" in text


def test_plan_is_inferred_from_text_when_no_field(env):
    (env / "docs").mkdir()
    (env / "docs" / "HARNESS-PLAN.md").write_text(PLAN, encoding="utf-8")
    inbox.add("P5b toolbox", "grows it", kind="hardline")
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
    # 2026-11-02: agents can no longer file "choice" items (only kind hardline reaches the owner), so the rule
    # "a choice needs words" has no item left to apply to; the owner's words are still recorded as given
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
    inbox.add("Need a yes", "because", "do it", kind="hardline")
    page = review.page_path(env)
    assert "Need a yes" in page.read_text(encoding="utf-8")
    inbox.done(1, "ok")
    assert "Nothing waits" in page.read_text(encoding="utf-8")


def test_open_writes_the_page_and_hands_it_to_the_viewer(env):
    _seed(env)
    got = []
    p = review.open_page(env, run=got.append)
    assert got == [str(p)] and "I-01" in p.read_text(encoding="utf-8")


def test_inbox_page_shows_owner_items_and_the_agent_ledger(env):
    inbox.add("Owner must sign", "hard line", "do-it --now", kind="hardline")
    inbox.add("Worker note", "agents handle it", "fieldkit x", kind="approval")
    text = review.inbox_page_path(env).read_text(encoding="utf-8")
    owner, _, agents = text.partition("## Handled by the agents")
    assert "Owner must sign" in owner and "hard line" in owner and "do-it --now" in owner
    assert "Worker note" in agents and "Worker note" not in owner
    got = []
    assert review.open_page(env, run=got.append, inbox=True) == review.inbox_page_path(env) and got


def test_a_row_the_owner_holds_is_never_shown_as_work_to_do(env):
    # 2026-11-10 (owner, angry): W10 (the final goal) was listed under "Rows not finished yet" and pushed as work
    (env / "docs").mkdir()
    (env / "docs" / "HARNESS-PLAN.md").write_text(PLAN.replace("| FINAL GOAL |", "| FINAL GOAL, not now |"),
                                                     encoding="utf-8")
    lines = review.where_we_are(env)
    todo = lines[:lines.index(next(x for x in lines if x.startswith("Held by the owner")))]
    assert any(x.startswith("  - P5 -") for x in todo) and not any("W10" in x for x in todo), lines
    assert any(x.startswith("  - W10 - ") and x.endswith("[held]") for x in lines), lines
    (env / "docs" / "HARNESS-PLAN.md").write_text(
        PLAN.replace("| FINAL GOAL |", "| FINAL GOAL, not now |").replace("| PLANNED |", "| DONE |"), encoding="utf-8")
    lines = review.where_we_are(env)
    assert lines[0] == "Where we are: no job is running now." and "W10" in lines[-1] and "[held]" in lines[-1]


def test_items_of_a_kind_that_no_longer_reaches_the_owner_move_to_the_ledger(env):
    # 2026-11-10: 13 items from 10-31/11-01 (build, paused, report, approval) still sat in the owner's review
    import json as _json
    inbox.INBOX.mkdir(parents=True, exist_ok=True)
    for n, kind in ((1, "build"), (2, "paused"), (3, "approval")):
        (inbox.INBOX / f"{n:04d}-{kind}.json").write_text(_json.dumps(
            {"n": n, "kind": kind, "title": f"old {kind}", "why": "w", "state": "open"}), encoding="utf-8")
    inbox.add("Publish v1", "release is green", "fieldkit ship gh-release v1", kind="hardline")
    es = review.entries(env)
    assert [e["title"] for e in es if e["type"] == "inbox"] == ["Publish v1"]
    assert {r["title"] for r in map(_json.loads, inbox.ledger_file().read_text().splitlines())} >= {"old build", "old paused", "old approval"}
    assert review.entries(env) == es                                          # idempotent


def test_an_item_without_words_is_explained_from_its_own_fields(env):
    import json as _json
    inbox.INBOX.mkdir(parents=True, exist_ok=True)
    (inbox.INBOX / "0007-hardline.json").write_text(_json.dumps(
        {"n": 7, "kind": "hardline", "title": "Tag v2", "why": "CI green on abc", "command": "fieldkit ship push --tag v2",
         "state": "open"}), encoding="utf-8")
    e = [x for x in review.entries(env) if x["ref"] == "#7"][0]
    assert e["what"] == "Tag v2: CI green on abc"
    assert "runs at once" in e["if_approve"] and "fieldkit ship push --tag v2" in e["if_approve"]
    assert e["if_leave"].startswith("It stays open")


def test_an_idea_already_built_as_a_plan_row_says_so_and_accepting_makes_no_new_row(env):
    # 2026-11-10: I-01/I-02 were built as P23/P24 and review still said "It becomes a row in the plan"
    import time as _t
    day = _t.strftime("%Y-%m-%d", _t.localtime(NOW))
    (env / "docs").mkdir(exist_ok=True)
    (env / "docs" / "HARNESS-PLAN.md").write_text(          # ids were reused: the old I-01 is P2, never this one
        PLAN + "| P2 | Old metrics (idea I-01, owner 2026-10-28) | x | y | DONE (PR #300) |\n"
        f"| P23 | One loop (idea I-01, owner {day}) | x | y | DONE (PR #412) |\n", encoding="utf-8")
    ideas.add("One loop", "does one", root=env, now=NOW)
    e = [x for x in review.entries(env) if x["ref"] == "I-01"][0]
    assert "ALREADY BUILT as plan row P23 (#412)" in e["what"] and "no new plan row" in e["if_approve"]
    r = ideas.set_status(["I-01"], "accepted", root=env, now=NOW)[0]
    assert r["plan_id"] == "P23" and r["brief"] is None


def test_where_we_are_names_what_is_running_not_the_order_table(env):
    # 2026-11-12: the line said "phase 7d ... P5" while P27/P9 were running
    (env / "docs").mkdir()
    (env / "docs" / "HARNESS-PLAN.md").write_text(PLAN, encoding="utf-8")
    lines = review.where_we_are(env, running=[("W10", "the one-command job")])
    assert lines[0] == "Where we are: 1 job(s) are running right now:" and "plan row W10" in lines[1]
    assert not any("P5" in x for x in lines), lines
    assert any("P5 -" in x for x in review.where_we_are(env, running=[]))


def test_inner_names_are_spelled_out_in_plain_words():
    t = review.plain("blocked by HARD LINE 3: P9 needs owner_steps", {"P9": "Counsel wiring"})
    assert "HARD LINE" not in t and "owner_steps" not in t and "plan row P9 (Counsel wiring)" in t


def test_a_defect_never_reaches_the_review(env):
    ideas.add("Real suggestion", "owner wants it", root=env, now=NOW)
    ideas.add("Stop kills the worker", "found broken", source="agent:zero", root=env, now=NOW, defect=True)
    ideas.mark_defect("I-01", root=env)
    assert [e["raw"]["id"] for e in review.entries(env) if e["type"] == "idea"] == []
    ideas.add("Another suggestion", "owner wants it too", root=env, now=NOW)
    assert [e["raw"]["id"] for e in review.entries(env) if e["type"] == "idea"] == ["I-03"]
    assert "Stop kills" not in "\n".join(review.render(review.entries(env), env))
    assert [e["id"] for e in ideas.defects(env)] == ["I-01", "I-02"]
def test_an_approval_at_the_owners_terminal_runs_its_command_right_there(env):
    """Owner 2026-11-12: the review recorded 'a' and ran nothing; the owner then had to run the script by hand."""
    inbox.add("Allow local CI", "ruleset", "bash state/owner/x.sh", kind="hardline")
    out = []
    res = review.review(env, ask=_ask("a"), say=out.append, now=NOW)
    assert RAN.calls == ["bash state/owner/x.sh"]
    assert any(r.get("ran") == "ran" and r["rc"] == 0 for r in res)
    assert any("running now: bash state/owner/x.sh" in x for x in out) and any("result: exit 0" in x for x in out)
    row = (env / "state" / "review-ran.jsonl").read_text(encoding="utf-8")
    assert "bash state/owner/x.sh" in row and '"rc": 0' in row


def test_a_dismissal_and_a_kept_item_run_nothing(env):
    inbox.add("Allow local CI", "ruleset", "bash state/owner/x.sh", kind="hardline")
    review.review(env, ask=_ask("f"), say=lambda _x: None, now=NOW)
    inbox.add("Another", "ruleset", "bash state/owner/y.sh", kind="hardline")
    review.review(env, ask=_ask("p"), say=lambda _x: None, now=NOW)
    assert RAN.calls == []


def test_prose_in_the_command_field_is_not_run(env):
    inbox.add("Think", "why", "the owner decides which pack", kind="hardline")
    out = []
    review.review(env, ask=_ask("a"), say=out.append, now=NOW)
    assert RAN.calls == [] and any("not run" in x for x in out)
