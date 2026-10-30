# Version: 1.0.0 · updated 26-10-28-22-00
"""Lint a brief before a session or a headless run is started from it (HARNESS-PLAN O3: 58 of 75 sessions were
started by another session, $703). A brief names the plan item it serves and stays short: the reader starts with
nothing but the brief, and every token of it is re-sent on every call.

    no plan id      REFUSED by `fieldkit task start` (a job that serves no plan item is not started)
    over N tokens   a finding (MAX_TOKENS, estimated at 4 characters per token); the start records it as a warning
"""
import re
from pathlib import Path

MAX_TOKENS = 1500
CHARS_PER_TOKEN = 4
FALLBACK_ID = re.compile(r"\b[A-Z][0-9]{1,2}\b")          # O3, L3, C4: used only when the plan file is not there
ROW_ID = re.compile(r"^\|\s*([A-Z][0-9]{1,2})\s*\|", re.M)


def tokens(text):
    return -(-len(text) // CHARS_PER_TOKEN)


def plan_ids(root):
    """-> (ids of the plan rows, where they came from). From docs/HARNESS-PLAN.md under root; none when it is absent."""
    p = Path(root) / "docs" / "HARNESS-PLAN.md"
    try:
        return set(ROW_ID.findall(p.read_text(encoding="utf-8", errors="replace"))), str(p)
    except OSError:
        return set(), ""


def plan_id_of(text, root="."):
    """-> the plan id the brief names, or ''. A row id of the plan file when the file is there, else any id-shaped word."""
    ids, _ = plan_ids(root)
    for m in FALLBACK_ID.findall(text):
        if not ids or m in ids:
            return m
    return ""


def lint(text, root=".", plan=None):
    """-> list of (severity, message); severity is 'refuse' or 'warn'. `plan` is an id given outside the text."""
    out = []
    ids, src = plan_ids(root)
    if plan:
        if ids and plan not in ids:
            out.append(("refuse", f"plan id {plan} is not a row of {src}"))
    elif not plan_id_of(text, root):
        out.append(("refuse", "the brief names no plan id (a row of docs/HARNESS-PLAN.md, e.g. O3); name the item "
                              "it serves, or pass --plan ID"))
    n = tokens(text)
    if n > MAX_TOKENS:
        out.append(("warn", f"the brief is about {n} tokens (over {MAX_TOKENS}): point at files and plan rows "
                            "instead of retyping them"))
    return out
