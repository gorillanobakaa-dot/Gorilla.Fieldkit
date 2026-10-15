# Version: 1.0.0 · updated 26-10-16-01-30
"""Port-and-teach lessons that have BECOME harness rules: each one names the tool that now does it and the test that
proves it. `build-harness lessons <task>` lists as waiting only the lessons no rule here covers, so the count at
session start goes down when a lesson is turned into a tool, and only then (2026-10-16: 12 lessons sat in the brief
with no way to close them; the owner asked that every experience become a deterministic tool).

A rule is data checked by tests/test_buildh_lessonrules.py: its `test` file must exist and its pattern must match
the lesson it claims. A new lesson: build the tool and its test, then add the rule here.
"""
import re

RULES = [
    {"match": r"renamed with a 2 suffix|put the patch's wording on the new id",
     "tool": "build-harness rebase-set: rebase.fluent_moves finds Gorilla wording a superseded patch gave a message "
             "whose id upstream renamed (tabs3 -> tabs4) and names the live successor",
     "test": "tests/test_buildh_rebase.py"},
    {"match": r"before removing a Fluent message, find what still uses its id",
     "tool": "build-harness fluent-check: every removed message id is no longer used by visible UI (fluent.orphans)",
     "test": "tests/test_buildh_fluent_orphans.py"},
    {"match": r"hunk that failed to apply must always become a step",
     "tool": "build-harness rebase-set: replay lists every patch that fails on pristine; carried() checks each "
             "superseded patch line by line (SUPERSEDED.md), so a failed hunk can no longer be lost silently",
     "test": "tests/test_buildh_rebase.py"},
    {"match": r"upstream moves a declaration the removed block used|upstream adds a clause to a condition",
     "tool": "build-harness rebase-set: carried() reports PARTLY with the exact lines (an added line missing, a removed "
             "line still there), which is what a moved declaration or a new clause looks like; review those",
     "test": "tests/test_buildh_rebase.py"},
    {"match": r"GORILLA REPAIR .* grafted verbatim|messages are then upstream's own and must stay",
     "tool": "build-harness fluent-check (a removed message still in use is an orphan) and rebase-set carried() "
             "(ALREADY-UPSTREAM when upstream already has the lines)",
     "test": "tests/test_buildh_fluent_orphans.py"},
    {"match": r"proposal saved before later steps edited the same file can conflict",
     "tool": "build-harness accept: a proposal is applied three-way and, when it still conflicts, its step goes back "
             "to failed for a fresh port on the current tree (task.accept)",
     "test": "tests/test_buildh_task.py"},
]


def rule_for(lesson):
    return next((r for r in RULES if re.search(r["match"], lesson or "", re.I)), None)


def split(rows):
    """-> (waiting, ruled): lesson rows without a rule, and (row, rule) pairs."""
    waiting, ruled = [], []
    for r in rows:
        rule = rule_for(r.get("lesson"))
        (ruled.append((r, rule)) if rule else waiting.append(r))
    return waiting, ruled
