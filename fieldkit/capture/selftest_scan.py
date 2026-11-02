"""`fieldkit capture self-test --roots|--kinds`: prove the scan in a sandbox home through the real entry points.

--roots  one script in every root of the table, one transcript per agent (Claude jsonl, Gemini json, OpenCode json and
         sqlite) holding a single-use command block, a repeated command and a secret; then: all found, repeats counted,
         nothing counted twice on a second scan or after the scan state is lost, a growing transcript read from where
         it stopped, a half-written line waited for, credential files never opened, the table right for Windows,
         Linux and macOS.
--kinds  one item of each kind (script, program, command, command sequence, config change, debugging procedure,
         workaround, test, build step, workflow), each carrying its kind, each found by one word.
"""
import json
import sqlite3
import sys
import uuid
from pathlib import Path

from . import roots as rootmod, scan as scanmod
from .selftest import Box

SECRET = "ghp_" + "Q9w8E7r6T5y4U3i2O1p0A9s8D7f6"
CRED_MARK = "credmark"


def _mark():
    return "fk" + uuid.uuid4().hex[:12]


def _put(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sandbox_env(b):
    h = b.dir / "home"
    env = {"FIELDKIT_SCAN_HOME": str(h), "FIELDKIT_SCAN_DOCUMENTS": str(h / "Documents"), "FIELDKIT_SCAN_LOCAL": str(h / "fkl"),
           "FIELDKIT_SCRATCH_ROOT": str(h / "scratch"), "TMP": str(h / "tmp"), "TEMP": str(h / "tmp"), "TMPDIR": str(h / "tmp"),
           "USERPROFILE": str(h), "HOME": str(h), "APPDATA": str(h / "AppData" / "Roaming"),
           "LOCALAPPDATA": str(h / "AppData" / "Local")}
    (h / "tmp").mkdir(parents=True, exist_ok=True)
    return h, env


def claude_line(kind, **kw):
    return json.dumps(dict(kw, type=kind), ensure_ascii=False) + "\n"


def claude_session(sid, prompt, calls, cwd="/work"):
    """calls: [(tool_use_id, tool name, input, is_error, result text)] -> a Claude Code transcript."""
    out = [claude_line("user", sessionId=sid, cwd=cwd, timestamp="2026-11-01T10:00:00Z",
                       message={"role": "user", "content": prompt})]
    for cid, name, inp, err, text in calls:
        out.append(claude_line("assistant", sessionId=sid, cwd=cwd, timestamp="2026-11-01T10:00:01Z",
                               message={"role": "assistant", "content": [{"type": "tool_use", "id": cid, "name": name, "input": inp}]}))
        out.append(claude_line("user", sessionId=sid, cwd=cwd, timestamp="2026-11-01T10:00:02Z",
                               message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": cid, "is_error": err,
                                                                     "content": text}]}))
    return "".join(out)


def _find(b, *words):
    return b.find(*words)


def roots_test():
    b = Box()
    h, env = _sandbox_env(b)
    m = {k: _mark() for k in ("docs", "local", "scratch", "sscripts", "temp", "pad", "claude", "gemini", "opencode",
                              "oc2", "cfg", "gcfg", "cmdclaude", "cmdgemini", "cmdoc", "cmdsql", "rep", "cred", "tok")}
    # -- the table, for each system
    for plat, home, must in (("win32", "C:/h", ["documents-scripts", "system-temp", "claude-scratchpad", "opencode-sessions",
                                                      "claude-transcripts", "gemini-transcripts"]),
                             ("linux", "/h", ["documents-scripts", "system-temp", "system-temp-tmp", "linux-config",
                                                   "claude-transcripts", "opencode-sessions"]),
                             ("darwin", "/mac/h", ["system-temp", "opencode-data", "claude-transcripts"])):
        t = {r["name"]: r["path"].replace("\\", "/") for r in rootmod.roots(plat, home=home, fieldkit=Path("/fk/local"))}
        b.check(all(k in t for k in must), f"the root table for {plat} has {len(must)} required roots", str([k for k in must if k not in t]))
    lin = {r["path"].replace("\\", "/") for r in rootmod.roots("linux", home="/h", fieldkit=Path("/fk/local"))}
    b.check({"/tmp", "/h/.config/opencode", "/h/.local/share/opencode", "/h/.gemini", "/h/.claude",
             "/h/.config/gemini"} <= lin, "Linux paths: /tmp, ~/.config, ~/.local/share, ~/.claude, ~/.gemini")
    # -- seed one script per root
    _put(h / "Documents" / "Scripts" / f"{m['docs']}.py", f"# {m['docs']}\nprint(1)\n")
    _put(h / "fkl" / f"{m['local']}.sh", f"#!/bin/sh\n# {m['local']}\necho hi\n")
    _put(h / "scratch" / "job1" / f"{m['scratch']}.ps1", f"# {m['scratch']}\nWrite-Host hi\n")
    _put(h / "scratch" / "scripts" / f"{m['sscripts']}.py", f"# {m['sscripts']}\nprint(2)\n")
    _put(h / "tmp" / f"{m['temp']}.py", f"# {m['temp']}\nprint(3)\nTOKEN='{SECRET}'\n")
    _put(h / "tmp" / "claude" / "proj" / "sess" / "scratchpad" / f"{m['pad']}.py", f"# {m['pad']}\nprint(4)\n")
    _put(h / ".claude" / f"{m['claude']}.sh", f"# {m['claude']}\necho 5\n")
    _put(h / ".gemini" / f"{m['gemini']}.sh", f"# {m['gemini']}\necho 6\n")
    _put(h / ".local" / "share" / "opencode" / f"{m['opencode']}.ts", f"// {m['opencode']}\nconsole.log(7)\n")
    _put(h / ".config" / "opencode" / f"{m['oc2']}.py", f"# {m['oc2']}\nprint(8)\n")
    _put(h / ".config" / "gemini" / f"{m['gcfg']}.sh", f"# {m['gcfg']}\necho 9\n")
    _put(h / ".claude" / ".credentials.json", json.dumps({"accessToken": m["cred"]}))
    _put(h / ".claude" / f"credentials_{m['cred']}.py", f"print('{m['cred']}')\n")
    # -- transcripts: a single-use heredoc block, a repeated command in two sessions, a secret on a command line
    heredoc = f"python - <<'EOF'\nprint('{m['cmdclaude']}')\nEOF"
    rep = f"echo {m['rep']}"
    call = lambda i, cmd, err=False, text="ok": (i, "Bash", {"command": cmd, "description": "d"}, err, text)
    _put(h / ".claude" / "projects" / "p" / "s1.jsonl", claude_session("sess-one", f"fix the thing {m['claude']}", [
        call("t1", heredoc), call("t2", rep), call("t3", f"curl -H 'Authorization: Bearer {SECRET}' http://x/{m['tok']}", True, "Exit code 22\nfail")]))
    _put(h / ".claude" / "projects" / "p" / "s2.jsonl", claude_session("sess-two", "again", [call("t9", rep)]))
    gem = {"sessionId": "gem-1", "messages": [{"type": "user", "content": f"gemini task {m['gemini']}"},
                                              {"type": "gemini", "toolCalls": [{"id": "g1", "name": "run_shell_command",
                                                                                  "args": {"command": f"echo {m['cmdgemini']}"}, "result": "ok"}]}]}
    _put(h / ".gemini" / "tmp" / "abc" / "chats" / "session-1.json", json.dumps(gem))
    part = {"type": "tool", "tool": "bash", "callID": "c1", "state": {"status": "completed", "input": {"command": f"echo {m['cmdoc']}"}, "output": "ok"}}
    _put(h / ".local" / "share" / "opencode" / "storage" / "part" / "ses1" / "prt1.json", json.dumps(part))
    db = h / ".local" / "share" / "gorilla-opencode" / "opencode.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("create table part (id text, session_id text, data text)")
    con.execute("insert into part values (?,?,?)", ("p1", "ses2", json.dumps(dict(part, callID="c2", state={"status": "completed", "input": {"command": f"echo {m['cmdsql']}"}, "output": "ok"}))))
    con.commit()
    con.close()

    rc, out = b.fk("capture", "scan", **env)
    b.check(rc == 0 and "scan:" in out, "`capture scan` runs and exits 0", out.strip()[-200:])
    for key, what in (("docs", "Documents/Scripts"), ("local", "Fieldkit/local"), ("scratch", "scratch root"), ("sscripts", "scratch scripts folder"),
                      ("temp", "system temp"), ("pad", "Claude scratchpad"), ("claude", "~/.claude"), ("gemini", "~/.gemini"),
                      ("opencode", "OpenCode data dir"), ("oc2", "~/.config/opencode"), ("gcfg", "~/.config/gemini")):
        r = [x for x in _find(b, m[key]) if x["kind"] == "script"]
        b.check(len(r) == 1 and r[0]["state"] == "raw" and r[0]["count"] == 1, f"a single-use script in {what} is captured, queued raw", str(len(r)))
    for key, what in (("cmdclaude", "Claude Code transcript heredoc"), ("cmdgemini", "Gemini transcript command"),
                      ("cmdoc", "OpenCode session json command"), ("cmdsql", "OpenCode sqlite session command")):
        r = [x for x in _find(b, m[key]) if x["kind"] == "command"]
        b.check(len(r) == 1 and r[0]["session"] and r[0]["problem"], f"{what}: captured with session id and problem", str(r[:1]))
    r = [x for x in _find(b, m["claude"]) if x["kind"] == "command" and "heredoc" not in x["command"] and m["cmdclaude"] in x["command"]]
    b.check(len(r) == 1 and "fix the thing" in r[0]["problem"] and r[0]["session"] == "sess-one" and r[0]["cwd"] == "/work",
            "problem = the user's prompt, cwd and session id come from the transcript", str(r[:1]))
    rr = [x for x in _find(b, m["rep"]) if x["kind"] == "command"]
    b.check(len(rr) == 1 and rr[0]["count"] == 2, "a command repeated in two sessions is one item counted twice", str([x["count"] for x in rr]))
    fail = [x for x in _find(b, m["tok"]) if x["kind"] == "command"]
    b.check(len(fail) == 1 and fail[0]["exit_code"] == 22, "a failed transcript command keeps its exit code", str(fail[:1]))
    raw = ""
    for p in b.cap.rglob("*"):
        if p.is_file() and p.suffix in (".jsonl", ".json", "") or p.parent.name == "blobs":
            raw += p.read_text(encoding="utf-8", errors="replace")
    b.check(SECRET not in raw, "the token in a script and on a command line never reaches the store")
    b.check(m["cred"] not in raw, "a credentials file is never opened (its content and a credentials_* script are absent)")
    # -- idempotent, incremental, crash safe
    before = b.ledger()
    rc, out = b.fk("capture", "scan", "--json", **env)
    s2 = json.loads(out) if rc == 0 else {}
    after = b.ledger()
    b.check(s2.get("new") == 0 and s2.get("repeat") == 0 and len(after["items"]) == len(before["items"])
            and after["deduplicated"] == before["deduplicated"], "a second scan adds nothing and counts nothing twice", out.strip()[-200:])
    (b.cap / "scan-state.json").unlink()
    rc, out = b.fk("capture", "scan", "--json", **env)
    s3 = json.loads(out) if rc == 0 else {}
    b.check(s3.get("new") == 0 and s3.get("repeat") == 0 and b.ledger()["deduplicated"] == before["deduplicated"],
            "with the scan state lost, the seen-log still stops double counting", out.strip()[-200:])
    tr = h / ".claude" / "projects" / "p" / "s1.jsonl"
    grown = _mark()
    extra = claude_session("sess-one", "later prompt", [call("t10", f"echo {grown}")])
    half = extra.splitlines(True)
    with open(tr, "a", encoding="utf-8") as f:
        f.write("".join(half[:2]) + half[2][:30])                  # the tool_use line complete, its result half written
    b.fk("capture", "scan", **env)
    got = [x for x in _find(b, grown) if x["kind"] == "command"]
    b.check(len(got) == 1, "a transcript that grew is read from where it stopped (the half-written line waits)", str(len(got)))
    with open(tr, "a", encoding="utf-8") as f:
        f.write(half[2][30:])
    b.fk("capture", "scan", **env)
    got = [x for x in _find(b, grown) if x["kind"] == "command"]
    b.check(len(got) == 1 and got[0]["count"] == 1, "the finished line adds no second copy", str([x["count"] for x in got]))
    rc, out = b.fk("capture", "verify", **env)
    b.check(rc == 0, "verify: store intact after the scans", out.strip()[-160:])
    rc, out = b.fk("capture", "ledger", "--strict", **env)
    b.check(rc == 0, "strict ledger: nothing lost or unaccounted", out.strip()[-160:])
    return b.done()


def kinds_test():
    b = Box()
    h, env = _sandbox_env(b)
    k = {x: _mark() for x in ("script", "program", "command", "sequence", "config", "debug", "workaround", "test", "build", "workflow")}
    mk = lambda i, cmd: (i, "Bash", {"command": cmd, "description": "d"}, False, "ok")
    calls = [
        ("w1", "Write", {"file_path": f"/tmp/{k['script']}.py", "content": f"# {k['script']}\nimport json\nprint(json.dumps(1))\n"}, False, "ok"),
        ("w2", "Write", {"file_path": f"/tmp/{k['program']}.go", "content": f"package main\n// {k['program']}\nfunc main() {{}}\n"}, False, "ok"),
        mk("c1", f"fieldkit size find {k['command']}"),
        mk("c2", f"cd /tmp && ls {k['sequence']} && echo done"),
        mk("c3", f"git config --global core.autocrlf false  # {k['config']}"),
        mk("c4", f"journalctl -u svc-{k['debug']} --since today"),
        mk("c5", f"pip install {k['workaround']} --force-reinstall  # workaround for the stale wheel cache"),
        mk("c6", f"python -m pytest -q tests/test_{k['test']}.py"),
        mk("c7", f"cargo build --release --manifest-path {k['build']}/Cargo.toml"),
        mk("c8", f"git fetch origin\ngit checkout main\nfor f in a b; do cp $f /tmp/{k['workflow']}; done\npython -m build\necho {k['workflow']}"),
    ]
    _put(h / ".claude" / "projects" / "p" / "s.jsonl", claude_session("kinds-1", "solve one problem of each kind", calls))
    rc, out = b.fk("capture", "scan", **env)
    b.check(rc == 0, "`capture scan` runs and exits 0", out.strip()[-200:])
    want = {"script": "script", "program": "program", "command": "command", "sequence": "command sequence", "config": "config change",
            "debug": "debugging procedure", "workaround": "workaround", "test": "test", "build": "build step", "workflow": "workflow"}
    for key, label in want.items():
        r = _find(b, k[key])
        have = sorted({x for it in r for x in (it.get("labels") or [])})
        b.check(len(r) == 1 and label in have and r[0]["state"] == "raw" and r[0]["session"] == "kinds-1" and r[0]["problem"],
                f"kind '{label}': captured once, labelled, queued raw, with session and problem", f"{len(r)} items, labels {have}")
    both = [x for x in _find(b, k["command"]) if "command sequence" in (x.get("labels") or [])]
    b.check(not both, "a plain command is not labelled a command sequence")
    rc, out = b.fk("capture", "ledger", "--strict", **env)
    b.check(rc == 0, "strict ledger: every item has a state, nothing lost", out.strip()[-160:])
    return b.done()


TESTS = {"roots": roots_test, "kinds": kinds_test}


def main(names):
    rc = 0
    for n in names:
        print(f"== self-test --{n}")
        rc = max(rc, TESTS[n]())
    print("NEXT: " + ("nothing to do" if rc == 0 else "fix the FAIL lines above"))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or list(TESTS)))
