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
import sys

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
]


def _text(lines):
    return "\n".join(lines) if isinstance(lines, list) else str(lines)


def call_tool(name, args):
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
        result = {"tools": TOOLS}
    elif method == "tools/call":
        text, err = call_tool(params.get("name"), params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": err}
    elif method == "ping":
        result = {}
    elif mid is None:
        return None                                              # a notification: no reply
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None if mid is None else {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=None, stdout=None):
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
