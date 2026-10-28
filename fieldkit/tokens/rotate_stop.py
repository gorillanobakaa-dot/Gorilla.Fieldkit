"""R1 (docs/HARNESS-PLAN.md): past the ROTATE line the session may only hand over.

Runs inside the tool router's PreToolUse hook on every tool call, so it imports nothing heavier than the standard
library (the hook process has a 100 ms budget, tests/test_zero_command.py). The level comes from the mark the prompt
hook writes (fieldkit/tokens/rotate.py: hook_line); nothing is measured here.
"""
import json
import os
import re
from pathlib import Path

# What still runs past ROTATE: committing, pushing, the handover itself, reading state, and writing the handover
# note or the Board row.
HANDOVER_CMD = re.compile(r"\bfieldkit\s+(rotate|ship|task|privacy|testcost|zero|inbox|sessions)\b"
                          r"|\bgit\s+(status|add|commit|log|diff|show|rev-parse|branch|worktree\s+list)\b"
                          r"|\bgh\s+pr\s+(create|view|checks)\b")
HANDOVER_FILES = ("PLAN.md", "HANDOFF.md")


def _said_dir():
    root = os.environ.get("FIELDKIT_ROTATE_DIR") or (Path(__file__).resolve().parents[2] / "state" / "rotate")
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
