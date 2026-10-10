"""Cards that stay true to their commands, and new cards started from the code (docs/METHODS.md #4).

    fieldkit cards check [ID]      every reviewed card against its command
    fieldkit cards draft FILE.py   a card to review, read from a Python CLI's argparse code (nothing is run)

check, for each reviewed card:
  - the command starts: ENTRY --help exits 0 (Fieldkit's own commands, and any Python entry)
  - every input with a flag appears in that help (a renamed option breaks the card silently otherwise)
  - every choices list on the card is offered by the help, when the help lists choices
  - its test file exists
  - a card that changes things has a scope, an undo command, or says why not (effects without a way back)
draft: inputs from add_argument calls, effects from what the code touches, a safety guess - marked draft: true so
the agent door refuses it until a person reviews it and removes that line.
Exit 0 when every card holds, 3 when one does not, 2 for bad input.
"""
import re
import subprocess
import sys
from pathlib import Path

from ..core import settings
from . import cards as cardmod


def _help_text(entry, timeout=60):
    argv = [sys.executable if a in ("python", "python3") else a for a in entry]
    try:
        r = subprocess.run(argv + ["--help"], capture_output=True, text=True, timeout=timeout,
                           cwd=str(settings.ROOT))
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    return (r.stdout + r.stderr) if r.returncode == 0 else None, (r.stderr or r.stdout)[-200:]


def check_card(card):
    """-> (problems, notes). Notes are facts about this machine (a gathered tool not here), not card faults."""
    problems, notes = [], []
    entry = card.get("entry") or []
    script = next((settings.expand(str(x), strict=False) for x in entry[1:2] if str(x).endswith(".py")), None)
    if script and not Path(script).is_file():
        return problems, [f"not on this machine: {script} (fieldkit gather)"]
    if entry[:1] in (["python"], ["python3"]):
        # --help goes before the first option of the entry ("harvest --find" takes a value)
        cut = next((i for i, x in enumerate(entry) if i > 2 and str(x).startswith("-")), len(entry))
        text, err = _help_text([settings.expand(str(x), strict=False) for x in entry[:cut]])
        if text is not None and card.get("inputs_basis") and re.search(r"^\s*\{[\w,-]+\}", text, re.M):
            problems.append("the command has subcommands, and the card's inputs were read from the code of all of "
                            "them at once: review the card (a subcommand in its entry, that subcommand's inputs)")
            return problems, notes
        if text is None:
            problems.append(f"`{' '.join(entry)} --help` fails: {err.strip()[:120]}")
        else:
            for i in card.get("inputs") or []:
                flag = i.get("flag")
                if flag and not re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", text):
                    problems.append(f"input {i['name']}: {flag} is not in the command's help")
                for c in i.get("choices") or []:
                    if "{" in text and flag and re.search(re.escape(flag) + r"\s*\{", text) \
                            and not re.search(re.escape(flag) + r"\s*\{[^}]*\b" + re.escape(str(c)) + r"\b", text):
                        problems.append(f"input {i['name']}: choice {c!r} is not offered by {flag}")
    for t in card.get("tests") or []:
        for part in t if isinstance(t, list) else [t]:
            s = str(settings.expand(part, strict=False))
            if s.endswith(".py") and ("/tests/" in s.replace("\\", "/")) and not Path(s).is_file():
                problems.append(f"test file missing: {s}")
    changing = set(card.get("effects") or []) & cardmod.CHANGING
    if changing and card.get("safety") == "read-only":
        problems.append(f"marked read-only but its effects change things: {', '.join(sorted(changing))}")
    return problems, notes


def check(card_id=None):
    reviewed = [c for c in cardmod.all_cards() if c.get("reviewed") and not c.get("draft") and c.get("inputs")
                is not None and not c.get("retired")]
    if card_id:
        reviewed = [c for c in reviewed if c["id"] == card_id]
        if not reviewed:
            raise KeyError(f"no reviewed card {card_id!r}")
    rows = []
    for c in reviewed:
        problems, notes = check_card(c)
        rows.append({"id": c["id"], "problems": problems, "notes": notes})
    bad = [r for r in rows if r["problems"]]
    return {"ok": not bad, "checked": len(rows), "failing": len(bad), "cards": bad,
            "not_here": [f"{r['id']}: {n}" for r in rows for n in r["notes"]],
            "next": "update the card (or the command) so they agree, then check again" if bad else ""}


def draft(path):
    p = Path(path)
    if not p.is_file() or p.suffix != ".py":
        raise FileNotFoundError(f"not a Python file: {p}")
    inputs = cardmod.argparse_inputs(p)
    effects = sorted(cardmod.code_effects(p))
    safety = cardmod.guess_safety(effects, None)
    lines = [f"- id: {p.stem.replace('_', '-')}", "  title: \"REVIEW: what it does, in plain words\"",
             f"  entry: [python, \"{p}\"]", f"  path: {p}", "  draft: true          # remove after review",
             "  inputs:" if inputs else "  inputs: []"]
    for i in inputs:
        lines.append("    - {" + ", ".join(f"{k}: {('null' if v is None else repr(v) if isinstance(v, str) else v)}"
                                           for k, v in i.items()) + "}")
    lines += [f"  effects: [{', '.join(effects)}]    # REVIEW", f"  safety: {safety}    # REVIEW",
              "  output: third-party    # own only if the answer is the owner's own words or Fieldkit's verdicts",
              "  platforms: [windows, linux]    # REVIEW",
              "  test: null    # REVIEW: a command that proves it works"]
    return "\n".join(lines) + "\n"
