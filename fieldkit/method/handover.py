"""Report fixes to someone else's tool as a handover (docs/METHODS.md #13).

    fieldkit handover FIXES.yaml [--target FOLDER] [--out handover.md]

The recipient (often another AI, on another machine) cannot see your session. A handover gives them, for every
fix: the file, the function, the exact text before and after, why, what the user sees today and after, and a
regression test - plus the proof (their own suite before and after, from `fieldkit regress`) and what is NOT a bug.

FIXES.yaml:
    title: Three fixes for the reference checker
    to: Claude on the other machine          # who reads it
    regress: [before, after]                 # labels recorded with fieldkit regress (optional)
    fixes:
      - file: reference_auditor.py
        function: _split_author_year
        before: 'm = re.search(r"\\b(" + YEAR_OR_ND + r")\\b", chunk, flags=re.I)'
        after:  'm = re.search(r"(?<!\\w)(" + YEAR_OR_ND + r")(?!\\w)", chunk, flags=re.I)'
        why: \\b needs a word character on one side; "n.d." ends in a full stop
        today: (Health Board, n.d.) is never found
        after_fix: found and matched to its entry
        test: |
          def test_nd(): ...
    not_bugs: [the Windows path tests fail on Linux by design]

With --target, each `before` must occur exactly once in its file there, so the recipient finds exactly one place
(a handover that points at text that is not there, or is there twice, is refused).
Exit 0 written, 3 refused (the text is not where the handover says), 2 bad input.
"""
import argparse
from pathlib import Path

import yaml

from . import emit


def locate(target, fixes):
    problems = []
    for i, f in enumerate(fixes, 1):
        p = Path(target) / f["file"]
        if not p.is_file():
            problems.append(f"fix {i}: {f['file']} is not in {target}")
            continue
        n = p.read_text(encoding="utf-8", errors="replace").count(f["before"])
        if n != 1:
            problems.append(f"fix {i}: the 'before' text occurs {n} time(s) in {f['file']}, not once")
    return problems


def render(spec, proof=None):
    out = [f"# {spec.get('title', 'Fixes')}", ""]
    if spec.get("to"):
        out += [f"For: {spec['to']}", ""]
    if proof:
        out += ["## Proof", "",
                f"Your own test suite, before and after these fixes: **{proof['before']}** and **{proof['after']}**.",
                ("No test that passed before fails after." if not proof["new_failures"] else
                 "NEW failures after the fixes: " + ", ".join(proof["new_failures"])),
                ("Still failing, before and after (not caused by these fixes): " + ", ".join(proof["still_failing"][:15])
                 if proof["still_failing"] else ""), ""]
    out += ["Back up first. Run the test suite before and after, and compare.", ""]
    for i, f in enumerate(spec.get("fixes") or [], 1):
        out += [f"## {i}. {f.get('title') or f.get('why', '').split(';')[0]}", "",
                f"**File:** `{f['file']}`" + (f", function `{f['function']}`" if f.get("function") else ""), "",
                "**Before:**", "", "```", f["before"], "```", "", "**After:**", "", "```", f["after"], "```", ""]
        if f.get("why"):
            out += [f"**Why:** {f['why']}", ""]
        if f.get("today") or f.get("after_fix"):
            out += [f"**Today:** {f.get('today', '')}  ", f"**After the fix:** {f.get('after_fix', '')}", ""]
        if f.get("test"):
            out += ["**Regression test:**", "", "```python", f["test"].rstrip(), "```", ""]
    if spec.get("not_bugs"):
        out += ["## Not bugs", ""] + [f"- {n}" for n in spec["not_bugs"]] + [""]
    return "\n".join(l for l in out if l is not None).rstrip() + "\n"


def main(argv=None, prog="fieldkit handover"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fixes")
    ap.add_argument("--target", help="the recipient's copy: every 'before' must occur there exactly once")
    ap.add_argument("--out")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    p = Path(a.fixes)
    if not p.is_file():
        print(f"REFUSED: no such file: {p}")
        return 2
    spec = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    fixes = spec.get("fixes") or []
    if not fixes or any(not {"file", "before", "after"} <= set(f) for f in fixes):
        print("REFUSED: every fix needs file, before and after")
        return 2
    if a.target:
        problems = locate(a.target, fixes)
        if problems:
            emit({"ok": False, "problems": problems, "next": "correct the handover: the recipient must find each "
                                                             "'before' exactly once"}, a.json,
                 lambda d: [f"REFUSED: {x}" for x in d["problems"]])
            return 3
    proof = None
    if spec.get("regress"):
        from . import regress
        proof = regress.compare(regress.load(spec["regress"][0]), regress.load(spec["regress"][1]))
    text = render(spec, proof)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        emit({"ok": True, "out": a.out, "fixes": len(fixes)}, a.json, lambda d: [f"{d['fixes']} fix(es) -> {d['out']}"])
    else:
        print(text, end="")
    return 0
