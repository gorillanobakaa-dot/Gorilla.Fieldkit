# Version: 1.0.0 · updated 26-10-16-01-30
"""A lesson leaves the waiting list only when a rule names the tool that does it and a test that exists."""
from pathlib import Path

from fieldkit.buildh import lessonrules as lr

# the 2026-10-14/15 lessons of the Linux 158 port, as recorded
LESSONS = [
    "a hunk that removes messages from a 'GORILLA REPAIR ... grafted verbatim' block is obsolete when the target has no such block; the messages are then upstream's own and must stay",
    "when upstream moves a declaration the removed block used out of it (158 moved PlanarYCbCrImage* image above, for the colour metadata), keep the declaration and port the removal by the block's comment and structure",
    "a patch hunk that failed to apply must always become a step; here hunk 2 (AV1) of DefaultCodecPreferences.cpp left no step and would have been lost silently",
    "when a Fluent message is renamed with a 2 suffix, put the patch's wording on the new id (fluent.transfer could do this without a porter)",
    "before removing a Fluent message, find what still uses its id (or its renamed successor) in the new tree; a removal whose users remain is a broken menu, not a cut",
    "when upstream adds a clause to a condition the hunk rewrites, remove only what the hunk removes and keep the new clause",
    "a proposal saved before later steps edited the same file can conflict at acceptance; re-port it on the current tree",
]


def test_every_rule_names_a_test_that_exists():
    for r in lr.RULES:
        assert Path(r["test"]).is_file(), r["test"]
        assert r["tool"].startswith("build-harness "), r["tool"]


def test_the_158_lessons_are_all_ruled_and_a_new_one_waits():
    waiting, ruled = lr.split([{"lesson": x} for x in LESSONS] + [{"lesson": "something nobody has built yet"}])
    assert len(ruled) == len(LESSONS) and [w["lesson"] for w in waiting] == ["something nobody has built yet"]
