"""R1 (docs/HARNESS-PLAN.md): past the ROTATE line the session may only hand over.

Runs inside the tool router's PreToolUse hook on every tool call, so it imports nothing heavier than the standard
library (the hook process has a 100 ms budget, tests/test_zero_command.py). The level comes from the mark the prompt
hook writes (fieldkit/tokens/rotate.py: hook_line); nothing is measured here.
"""
import json
import os
import re
from pathlib import Path
from ..core.settings import state_root as _state_root

# What still runs past ROTATE: committing, pushing, the handover itself, reading state, and writing the handover
# note or the Board row.
HANDOVER_CMD = re.compile(r"\bfieldkit\s+(rotate|ship|task|privacy|testcost|zero|inbox|sessions)\b"
                          r"|\bgit\s+(status|add|commit|log|diff|show|rev-parse|branch|worktree\s+list)\b"
                          r"|\bgh\s+pr\s+(create|view|checks)\b")
HANDOVER_FILES = ("PLAN.md", "HANDOFF.md")


def zero_session(stem):
    """True when this transcript stem is the session that is Zero now (NET.yaml zero.session). Owner 2026-11-10: "Zero
    never rotates" - the exemption follows the seat, not one transcript id. Read with a regex: the hook loads no YAML
    parser (100 ms budget). FIELDKIT_COORDINATION_DIR overrides the folder. Never raises."""
    try:
        d = os.environ.get("FIELDKIT_COORDINATION_DIR") or (_state_root(Path(__file__).resolve().parents[2]) / "state" / "coordination")
        m = re.search(r"(?ms)^zero:\s*\n(?:[ \t]+.*\n)*?[ \t]+session:\s*['\"]?([0-9a-fA-F-]+)", (Path(d) / "NET.yaml").read_text(encoding="utf-8"))
        return bool(stem) and m is not None and m.group(1) == stem
    except Exception:  # noqa: BLE001 - no net, unreadable file: not Zero
        return False


def permanent_zero(stem):
    """True when this transcript stem is exempt from rotation: the session that is Zero now (zero_session), or one the
    owner listed (2026-11-09, rules/permanent_zero.json; FIELDKIT_PERMANENT_ZERO overrides the path). Never raises."""
    if zero_session(stem):
        return True
    try:
        f = os.environ.get("FIELDKIT_PERMANENT_ZERO") or (Path(__file__).resolve().parents[2] / "rules" / "permanent_zero.json")
        return bool(stem) and stem in (json.loads(Path(f).read_text(encoding="utf-8")).get("transcripts") or [])
    except Exception:  # noqa: BLE001 - unreadable list: nobody is exempt
        return False


def _said_dir():
    root = os.environ.get("FIELDKIT_ROTATE_DIR") or (_state_root(Path(__file__).resolve().parents[2]) / "state" / "rotate")
    return Path(root) / "said"


def stop_refusal(call):
    """PreToolUse: -> the refusal text when this session is over the ROTATE line and the call is not part of handing
    over, else None. Never raises (fails open)."""
    try:
        if os.environ.get("FIELDKIT_JOB_DIR") or os.environ.get("FIELDKIT_ROTATE") == "off":
            return None                                   # inside an autopilot job the budget hook enforces it
        tp = call.get("transcript_path")
        if not tp:
            return None
        if permanent_zero(Path(tp).stem):
            return None                                   # owner 2026-11-09: Session 0 never rotates
        said = json.loads((_said_dir() / (Path(tp).stem + ".json")).read_text(encoding="utf-8"))
        if said.get("level") != "ROTATE":
            return None
        tool = (call.get("tool_name") or "").lower()
        ti = call.get("tool_input") or {}
        if tool in ("bash", "powershell"):
            if HANDOVER_CMD.search(ti.get("command") or ""):
                return None
        elif tool in ("edit", "write", "multiedit", "notebookedit"):
            if Path(ti.get("file_path") or ti.get("notebook_path") or "").name in HANDOVER_FILES:
                return None
        else:
            return None
        return ("SESSION ROTATION STOP (fieldkit rotate, R1): this session is over the ROTATE line, so it may only "
                "hand over: commit, push, write HANDOFF.md or the Board row, then `python -m fieldkit rotate handover "
                "...` and start the successor. This call is not part of that.")
    except Exception:  # noqa: BLE001 - no mark, unreadable mark: fail open
        return None
