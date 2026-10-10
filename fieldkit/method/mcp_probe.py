"""Speak MCP to a server the way a client does (docs/METHODS.md #6).

    fieldkit mcp-probe -- fieldkit mcp
    fieldkit mcp-probe --call describe --args '{"tool":"academic-refs"}' -- python -m fieldkit mcp
    fieldkit mcp-probe --protocol 2025-03-26 -- some-server --stdio

"Works with my test client" is not "works with the client people use". This opens a session with the exact
messages an MCP client sends (initialize with clientInfo and capabilities, notifications/initialized, tools/list,
optionally one tools/call), over stdio, one JSON message per line, with a deadline on every step, and reports:

  - the protocol version the server answered (and whether it is the one asked for)
  - every tool, and the size of the whole tool list in bytes and in estimated tokens (bytes / 4): what the list
    costs a client that does not defer schemas, on every turn
  - for --call: the answer, isError, and the _meta labels (untrusted / egress) a client may act on
  - a server that answers a notification, prints non-JSON to stdout, or never answers: named, not hung on

The default protocol, 2024-11-05, is what mcp-go v0.17 (Gorilla OpenCode) sends.
Exit 0 when the server speaks the protocol, 3 when it does not, 2 for bad input.
"""
import argparse
import json
import queue
import subprocess
import threading
import time

from . import emit

CLIENT = {"name": "fieldkit-mcp-probe", "version": "1"}


class Session:
    def __init__(self, argv, cwd=None, env=None):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", errors="replace", cwd=cwd, env=env, bufsize=1)
        self.lines = queue.Queue()
        self.junk = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def answer(self, mid, timeout):
        """The response to request `mid`; stray lines are kept, never treated as the answer."""
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return None
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                return None
            if line is None:
                return None
            try:
                msg = json.loads(line)
            except ValueError:
                self.junk.append(line.strip()[:200])
                continue
            if msg.get("id") == mid:
                return msg
            self.junk.append(json.dumps(msg)[:200])

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
        return self.p.stderr.read()[-500:] if self.p.stderr else ""


def probe(argv, call=None, args=None, protocol="2024-11-05", timeout=30.0, cwd=None, env=None):
    rep = {"server": argv, "ok": False, "problems": []}
    t0 = time.monotonic()
    try:
        s = Session(argv, cwd, env)
    except OSError as e:
        rep["problems"].append(f"cannot start the server: {e}")
        return rep
    try:
        s.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": protocol, "capabilities": {}, "clientInfo": CLIENT}})
        init = s.answer(1, timeout)
        if init is None:
            rep["problems"].append(f"no answer to initialize within {timeout:g}s")
            return rep
        if "error" in init:
            rep["problems"].append(f"initialize refused: {init['error']}")
            return rep
        res = init.get("result") or {}
        rep["protocol"] = res.get("protocolVersion")
        rep["serverInfo"] = res.get("serverInfo")
        rep["handshake_seconds"] = round(time.monotonic() - t0, 2)
        if rep["protocol"] != protocol:
            rep["problems"].append(f"asked for protocol {protocol}, the server answered {rep['protocol']}")
        s.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        s.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tl = s.answer(2, timeout)
        if tl is None or "error" in tl:
            rep["problems"].append("tools/list: " + ("no answer" if tl is None else str(tl["error"])))
            return rep
        tools = (tl.get("result") or {}).get("tools") or []
        size = len(json.dumps(tl.get("result")))
        rep["tools"] = [{"name": t.get("name"), "bytes": len(json.dumps(t)),
                         "description": (t.get("description") or "")[:100]} for t in tools]
        rep["tool_list_bytes"], rep["tool_list_tokens_est"] = size, size // 4
        if call:
            s.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": call, "arguments": args or {}}})
            c = s.answer(3, timeout)
            if c is None or "error" in c:
                rep["problems"].append(f"tools/call {call}: " + ("no answer" if c is None else str(c["error"])))
            else:
                r = c.get("result") or {}
                text = "\n".join(x.get("text", "") for x in r.get("content") or [] if isinstance(x, dict))
                rep["call"] = {"name": call, "isError": r.get("isError", False), "meta": r.get("_meta"),
                               "text": text[:2000], "chars": len(text)}
        if s.junk:
            rep["problems"].append(f"{len(s.junk)} line(s) on stdout that were not the answers asked for "
                                   f"(first: {s.junk[0]}); stdout must carry only protocol messages")
        rep["ok"] = not rep["problems"]
        return rep
    finally:
        err = s.close()
        if err.strip() and not rep.get("ok", False):
            rep["stderr"] = err.strip()


def _lines(d):
    out = [f"server: {' '.join(d['server'])}"]
    if d.get("protocol"):
        out.append(f"protocol {d['protocol']}, {d.get('serverInfo')}, handshake {d.get('handshake_seconds')}s")
    if "tools" in d:
        out.append(f"{len(d['tools'])} tools, {d['tool_list_bytes']:,} bytes (~{d['tool_list_tokens_est']:,} tokens "
                   "per turn if not deferred)")
        out += [f"  {t['name']:<24} {t['bytes']:>6} B  {t['description']}" for t in d["tools"]]
    if d.get("call"):
        c = d["call"]
        out.append(f"call {c['name']}: isError={c['isError']} _meta={c['meta']}")
        out += ["  " + l for l in c["text"].splitlines()[:20]]
    out += [f"PROBLEM: {p}" for p in d["problems"]]
    if d.get("stderr"):
        out.append(f"stderr: {d['stderr'][-300:]}")
    out.append("OK: speaks the protocol" if d["ok"] else "NOT OK")
    return out


def main(argv=None, prog="fieldkit mcp-probe"):
    argv = list(argv or [])
    if "--" not in argv:
        print("REFUSED: give the server command after --, e.g. fieldkit mcp-probe -- fieldkit mcp")
        return 2
    i = argv.index("--")
    own, server = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--call")
    ap.add_argument("--args", default="{}", help="the call's arguments as JSON")
    ap.add_argument("--protocol", default="2024-11-05")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--cwd")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(own)
    if not server:
        print("REFUSED: no server command after --")
        return 2
    try:
        args = json.loads(a.args)
    except ValueError as e:
        print(f"REFUSED: --args is not JSON: {e}")
        return 2
    r = probe(server, a.call, args, a.protocol, a.timeout, a.cwd)
    emit(r, a.json, _lines)
    return 0 if r["ok"] else 3
