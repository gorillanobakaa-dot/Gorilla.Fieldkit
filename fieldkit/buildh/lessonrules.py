# Version: 2.0.0 · updated 26-11-16-02-00
"""Port lessons that have BECOME rules: a harness tool (kind tool, with the test that proves it), a written rule
every port step is given (kind port-rule), or a recorded reason it is not a rule (kind no-rule). The data is
fieldkit/firefox/lesson_rules.yaml (P38, 2026-11-16: data before code; 160 had 109 lessons and 0 rules).
`build-harness lessons <task>` lists as waiting only the lessons no entry covers; the migration's S9.lessons gate
does not close a migration while one waits.
"""
import re
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent.parent / "firefox" / "lesson_rules.yaml"
KINDS = ("tool", "port-rule", "no-rule")


def load(path=DATA):
    rows = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    for r in rows:
        if r.get("kind") not in KINDS:
            raise ValueError(f"{path}: {r.get('id')}: kind must be one of {KINDS}")
        need = {"tool": ("tool", "test"), "port-rule": ("rule",), "no-rule": ("because",)}[r["kind"]]
        missing = [k for k in ("id", "match") + need if not str(r.get(k) or "").strip()]
        if missing:
            raise ValueError(f"{path}: {r.get('id')}: missing {missing}")
    return rows


ENTRIES = load()
RULES = [r for r in ENTRIES if r["kind"] == "tool"]          # the harness tools (each with its test)
PORT_RULES = [r for r in ENTRIES if r["kind"] == "port-rule"]


def rule_for(lesson, entries=None):
    return next((r for r in (entries or ENTRIES) if re.search(r["match"], lesson or "", re.I)), None)


def split(rows, entries=None):
    """-> (waiting, ruled): lesson rows without an entry, and (row, entry) pairs."""
    waiting, ruled = [], []
    for r in rows:
        rule = rule_for(r.get("lesson"), entries)
        (ruled.append((r, rule)) if rule else waiting.append(r))
    return waiting, ruled


def port_rules_text():
    """The lines a port step is given under PORT RULES (task.next)."""
    return [f"- {r['rule']}" for r in PORT_RULES]
