"""worker - a separate, minimal Gorilla OpenCode profile for harness jobs.

Found in live run 2 (2026-09-30), with Gemma on LM Studio:
  - the owner's profile sends ~9,000 tokens of system prompt and tool list with every job,
    which a local model reads at ~58 tokens/s: 2.5 minutes before it starts the job;
  - the "Unconstrained" connection profile (first byte within 60 s, 45 s of silence, 5
    retries) treats a slow LOCAL model as a dead network link: Gorilla OpenCode cut the
    request and started again 6 times in 23 minutes, so the answer could never finish.

The worker profile lives in its own folder (XDG_CONFIG_HOME), so the owner's everyday
settings are never touched. It has only the edit and view tools plus Fieldkit's tools:
no shell (so no git and no installs are even possible), no web, no sub-agents. The driver
also sets local-model time limits for the run.

    fieldkit build-harness worker-profile      (re)write the profile and show where it is
"""
import json
import os
import shutil
from pathlib import Path

from ..core import settings

KEEP_TOOLS = {"tool.edit", "tool.view"}
KEEP_PROMPT = {"prompt.section.preamble", "prompt.section.scope", "prompt.section.honesty",
               "prompt.section.tools", "prompt.section.output"}
LOCAL_LIMITS = {"GORILLA_OPENCODE_FIRST_BYTE_TIMEOUT": "20m",     # reading a long prompt on a CPU takes minutes
                "GORILLA_OPENCODE_STREAM_STALL_TIMEOUT": "10m"}   # a thinking model may pause between chunks


def owner_config_dir():
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) / "gorilla-opencode" if xdg else Path.home() / ".config" / "gorilla-opencode"


def profile_root():
    from . import vault
    return vault.root().parent / "Build.Work" / "worker-config"


def write_profile(model=None):
    """-> the XDG_CONFIG_HOME to use. Rebuilt from the owner's settings each time; secrets are not copied."""
    src = owner_config_dir()
    owner = json.loads((src / "config.json").read_text(encoding="utf-8"))
    loadout = json.loads((src / "loadout.json").read_text(encoding="utf-8")) if (src / "loadout.json").is_file() else {}
    root = profile_root()
    d = root / "gorilla-opencode"
    d.mkdir(parents=True, exist_ok=True)
    agents = owner.get("agents") or {}
    if model:
        agents = {k: {**v, "model": model} for k, v in agents.items()}
    exe = shutil.which("fieldkit") or "fieldkit"
    cfg = {"localEndpoints": owner.get("localEndpoints") or [], "agents": agents,
           "extrasChoiceMade": True, "tui": owner.get("tui") or {},
           "mcpServers": {"fieldkit": {"type": "stdio", "command": exe, "args": ["mcp"],
                                        "env": ["FIELDKIT_MCP_TOOLS=build_harness_submit,build_harness_status"]}}}
    (d / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    worker_loadout = {k: (k in KEEP_TOOLS or k in KEEP_PROMPT) for k in loadout} or \
        {**{k: True for k in KEEP_TOOLS | KEEP_PROMPT}}
    (d / "loadout.json").write_text(json.dumps(worker_loadout, indent=1), encoding="utf-8")
    (d / "connection.json").write_text(json.dumps({"profile": "unconstrained", "chosen": True, "samples": []}),
                                       encoding="utf-8")
    return root


def environment(root):
    return {"XDG_CONFIG_HOME": str(root), **LOCAL_LIMITS}
