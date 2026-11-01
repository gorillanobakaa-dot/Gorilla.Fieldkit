"""fieldkit capture: the journal, the hook, redaction, links, recovery. Each check can fail (mutations.yaml)."""
import os
import subprocess
import sys

import pytest

from fieldkit.capture import hook, store as st


@pytest.fixture
def s(tmp_path):
    return st.Store(tmp_path / "cap")


def rec(cmd="echo hi", code=0, session="s1", **kw):
    return dict({"kind": "command", "source": cmd, "command": cmd, "cwd": ".", "exit_code": code, "agent": "t",
                 "session": session, "path": None}, **kw)


def fk(*args, **kw):
    return subprocess.run([sys.executable, "-m", "fieldkit", "capture", *args], capture_output=True, text=True, **kw)


def test_same_content_is_counted_not_stored_twice(s):
    a, new_a = s.record(rec())
    b, new_b = s.record(rec(session="s2"))
    assert a == b and new_a and not new_b
    assert s.state()["counts"][a] == 2 and s.ledger()["deduplicated"] == 1


def test_a_failed_and_a_passing_run_are_two_items(s):
    a, _ = s.record(rec(code=1))
    b, _ = s.record(rec(code=0))
    assert a != b and not s.verify()[0]


def test_every_version_of_a_file_is_kept(s):
    a, _ = s.record(rec(kind="script", source="v1", command="write x.py", path="x.py"))
    b, _ = s.record(rec(kind="script", source="v2", command="write x.py", path="x.py"))
    v = s.find(["v2"])[0]
    assert v["version"] == 2 and v["prev_version"] == a and b != a


def test_chain_detects_an_altered_entry(s):
    s.record(rec("one"))
    s.record(rec("two"))
    s.journal.write_text(s.journal.read_text(encoding="utf-8").replace("echo one", "echo ONE", 1), encoding="utf-8")
    s.journal.write_text(s.journal.read_text(encoding="utf-8").replace('"one"', '"ONE"', 1), encoding="utf-8")
    assert any("altered" in x or "chain" in x for x in s.verify()[0])


def test_an_untouched_journal_verifies(s):
    s.record(rec("one"))
    assert s.verify()[0] == []


def test_torn_tail_is_kept_and_healed(s):
    s.record(rec("one"))
    with open(s.journal, "ab") as f:
        f.write(b'{"seq":9,"half')
    s.record(rec("two"))
    assert b'"half' in s.torn.read_bytes() and s.verify()[0] == [] and len(s.state()["items"]) == 2


def test_orphan_event_becomes_its_record(s):
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "q",
               "tool_input": {"command": "echo orphan"}, "tool_response": {"exit_code": 0}}
    s.begin_event(payload, "claude")
    assert s.ledger()["unaccounted"] and s.verify()[0]
    assert s.recover()["events_recovered"] == 1
    assert not s.ledger()["unaccounted"] and s.find(["orphan"])


def test_secrets_never_reach_the_journal(s):
    s.record(rec("curl -H 'Authorization: Bearer abcdef123456' --token tok123456 x", env={"MY_API_KEY": "v1", "HOME": "h"}))
    raw = s.journal.read_text(encoding="utf-8")
    assert "abcdef123456" not in raw and "tok123456" not in raw and '"v1"' not in raw and '"h"' in raw


def test_failure_links_to_the_related_success_in_its_session_only(s):
    f, _ = s.record(rec("pytest tests/a.py", code=2))
    s.record(rec("ls", code=0))                                    # unrelated success: no link
    s.record(rec("pytest tests/a.py", code=0, session="other"))    # other session: no link
    assert s.state()["links"] == {}
    ok, _ = s.record(rec("pytest -x tests/a.py", code=0))
    assert s.state()["links"] == {f: ok}


def test_processing_failure_keeps_the_item_in_the_queue(s):
    i, _ = s.record(rec())
    s.run_processing(i, lambda x: (_ for _ in ()).throw(RuntimeError("no")))
    assert s.state()["state"][i]["state"] == "raw" and i in s.ledger()["outstanding"]


def test_done_state_needs_evidence_for_the_strict_ledger(s):
    i, _ = s.record(rec())
    s.set_state(i, "tool")
    assert s.ledger()["unaccounted"] == [i]
    s.set_state(i, "tool", evidence="card x")
    assert s.ledger()["unaccounted"] == []


def test_bad_state_is_refused(s):
    i, _ = s.record(rec())
    with pytest.raises(ValueError):
        s.set_state(i, "done")


def test_large_field_goes_to_a_blob_and_a_missing_blob_is_lost(s):
    i, _ = s.record(rec(source="Y" * 40000))
    ref = s.state()["items"][i]["source"]
    assert s.blob(ref) == "Y" * 40000
    (s.blobs / ref["blob"]).unlink()
    assert s.ledger()["lost"] == [i] and s.verify()[0]


@pytest.mark.parametrize("agent,payload,kind,code", [
    ("claude", {"tool_name": "Bash", "tool_input": {"command": "x"}, "tool_response": {"stdout": "", "stderr": ""}}, "command", 0),
    ("claude", {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_input": {"command": "x"},
                "error": "Exit code 7\nboom"}, "command", 7),
    ("gemini", {"hook_event_name": "AfterTool", "tool_name": "run_shell_command", "tool_input": {"command": "x"},
                "tool_response": {"llmContent": "ok"}}, "command", 0),
    ("claude", {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "import os\n"}}, "script", 0),
    ("gemini", {"tool_name": "replace", "tool_input": {"file_path": "a.txt", "old_string": "a", "new_string": "b"}}, "edit", 0),
    ("opencode", {"tool": "bash", "args": {"command": "x"}, "output": "o", "sessionID": "z"}, "command", 0),
    ("claude", {"hook_event_name": "Stop", "session_id": "q"}, "session", None),
])
def test_agent_payloads_normalise(agent, payload, kind, code):
    r = hook.parse(agent, payload)
    assert r["kind"] == kind and r["exit_code"] == code


def test_work_tools_only():
    assert hook.parse("claude", {"tool_name": "Read", "tool_input": {}}) is None


def test_python_script_deps_are_read():
    r = hook.parse("claude", {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "import os, json\nfrom yaml import x\n"}})
    assert r["deps"] == ["os", "json", "yaml"]


def test_hook_never_fails_the_agent(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the store directory should be")
    r = subprocess.run([sys.executable, "-m", "fieldkit", "capture", "hook", "--agent", "claude"], input="{}",
                       capture_output=True, text=True, env={**os.environ, "FIELDKIT_CAPTURE_DIR": str(blocked)})
    assert r.returncode == 0


@pytest.mark.parametrize("flag", ["first-use", "context", "failed", "before-processing", "crash"])
def test_self_tests_pass(flag):
    r = fk("self-test", "--" + flag)
    assert r.returncode == 0 and "PASS" in r.stdout and "FAIL" not in r.stdout, r.stdout


@pytest.mark.parametrize("flag", ["kinds", "roots", "transform", "fresh-session"])
def test_later_jobs_are_stubs_that_exit_2(flag):
    r = fk("self-test", "--" + flag)
    assert r.returncode == 2 and "NOT BUILT YET" in r.stderr
    assert fk("scan").returncode == 2


def test_a_broken_store_makes_strict_and_verify_exit_3(tmp_path):
    d = tmp_path / "c"
    s = st.Store(d)
    i, _ = s.record(rec(source="Z" * 40000))
    (s.blobs / s.state()["items"][i]["source"]["blob"]).unlink()
    env = {**os.environ, "FIELDKIT_CAPTURE_DIR": str(d)}
    for args in (["ledger", "--strict"], ["verify"]):
        assert fk(*args, env=env).returncode == 3


def test_same_program_links_even_with_different_arguments(s):
    f, _ = s.record(rec("make build", code=2))
    ok, _ = s.record(rec("make test", code=0))
    assert s.state()["links"] == {f: ok}
