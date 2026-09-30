"""MCP server: speaks the protocol over real stdio, and never lets a model approve anything."""
import json
import subprocess
import sys
from pathlib import Path

from fieldkit import mcp

ROOT = Path(__file__).resolve().parents[1]


def _session(*msgs):
    lines = "\n".join(json.dumps(m) for m in msgs) + "\n"
    r = subprocess.run([sys.executable, "-m", "fieldkit", "mcp"], input=lines, capture_output=True, text=True,
                       encoding="utf-8", cwd=ROOT, timeout=120)
    return [json.loads(l) for l in r.stdout.splitlines() if l.strip()]


def test_handshake_list_and_call_over_stdio():
    out = _session({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                   {"jsonrpc": "2.0", "method": "notifications/initialized"},
                   {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                   {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "discover", "arguments": {"goal": "remove names from a word document"}}},
                   {"jsonrpc": "2.0", "id": 4, "method": "no/such"})
    assert [m["id"] for m in out] == [1, 2, 3, 4]                      # the notification got no reply
    assert out[0]["result"]["serverInfo"]["name"] == "fieldkit"
    names = {t["name"] for t in out[1]["result"]["tools"]}
    assert {"discover", "describe", "run", "undo", "next", "readiness",
            "build_harness_status", "build_harness_next", "build_harness_submit"} == names
    text = out[2]["result"]["content"][0]["text"]
    assert "office-scrub" in text and text.rstrip().splitlines()[-1].startswith("NEXT:")
    assert out[3]["error"]["code"] == -32601


def test_run_has_no_approve_argument_and_refusals_are_answers():
    run_schema = next(t for t in mcp.TOOLS if t["name"] == "run")["inputSchema"]["properties"]
    assert "approve" not in run_schema
    text, err = mcp.call_tool("run", {"tool": "no-such-tool"})
    assert text.startswith("REFUSED:") and "NEXT:" in text and err is False


def test_describe_a_real_card():
    text, err = mcp.call_tool("describe", {"tool": "office-scrub"})
    assert not err and "safety: reversible" in text and "file (str, required)" in text
    assert text.splitlines()[-1] == "NEXT: run it with mode=preview first."


def test_build_harness_tools_offer_no_approval_and_every_call_is_recorded(tmp_path, monkeypatch):
    import io
    import json
    from fieldkit import mcp
    from fieldkit.buildh import task
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    names = [t["name"] for t in mcp.TOOLS]
    assert {"build_harness_status", "build_harness_next", "build_harness_submit"} <= set(names)
    assert not any("approve" in n or "unblock" in n for n in names)
    rec = tmp_path / "rec.jsonl"
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "build_harness_next", "arguments": {}}}]
    out = io.StringIO()
    mcp.serve(io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n"), out, recorder=rec)
    reply = json.loads(out.getvalue().splitlines()[0])
    assert "REFUSED: no build job" in reply["result"]["content"][0]["text"]
    line = json.loads(rec.read_text(encoding="utf-8").splitlines()[0])
    assert line["tool"] == "build_harness_next" and "REFUSED" in line["answer"]
