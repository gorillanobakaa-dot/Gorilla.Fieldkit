"""answer - the model writes its change as short line operations; the harness applies them.

Live run 3 (2026-09-30): given edit and submit tools, Gemma (gemma-4-e2b) made no tool call
and wrote reports of edits it never made. Live runs 4 and 5: asked to rewrite a whole block
of lines, it dropped Mozilla's lines, put old ones back and duplicated others - a 4.6B model
cannot copy 30 lines back faithfully. So the model never copies lines. It names them:

    DELETE 744
    DELETE 745-748
    CHANGE 750: pref("browser.x", false);
    INSERT AFTER 760: pref("browser.y", 1);

Line numbers are the ones shown in the job. The harness applies the operations from the
bottom up, so every number keeps meaning what the model saw. Enforced, so a sloppy answer
is refused instead of guessed at:
  - at least one operation; every number inside the file;
  - no line touched by two operations;
  - text outside the operation lines is ignored (the model may think aloud before them).
"""
import re

OP = re.compile(r"^\s*(?:(DELETE)\s+(\d+)(?:\s*-\s*(\d+))?|(CHANGE)\s+(\d+)\s*:\s?(.*)|(INSERT AFTER)\s+(\d+)\s*:\s?(.*))\s*$")

INSTRUCTIONS = """Answer with line operations. You MUST wrap your operations in a ``` code block.

DELETE <n>                  remove line n
DELETE <n>-<m>              remove lines n to m
CHANGE <n>: <new text>      replace line n with the new text
INSERT AFTER <n>: <text>    add a new line after line n

If your new text spans multiple lines, just write the extra lines normally below the CHANGE or INSERT AFTER line.
Use the line numbers shown above. Do not copy lines you are not changing.
"""


class BadAnswer(ValueError):
    pass


def parse(text, n_lines):
    import re
    # Extract code block if present to avoid swallowing conversational text
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL)
    if blocks:
        text = blocks[0]
        
    ops = []
    current_op = None
    
    for raw in (text or "").splitlines():
        line = raw.strip("\r\n")  # Keep indentation, strip newlines
        # OP match ignores leading/trailing whitespace around the command itself
        m = OP.match(line)
        if m:
            if current_op:
                ops.append(current_op)
            if m.group(1):
                lo = int(m.group(2))
                hi = int(m.group(3) or lo)
                current_op = ("delete", lo, hi, None)
            elif m.group(4):
                current_op = ("change", int(m.group(5)), int(m.group(5)), m.group(6))
            else:
                current_op = ("insert", int(m.group(8)), int(m.group(8)), m.group(9))
        else:
            if current_op and current_op[0] in ("change", "insert"):
                kind, lo, hi, txt = current_op
                # Only append if the line isn't just an empty string at the very start
                # (to avoid leading newlines if m.group(6)/(9) was empty)
                txt = (txt + "\n" + line) if txt else line
                current_op = (kind, lo, hi, txt)
            elif not current_op and line.strip() and not line.strip().startswith("`"):
                # Non-empty line before any operation is found
                pass

    if current_op:
        ops.append(current_op)

    if not ops:
        raise BadAnswer("no operations found inside a code block (DELETE n / DELETE n-m / CHANGE n: text / INSERT AFTER n: text)")
    
    touched = {}
    for kind, lo, hi, _ in ops:
        if not (1 <= lo <= hi <= n_lines) and not (kind == "insert" and lo == 0):
            raise BadAnswer(f"{kind.upper()} {lo}{'-' + str(hi) if hi != lo else ''} is not inside the file "
                            f"(it has {n_lines} lines)")
        if kind == "insert":
            continue
        for n in range(lo, hi + 1):
            if n in touched:
                raise BadAnswer(f"line {n} is named by two operations; name each line once")
            touched[n] = kind
    return ops


def apply(path, text):
    """Apply the operations to the file at `path`. -> (count, summary). Raises BadAnswer."""
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        raw = f.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(nl)
    trailing = lines[-1] == ""
    if trailing:
        lines = lines[:-1]
    ops = parse(text, len(lines))
    # bottom-up, inserts after changes at the same line, so every number means what the model saw
    for kind, lo, hi, body in sorted(ops, key=lambda o: (o[1], o[0] == "insert"), reverse=True):
        if kind == "delete":
            del lines[lo - 1:hi]
        elif kind == "change":
            lines[lo - 1] = body
        else:
            lines[lo:lo] = [body]
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(nl.join(lines) + (nl if trailing else ""))
    return len(ops), "; ".join(f"{k} {lo}{'-' + str(hi) if hi != lo else ''}" for k, lo, hi, _ in ops)


# ── question form (run 6 countermeasure) ────────────────────────────────
# Gemma (4.6B) cannot compose line operations: she duplicates lines, echoes
# the diff, and gets the arithmetic wrong. So the harness identifies which
# lines must go and asks the model about the uncertain ones only:
#   745 REMOVE
#   749 KEEP
# One answer per line, nothing else accepted.

Q_OP = re.compile(r"^\s*<?(\d+)>?\s+(REMOVE|KEEP)\s*$", re.IGNORECASE)

Q_INSTRUCTIONS = """Answer each line below with its number and REMOVE or KEEP, one per line:

123 REMOVE     this line should be deleted
123 KEEP       this line should stay

Answer every line exactly once. Do not add, change, or reorder lines. Do not
use any other word. The harness applies your decisions and checks the file."""


def parse_questions(text, asked):
    """Parse REMOVE/KEEP answers for the given set of asked line numbers.

    Returns [(line_no, 'remove'|'keep'), ...] sorted by line number.
    Raises BadAnswer if any asked line is missing, answered twice, or an
    unknown line appears.
    """
    answered = {}
    for raw in (text or "").splitlines():
        line = raw.strip().strip("`").strip()
        m = Q_OP.match(line)
        if not m:
            continue
        n = int(m.group(1))
        verdict = m.group(2).lower()
        if n not in asked:
            raise BadAnswer(f"line {n} is not a question (asked: {sorted(asked)})")
        if n in answered:
            raise BadAnswer(f"line {n} answered twice")
        answered[n] = verdict
    if not answered:
        raise BadAnswer("no answers found (expected: 123 REMOVE or 123 KEEP)")
    missing = sorted(asked - set(answered))
    if missing:
        raise BadAnswer(f"line(s) not answered: {missing}")
    return sorted(answered.items())

