"""Per-session multi-agent launch choice (owner order 2026-11-25, P39 hook enforcement).

Whenever an interactive AI session wants to start another coding session (`fieldkit peers link ask` over the cable or
`fieldkit task start` locally/remotely), it MUST NOT default to Claude or guess an agent on its own:
1. The PreToolUse hook blocks the launch until the owner has been asked (AskUserQuestion / ask_question) to pick from
   the available agents (Claude, Antigravity IDE, agy, Gemini CLI, OpenAI Codex, Gorilla OpenCode), provider, model,
   and reasoning level (`fieldkit models menu` / `fieldkit models menu --device peer`).
2. The owner's answer is recorded for this session via:
   `python -m fieldkit route choose --session-agent AGENT [--session-provider PROV] --session-model MODEL --session-thinking LEVEL [--session SID]`
3. Subsequent `peers link ask` / `task start` commands in this session are allowed only when their `--agent`, `--model`,
   and `--thinking` match what the owner chose. Headless autopilot runs (`FIELDKIT_AUTOPILOT=1` / `FIELDKIT_JOB_DIR`)
   continue to use `choices.json` (`fieldkit models remember`) without blocking.
"""
import json
import os
import re
import shlex
import time

from . import sessionmode

LAUNCH_RE = re.compile(
    r"(?:^|[;&|]\s*|\s)"
    r"(?:fieldkit(?:\.exe)?|python[\d.]*(?:\.exe)?\s+-m\s+fieldkit)\s+"
    r"(?:(peers\s+link\s+ask)|(task\s+start))\b",
    re.I,
)


def _path(sid):
    clean = sessionmode.SAFE.sub("_", str(sid or "unknown"))[:80]
    return sessionmode.mode_dir() / f"{clean}.agent.json"


def current(sid):
    """-> the owner's recorded {agent, provider, model, thinking, at} for session `sid`, or None."""
    try:
        d = json.loads(_path(sid).read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("agent") and d.get("model") and d.get("thinking"):
            return d
    except (OSError, ValueError, TypeError):
        pass
    return None


def write(sid, agent, model, thinking, provider=None):
    """Validate `(agent, provider, model, thinking)` against choice.yaml + spend/quota guards and record for `sid`."""
    from . import choice, models

    chk = models.validate_agent_choice(
        agent=agent, model=model, thinking=thinking, require_explicit=True, role="worker", origin="route choose"
    )
    if not chk["ok"]:
        raise ValueError(chk["why"])
    cdata = choice.load()
    ainfo = (cdata.get("agents") or {}).get(chk["agent"]) or {}
    provs = list(ainfo.get("providers") or [])
    prov = (provider or "").strip().lower()
    if prov and prov not in provs:
        raise ValueError(f"{chk['agent']} does not drive provider {prov!r}; valid: {', '.join(provs)}")
    if not prov:
        prov = provs[0] if len(provs) == 1 else ""
    rec = {
        "agent": chk["agent"],
        "provider": prov,
        "model": chk["model"],
        "thinking": chk["thinking"],
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    p = _path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def _extract_flag(tokens, *names):
    want = {n.lower() for n in names}
    for i, tok in enumerate(tokens):
        low = tok.lower()
        if low in want and i + 1 < len(tokens):
            return tokens[i + 1]
        for n in want:
            if low.startswith(n + "="):
                return tok.split("=", 1)[1]
    return None


def _tokens(seg):
    try:
        return shlex.split(seg, posix=os.name != "nt")
    except ValueError:
        return seg.split()


def command_refusal(command, sid="unknown"):
    """-> refusal message when `command` launches `peers link ask` or `task start` without the owner's confirmed agent
    choice for `sid` (or with mismatched `--agent`/`--model`/`--thinking`), else None."""
    if not command or os.environ.get("FIELDKIT_AUTOPILOT") == "1" or os.environ.get("FIELDKIT_JOB_DIR"):
        return None
    for seg in re.split(r";|&&|\|\||\r?\n", command):
        m = LAUNCH_RE.search(seg)
        if not m:
            continue
        toks = [t.strip("\"'") for t in _tokens(seg)]
        low_toks = {t.lower() for t in toks}
        if "-h" in low_toks or "--help" in low_toks:
            continue
        if m.group(2) and "--run" in low_toks:  # `fieldkit task start --run` is a plain deterministic command, no AI
            continue
        rec = current(sid)
        if rec is None:
            return (
                "ASK THE OWNER WHICH CODING AGENT, PROVIDER AND MODEL TO START (P39 multi-agent rule): "
                "never default to Claude or pick an agent on your own. "
                "1) Ask the owner first (AskUserQuestion / ask_question) which coding agent to start — "
                "Claude (`claude`), Antigravity IDE (`antigravity`), Antigravity CLI (`agy`), Gemini CLI (`gemini`), "
                "OpenAI Codex (`codex`), or Gorilla OpenCode (`gorilla-opencode`) — and which provider/model "
                "(`fieldkit models menu` or `fieldkit models menu --device peer` shows what works right now). "
                "2) Record the owner's answer: "
                f"`python -m fieldkit route choose --session-agent <AGENT> --session-provider <PROVIDER> "
                f"--session-model <MODEL> --session-thinking <LEVEL> --session {sid}`. "
                "3) Run `peers link ask` / `task start` with that exact `--agent`, `--model`, and `--thinking`."
            )
        asked_agent = (_extract_flag(toks, "--agent") or "").strip().lower()
        asked_model = (_extract_flag(toks, "--model") or "").strip()
        asked_think = (_extract_flag(toks, "--thinking") or "").strip().lower()
        if not asked_agent or not asked_model or not asked_think:
            return (
                f"SESSION AGENT CHOICE RECORDED ({rec['agent']} / {rec['model']} / {rec['thinking']}): "
                f"pass explicit `--agent {rec['agent']} --model {rec['model']} --thinking {rec['thinking']}`."
            )
        if (
            asked_agent != rec["agent"].lower()
            or asked_model.lower() != rec["model"].lower()
            or asked_think != rec["thinking"].lower()
        ):
            return (
                f"SESSION AGENT MISMATCH: the owner chose agent={rec['agent']!r}, model={rec['model']!r}, "
                f"thinking={rec['thinking']!r} for this session, but the command asked for "
                f"agent={asked_agent!r}, model={asked_model!r}, thinking={asked_think!r}. "
                "Use the owner's chosen agent/model, or ask the owner before switching."
            )
    return None


def call_refusal(call):
    """PreToolUse check for shell calls in `toolrouter.verdict`."""
    tool = str((call or {}).get("tool_name") or "").strip().lower()
    if tool not in ("bash", "powershell", "exec_command", "run_shell_command", "run_command"):
        return None
    ti = (call or {}).get("tool_input") or {}
    cmd = ti.get("command") or ti.get("CommandLine") or ""
    return command_refusal(str(cmd), sessionmode.session_id(call))
