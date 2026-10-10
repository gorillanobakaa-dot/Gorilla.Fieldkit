"""The labels on every MCP answer (_meta.untrusted / _meta.egress), the network rule, exit codes that are answers,
and one real session spoken the way Gorilla OpenCode's MCP client (mcp-go) speaks it.

A client that trusts Fieldkit as a local server skips its "this leaves the machine" question and its taint only
on what these labels say, so each label is shown to be set where it must be, and to fail closed when unsure.
"""
import json
import os
import subprocess
import sys

import pytest

from fieldkit import agent, mcp
from fieldkit.desk import cards as cardmod

PY = sys.executable


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(agent, "BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(cardmod, "test_results", lambda: {})
    monkeypatch.setattr(mcp, "RECORDER", None)


def _card(id="t", **kw):
    c = {"id": id, "title": "A tool", "path": None, "entry": [PY, "-c", "print('hi')"], "inputs": [],
         "effects": [], "safety": "read-only", "modes": {}, "tests": ["x"], "platforms": [], "draft": False,
         "reviewed": True, "portable": True, "probe": "safe", "scope": [], "output": "third-party", "exits": {},
         "answer_lines": 15}
    c.update(kw)
    return c


def _call(name, args):
    return mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": name, "arguments": args}})["result"]


# -- labels ---------------------------------------------------------------------------------------------
def test_own_answers_are_trusted_and_third_party_ones_are_not(monkeypatch):
    own, other = _card("own", output="own"), _card("other")
    monkeypatch.setattr(agent, "_cards", lambda: [own, other])
    assert _call("run", {"tool": "own"})["_meta"] == {"untrusted": False, "egress": False}
    assert _call("run", {"tool": "other"})["_meta"] == {"untrusted": True, "egress": False}
    assert _call("discover", {"goal": "anything"})["_meta"]["untrusted"] is False


def test_a_card_that_does_not_say_own_is_untrusted():
    assert cardmod.curated_card({"id": "x", "entry": ["true"], "inputs": [], "safety": "read-only"})["output"] \
        == "third-party"


def test_build_harness_answers_quoting_source_are_untrusted():
    assert mcp.labels("build_harness_next", {})["untrusted"] is True
    assert mcp.labels("build_harness_briefs", {})["untrusted"] is True
    assert mcp.labels("build_harness_status", {})["untrusted"] is False


# -- network ----------------------------------------------------------------------------------------------
def test_a_network_tool_runs_only_when_the_call_says_so(monkeypatch, tmp_path):
    marker = tmp_path / "ran"
    online = _card("online", effects=["network"], output="own",
                   entry=[PY, "-c", f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x'); print('hi')"])
    monkeypatch.setattr(agent, "_cards", lambda: [online])
    r = _call("run", {"tool": "online"})
    assert "network=true" in r["content"][0]["text"] and not marker.exists()
    assert r["_meta"] == {"untrusted": False, "egress": False}            # refused before anything ran
    r = _call("run", {"tool": "online", "network": True})
    assert marker.exists() and r["_meta"]["egress"] is True
    assert "network" in json.dumps([t for t in mcp.TOOLS if t["name"] == "run"][0]["inputSchema"])


def test_an_unknown_tool_is_refused_in_fieldkits_own_words(monkeypatch):
    monkeypatch.setattr(agent, "_cards", lambda: [])
    r = _call("run", {"tool": "nope"})
    assert r["content"][0]["text"].startswith("REFUSED:") and r["_meta"] == {"untrusted": False, "egress": False}


# -- exit codes that are answers ------------------------------------------------------------------------------
def test_findings_and_questions_are_answers_not_failures(monkeypatch):
    finds = _card("finds", exits={3: "findings"}, entry=[PY, "-c", "print('  FIX   x'); raise SystemExit(3)"])
    asks = _card("asks", exits={2: "question"}, entry=[PY, "-c", "print('QUESTION: when?'); raise SystemExit(2)"])
    broken = _card("broken", entry=[PY, "-c", "raise SystemExit(3)"])
    monkeypatch.setattr(agent, "_cards", lambda: [finds, asks, broken])
    r = _call("run", {"tool": "finds"})
    assert r["isError"] is False and "found things to fix" in r["content"][0]["text"]
    assert "FIX lines" in r["content"][0]["text"].splitlines()[-1]
    r = _call("run", {"tool": "asks"})
    assert r["isError"] is False and "QUESTION lines" in r["content"][0]["text"]
    assert _call("run", {"tool": "broken"})["isError"] is True                # an exit the card does not name


def test_a_changing_tool_that_only_asks_changes_nothing_and_restores_nothing(monkeypatch, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("mine")
    asks = _card("asks", safety="reversible", scope=["file"], exits={2: "question"},
                 inputs=[{"name": "file", "flag": None, "type": "str", "required": True}],
                 entry=[PY, "-c", "print('QUESTION: name?'); raise SystemExit(2)"])
    rec = agent.run("asks", {"file": str(f)}, cards=[asks])
    assert rec["ok"] and rec["changed"] == [] and f.read_text() == "mine"


def test_answer_lines_keeps_a_long_report_whole(monkeypatch):
    long = _card("long", answer_lines=40, entry=[PY, "-c", "[print(i) for i in range(30)]"])
    rec = agent.run("long", {}, cards=[long])
    assert "  0" in rec["answer"] and "  29" in rec["answer"]


# -- the real cards -------------------------------------------------------------------------------------------
def test_every_academic_command_has_a_reviewed_card():
    from fieldkit.academic.cli import COMMANDS
    ids = {c["id"]: c for c in cardmod.all_cards(use_cache=False)}
    for cmd in COMMANDS:
        c = ids.get(f"academic-{cmd}")
        assert c, cmd
        level, why = cardmod.trust(c, results={})
        assert level == "carded", (cmd, why)
        assert c["entry"][-1] == cmd and c["safety"] in ("read-only", "reversible")
    # the reading tools carry other people's text; the checks of the student's own draft do not
    assert ids["academic-search"]["output"] == "third-party" and ids["academic-plagiarism"]["output"] == "third-party"
    assert ids["academic-refs"]["output"] == "own" and ids["academic-refs"]["exits"] == {3: "findings"}
    assert ids["office-read"]["output"] == "third-party"


# -- a real session, the way mcp-go v0.17 (Gorilla OpenCode) opens one -----------------------------------------
def test_a_real_stdio_session_as_mcp_go_speaks_it(tmp_path):
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "OpenCode", "version": "0.1.145"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "describe", "arguments": {"tool": "academic-refs"}}},
    ]
    env = dict(os.environ, FIELDKIT_RECORDER=str(tmp_path / "rec.jsonl"))
    out = subprocess.run([PY, "-m", "fieldkit", "mcp"], input="".join(json.dumps(m) + "\n" for m in msgs),
                         capture_output=True, text=True, timeout=60, env=env,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    replies = [json.loads(l) for l in out.stdout.splitlines() if l.strip()]
    assert [r["id"] for r in replies] == [1, 2, 3]                          # the notification gets no reply
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    assert "run" in names and "discover" in names
    text = replies[2]["result"]["content"][0]["text"]
    assert text.startswith("academic-refs:") and replies[2]["result"]["_meta"]["untrusted"] is False
    # the whole tool list costs little: a client that defers schemas pays even less
    assert len(json.dumps(replies[1]["result"])) < 6000
