"""Circuit breakers (L2 in docs/HARNESS-PLAN.md; the owner's spec of 2026-10-22, Long Horizon DESIGN.md section 3).

First breaker: the same tool call repeated. The same tool with the same input LIMIT times in a row is a loop: the
call is refused until the session does something different. Runs inside the tool router's PreToolUse hook on every
tool call, so it imports only the standard library (the hook process has a 100 ms budget).

State: <FIELDKIT_ROTATE_DIR or state/rotate>/breaker/<transcript stem>.json = {"last": hash, "n": count}.
FIELDKIT_BREAKER=off switches it off.
"""
import hashlib
import json
import os
from pathlib import Path
from ..core.settings import state_root as _state_root

LIMIT = 5


def _dir():
    root = os.environ.get("FIELDKIT_ROTATE_DIR") or (_state_root(Path(__file__).resolve().parents[2]) / "state" / "rotate")
    return Path(root) / "breaker"


def repeat_refusal(call):
    """PreToolUse: -> the refusal text when this call is the LIMIT-th identical call in a row, else None.
    Counts every call it sees; never raises (fails open)."""
    try:
        if os.environ.get("FIELDKIT_BREAKER") == "off":
            return None
        tp = call.get("transcript_path")
        if not tp:
            return None
        key = json.dumps([call.get("tool_name") or "", call.get("tool_input") or {}], sort_keys=True, default=str)
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        f = _dir() / (Path(tp).stem + ".json")
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        n = st.get("n", 0) + 1 if st.get("last") == h else 1
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"last": h, "n": n}), encoding="utf-8")
        if n < LIMIT:
            return None
        return (f"CIRCUIT BREAKER (L2, repeated call): this exact {call.get('tool_name')} call has now been made {n} "
                "times in a row. It is a loop: stop repeating it. Write what you know into HANDOFF.md or the Board "
                "row, then try a different approach or hand the job over. A wait goes through `fieldkit wait`, one "
                "wake, never polling.")
    except Exception:  # noqa: BLE001 - a breaker must never break the session: fail open
        return None
