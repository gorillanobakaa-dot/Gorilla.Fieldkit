"""P18 stages 2-4: failure -> fix signatures, refusal table, intent groups. Each check can fail (mutations.yaml)."""
import re

import pytest

from fieldkit.capture import distill as D, episodes as ep, store as st


@pytest.fixture
def s(tmp_path):
    return st.Store(tmp_path / "cap")


def rec(cmd, code=0, errors=None, session="s1", problem="fix the build", n=[0], **kw):
    n[0] += 1
    return dict({"kind": "command", "source": cmd, "command": cmd, "cwd": ".", "exit_code": code, "agent": "t",
                 "session": session, "path": None, "problem": problem, "description": kw.pop("desc", ""), "errors": errors,
                 "timestamp": f"2026-11-03T00:00:{n[0] % 60:02d}+00:00" if n[0] < 60 else f"2026-11-03T00:{n[0] // 60:02d}:{n[0] % 60:02d}+00:00"}, **kw)


def episodes_of(s):
    ep.store_episodes(s)


def test_norm_hides_paths_numbers_and_hashes():
    assert D.norm(r"Error in D:" + chr(92) + "work" + chr(92) + "a.py line 42 at deadbeef12") == "error in <P> line <N> at <H>"
    assert "secretname" not in D.norm("open /srv/secretname/x/y.txt failed")


@pytest.mark.parametrize("cmd,fam", [("cd /x && git push origin main", "git push"), ("python -m pytest -q", "python -m pytest"),
                                     (r"C:\a\python.exe run.py", "python script"), ("ls -la", "ls")])
def test_family(cmd, fam):
    assert D.family(D.command_of({"command": cmd})) == fam


def test_key_regex_matches_its_own_line():
    key = D.error_key("noise\nError: cannot open 'foo.txt' in C:/w/x line 12")
    assert re.search(D.key_regex(key), D.norm("Error: cannot open 'bar.txt' in C:/q line 99"), re.I)


def test_two_alike_pairs_make_one_signature_and_a_single_is_a_procedure(s):
    for i in range(2):
        s.record(rec(f"tool run {i}", code=1, errors=f"fatal: cannot open file number {i} here", problem="req a"))
        s.record(rec(f"tool fixed {i}", problem="req a"))
    s.record(rec("other thing", code=1, errors="weird unique failure text x", problem="req a"))
    s.record(rec("other fixed", problem="req a"))
    episodes_of(s)
    sm, sigs, marks = D.stage2(s)
    assert sm["pairs"] == 3 and sm["signatures"] == 1 and sm["pairs_accounted"] == 3
    assert sm["pairs_in_signatures"] == 2 and sm["pairs_as_procedures"] == 1
    assert all(re.search(sigs[0]["pattern"], "fatal: cannot open file number 7 here", re.I) for _ in [0])
    assert D.apply_marks(s, marks) == 3
    assert D.apply_marks(s, marks) == 0                       # idempotent
    assert s.ledger()["unaccounted"] == []                    # every state has evidence


def test_a_refusal_is_a_rule_not_a_failure_pair(s):
    msg = "PreToolUse:Bash hook error: TOOL ROUTER: this does by hand what a harness tool does (sleep-to-wait). Use x"
    for n in (60, 90):
        s.record(rec(f"sleep {n}", code=1, errors=msg))
        s.record(rec(f"python -m fieldkit wait --until x{n}"))
    episodes_of(s)
    sm, sigs, _ = D.stage2(s)
    assert sm["pairs_that_are_refusals"] == 2 and sm["signatures"] == 0       # two alike would make a signature
    s3, marks = D.stage3(s)
    assert s3["table"][0]["rule"] == "router:sleep-to-wait" and s3["table"][0]["count"] == 2
    assert "python -m fieldkit wait" in s3["table"][0]["correction"]


def test_prefixed_correction_is_named(s):
    msg = "hook error: TOOL ROUTER: x (heavy-work-outside-the-guard). Use y"
    s.record(rec("pytest -q tests", code=1, errors=msg))
    s.record(rec("python -m fieldkit guard -- pytest -q tests"))
    episodes_of(s)
    row = D.stage3(s)[0]["table"][0]
    assert row["correction"].startswith("same command, prefixed:") and "guard" in row["correction"]


@pytest.mark.parametrize("text,kind", [("build the firefox installer", "build"), ("why does it fail", "debug"),
                                       ("run pytest", "test"), ("tag and publish v1", "release"), ("hello there", None)])
def test_intent_of(text, kind):
    assert D.intent_of(text) == kind


def test_every_episode_gets_one_intent_from_request_or_purposes(s):
    s.record(rec("a", problem="build the kernel", session="a"))
    s.record(rec("b", problem="hello there", session="b", desc="run the tests"))
    s.record(rec("c", problem="hello there", session="c"))
    episodes_of(s)
    sm, assigned = D.stage4(s)
    kinds = sorted(assigned.values())
    assert sm["episodes"] == sm["assigned"] == 3 and kinds == ["build", "other", "test"]
    assert D.apply_intents(s, assigned) == 3 and D.apply_intents(s, assigned) == 0
