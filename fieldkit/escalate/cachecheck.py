"""The 1h prompt-cache settings every session depends on (owner order 2026-11-23).

A session started with a 5-minute cache pays for the whole conversation again after every pause. The settings file
must carry promptCacheTtl and subagentPromptCacheTtl = "1h", and the environment (the settings `env` block or the
process) the three variables below. `problems()` returns one line per gap; empty = fine.
"""
import json
import os
from pathlib import Path

TTL_KEYS = ("promptCacheTtl", "subagentPromptCacheTtl")
ENV_VARS = {"CLAUDE_CODE_PROMPT_CACHE_TTL": "1h", "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h",
            "ENABLE_PROMPT_CACHING_1H": "1"}


def settings_path():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"


def problems(path=None, environ=None):
    p = Path(path) if path else settings_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as e:
        return [f"{p}: cannot be read as a settings file ({type(e).__name__})"]
    out = [f"{p}: {k} is {data.get(k)!r}, must be '1h'" for k in TTL_KEYS if data.get(k) != "1h"]
    env = dict(data.get("env") or {})
    env.update(environ if environ is not None else os.environ)
    out += [f"{p}: env {k} is {env.get(k)!r}, must be {v!r}" for k, v in ENV_VARS.items() if str(env.get(k)) != v]
    return out


def env_for_children(base=None):
    """The three variables every session escalate starts must inherit (set even if the parent lost them)."""
    env = dict(base or {})
    env.update(ENV_VARS)
    return env
