"""Referencing styles: what one style asks for that the others do not, as data (styles.yaml).

The shared part, that every citation has an entry and every entry is cited, in alphabetical order, with a page for
every direct quotation, is reference_auditor.py (author-date, any of the seven languages). This module checks the
shape each style gives an entry and an in-text citation:

    harvard-ctr   Smith, J. (2020) Title.                  (Smith and Jones, 2020)
    apa7          Smith, J. (2020). Title.                 (Smith & Jones, 2020)
    iso690        SMITH, John, 2020. Title.                (Smith 2020) / (Smith, 2020)

A shape that does not match is a WARNING with the expected form beside it, never a failure: the student fixes it
by hand, and a style variant a university prefers is not wrongly failed.
"""
import re
from pathlib import Path

import yaml

from . import reference_auditor as ra

HERE = Path(__file__).parent


def load():
    return yaml.safe_load((HERE / "styles.yaml").read_text(encoding="utf-8"))["styles"]


def names():
    return sorted(load())


def get(style):
    s = load()
    if style not in s:
        raise ValueError(f"style {style!r}: one of {', '.join(sorted(s))}")
    return s[style]


def _entries(text):
    _, ref_text = ra.split_body_and_references(text)
    return [re.sub(r"^[\-\*•]\s*", "", e).strip() for e in ra._group_entries(ref_text)] if ref_text.strip() else []


def check(text, style):
    """-> {style, entries: [{entry, ok, expected}], citations: [{citation, ok, expected}], warnings: n}."""
    st = get(style)
    entry_rx = re.compile(st["entry"].replace("{YEAR}", ra.YEAR_OR_ND), re.I if st.get("ignore_case") else 0)
    out = {"style": style, "label": st["label"], "entries": [], "citations": []}
    for e in _entries(text):
        if ra.is_legislation(ra.normalise_key(e.split("(")[0], "2000")):   # statutes follow their own form
            continue
        ok = bool(entry_rx.search(ra._strip_markdown(e)))
        out["entries"].append({"entry": e[:120], "ok": ok, "expected": None if ok else st["entry_example"]})
    body, _ = ra.split_body_and_references(text)
    wrong = st.get("parenthetical_join_not")
    if wrong:
        for inner in re.findall(r"\(([^()]*)\)", ra._strip_markdown(body)):
            for chunk in inner.split(";"):
                if re.search(r"\b" + ra.YEAR_OR_ND, chunk) and re.search(wrong, chunk):
                    out["citations"].append({"citation": f"({chunk.strip()})", "ok": False,
                                             "expected": st["citation_example"]})
    out["warnings"] = sum(not x["ok"] for x in out["entries"] + out["citations"])
    return out


def lines(r):
    out = [f"style: {r['label']}"]
    for e in r["entries"]:
        if not e["ok"]:
            out.append(f"  WARN  entry: {e['entry']}")
            out.append(f"        expected like: {e['expected']}")
    for c in r["citations"]:
        out.append(f"  WARN  citation: {c['citation']}")
        out.append(f"        expected like: {c['expected']}")
    out.append(f"{len(r['entries'])} entries, {r['warnings']} not in {r['label']} form")
    return out
