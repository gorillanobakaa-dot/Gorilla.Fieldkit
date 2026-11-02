"""`fieldkit capture scan`: gather what agents left behind, from the files and the session transcripts.

    fieldkit capture scan [--root NAME]... [--list] [--json]       walk every root, journal what is new
    fieldkit capture self-test --roots|--kinds                      prove it in a sandbox (selftest_scan.py)

Roots (roots.py): Documents/Scripts, Fieldkit/local, the scratch root and its scripts folder, the system temp folder,
Claude scratchpads, ~/.claude, ~/.gemini, OpenCode data dirs; transcripts of Claude Code (jsonl), Gemini and OpenCode.
From a transcript it takes every shell command (heredocs and inline blocks included), every written file, with the
session id, the working folder and the last thing the user asked (the problem). Single-use items are kept exactly like
repeated ones; a repeat only raises the count. Secrets are redacted by the store at capture time; files named like
credentials are never opened.

Incremental and idempotent: unchanged files are skipped (size+mtime, then sha256), a transcript is read from its last
complete line, and a scan-seen log keeps an item from being counted twice. Crash safe: every item reaches the core
journal (fsync) before the scan state moves on; a crash can at worst count one repeat twice, never lose an item.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import hook, roots as rootmod, store as st

KINDS = ("script", "program", "command", "command sequence", "config change", "debugging procedure", "workaround",
         "test", "build step", "workflow")
COMPILED = {"rust", "go", "java", "c", "cpp", "csharp"}
_SEQ = re.compile(r"&&|\|\||;|\n")
_CONFIG = re.compile(r"(?i)(\bgit\s+config\b|\bsetx\b|\breg\s+(add|delete|import)\b|set-itemproperty|new-itemproperty|"
                     r"\bgsettings\s+set\b|\bdconf\s+write\b|\bsystemctl\s+(--user\s+)?(enable|disable|edit|mask)\b|"
                     r"\bsed\s+-i\b|\bcrontab\b|\bupdate-alternatives\b|\bnetsh\b|SetEnvironmentVariable|"
                     r"\bschtasks\s+/create\b|\bregister-scheduledtask\b|\bxdg-mime\b|\bupdate-desktop-database\b|"
                     r"\.(conf|ini|toml|cfg|ya?ml|json)\b.*(>|\btee\b)|(>|\btee\b).*\.(conf|ini|toml|cfg|ya?ml|json)\b)")
_DEBUG = re.compile(r"(?i)(\bjournalctl\b|\bdmesg\b|\bstrace\b|\bltrace\b|\bgdb\b|\blldb\b|get-winevent|get-eventlog|"
                    r"\blsof\b|\bnetstat\b|\bss\s+-|\btasklist\b|\bldd\b|\bobjdump\b|\bdumpbin\b|--verbose|\s-vv\b|"
                    r"--trace|RUST_BACKTRACE|\bDEBUG=|traceback|\btail\b.*\.log|\bcat\b.*\.log|\bgrep\b.*\blogs?\b|"
                    r"\bwhere\.exe\b|\bwhich\b|get-process|get-service|\bstrings\b)")
_WORKAROUND = re.compile(r"(?i)(workaround|work-around|\bhack\b|bypass|fall-?back|--force\b|-Force\b|--no-verify|"
                         r"kludge|temporary fix|stop-gap|\bband-?aid\b|--ignore-|--skip-)")
_TEST = re.compile(r"(?i)(\bpytest\b|\bunittest\b|cargo\s+test|go\s+test|npm\s+(run\s+)?test|\bjest\b|\bvitest\b|\bmocha\b|"
                   r"\bctest\b|dotnet\s+test|gradle\w*\s+test|mvn\s+test|invoke-pester|\bbats\b|\btox\b|make\s+test|"
                   r"fieldkit\s+(testcost|mutate|regress)|--self-test|\bself-test\b)")
_BUILD = re.compile(r"(?i)(\bmake\b|\bcmake\b|\bninja\b|cargo\s+(build|install)|go\s+(build|install)|npm\s+(run\s+build|install|ci)|"
                    r"pip\d?\s+(install|wheel)|python\d?\s+-m\s+(build|pip\s+install)|\bmsbuild\b|dotnet\s+(build|publish)|"
                    r"\bgradle|\bmvn\b|docker\s+build|\bnfpm\b|fieldkit\s+emerge|\bpyinstaller\b|\btsc\b|\bjavac\b|"
                    r"\bgcc\b|\bg\+\+|\brustc\b|dpkg-deb|apt(-get)?\s+install|winget\s+install|choco\s+install|"
                    r"\bcompile\b|\bpackage\b)")
_INLINE = re.compile(r"(?i)(<<-?\s*['\"]?\w+|python\d?\s+-c\b|python\d?\s+-\s*<|node\s+-e\b|"
                     r"(powershell|pwsh)(\.exe)?\s+.*-(command|c|encodedcommand)\b|perl\s+-e\b|ruby\s+-e\b|bash\s+-c\b)")
_TEST_CODE = re.compile(r"(def\s+test_|#\[test\]|func\s+Test\w+\(|@Test\b|\bdescribe\(|\bit\(['\"]|Describe\s+['\"])")


def labels_for(kind, text, path="", language=None):
    """The kinds (KINDS) an item is: any number, deterministic, from its text and file name alone."""
    text = (text or "")[:20000]
    name = Path(path).name.lower() if path else ""
    out = []
    if kind == "command":
        out.append("command")
        segs = [s.strip() for s in _SEQ.split(text) if s.strip() and not s.strip().startswith("#")]
        if len(segs) >= 2:
            out.append("command sequence")
        progs = {s.split()[0] for s in segs if s.split()}
        if len(progs) >= 3 and (len(segs) >= 4 or re.search(r"(?m)<<|^\s*(for|while|if)\b", text)):
            out.append("workflow")
        if _INLINE.search(text):
            out.append("script")
    elif kind in ("script", "file"):
        if kind == "script" or language:
            out.append("script")
        if language in COMPILED:
            out.append("program")
        lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
        if language in ("bash", "powershell", "batch") and len(lines) >= 4 and len({l.split()[0] for l in lines}) >= 3:
            out.append("workflow")
        if re.match(r"(test_|.*_test\.|.*\.test\.|.*\.spec\.)", name) or _TEST_CODE.search(text):
            out.append("test")
        if re.search(r"(build|make|compile|package|install)", name) or re.search(r"(?m)^\s*(cargo build|go build|make\b|cmake\b)", text):
            out.append("build step")
        if re.search(r"(debug|diag|probe|inspect|trace|doctor)", name):
            out.append("debugging procedure")
        if re.search(r"(config|setup|settings|configure)", name):
            out.append("config change")
    elif kind == "edit":
        if re.search(r"\.(conf|ini|toml|cfg|ya?ml|json|plist|reg)$|settings|config", name):
            out.append("config change")
    if kind != "edit":
        if _CONFIG.search(text):
            out.append("config change")
        if _DEBUG.search(text):
            out.append("debugging procedure")
        if _TEST.search(text) and "test" not in out:
            out.append("test")
        if _BUILD.search(text) and "build step" not in out:
            out.append("build step")
    if _WORKAROUND.search(text):
        out.append("workaround")
    seen = []
    for k in out:
        if k not in seen:
            seen.append(k)
    return seen


# ---- state ----------------------------------------------------------------------------------------------------------
class State:
    """scan-state.json (what was read) + scan-seen.log (keys already recorded). Both live in the capture store folder."""

    def __init__(self, root):
        self.path = Path(root) / "scan-state.json"
        self.seen_path = Path(root) / "scan-seen.log"
        self.data = {"files": {}, "transcripts": {}, "runs": []}
        try:
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self.seen = set()
        try:
            self.seen = set(self.seen_path.read_text(encoding="utf-8").split())
        except OSError:
            pass
        self._new = []

    def mark(self, key):
        """True the first time a key is seen."""
        if key in self.seen:
            return False
        self.seen.add(key)
        self._new.append(key)
        if len(self._new) >= 200:
            self.flush_seen()
        return True

    def flush_seen(self):
        if self._new:
            with open(self.seen_path, "a", encoding="utf-8") as f:
                f.write("\n".join(self._new) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._new = []

    def flush(self):
        self.flush_seen()
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)


def _key(*parts):
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8", "surrogatepass")).hexdigest()[:24]


def _iso(ts):
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts, timezone.utc).isoformat(timespec="microseconds")
    return str(ts) if ts else st.now()


# ---- record makers ----------------------------------------------------------------------------------------------------
def finish(rec, agent, file, labels_kind=None):
    rec["env"] = {}
    rec["origin"] = f"scan:{agent}"
    rec["source_file"] = str(file)
    rec["labels"] = labels_for(rec.get("kind"), rec.get("source") if rec.get("kind") != "edit" else rec.get("command", "") + " "
                               + (rec.get("source") or ""), rec.get("path") or (rec.get("args") or [""])[0], rec.get("language"))
    return rec


def payload_for(agent, session, cwd, ts, name, inp, out="", err="", failed=False, code=None):
    resp = {"stdout": out, "stderr": err}
    if code is not None:
        resp["exit_code"] = code
    p = {"session_id": session, "hook_event_name": "PostToolUseFailure" if failed else "PostToolUse", "tool_name": name,
         "cwd": cwd or "", "tool_input": inp, "tool_response": resp}
    if failed and code is None:
        p["error"] = err or "failed"
    return p


def _record(store, state, key, payload, agent, file, ts, prompt, stats):
    if not state.mark(key):
        stats["seen"] += 1
        return
    rec = hook.parse(agent, payload, store)
    if rec is None or rec.get("kind") == "session":
        return
    rec = finish(rec, agent, file)
    rec["timestamp"] = _iso(ts)
    if prompt:
        rec["problem"] = prompt
    elif not rec.get("problem"):
        rec["problem"] = f"seen in {Path(file).name}"
    _, new = store.record(rec)
    stats["new" if new else "repeat"] += 1
    stats["by_kind"][rec["kind"]] = stats["by_kind"].get(rec["kind"], 0) + 1


# ---- transcripts ------------------------------------------------------------------------------------------------------
def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in content
                         if not isinstance(x, dict) or x.get("type") in (None, "text"))
    return ""


def _claude_lines(store, state, path, stats, agent="claude"):
    key = str(path)
    ent = state.data["transcripts"].setdefault(key, {"offset": 0, "prompt": "", "session": "", "cwd": ""})
    size = path.stat().st_size
    if ent["offset"] > size:
        ent["offset"] = 0                                 # the file was replaced: read it again (seen keys stop repeats)
    if ent["offset"] == size:
        return
    with open(path, "rb") as f:
        f.seek(ent["offset"])
        data = f.read()
    cut = data.rfind(b"\n") + 1                            # a half-written last line waits for the next scan
    complete = data[:cut]
    objs = []
    for n, raw in enumerate(complete.split(b"\n")):
        if not raw.strip():
            continue
        try:
            objs.append(json.loads(raw.decode("utf-8", "replace")))
        except ValueError:
            continue
    results = {}
    for o in objs:
        msg = o.get("message") if isinstance(o, dict) else None
        c = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = (bool(b.get("is_error")), _text_of(b.get("content")))
    line_no = ent.get("lines", 0)
    for o in objs:
        line_no += 1
        if not isinstance(o, dict):
            continue
        ent["session"] = o.get("sessionId") or ent["session"]
        ent["cwd"] = o.get("cwd") or ent["cwd"]
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        c = msg.get("content")
        if o.get("type") == "user" and msg.get("role") == "user" and not o.get("isMeta"):
            t = _text_of(c).strip() if not (isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)) else ""
            if t and not t.startswith("<"):
                ent["prompt"] = st.redact_text(t)[:2000]
        elif o.get("type") == "assistant" and isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use" and hook.tool_kind(b.get("name")):
                    failed, text = results.get(b.get("id"), (False, ""))
                    p = payload_for(agent, ent["session"], ent["cwd"], o.get("timestamp"), b["name"], b.get("input") or {},
                                    out="" if failed else text, err=text if failed else "", failed=failed)
                    _record(store, state, _key(agent, key, b.get("id") or line_no), p, agent, path, o.get("timestamp"),
                            ent["prompt"], stats)
    ent["lines"] = line_no
    ent["offset"] += cut


_NAME_KEYS = ("name", "tool", "toolName", "tool_name")
_INPUT_KEYS = ("args", "input", "tool_input", "parameters", "arguments", "params")


def _find_calls(o, out, depth=0):
    """Tool calls anywhere in a JSON document (Gemini chats, OpenCode parts): -> list of (id, name, input, output, failed)."""
    if depth > 12:
        return
    if isinstance(o, dict):
        name = next((o[k] for k in _NAME_KEYS if isinstance(o.get(k), str)), None)
        state = o.get("state") if isinstance(o.get("state"), dict) else {}
        inp = next((o[k] for k in _INPUT_KEYS if isinstance(o.get(k), dict)), None) or (state.get("input") if isinstance(state.get("input"), dict) else None)
        if name and inp is not None and hook.tool_kind(name):
            res = o.get("result") if o.get("result") is not None else o.get("output") if o.get("output") is not None else state.get("output")
            failed = bool(o.get("error")) or state.get("status") == "error" or str(o.get("status", "")).lower() in ("error", "failed")
            out.append((o.get("id") or o.get("callID") or o.get("callId"), name, inp,
                        res if isinstance(res, str) else json.dumps(res) if res else "", failed))
        for v in o.values():
            _find_calls(v, out, depth + 1)
    elif isinstance(o, list):
        for v in o:
            _find_calls(v, out, depth + 1)


def _json_doc(store, state, path, agent, stats):
    key = str(path)
    ent = state.data["transcripts"].setdefault(key, {})
    sig = [path.stat().st_size, int(path.stat().st_mtime)]
    if ent.get("sig") == sig:
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        docs = [json.loads(text)] if path.suffix == ".json" else [json.loads(l) for l in text.splitlines() if l.strip().startswith(("{", "["))]
    except ValueError:
        return                                            # half written: the signature is not saved, so it is retried
    session = ""
    prompt = ""
    calls = []
    for d in docs:
        if isinstance(d, dict):
            session = str(d.get("sessionId") or d.get("session_id") or d.get("id") or session)
            for m in d.get("messages", []) if isinstance(d.get("messages"), list) else []:
                if isinstance(m, dict) and m.get("type") in ("user",) or isinstance(m, dict) and m.get("role") == "user":
                    prompt = st.redact_text(_text_of(m.get("content")))[:2000] or prompt
        _find_calls(d, calls)
    session = session or path.stem
    for i, (cid, name, inp, out, failed) in enumerate(calls):
        p = payload_for(agent, session, "", None, name, inp, out="" if failed else out, err=out if failed else "", failed=failed)
        _record(store, state, _key(agent, key, cid or i, _key(json.dumps(inp, sort_keys=True))), p, agent, path,
                path.stat().st_mtime, prompt, stats)
    ent["sig"] = sig


def _opencode_db(store, state, path, stats):
    key = str(path)
    ent = state.data["transcripts"].setdefault(key, {"sig": None})
    sig = [path.stat().st_size, int(path.stat().st_mtime)]
    if ent.get("sig") == sig:
        return
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        prompts = {}
        try:
            for sid, data in con.execute("select session_id, data from message order by time_created"):
                try:
                    d = json.loads(data)
                except ValueError:
                    continue
                if d.get("role") == "user":
                    prompts[sid] = d.get("summary", {}).get("title", "") if isinstance(d.get("summary"), dict) else ""
        except sqlite3.Error:
            pass
        rows = con.execute("select id, session_id, data from part").fetchall()
    except sqlite3.Error:
        return
    finally:
        con.close()
    for pid, sid, data in rows:
        try:
            d = json.loads(data)
        except ValueError:
            continue
        calls = []
        _find_calls(d, calls)
        for cid, name, inp, out, failed in calls:
            p = payload_for("opencode", sid, "", None, name, inp, out="" if failed else out, err=out if failed else "", failed=failed)
            _record(store, state, _key("opencode", key, pid), p, "opencode", path, path.stat().st_mtime, prompts.get(sid, ""), stats)
    ent["sig"] = sig


def scan_transcripts(store, state, root, stats):
    base = Path(root["path"])
    if not base.is_dir():
        return
    agent = root["agent"]
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in rootmod.SKIP_DIRS - {"plugins"} and not rootmod.is_secret_name(d)]
        if len(Path(dirpath).relative_to(base).parts) >= rootmod.MAX_DEPTH:
            dirs[:] = []
        for fn in files:
            p = Path(dirpath) / fn
            if rootmod.is_secret_name(fn):
                continue
            try:
                if fn.endswith(".jsonl") and agent == "claude":
                    _claude_lines(store, state, p, stats)
                elif fn.endswith(".jsonl") and "/tool-results/" not in p.as_posix():
                    _json_doc(store, state, p, agent, stats)
                elif fn.endswith(".json") and agent in ("gemini", "opencode") and (
                        "chat" in p.as_posix().lower() or "session" in p.as_posix().lower() or "part" in p.parts
                        or "storage" in p.parts or "message" in p.parts):
                    _json_doc(store, state, p, agent, stats)
                elif fn.endswith(".db") and agent == "opencode" and fn.startswith("opencode"):
                    _opencode_db(store, state, p, stats)
                stats["transcripts"] += 1
            except (OSError, sqlite3.Error, ValueError, UnicodeError) as exc:
                stats["errors"].append(f"{p}: {type(exc).__name__}")
        state.flush()


# ---- loose files ------------------------------------------------------------------------------------------------------
def scan_files(store, state, root, stats, visited):
    base = Path(root["path"])
    if not base.is_dir():
        return
    n = 0
    for dirpath, dirs, files in os.walk(base):
        rel = Path(dirpath).relative_to(base).parts
        dirs[:] = [d for d in dirs if d not in rootmod.SKIP_DIRS and not rootmod.is_secret_name(d)
                   and not (root["name"] == "claude-home" and d in ("projects", "todos", "shell-snapshots", "statsig"))
                   and not (root["name"] == "system-temp" and d == "claude")]
        dirs[:] = [d for d in dirs if not d.startswith(rootmod.SKIP_PREFIX) and not (Path(dirpath) / d / ".git").exists()]    # a checkout is kept by git itself
        if len(rel) >= rootmod.MAX_DEPTH:
            dirs[:] = []
        for fn in files:
            ext = Path(fn).suffix.lower()
            if ext not in rootmod.SCRIPT_EXT or rootmod.is_secret_name(fn):
                continue
            p = Path(dirpath) / fn
            try:
                real = os.path.normcase(os.path.realpath(p))
                if real in visited:
                    continue
                visited.add(real)
                s = p.stat()
                if s.st_size > rootmod.MAX_FILE or s.st_size == 0:
                    continue
                sig = [s.st_size, int(s.st_mtime)]
                ent = state.data["files"].get(real)
                if ent and ent["sig"] == sig:
                    continue
                raw = p.read_bytes()
                h = hashlib.sha256(raw).hexdigest()
                state.data["files"][real] = {"sig": sig, "sha": h}
                if ent and ent["sha"] == h:
                    continue
                stats["files"] += 1
                text = raw.decode("utf-8", "replace")
                lang = rootmod.SCRIPT_EXT[ext]
                if not state.mark(_key("file", real, h)):
                    stats["seen"] += 1
                    continue
                rec = {"agent": "scan", "session": "", "cwd": str(p.parent), "env": {}, "timestamp": _iso(s.st_mtime * 1000),
                       "kind": "script", "source": text, "command": f"write {p}", "args": [str(p)], "language": lang,
                       "deps": hook.deps_of("write", "", text, str(p)), "inputs": [], "outputs": {"files": [str(p)]},
                       "exit_code": None, "errors": "", "path": str(p),
                       "problem": f"left in {root['name']} ({root['path']}); the session that wrote it is unknown",
                       "description": ""}
                rec = finish(rec, "files", p)
                rec["origin"] = f"scan:{root['name']}"
                _, new = store.record(rec)
                stats["new" if new else "repeat"] += 1
                stats["by_kind"]["script"] = stats["by_kind"].get("script", 0) + 1
                n += 1
                if n % 100 == 0:
                    state.flush()
            except OSError as exc:
                stats["errors"].append(f"{p}: {type(exc).__name__}")
    state.flush()


# ---- the scan -------------------------------------------------------------------------------------------------------
def scan(store=None, only=None, platform=None):
    store = store or st.Store()
    store.recover()
    with st._locked(store.root / "scan.lock"), store.batch():
        state = State(store.root)
        stats = {"new": 0, "repeat": 0, "seen": 0, "files": 0, "transcripts": 0, "errors": [], "by_kind": {}, "roots": []}
        visited = set()
        table = rootmod.roots(platform)
        for root in sorted(table, key=lambda r: r["mode"] != "files"):      # files first, transcripts after
            if only and root["name"] not in only:
                continue
            if not Path(root["path"]).is_dir():
                continue
            stats["roots"].append(root["name"])
            (scan_transcripts if root["mode"] == "transcripts" else scan_files)(store, state, root, stats, *([] if root["mode"] == "transcripts" else [visited]))
        state.data["runs"] = (state.data["runs"] + [{"ts": st.now(), "new": stats["new"], "repeat": stats["repeat"]}])[-20:]
        state.flush()
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit capture scan", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", help="only this root (see --list)")
    ap.add_argument("--list", action="store_true", help="print the root table for this system and stop")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        for r in rootmod.roots():
            print(f"{r['name']:24} {r['mode']:11} {'exists' if Path(r['path']).is_dir() else 'absent':7} {r['path']}")
        print("NEXT: `fieldkit capture scan` walks the roots that exist")
        return 0
    stats = scan(only=set(a.root) if a.root else None)
    if a.json:
        print(json.dumps(stats, indent=1))
    else:
        print(f"scan: {stats['new']} new item(s), {stats['repeat']} repeat(s) counted, {stats['seen']} already seen; "
              f"{stats['files']} file(s) read, {stats['transcripts']} transcript file(s) checked, roots: {', '.join(stats['roots'])}")
        for k, v in sorted(stats["by_kind"].items()):
            print(f"  {k}: {v}")
        for e in stats["errors"][:10]:
            print(f"  unreadable (kept for next scan): {e}")
        print("NEXT: `fieldkit capture ledger` shows the queue; `fieldkit capture find WORDS` finds an item")
    return 0


if __name__ == "__main__":
    sys.exit(main())
