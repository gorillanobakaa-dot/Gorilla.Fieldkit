"""Portable Antigravity global hook installer, checker, and 150k context warning (owner order 2026-11-25).

Installs and verifies the global Antigravity hooks under `<home>/.gemini` -> `config`:
  - `hooks.json` (`owner-guard`: `PreToolUse` + `PreInvocation`)
  - `hooks/guard.py` (`v1.7.0`: hard lines, protected paths, `toolrouter`, Session Mode spawn cap on
    `invoke_subagent` / `run_workflow` / `swarm`, P39 multi-agent launch choice check on `peers link ask` /
    `task start`, and the 150k context warning telling the user and agent to run `/compact` or open a new session).

Zero machine-specific or owner-specific paths are stored in the repository; `apply()` resolves `sys.executable` and
the active Fieldkit checkout dynamically on any Windows, Linux, or macOS computer.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

GUARD_VERSION = "1.7.0"
MARKER = f"# fieldkit-antigravity-guard v{GUARD_VERSION}"
CFG_PARTS = (".gemini", "config")
HANDOVER_SAFE_RE = re.compile(
    r"\bfieldkit\b[^\n]*\b(?:rotate\s+(?:handover|check)|zero\s+checkin\b[^\n]*--off|sitrep\s+check)\b",
    re.I,
)


def config_dir(home=None) -> Path:
    return Path(home or Path.home()).joinpath(*CFG_PARTS)


def hooks_json_path(home=None) -> Path:
    return config_dir(home) / "hooks.json"


def guard_py_path(home=None) -> Path:
    return config_dir(home) / "hooks" / "guard.py"


def _fieldkit_root(root=None) -> Path:
    base = Path(root) if root else Path(__file__).resolve().parents[2]
    inst = base.parent / "Fieldkit-installed"
    return inst if (inst / "fieldkit").is_dir() else base


def _transcript_tail_tokens(path: Path) -> int:
    """Read the last PLANNER_RESPONSE token usage from an Antigravity `transcript.jsonl` tail (fast, bounded 128 KB)."""
    if not path or not path.is_file():
        return 0
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > 131_072:
                fh.seek(size - 131_072)
            raw = fh.read()
    except OSError:
        return 0
    best = 0
    for line in reversed(raw.splitlines()):
        if b"PLANNER_RESPONSE" not in line and b"input_tokens" not in line:
            continue
        try:
            d = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        inp = int(d.get("input_tokens") or 0)
        cached = int(d.get("cache_read_tokens") or 0)
        total = inp + cached
        if total > 0:
            best = total
            break
    return best


def context_tokens(conversation_id: str, transcript_path: str | None = None, home=None) -> int:
    """Return the latest single-call context token count for an Antigravity conversation (`db` + `transcript.jsonl`)."""
    conv = str(conversation_id or "").strip()
    h = Path(home or Path.home())
    ctx = 0
    if conv and conv != "?":
        db = h.joinpath(".gemini", "antigravity", "conversations", f"{conv}.db")
        if db.is_file():
            try:
                from ..tokens import rotate, session_cost as sc

                row = rotate.measure(sc.Antigravity(db.parent), db)
                ctx = max(ctx, int(row.get("context_last") or 0))
            except Exception:  # noqa: BLE001
                pass
    candidates = []
    if transcript_path:
        candidates.append(Path(transcript_path))
    if conv and conv != "?":
        for prod in ("antigravity", "antigravity-cli", "antigravity-ide"):
            candidates.append(h.joinpath(".gemini", prod, "brain", conv, ".system_generated", "logs", "transcript.jsonl"))
    for tp in candidates:
        t_ctx = _transcript_tail_tokens(tp)
        if t_ctx > ctx:
            ctx = t_ctx
    return ctx


def context_warning(payload: dict, home=None, cfg: dict | None = None) -> dict | None:
    """Return warning texts when the conversation's context tokens reach `ctx_rotate` (default 150,000), else None."""
    if os.environ.get("FIELDKIT_ROTATE") == "off":
        return None
    if cfg is None:
        try:
            from ..tokens import rotate

            cfg = rotate.config()
        except Exception:  # noqa: BLE001
            cfg = {"ctx_rotate": 150_000}
    limit = int((cfg or {}).get("ctx_rotate") or 150_000)
    conv = str((payload or {}).get("conversationId") or "").strip()
    ctx = context_tokens(conv, (payload or {}).get("transcriptPath"), home=home)
    if ctx < limit:
        return None
    ctx_k = round(ctx / 1000)
    lim_k = round(limit / 1000)
    short = conv[:8] if conv else "session"
    markdown = (
        f"⚠️ **Context limit reached ({ctx_k}k >= {lim_k}k tokens — ROTATE):** "
        f"Every call now re-sends ~{ctx_k}k tokens. Run **`/compact`** to compress this conversation, "
        f"or run `fieldkit rotate handover --session {conv or '<id>'} ...` and **open a new session**."
    )
    system = (
        f"SESSION ROTATION WARNING ({ctx_k}k >= {lim_k}k tokens, ROTATE): context has reached {ctx_k}k tokens. "
        f"Tell the user clearly that context passed {lim_k}k tokens and they should either run `/compact` "
        f"or switch to a new session after `python -m fieldkit rotate handover --session {conv or short} "
        "--title \"<job>\" --done \"<done>\" --next \"<next>\" --not-verified \"<unverified>\"`."
    )
    reason = (
        f"Context reached {ctx_k}k tokens (>= {lim_k}k ROTATE limit). "
        "Run `/compact` now to shrink context, or finish handover (`fieldkit rotate handover`) and open a new session."
    )
    return {"context": ctx, "limit": limit, "markdown": markdown, "system": system, "reason": reason}


def pre_invocation(payload: dict, home=None, cfg: dict | None = None) -> dict:
    """Antigravity `PreInvocation` JSON output: injects visible user banner + model notice when context >= 150k."""
    w = context_warning(payload, home=home, cfg=cfg)
    if not w:
        return {}
    return {
        "injectSteps": [
            {
                "systemMessage": {
                    "systemMessage": w["system"],
                    "metadata": {"user_markdown": w["markdown"]},
                }
            }
        ]
    }


def midturn_context_gate(payload: dict, state_file: Path | None = None, home=None, cfg: dict | None = None):
    """Return `('force_ask', reason)` on the first tool call after crossing 150k (and every 10 calls while uncompacted),
    resetting automatically once `/compact` brings context below 150k. Never blocks handover/cleanup commands."""
    call = (payload or {}).get("toolCall") or {}
    args = call.get("args") or {}
    cmd = next((v for k, v in args.items() if k.lower() == "commandline" and isinstance(v, str)), "")
    if cmd and HANDOVER_SAFE_RE.search(cmd):
        return None
    conv = str((payload or {}).get("conversationId") or "").strip()
    if not conv or conv == "?":
        return None
    sfile = Path(state_file) if state_file else (config_dir(home) / "hooks" / "context-warn-state.json")
    try:
        st = json.loads(sfile.read_text(encoding="utf-8")) if sfile.is_file() else {}
    except (OSError, ValueError):
        st = {}
    w = context_warning(payload, home=home, cfg=cfg)
    if not w:
        if conv in st:
            st.pop(conv, None)
            try:
                sfile.parent.mkdir(parents=True, exist_ok=True)
                sfile.write_text(json.dumps(st), encoding="utf-8")
            except OSError:
                pass
        return None
    calls_over = int((st.get(conv) or {}).get("calls", 0)) + 1
    st[conv] = {"calls": calls_over, "context": w["context"], "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if len(st) > 300:
        st = dict(list(st.items())[-150:])
    try:
        sfile.parent.mkdir(parents=True, exist_ok=True)
        sfile.write_text(json.dumps(st), encoding="utf-8")
    except OSError:
        pass
    if calls_over == 1 or calls_over % 10 == 1:
        return "force_ask", w["reason"]
    return None


GUARD_TEMPLATE = '''"""Antigravity PreToolUse & PreInvocation guard installed by Fieldkit (`fieldkit rules install`).
__MARKER__

Enforces deterministic hard lines, protected-path write blocks, `fieldkit.autopilot.toolrouter`, Session Mode caps on
`invoke_subagent` / `run_workflow` / `swarm`, P39 multi-agent session-launch confirmation (`peers link ask` /
`task start`), and the 150k-token context warning (`/compact` or start a new session).

    python guard.py --selftest        runs the built-in self-test suite
    python guard.py --pre-invocation  runs the PreInvocation 150k context check
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "guard.log")
STATE = os.path.join(HERE, "guard-state.json")
CTX_STATE = os.path.join(HERE, "context-warn-state.json")
COMMANDS_BEFORE_ASKING = 25
ASK_EVERY = 10


def _resolve_fieldkit():
    cands = []
    if os.environ.get("FIELDKIT_ROOT"):
        cands.append(Path(os.environ["FIELDKIT_ROOT"]))
    home = Path.home()
    cands.extend([
        home / "Documents" / "Scripts" / "Fieldkit-installed",
        home / "Documents" / "Scripts" / "Fieldkit",
    ])
    configured = __CONFIGURED_ROOT__
    if configured:
        cp = Path(configured)
        cands.extend([cp.parent / "Fieldkit-installed", cp])
    for c in cands:
        if (c / "fieldkit").is_dir():
            return str(c)
    return ""


FIELDKIT = _resolve_fieldkit()
AUTOPILOT_ON = os.path.join(os.path.expanduser("~"), ".fieldkit", "autopilot", "on.json")

READ_ONLY = {
    "view_file", "view_file_outline", "list_directory", "grep_search", "find", "find_by_name",
    "finda", "code_search", "internal_search", "find_all_references",
    "command_status", "list_resources", "view_code_item", "list_browser_pages",
    "conversation_history", "knowledge_artifacts", "ask_question", "finish",
    "checkpoint", "ephemeral_message", "error_message", "findings",
}
EXTERNAL = {"search_web", "read_url_content"}
EDIT_TOOLS = {
    "code_action", "file_change", "write_blob", "write_to_file",
    "replace_file_content", "multi_replace_file_content", "edit_notebook",
    "move", "delete_directory", "write_file", "create_file",
}

PROTECTED = [
    (r"(^|[/\\s\\"'])" + "har" + "ness/", "the build harness (pre-build checks and build stages)"),
    ("pre" + r"flight[^/\\s\\"']*\\.py", "a pre-build check"),
    (r"\\.git/" + "hooks/", "git hooks"),
    (r"(^|/)hooks/(pre-|" + "post-)[a-z-]+", "git hooks"),
    (r"(^|[/\\s\\"'])(gemini|agents|claude)" + r"\\.md\\b", "an AI rules file"),
    (r"(^|[/\\s\\"'])[._]agent" + "s?/", "an AI configuration folder"),
    (r"\\.gemini/" + "config", "Antigravity's configuration and this guard"),
    (r"\\.gemini/" + r"gemini\\.md", "the global rules file"),
]
WRITE_IN_COMMAND = re.compile(
    r"(set-content|add-content|out-file|new-item|copy-item|move-item|rename-item|"
    r"remove-item|clear-content|\\bdel\\b|\\berase\\b|\\bcopy\\b|\\bmove\\b|\\bren\\b|\\bmv\\b|\\bcp\\b|"
    r"\\brm\\b|sed\\s+-i|tee\\b|>>?|open\\([^)]*['\\"][wa]|write_text|writelines|\\.write\\(|"
    r"git\\s+(add|commit|checkout|restore|rm|mv|apply|am)\\b|patch\\b|truncate|chmod|attrib|icacls)",
    re.I,
)
DENY_COMMANDS = [
    (r"\\bgit\\b[^\\n]*\\bpush\\b", "Pushing is the owner's job, never the model's."),
    (r"--no-verify\\b|-c\\s+core\\.hookspath|core\\.hookspath", "Bypassing or moving git hooks is not allowed."),
    (r"\\bgit\\b[^\\n]*\\bcommit\\b[^\\n]*\\s-[a-z]*n[a-z]*(\\s|$)", "git commit -n skips the hooks (it is --no-verify). Not allowed."),
    (r"gorilla_owner_approved", "GORILLA_OWNER_APPROVED is the owner's own signature on a harness change. A model never sets it."),
    (r"gorilla_build\\.py[^\\n]*--force\\b|\\bgorilla(\\.cmd)?\\s[^\\n]*--force\\b", "--force builds despite failing pre-build checks. Report the failing check and ask."),
    (r"\\bgit\\b[^\\n]*\\breset\\b[^\\n]*--hard", "git reset --hard destroys uncommitted work."),
    (r"\\bgit\\b[^\\n]*\\bclean\\b", "git clean deletes untracked files, such as unbacked notes."),
    (r"\\bgit\\b[^\\n]*\\b(rebase|filter-branch|filter-repo)\\b", "Rewriting history is the owner's decision."),
    (r"\\bgit\\b[^\\n]*\\bcommit\\b[^\\n]*--amend", "Amending rewrites history; make a new commit and ask."),
    (r"\\bgit\\b[^\\n]*\\bbranch\\b[^\\n]*\\s-D\\b", "Force-deleting a branch loses work."),
    (r"\\bgit\\b[^\\n]*\\bstash\\b[^\\n]*\\b(drop|clear)\\b", "Dropping a stash loses work."),
    (r"\\bgit\\b[^\\n]*\\bcheckout\\b[^\\n]*(\\s--\\s|\\s\\.\\s*$)", "Discarding file changes loses work."),
    (r"\\bgit\\b[^\\n]*\\badd\\b[^\\n]*(\\s-f\\b|--force\\b)", "Forcing a file past .gitignore needs the owner; .gitignore is a decision, not a default."),
    (r"\\brm\\b[^\\n]*\\s-[a-z]*r|\\brmdir\\b[^\\n]*/s|\\brd\\b[^\\n]*/s|\\bdel\\b[^\\n]*/s|remove-item[^\\n]*-recurse|shutil\\.rmtree|\\bformat\\b\\s+[a-z]:",
     "Recursive deletion. Use fieldkit size recycle instead."),
]
STATEMENT_SPLIT = re.compile(r";|&&|\\|\\||\\r?\\n")


def _ensure_fieldkit():
    if FIELDKIT and FIELDKIT not in sys.path:
        sys.path.insert(0, FIELDKIT)


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from strings(v)


def protected_hit(text):
    t = text.replace("\\\\", "/").lower()
    for pat, what in PROTECTED:
        if re.search(pat, t):
            return what
    return None


def statements(cmd):
    return [s for s in STATEMENT_SPLIT.split(cmd) if s.strip()]


def restore_is_destructive(cmd):
    for m in re.finditer(r"\\bgit\\b[^;&\\n|]*\\brestore\\b([^;&\\n|]*)", cmd, re.I):
        rest = m.group(1).lower()
        if "--worktree" in rest:
            return True
        if "--staged" in rest:
            continue
        if re.search(r"\\S", rest):
            return True
    return False


def bump(conv, state_path=None):
    sp = state_path or STATE
    try:
        with open(sp, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    n = st.get(conv, 0) + 1
    st[conv] = n
    if len(st) > 500:
        st = dict(list(st.items())[-200:])
    try:
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except OSError:
        pass
    return n


def autopilot_decide(payload):
    try:
        _ensure_fieldkit()
        from fieldkit.autopilot import hardlines
    except Exception as e:  # noqa: BLE001
        return "force_ask", "autopilot is on but its hard lines could not be loaded (%s): asking instead" % e
    call = payload.get("toolCall") or {}
    decision, why = hardlines.decide(call.get("name"), call.get("args") or {})
    return ("deny", "BLOCKED by the autopilot hard lines: " + why) if decision == "deny" else ("allow", "autopilot: " + why)


def toolroute(payload):
    call = payload.get("toolCall") or {}
    args = call.get("args") or {}
    name = str(call.get("name") or "").strip().lower()
    if name in ("invoke_subagent", "run_workflow", "swarm"):
        try:
            _ensure_fieldkit()
            from fieldkit.lifecycle import sessionmode

            why = sessionmode.antigravity_spawn_refusal(payload)
        except Exception as e:  # noqa: BLE001
            why = "SESSION MODE unreadable (%s): sub-agents are refused until it can be read (Economy)." % type(e).__name__
        if why:
            return "deny", why
    cmd = next((v for k, v in args.items() if k.lower() == "commandline" and isinstance(v, str)), None)
    if not cmd:
        return None
    conv = str(payload.get("conversationId") or "unknown")
    try:
        _ensure_fieldkit()
        from fieldkit.lifecycle import session_choice

        sc_why = session_choice.command_refusal(cmd, conv)
        if sc_why:
            return "deny", sc_why
    except Exception:  # noqa: BLE001
        pass
    try:
        _ensure_fieldkit()
        from fieldkit.autopilot import toolrouter

        r = toolrouter.route(cmd)
    except Exception:  # noqa: BLE001
        return None
    return ("deny", toolrouter.message(r)) if r else None


def _context_check(payload, ctx_state_path=None):
    try:
        _ensure_fieldkit()
        from fieldkit.antigravity import hooks as agy_hooks

        return agy_hooks.midturn_context_gate(payload, state_file=ctx_state_path or CTX_STATE)
    except Exception:  # noqa: BLE001
        return None


def decide(payload, state_path=None, ctx_state_path=None):
    routed = toolroute(payload)
    if routed:
        return routed
    if os.path.exists(AUTOPILOT_ON):
        return autopilot_decide(payload)
    call = payload.get("toolCall") or {}
    name = (call.get("name") or "").lower()
    args = call.get("args") or {}
    texts = list(strings(args))
    joined = "\\n".join(texts)
    conv = payload.get("conversationId", "?")

    if name == "run_command" or "commandline" in {k.lower() for k in args}:
        cmd = next((v for k, v in args.items() if k.lower() == "commandline" and isinstance(v, str)), joined)
        for pat, why in DENY_COMMANDS:
            if re.search(pat, cmd, re.I):
                return "deny", "BLOCKED by the owner's guard: " + why
        if restore_is_destructive(cmd):
            return "deny", "BLOCKED by the owner's guard: Discarding file changes loses work."
        if re.search(r"record_decisions\\.py", cmd, re.I):
            return "force_ask", "record_decisions.py re-records the owner's pref decisions. Only the owner does that."
        cwd = next((v for k, v in args.items() if k.lower() == "cwd" and isinstance(v, str)), "")
        cwd_hit = protected_hit(cwd + "/") if cwd else None
        for seg in statements(cmd):
            what = protected_hit(seg) or cwd_hit
            if what and WRITE_IN_COMMAND.search(seg):
                return "deny", ("BLOCKED by the owner's guard: this command would change %s. "
                                "Report the need and ask; never weaken a check or a rules file.") % what
        if re.search(r"\\bgit\\b[^\\n]*\\bcommit\\b", cmd, re.I):
            return "force_ask", "Every commit needs the owner's explicit approval. Show the diff first."
        ctx_gate = _context_check(payload, ctx_state_path=ctx_state_path)
        if ctx_gate:
            return ctx_gate
        n = bump(conv, state_path=state_path)
        if n > COMMANDS_BEFORE_ASKING and (n - COMMANDS_BEFORE_ASKING) % ASK_EVERY == 1:
            return "force_ask", ("This conversation has run %d commands. Stop and report what is known, "
                                 "then ask before continuing.") % (n - 1)
        return "ask", "no guard rule applies; Antigravity's own settings decide"

    if name in READ_ONLY:
        ctx_gate = _context_check(payload, ctx_state_path=ctx_state_path)
        if ctx_gate:
            return ctx_gate
        return "allow", "read-only"
    if name in EXTERNAL or name.startswith("browser_") or name in {
        "click_browser_pixel", "execute_browser_javascript", "capture_browser_screenshot"
    }:
        return "force_ask", "External access (web or browser) needs the owner's explicit approval every time."
    if name == "git_commit":
        return "force_ask", "Every commit needs the owner's explicit approval. Show the diff first."
    what = protected_hit(joined)
    if what:
        return "deny", "BLOCKED by the owner's guard: %s may not be changed by a model. Report the need and ask the owner." % what
    if name == "delete_directory":
        return "force_ask", "Deleting a folder needs the owner's explicit approval (use fieldkit size recycle)."
    ctx_gate = _context_check(payload, ctx_state_path=ctx_state_path)
    if ctx_gate:
        return ctx_gate
    if name in EDIT_TOOLS:
        return "allow", "edit outside protected paths"
    return "ask", "unknown tool: Antigravity's own settings decide"


def log(payload, decision, reason):
    call = payload.get("toolCall") or {}
    try:
        args = json.dumps(call.get("args"), ensure_ascii=False)[:600]
    except (TypeError, ValueError):
        args = "?"
    line = "%s  %s  conv=%s  model=%s  tool=%s  -> %s  (%s)  args=%s\\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), decision.upper(), str(payload.get("conversationId", "?"))[:8],
        payload.get("modelName", "?"), call.get("name"), decision, reason, args)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def selftest():
    tmp = tempfile.mkdtemp(prefix="fk-agy-guard-")
    old_mode = os.environ.get("FIELDKIT_MODE_DIR")
    os.environ["FIELDKIT_MODE_DIR"] = os.path.join(tmp, "mode")
    sp = os.path.join(tmp, "state.json")
    cp = os.path.join(tmp, "ctx.json")
    cases = [
        ("invoke_subagent", {"TypeName": "research", "Role": "R", "Prompt": "x"}, "deny"),
        ("run_workflow", {"Script": "x"}, "deny"),
        ("swarm", {"action": "add", "name": "w", "brief": "x"}, "deny"),
        ("swarm", {"action": "status"}, "ask"),
        ("run_command", {"CommandLine": "fieldkit peers link ask --agent claude --model sonnet --thinking medium --brief b.md"}, "deny"),
        ("run_command", {"CommandLine": "Get-ChildItem C:\\\\ -Recurse -Filter vctip.exe"}, "deny"),
        ("run_command", {"CommandLine": "Get-Content somefile.txt"}, "deny"),
        ("run_command", {"CommandLine": "cargo test"}, "deny"),
        ("run_command", {"CommandLine": "git push origin main"}, "deny"),
        ("run_command", {"CommandLine": "git commit --no-verify -m x"}, "deny"),
        ("run_command", {"CommandLine": "git restore --staged .env"}, "ask"),
        ("run_command", {"CommandLine": "git restore README.md"}, "deny"),
        ("view_file", {"AbsolutePath": "README.md"}, "allow"),
        ("write_file", {"Path": "src/app.py", "Content": "x = 1"}, "allow"),
        ("write_file", {"Path": ".git/" + "hooks/pre-commit", "Content": "exit 0"}, "deny"),
    ]
    failed = 0
    try:
        for i, (tool, args, want) in enumerate(cases):
            got, why = decide(
                {"conversationId": f"selftest-{i}", "toolCall": {"name": tool, "args": args}},
                state_path=sp,
                ctx_state_path=cp,
            )
            ok = got == want
            print(f"{'ok  ' if ok else 'FAIL'} {tool:<18} -> {got:<9} (want {want})  {why[:70]}")
            if not ok:
                failed += 1
        # Loop brake check using a benign command not caught by toolrouter
        last = None
        for _ in range(26):
            last, _ = decide(
                {"conversationId": "selftest-loop", "toolCall": {"name": "run_command", "args": {"CommandLine": "git status --short"}}},
                state_path=sp,
                ctx_state_path=cp,
            )
        print(f"{'ok  ' if last == 'force_ask' else 'FAIL'} loop brake 26x     -> {last:<9} (want force_ask)")
        if last != "force_ask":
            failed += 1
        # 150k context check via synthetic transcript.jsonl
        tp = os.path.join(tmp, "transcript.jsonl")
        with open(tp, "w", encoding="utf-8") as tf:
            tf.write(json.dumps({"type": "PLANNER_RESPONSE", "input_tokens": 10_000, "cache_read_tokens": 155_000}) + "\\n")
        c_dec, c_why = decide(
            {"conversationId": "selftest-150k", "transcriptPath": tp, "toolCall": {"name": "view_file", "args": {"AbsolutePath": "a.py"}}},
            state_path=sp,
            ctx_state_path=cp,
        )
        ok_ctx = c_dec == "force_ask" and "/compact" in c_why
        print(f"{'ok  ' if ok_ctx else 'FAIL'} 150k context gate  -> {c_dec:<9} (want force_ask + /compact)")
        if not ok_ctx:
            failed += 1
    finally:
        if old_mode is None:
            os.environ.pop("FIELDKIT_MODE_DIR", None)
        else:
            os.environ["FIELDKIT_MODE_DIR"] = old_mode
        shutil.rmtree(tmp, ignore_errors=True)
    print("all guard cases pass" if not failed else f"{failed} case(s) FAILED")
    return 1 if failed else 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        import shutil as _sh  # noqa: F401
        global shutil
        shutil = _sh
        return selftest()
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    if "--pre-invocation" in argv or payload.get("hookEventName") == "PreInvocation":
        try:
            _ensure_fieldkit()
            from fieldkit.antigravity import hooks as agy_hooks

            out = agy_hooks.pre_invocation(payload)
        except Exception:  # noqa: BLE001
            out = {}
        sys.stdout.write(json.dumps(out))
        return 0
    try:
        decision, reason = decide(payload)
    except Exception as e:  # noqa: BLE001
        decision, reason = "force_ask", "guard error (%s): asking the owner instead" % e
    log(payload, decision, reason)
    out = {"decision": decision}
    if decision != "allow":
        out["reason"] = reason
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def render_guard_py(root=None) -> str:
    fk_root = _fieldkit_root(root)
    try:
        rel_to_home = fk_root.resolve().is_relative_to(Path.home().resolve())
    except Exception:  # noqa: BLE001
        rel_to_home = True
    cfg_str = "" if rel_to_home else str(fk_root)
    return (
        GUARD_TEMPLATE.replace("__MARKER__", MARKER)
        .replace("__CONFIGURED_ROOT__", repr(cfg_str))
    )


def expected_commands(home=None, python=None) -> tuple[str, str]:
    py = str(python or sys.executable)
    gp = str(guard_py_path(home))
    return f'"{py}" "{gp}"', f'"{py}" "{gp}" --pre-invocation'


def _has_guard_cmd(groups, flag: str = "") -> bool:
    if not isinstance(groups, list):
        return False
    for g in groups:
        if not isinstance(g, dict):
            continue
        hooks = g.get("hooks") if "hooks" in g else [g]
        for h in hooks if isinstance(hooks, list) else []:
            cmd = str((h or {}).get("command") or "")
            if "guard.py" in cmd and (not flag or flag in cmd):
                return True
    return False


def doctor(home=None, python=None, root=None) -> dict:
    """Inspect `<home>/.gemini` -> `config` -> `hooks.json` and `hooks/guard.py`."""
    hj = hooks_json_path(home)
    gp = guard_py_path(home)
    missing, stale = [], []
    try:
        data = json.loads(hj.read_text(encoding="utf-8")) if hj.is_file() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    og = data.get("owner-guard")
    if not isinstance(og, dict) or og.get("enabled") is False:
        missing.append("owner-guard")
    else:
        pre = og.get("PreToolUse")
        if not _has_guard_cmd(pre) or not any(
            isinstance(g, dict) and (g.get("matcher") or "*") == "*" for g in (pre or [])
        ):
            missing.append("PreToolUse")
        if not _has_guard_cmd(og.get("PreInvocation"), "--pre-invocation"):
            missing.append("PreInvocation")
    if not gp.is_file():
        missing.append("guard.py")
    else:
        try:
            gtext = gp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            gtext = ""
        if MARKER not in gtext:
            stale.append(f"guard.py (want {GUARD_VERSION})")
    pre_cmd, inv_cmd = expected_commands(home, python)
    snippet = {
        "owner-guard": {
            "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": pre_cmd, "timeout": 15}]}],
            "PreInvocation": [{"type": "command", "command": inv_cmd, "timeout": 10}],
        }
    }
    return {
        "ok": not missing and not stale,
        "missing": missing,
        "stale": stale,
        "settings": str(hj),
        "guard": str(gp),
        "version": GUARD_VERSION,
        "snippet": snippet,
    }


def apply(home=None, python=None, root=None, keep_dir=None) -> tuple[int, list[str]]:
    """Install or update `hooks.json` and `hooks/guard.py` under `<home>/.gemini` -> `config`, backing up existing files."""
    hj = hooks_json_path(home)
    gp = guard_py_path(home)
    r = doctor(home, python, root)
    if r["ok"]:
        return 0, [f"ANTIGRAVITY HOOKS OK ({hj}): nothing to change"]
    bak = Path(keep_dir) if keep_dir else (Path.home() / "Agents.Work.Trash" / "antigravity-hooks")
    stamp = time.strftime("%y-%m-%d-%H-%M-%S")
    try:
        data = json.loads(hj.read_text(encoding="utf-8")) if hj.is_file() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        if hj.is_file():
            bak.mkdir(parents=True, exist_ok=True)
            shutil.copy2(hj, bak / f"hooks.json.{stamp}")
        if gp.is_file():
            bak.mkdir(parents=True, exist_ok=True)
            shutil.copy2(gp, bak / f"guard.py.{stamp}")
        pre_cmd, inv_cmd = expected_commands(home, python)
        og = data.get("owner-guard")
        if not isinstance(og, dict):
            og = {}
        og.pop("enabled", None)
        og["PreToolUse"] = [{"matcher": "*", "hooks": [{"type": "command", "command": pre_cmd, "timeout": 15}]}]
        og["PreInvocation"] = [{"type": "command", "command": inv_cmd, "timeout": 10}]
        data["owner-guard"] = og
        gp.parent.mkdir(parents=True, exist_ok=True)
        gp.write_text(render_guard_py(root), encoding="utf-8")
        hj.parent.mkdir(parents=True, exist_ok=True)
        hj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return 3, [f"{hj}: could not write Antigravity hooks ({e})"]
    back = doctor(home, python, root)
    done = ", ".join(r["missing"] + r["stale"])
    return (0 if back["ok"] else 3), [
        f"{hj}: updated ({done})",
        "checked: ANTIGRAVITY HOOKS OK" if back["ok"] else f"NOT OK after writing: {back}",
    ]
