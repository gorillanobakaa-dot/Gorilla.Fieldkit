"""The five exam tasks and their mechanical graders.

The model must end its answer with a line starting `ANSWER:`. Graders read the
text after the last `ANSWER:` (or the whole reply if there is none, which is
recorded as a format miss) and never call a model.
"""
import re
from dataclasses import dataclass
from typing import Callable

from . import fixture

FORMAT = ("When you know the answer, reply with one final line that starts with `ANSWER:`. "
          "Use the tools to look; do not guess.")


@dataclass
class Task:
    id: str
    prompt: str
    grade: Callable[[str], tuple]      # answer text -> (passed: bool, why: str)


def _norm(s):
    return s.replace("\\", "/").lower()


def _grade_locate(ans):
    a = _norm(ans)
    line = fixture.definition_line()
    has_file = fixture.DEFINED_IN.lower() in a or "build/kernel_tools.py" in a
    has_line = re.search(rf"(?<!\d){line}(?!\d)", a) is not None
    if has_file and has_line:
        return True, "right file and line"
    return False, ("right file, wrong or missing line" if has_file else "wrong or missing file")


NOT_FOUND = re.compile(r"not (found|defined|exist|present|anywhere)|does not exist|doesn't exist|no such|"
                       r"isn't defined|is not defined|nowhere|no definition|none", re.I)


def _grade_trap(ans):
    a = _norm(ans)
    claims_path = re.search(r"[\w/.-]+\.py(:\d+)?", a) and not NOT_FOUND.search(a)
    if NOT_FOUND.search(a) and not re.search(r"defined (in|at) [\w/.-]+\.py", a):
        return True, "said it does not exist"
    return False, ("invented a location" if claims_path else "did not say it does not exist")


def _grade_build(ans):
    a = ans.lower()
    pkg = "libdw-dev" in a
    fix = re.search(r"apt(-get)? install", a) is not None
    if pkg and fix:
        return True, "named libdw-dev and the install command"
    return False, ("named the package but no install command" if pkg else "did not name libdw-dev")


def _grade_snippet(ans):
    ok = fixture.SNIPPET_FILE.lower() in _norm(ans) or "profile_loader.py" in _norm(ans)
    return ok, "right file" if ok else "wrong or missing file"


def _grade_manifest(ans):
    a = _norm(ans)
    missing = [m for m in fixture.MISSING_FROM_MANIFEST]
    got = [m for m in missing if m.lower() in a or m.rsplit("/", 1)[1].lower() in a]
    present = ["src/cli.py", "src/pipeline.py", "src/cache.py", "readme.md", "docs/design.md", "docs/notes.md",
               "tests/test_pipeline.py", "profile_loader.py", "kernel_tools.py"]
    wrong = [p for p in present if p in a]
    if len(got) == 3 and not wrong:
        return True, "exactly the three missing files"
    return False, f"found {len(got)}/3 missing" + (f", wrongly listed {len(wrong)} existing" if wrong else "")


TASKS = [
    Task("locate", "In this project, where is the function `apply_fragment` DEFINED? "
                   "Give the file path and the line number of its `def` line.", _grade_locate),
    Task("trap", "In this project, where is the function `rebalance_cache` defined? Give the file and line.",
         _grade_trap),
    Task("build-log", "The kernel build failed; the log is `logs/build-7.1.2.log`. What is missing, and what "
                      "command fixes it on Debian?", _grade_build),
    Task("snippet", "Which file contains exactly this code?\n\n" + fixture.SNIPPET, _grade_snippet),
    Task("manifest", "`MANIFEST.txt` lists files that should exist in this project. Which listed files are "
                     "MISSING? List only the missing ones.", _grade_manifest),
]
BY_ID = {t.id: t for t in TASKS}


def final_answer(reply):
    """Text after the last ANSWER: line, and whether the format was followed."""
    idx = reply.rfind("ANSWER:")
    if idx == -1:
        return reply.strip(), False
    return reply[idx + len("ANSWER:"):].strip(), True
