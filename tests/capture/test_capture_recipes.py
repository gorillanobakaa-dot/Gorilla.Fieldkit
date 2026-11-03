"""P18 stage 5: align an intent group into a recipe, name it, replay it. Each check can fail (mutations.yaml)."""
import json

import pytest

from fieldkit.capture import episodes as ep, recipes as R, store as st
from tests.capture.test_distill import rec


@pytest.fixture
def s(tmp_path):
    return st.Store(tmp_path / "cap")


def flow(s, session, cmds, request="release the build now"):
    for c in cmds:
        code, err = (1, "Error: boom happened") if c.startswith("!") else (0, None)
        s.record(rec(c.lstrip("!") + " " + session, code=code, errors=err, session=session, problem=request))


def episodes(s, intent="release"):
    ep.store_episodes(s)
    st_ = s.state()
    s.append_many("episode", [dict(e, intent=intent) for e in st_["episodes"].values()])
    return s.state()


def test_action_filter():
    assert R.is_action("git push") and R.is_action("python -m pytest") and R.is_action("gh release")
    assert not R.is_action("git status") and not R.is_action("cat") and not R.is_action("python script")


def test_template_keeps_the_start_only():
    assert R.template("cd /x && git push origin main --force | tail -3") == "git push origin main"
    assert "secretname" not in R.template("gh release upload v1 " + chr(47) + "srv" + chr(47) + "secretname" + chr(47) + "a.zip")


def test_align_keeps_supported_families_in_order(s):
    for i in range(4):
        flow(s, f"s{i}", ["git add .", "git commit -m x", "gh release create v1"])
    flow(s, "odd", ["git add .", "docker ps", "git commit -m x", "gh release create v1"])
    state = episodes(s)
    steps = R.align(R.group_episodes(s, state, "release"))
    assert [x["family"] for x in steps] == ["git add", "git commit", "gh release"]


def test_unsupported_family_is_not_a_step(s):
    for i in range(6):
        flow(s, f"s{i}", ["git add .", "git commit -m x", "gh release create v1"] + (["docker ps"] if i == 0 else []))
    steps = R.align(R.group_episodes(s, episodes(s), "release"))
    assert "docker" not in [x["family"] for x in steps]


def test_replay_needs_enough_steps_order_and_a_finished_run():
    steps = [{"family": f} for f in ["git add", "git commit", "gh release", "git fetch"]]
    a = lambda fam, failed=False: (fam, "", failed, "", "")
    assert R.replay(steps, [a("git add"), a("git commit"), a("gh release")])["ok"]
    assert not R.replay(steps, [a("git add"), a("git commit")])["ok"]                               # too few steps
    assert not R.replay(steps, [a("gh release"), a("git commit"), a("git add")])["ok"]              # wrong order
    assert not R.replay(steps, [a("git add"), a("git commit"), a("gh release", True)])["ok"]        # never finished
    assert R.replay(steps, [a("git add"), a("gh release", True), a("gh release"), a("git commit")])["order"] < 1


def test_recipe_accepted_only_when_three_episodes_replay(s):
    for i in range(2):
        flow(s, f"s{i}", ["git add .", "git commit -m x", "gh release create v1"])
    for i in range(3):                                           # the release never finished: these do not replay
        flow(s, f"bad{i}", ["git add .", "git commit -m x", "!gh release create v1"])
    episodes(s)
    r = R.build(s, "release")
    assert r["steps"] and r["episodes"]["replayed_ok"] == 2 and r["accepted"] is False
    flow(s, "s9", ["git add .", "git commit -m x", "gh release create v1"])
    episodes(s)
    r = R.build(s, "release")
    assert r["accepted"] and r["episodes"]["replayed_ok"] == 3


def test_episode_without_anchor_command_is_left_out(s):
    for i in range(3):
        flow(s, f"s{i}", ["git add .", "git commit -m x", "gh release create v1"])
    flow(s, "q", ["git add .", "git commit -m x", "git fetch"])
    r = R.build(s_with(s), "release")
    assert r["episodes"]["in_group"] == 3 and r["episodes"]["without_anchor_command"] == 1


def s_with(s):
    episodes(s)
    return s


STEPS = [{"id": "a", "command": "x", "purposes": [], "failures": []}, {"id": "b", "command": "y", "purposes": [], "failures": []}]


def ans(**kw):
    d = {"title": "Ship it", "goal": "Get it out", "steps": {"a": {"sentence": "Do the first thing", "check": "It worked fine"},
                                                          "b": {"sentence": "Do the second thing", "check": "It also worked"}}}
    d.update(kw)
    return "Here:\n" + json.dumps(d)


def test_names_accepted_when_complete():
    names, why = R.parse_names(ans(), STEPS)
    assert names and not why


@pytest.mark.parametrize("text", ["no json", ans(steps={"a": {"sentence": "one", "check": "two ok"}}),
                                  ans(steps={"a": {"sentence": "Do the first thing", "check": ""},
                                             "b": {"sentence": "Do the second thing", "check": "ok fine"}}),
                                  ans(steps={"a": {"sentence": "Do the first thing", "check": "It worked fine"},
                                             "zzz": {"sentence": "Do the second thing", "check": "It also worked"}}),
                                  ans(goal="Mail me at someone@example.com please")])
def test_names_refused_when_incomplete_or_personal(text):
    names, why = R.parse_names(text, STEPS)
    assert names is None and why


def test_model_answer_names_the_recipe_else_default(s):
    for i in range(3):
        flow(s, f"s{i}", ["git add .", "git commit -m x", "gh release create v1"])
    episodes(s)
    good = lambda p: ans_for(p)
    assert R.build(s, "release", runner=good)["named_by"] == "sonnet"
    r = R.build(s, "release", runner=lambda p: "nonsense")
    assert r["named_by"] == "deterministic" and r["naming_note"] and r["accepted"]


def ans_for(prompt):
    ids = [ln.split('"')[3] for ln in prompt.splitlines() if ln.strip().startswith('"id"')]
    return json.dumps({"title": "Release it", "goal": "Publish a release", "steps": {i: {"sentence": "Do " + i + " now", "check": "It passes ok"} for i in ids}})


def test_the_model_call_is_capped_and_goes_through_the_spend_guard(monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen["argv"], seen["env"] = argv, kw["env"]

        class P:
            stdout = "{}"
        return P()
    import subprocess
    monkeypatch.setattr(subprocess, "run", fake)
    R.run_sonnet("hi")
    a = seen["argv"]
    assert a[a.index("--model") + 1] == "sonnet" and a[a.index("--max-budget-usd") + 1] == R.BUDGET_USD
    assert seen["env"]["ANTHROPIC_MODEL"] == "sonnet"


def test_order_follows_what_most_episodes_do_not_the_average_position():
    # b is early on average (position of a long episode) but most episodes run a before b
    firsts = [{"a": 0, "b": 1}, {"a": 0, "b": 1}, {"a": 0, "b": 1}, {"b": 0, "a": 1}]
    assert R.kemeny(["b", "a"], firsts) == ["a", "b"]
    assert R.kemeny(["a", "b"], firsts) == ["a", "b"]


def test_build_group_needs_a_build_command(s):
    for i in range(3):
        flow(s, f"s{i}", ["git add .", "git commit -m x", "go build ./cmd"])
    flow(s, "q", ["git add .", "git commit -m x", "git fetch"])
    episodes(s, "build")
    r = R.build(s, "build")
    assert r["episodes"]["in_group"] == 3 and r["episodes"]["without_anchor_command"] == 1


def test_group_that_does_not_replay_becomes_a_procedure(s):
    for i in range(2):
        flow(s, f"s{i}", ["git add .", "git commit -m x", "go build ./cmd"])
    flow(s, "c", ["git add .", "git commit -m x", "cargo build"])
    state = episodes(s, "build")
    rec = R.build(s, "build")
    rec["accepted"] = False
    proc, marks = R.procedure(s, rec)
    assert proc["toolchain_split"] == {"go": 2, "cargo": 1} and proc["items_marked"] == len(marks) == 9
    assert all(v[0] == "procedure" and "not accepted" in v[1] for v in marks.values())
    tool_item = next(iter(marks))
    s.append_many("state", [{"item": tool_item, "state": "tool", "evidence": "x", "error": None}])
    assert tool_item not in R.procedure(s, rec)[1]                                      # an item with a state keeps it
def test_research_group_needs_a_search_command(s):
    for i in range(3):
        flow(s, f"s{i}", ["git add .", "git commit -m x", "gh api repos/x/y"])
    flow(s, "q", ["git add .", "git commit -m x", "git fetch"])
    episodes(s, "research")
    r = R.build(s, "research")
    assert r["episodes"]["in_group"] == 3 and r["episodes"]["without_anchor_command"] == 1


def test_documents_group_needs_a_document_or_privacy_command(s):
    for i in range(3):
        flow(s, f"s{i}", ["git add .", "git commit -m x", "fieldkit privacy scan docs"])
    flow(s, "q", ["git add .", "git commit -m x", "git fetch"])
    episodes(s, "documents")
    r = R.build(s, "documents")
    assert r["episodes"]["in_group"] == 3 and r["episodes"]["without_anchor_command"] == 1


def test_debug_group_needs_a_test_or_check_command(s):
    for i in range(3):
        flow(s, f"s{i}", ["git add .", "git commit -m x", "python -m pytest tests"])
    flow(s, "q", ["git add .", "git commit -m x", "git fetch"])
    episodes(s, "debug")
    r = R.build(s, "debug")
    assert r["episodes"]["in_group"] == 3 and r["episodes"]["without_anchor_command"] == 1
