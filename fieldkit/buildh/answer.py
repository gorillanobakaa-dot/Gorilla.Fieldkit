"""answer - the model writes its change as plain text in a fixed form; the harness applies it.

Found in live run 3 (2026-09-30): given edit and submit tools, Gemma (gemma-4-e2b) made
no tool call at all in two runs and wrote detailed reports of edits it never made
("I modified lines 266 through 284 ..."). A model that small is reliable at producing
text, not at driving tools. So the harness asks for text, and does the mechanical part:

    FROM LINE: 266
    TO LINE: 284
    NEW TEXT:
    <<<
    ...the lines that replace 266-284...
    >>>

Rules the parser enforces, so a sloppy answer is refused instead of guessed at:
  - exactly one FROM/TO pair, whole numbers, FROM <= TO, inside the file;
  - one NEW TEXT block between <<< and >>> (it may be empty: that deletes the lines);
  - anything outside the form is ignored (the model may think aloud before it).
"""
import re

FORM = re.compile(r"FROM LINE:\s*(\d+)\s*\n\s*TO LINE:\s*(\d+)\s*\n\s*NEW TEXT:\s*\n<<<\n?(.*?)\n?>>>", re.S)

INSTRUCTIONS = """Answer in exactly this form and nothing after it:

FROM LINE: <first line number to replace>
TO LINE: <last line number to replace>
NEW TEXT:
<<<
<the lines that replace them, exactly as they must appear in the file>
>>>

Use the line numbers shown above. Keep every line you do not need to change.
The harness applies your text to the file and checks it; your words are not checked."""


class BadAnswer(ValueError):
    pass


def parse(text, n_lines):
    found = FORM.findall(text or "")
    if not found:
        raise BadAnswer("no answer in the required form (FROM LINE / TO LINE / NEW TEXT between <<< and >>>)")
    if len(found) > 1:
        raise BadAnswer(f"{len(found)} answers given; give exactly one")
    a, b, body = found[0]
    lo, hi = int(a), int(b)
    if not 1 <= lo <= hi <= n_lines:
        raise BadAnswer(f"lines {lo}-{hi} are not inside the file (it has {n_lines} lines)")
    return lo, hi, body.split("\n") if body else []


def apply(path, text):
    """Apply one answer to the file at `path`. -> (from, to, lines written). Raises BadAnswer."""
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        raw = f.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(nl)
    trailing = lines[-1] == ""
    if trailing:
        lines = lines[:-1]
    lo, hi, new = parse(text, len(lines))
    out = lines[:lo - 1] + new + lines[hi:]
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(nl.join(out) + (nl if trailing else ""))
    return lo, hi, len(new)
