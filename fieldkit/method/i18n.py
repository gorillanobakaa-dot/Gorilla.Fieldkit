"""Translation tables are data, checked three ways (docs/METHODS.md #9).

    fieldkit i18n check FILE --table headings [--langs es,pt,it,de,ro] [--keys-from "CMD"]
    fieldkit i18n check FILE --disjoint distinctive

--table KEY: a mapping {source text: [one entry per language]} or {source: {lang: text}} under KEY.
  - complete: every row has an entry for every language, none empty
  - unique: no two sources share one translation in the same language (a reader could not tell them apart,
    and the reverse lookup - translated heading back to the section it is - would be ambiguous)
  - covered: with --keys-from, every source string the program uses (one per line, printed by CMD) has a row

--disjoint FIELD: a mapping {lang: {FIELD: [words]}} where no word may belong to two languages (the words that tell
  one language from another must not be shared: "este" in Spanish, Portuguese and Romanian decided nothing).

Found the hard way: "Cuprins" was both Contents and Main Body in Romanian; 12 shared words in the language lists.
Exit 0 when the table holds, 3 when it does not, 2 for bad input.
"""
import argparse
import subprocess
from collections import defaultdict
from pathlib import Path

import yaml

from . import emit, split_command


def check_table(rows, langs=None, keys=None):
    problems = []
    if not isinstance(rows, dict) or not rows:
        return ["the table is empty or not a mapping"]
    first = next(iter(rows.values()))
    as_list = isinstance(first, list)
    if langs is None:
        langs = list(range(len(first))) if as_list else sorted(first)
    seen = {lang: defaultdict(list) for lang in langs}
    for src, row in rows.items():
        for i, lang in enumerate(langs):
            try:
                text = row[lang if not as_list else i]
            except (IndexError, KeyError, TypeError):
                problems.append(f"incomplete: {src!r} has no {lang!r}")
                continue
            if not str(text or "").strip():
                problems.append(f"empty: {src!r} in {lang!r}")
                continue
            seen[lang][str(text).strip().lower()].append((src, str(text).strip()))
        if as_list and len(row) != len(langs):
            problems.append(f"incomplete: {src!r} has {len(row)} entries, expected {len(langs)}")
    for lang, texts in seen.items():
        for _, hits in texts.items():
            if len(hits) > 1:
                problems.append(f"not unique: {hits[0][1]!r} in {lang!r} translates "
                                f"{', '.join(repr(src) for src, _ in hits)}")
    for k in keys or []:
        if k not in rows:
            problems.append(f"missing: {k!r} is used but has no row")
    return problems


def check_disjoint(langs, field):
    owner = defaultdict(set)
    for lang, data in (langs or {}).items():
        for w in (data or {}).get(field) or []:
            owner[str(w).lower()].add(lang)
    return [f"shared: {w!r} is in {', '.join(sorted(ls))}" for w, ls in sorted(owner.items()) if len(ls) > 1]


def main(argv=None, prog="fieldkit i18n"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check"])
    ap.add_argument("file")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--table", help="the key holding {source: translations}")
    g.add_argument("--disjoint", help="the per-language field whose words must not be shared")
    ap.add_argument("--langs", help="comma-separated names of the list positions, or the languages to require")
    ap.add_argument("--keys-from", help="a command printing every source string in use, one per line")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    p = Path(a.file)
    if not p.is_file():
        print(f"REFUSED: no such file: {p}")
        return 2
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if a.table:
        if a.table not in data:
            print(f"REFUSED: no key {a.table!r} in {p.name}")
            return 2
        keys = None
        if a.keys_from:
            r = subprocess.run(split_command(a.keys_from), capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                print(f"REFUSED: --keys-from failed: {r.stderr.strip()[-200:]}")
                return 2
            keys = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        langs = a.langs.split(",") if a.langs else None
        problems = check_table(data[a.table], langs, keys)
        what = f"{len(data[a.table])} rows"
    else:
        problems = check_disjoint(data, a.disjoint)
        what = f"{len(data)} languages"
    r = {"ok": not problems, "file": str(p), "checked": what, "problems": problems,
         "next": "" if not problems else "fix the table: every row complete, every translation unique"}
    emit(r, a.json, lambda d: [f"  {x}" for x in d["problems"][:50]] +
         [f"OK: {d['checked']}" if d["ok"] else f"{len(d['problems'])} problem(s) in {d['checked']}"])
    return 0 if r["ok"] else 3
