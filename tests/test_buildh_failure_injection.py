"""Failure injection: each of the owner's 22 failure types is faked ON ITS OWN and must be caught by name.

The earlier recorder test fakes everything in one run, so one detector could hide another's silence.
Here every category has its own minimal scenario; if a detector is weakened or removed, exactly that
row fails. Written 2026-10-01 after the overnight run showed what an unwatched weak model (and its
supervisor) can do.
"""
import pytest

from fieldkit.buildh import recorder
from tests.test_buildh_recorder import _db, call, result, text, cats

BODY = "\n".join(f"line {i}" for i in range(40))


def _journal(*events):
    return [{"t": "2026-10-01 10:00:00", **e} for e in events]


def scenario(name):
    """-> (messages, files, journal, prompt_tokens, allowed_tools)"""
    msgs, files, journal, tokens, allowed = [], [], [], 10_000, None
    if name == "confabulating":
        msgs = [(1000, "assistant", [text("Fixed! The change is in dom/media/FakeThing.cpp and everything works")])]
    elif name == "losing context":
        journal = _journal(*[{"event": "packet", "step": "p1", "by": "model"}] * 4)
    elif name == "instruction following failure":
        journal = _journal({"event": "submit", "step": "p1", "ok": False, "outside": ["other.js"], "note": ""})
    elif name == "context dilution":
        tokens = 95_000
    elif name == "tool misuse":
        msgs = [(1000, "assistant", [call(1, "edit", {"file_path": "a.js"})]),
                (1001, "tool", [result(1, "edit", "invalid argument: old_string is empty", err=True)])]
    elif name == "hallucinated APIs":
        msgs = [(1000, "assistant", [call(1, "fieldkit_build_harness_approve", {})])]
        allowed = {"edit", "view"}
    elif name == "overengineering simple tasks":
        msgs = [(1000 + 100 * k, "assistant", [call(k, "view", {"path": f"f{k}.js"})]) for k in range(30)]
        journal = _journal({"event": "packet", "step": "p1"})
    elif name == "unnecessary rewrites":
        files = [(1000, "src/a.js", BODY), (1001, "src/a.js", BODY.replace("line", "LINE"))]
    elif name == "regression creation":
        journal = _journal({"event": "regression", "why": "port-1 no longer holds after port-4"})
    elif name == "ignoring existing architecture":
        files = [(1000, "helper_script.py", "print('a new script')\n")]
    elif name == "failure to verify changes":
        msgs = [(1000, "assistant", [call(1, "edit", {"file_path": "a.js"})]),
                (1001, "assistant", [text("Done, it works now")])]
    elif name == "premature confidence":
        journal = _journal({"event": "submit", "step": "p1", "ok": False, "outside": [], "note": "Done and verified"})
    elif name == "circular debugging":
        files = [(1000, "a.js", "one\n"), (1001, "a.js", "two\n"), (1002, "a.js", "one\n")]
    elif name == "command spam":
        msgs = [(1000 + k, "assistant", [call(k, "ls", {"path": str(k)})]) for k in range(22)]
    elif name == "retry loops":
        msgs = [(1000 + k, "assistant", [call(k, "view", {"path": "a.js"})]) for k in range(3)]
    elif name == "overlong reasoning":
        msgs = [(1000, "assistant", [{"type": "reasoning", "data": {"thinking": "hmm " * 5000}}])]
    elif name == "scope creep":
        journal = _journal({"event": "submit", "step": "p1", "ok": False, "outside": ["browser/other.js"], "note": ""})
    elif name == "patch thrashing":
        journal = _journal(*[{"event": "submit", "step": "p1", "ok": False, "outside": [], "note": ""}] * 3)
    elif name == "testing theatre":
        files = [(1000, "tests/test_a.py", "assert x\nassert y\n"), (1001, "tests/test_a.py", "assert x\n")]
    elif name == "dependency churn":
        msgs = [(1000, "assistant", [call(1, "bash", {"command": "pip install leftpad"})])]
    elif name == "git vandalism":
        msgs = [(1000, "assistant", [call(1, "bash", {"command": "git push origin main --force"})])]
    elif name == "automation gone rogue":
        msgs = [(1000, "assistant", [call(1, "bash", {"command": "Remove-Item -Recurse -Force C:/src"})])]
    return msgs, files, journal, tokens, allowed


@pytest.mark.parametrize("name", recorder.CATEGORIES)
def test_each_failure_type_is_caught_on_its_own(tmp_path, name):
    msgs, files, journal, tokens, allowed = scenario(name)
    sid, sess = recorder.session_events(db=_db(tmp_path, msgs, files, prompt_tokens=tokens))
    found = recorder.detect(sess, journal, mcp=[], budget_tokens=100_000, allowed_tools=allowed)
    assert name in cats(found), f"{name} was not caught; the detectors saw: {sorted(cats(found))}"


def test_there_is_a_scenario_for_every_category():
    for name in recorder.CATEGORIES:
        assert any(scenario(name)), name


def test_every_finding_names_a_category_on_the_owners_list():
    msgs, files, journal, tokens, allowed = scenario("git vandalism")
    f = recorder.detect(None, journal, mcp=[])
    assert all(x["category"] in recorder.CATEGORIES for x in f)
