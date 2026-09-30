"""MCP server: the agent interface for any MCP client (LM Studio, Gorilla OpenCode, Claude).

    fieldkit mcp          # speaks MCP over stdio (JSON-RPC 2.0, one message per line)

Tools offered: discover, describe, run, undo, next, readiness.

Rule: a model can never approve an irreversible change through this server. `run`
has no approve argument here; irreversible and unknown-safety tools answer with
a refusal telling the model to show the owner a preview. The owner approves on
the command line: `fieldkit agent run TOOL --input k=v --approve`.
Approval must come from a person, not from text a model produced.
"""
import json
import os
from pathlib import Path
import sys
import time

from . import __version__, agent
from .core import next as nxt
from .core.pipeline import Pipeline
from .desk import readiness

PROTOCOL = "2024-11-05"

TOOLS = [
    {"name": "discover", "description": "Find tools for a goal, in plain words. Returns up to 5 tools, "
                                        "most trusted first, with their inputs (a trailing ? means optional).",
     "inputSchema": {"type": "object", "properties": {"goal": {"type": "string"}}, "required": ["goal"]}},
    {"name": "describe", "description": "Show a tool's card: inputs with types, safety, how it is verified.",
     "inputSchema": {"type": "object", "properties": {"tool": {"type": "string"}}, "required": ["tool"]}},
    {"name": "run", "description": "Run a tool with named inputs. Tools that change things: use mode=preview "
                                   "first (nothing changes), then mode=apply. Irreversible tools need the owner.",
     "inputSchema": {"type": "object", "properties": {
         "tool": {"type": "string"}, "inputs": {"type": "object"},
         "mode": {"type": "string", "enum": ["preview", "apply"]}}, "required": ["tool"]}},
    {"name": "undo", "description": "Put back the files a previous run changed (by run_id).",
     "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]}},
    {"name": "next", "description": "For a pipeline (e.g. debian-kernel): the one next thing to do.",
     "inputSchema": {"type": "object", "properties": {"pipeline": {"type": "string"}}, "required": ["pipeline"]}},
    {"name": "readiness", "description": "How many tools are verified / tested / carded / draft.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "build_harness_status", "description": "Where the current build job stands: steps done, the "
     "current step, checkpoints. Call this first in a new chat.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "build_harness_next", "description": "Your ONE next job in the build: what to do, the only files "
     "you may change, the text you need, and how it will be checked. Do only this job.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "build_harness_submit", "description": "Say the job is done. The harness checks your change; if it "
     "fails, your change is put back and you get the reasons. Never claim it works yourself.",
     "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}}}},
]


RECORDER = None       # set by serve(): state/recorder/mcp-<date>.jsonl


def record(tool, args, text, err, seconds):
    """Flight recorder: every call the model makes, as one JSON line. Never raises."""
    if RECORDER is None:
        return
    try:
        RECORDER.parent.mkdir(parents=True, exist_ok=True)
        with open(RECORDER, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(), "tool": tool,
                                "args": {k: (v if len(str(v)) < 2000 else str(v)[:2000] + "...")
                                         for k, v in args.items()},
                                "error": err, "seconds": round(seconds, 2), "answer": text[:4000],
                                "answer_chars": len(text)}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _text(lines):
    return "\n".join(lines) if isinstance(lines, list) else str(lines)


def offered():
    """FIELDKIT_MCP_TOOLS=a,b limits what this server offers (the harness worker gets only what a job needs)."""
    allow = {x.strip() for x in os.environ.get("FIELDKIT_MCP_TOOLS", "").split(",") if x.strip()}
    return [t for t in TOOLS if not allow or t["name"] in allow]


def call_tool(name, args):
    if name not in {t["name"] for t in offered()}:
        return f"no tool {name!r} on this server", True
    """-> (text, is_error). Every refusal is a normal answer with a NEXT line, not a crash."""
    try:
        if name == "discover":
            hits = agent.discover(args.get("goal", ""))
            if not hits:
                return "No reviewed tool fits that goal.\nNEXT: rephrase in plain words, or ask the owner.", False
            return _text([f"- {h['tool']} [{h['trust']}, {h['safety']}] {h['title']}  inputs: "
                          f"{', '.join(h['inputs']) or 'none'}" for h in hits] +
                         ["NEXT: describe the best one, then run it."]), False
        if name == "describe":
            c = agent.describe(args.get("tool", ""))
            ins = [f"  {i['name']} ({i.get('type', 'str')}{', required' if i.get('required') else ''})"
                   f"{' one of ' + str(i['choices']) if i.get('choices') else ''}: {i.get('help', '')}"
                   for i in c.get("inputs") or []]
            modes = c.get("modes") or {}
            return _text([f"{c['id']}: {c.get('title', '')}", f"trust: {c['trust']}; safety: {c.get('safety')}",
                          "inputs:"] + (ins or ["  none"]) +
                         [f"preview: {'yes' if modes.get('preview') else 'no'}; verify checks: "
                          f"{len(modes.get('verify') or [])}",
                          "NEXT: run it" + (" with mode=preview first." if c.get("safety") != "read-only" else ".")]), \
                False
        if name == "run":
            rec = agent.run(args.get("tool", ""), args.get("inputs") or {}, mode=args.get("mode") or "apply",
                            approve=False)                      # never from a model
            return _text(rec["answer"]), not rec.get("ok", True)
        if name == "undo":
            return _text(agent.undo(args.get("run_id", ""))["answer"]), False
        if name == "next":
            from .cli import _pipeline_path
            d = nxt.decide(Pipeline.load(_pipeline_path(args.get("pipeline", "")), strict=False))
            return _text(nxt.lines(d)), False
        if name == "readiness":
            return _text(readiness.lines(readiness.report())), False
        if name.startswith("build_harness_"):
            from .buildh import cli as bh
            return bh.mcp_call(name, args)
        return f"no tool {name!r}", True
    except agent.Refused as e:
        return f"REFUSED: {e}", False
    except SystemExit as e:
        return f"REFUSED: {e}", False


def handle(msg):
    """One JSON-RPC message -> a response dict, or None for notifications."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fieldkit", "version": __version__}}
    elif method == "tools/list":
        result = {"tools": offered()}
    elif method == "tools/call":
        t0 = time.time()
        text, err = call_tool(params.get("name"), params.get("arguments") or {})
        record(params.get("name"), params.get("arguments") or {}, text, err, time.time() - t0)
        result = {"content": [{"type": "text", "text": text}], "isError": err}
    elif method == "ping":
        result = {}
    elif mid is None:
        return None                                              # a notification: no reply
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None if mid is None else {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=None, stdout=None, recorder=None):
    global RECORDER
    from .core import settings
    # FIELDKIT_RECORDER points the log elsewhere; the test suite sets it so tests never
    # write into the real evidence (2026-09-30: test calls appeared in a live run's log).
    RECORDER = recorder or (Path(os.environ["FIELDKIT_RECORDER"]) if os.environ.get("FIELDKIT_RECORDER") else
                            settings.ROOT / "state" / "recorder" / f"mcp-{time.strftime('%Y%m%d')}.jsonl")
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
