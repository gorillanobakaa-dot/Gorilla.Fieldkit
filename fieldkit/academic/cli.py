"""fieldkit academic: the commands. Every check returns data (--json) or short lines; exit codes are fixed:

    0 ok   2 a question to ask / bad input   3 a check found something to fix
"""
import argparse
import json
import os
import sys

from . import profile as prof

USAGE = """fieldkit academic COMMAND ...

  init        --name "FULL NAME" --country uk|us|es|pt|it|de|ro [--university U] [--programme P] [--style S]
              [--language L] [--ui en|ro] [--student-id N]       (without name/country: the questions to ask)
  setup       FOLDER --deadline DATE --upload DATE [--type T] [--words N] [--module M] [--title T]
  next        FOLDER                     days left to the earlier date, and the one next step
  accessed    FOLDER URL                 stamp a web source with today's date, in the profile's style
  extract     FOLDER                     read every study file into the library (text, page by page)
  search      FOLDER QUERY [--any]       find words or "a phrase" in the library, with the page to cite
  refs        FILE                       citations <-> reference list, order, duplicates, quotations with a page
  style       FILE [--style S]           entries and citations in the shape the style asks for
  language    FILE [--language L] [--spelling en-GB|en-US]   sentences in another language; spelling variant
  words       FILE [--target N]          assessed words (no title, references or appendices), +/-10%
  dashes      FILE                       long dashes (— –), where the country's rules count them against you
  plagiarism  FILE --sources FOLDER      runs of words copied from the sources, quoted or not
  structure   FILE --type T              the sections the document type asks for
  types       [TYPE]                     the document types
  countries                              the countries and what each one sets

  --json on any command: the result as JSON.
"""

FIX, WARN, OK = "FIX  ", "CHECK", "OK   "


def _p():
    try:
        return prof.load() or {}
    except Exception:
        return {}


def _emit(r, as_json, render):
    if as_json:
        print(json.dumps(r, indent=1, ensure_ascii=False, default=str))
    else:
        for line in render(r):
            print(line)
        if r.get("next"):
            print(f"NEXT: {r['next']}")


def _need_file(path):
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")
    return path


# -- renderers --------------------------------------------------------------

def _ask_lines(r):
    return [f"QUESTION: {q}" for q in r["questions"]] + [f"LATER: {q}" for q in r.get("later", [])]


def _kv(r):
    return [f"{k}: {v}" for k, v in r.items() if k not in ("next", "ok")]


def _refs_lines(r):
    out = [f"citations: {r['total_citations']} ({r['unique_citations']} sources); entries: {r['total_references']}; "
           f"quotations: {r['quotes']}"]
    for o in r["orphans"]:
        out.append(f"  {FIX} cited, not in the reference list: {o}")
    for u in r["unused"]:
        out.append(f"  {FIX} in the reference list, never cited: {u}")
    for d, n in sorted(r["duplicates"].items()):
        out.append(f"  {FIX} listed {n} times: {d}")
    for e in r["out_of_order"]:
        out.append(f"  {FIX} out of alphabetical order: {e[:90]}")
    for q in r["quotes_unattributed"]:
        out.append(f"  {WARN} quotation without author and year: \"{q['quote'][:80]}\"")
    for q in r["quotes_without_page"]:
        out.append(f"  {WARN} quotation without a page: \"{q['quote'][:80]}\"")
    for c in r["citations_without_page"][:10]:
        out.append(f"  {WARN} no page (needed only for a direct quotation): {c}")
    for k in r["cited_only_in_notes"]:
        out.append(f"  {WARN} cited only in the speaker notes: {k}")
    out.append(f"{OK} nothing to fix" if not r["issues"] else f"{r['issues']} thing(s) to fix")
    out.append("This checks citations against entries, not whether a source says what the work says it does.")
    return out


def _words_lines(r):
    out = [f"words: {r['words']:,} (no title, references or appendices)"]
    if r.get("target"):
        out.append(f"target: {r['target']:,} (accepted {r['lower']:,} - {r['upper']:,})")
        if r["words"] > r["upper"]:
            out.append(f"  {FIX} cut {r['words'] - r['upper']:,} words")
        elif r["words"] < r["lower"]:
            out.append(f"  {FIX} write {r['lower'] - r['words']:,} more words")
        else:
            out.append(f"  {OK} within range")
    return out


def _dash_lines(r):
    out = []
    for f in r["faults"][:30]:
        how = "use a plain hyphen: 14-16" if f["kind"] == "range" else "rewrite: a comma, a colon, brackets or two sentences"
        out.append(f"  {FIX} line {f['line']}: {f['snippet']}   ({how})")
    for w in r["warnings"][:10]:
        out.append(f"  {WARN} line {w['line']}: a hyphen used as a dash: {w['snippet']}")
    if r["faults"]:
        out.append("In Word: Ctrl+H, find ^+ (long dash) or ^= (medium dash).")
    note = "" if r["counts"] else "  (your country does not count them against you: shown for information)"
    out.append((f"{OK} no long dashes" if not r["faults"] else f"{len(r['faults'])} long dash(es)") + note)
    return out


def _plag_lines(r):
    out = []
    for m in r["matches"][:30]:
        tag = "quoted" if m["quoted"] else (FIX.strip() if m["serious"] else WARN.strip())
        out.append(f"  [{tag}] {m['length']} words from {m['source']} ({m['role']}): {m['text'][:100]}")
    if r["no_sources"]:
        out.append(f"  {FIX} no readable sources in the folder: nothing was compared")
    out.append(f"{r['serious']} long unquoted run(s) copied from a source" if r["serious"]
               else f"{OK} no long unquoted copied runs")
    out.append(f"Before {r['tool']}: rewrite in your own words, or quote it with the page.")
    return out


def _next_lines(r):
    return list(r["say"])


def _search_lines(r):
    out = []
    for h in r["hits"]:
        out.append(f"{h['file']}  ({h['cite']})")
        out.append(f"   {h['snippet'][:300]}")
    out.append(f"{r['total']} section(s) in {r['files']} file(s)")
    return out


def _structure_lines(r):
    return ([f"type: {r['label']}"] + [f"  {OK} {x}" for x in r["passes"]] + [f"  {WARN} {x}" for x in r["warnings"]]
            + [f"  {FIX} {x}" for x in r["failures"]])


# -- commands ---------------------------------------------------------------

def run(a):
    p = _p()
    c = a.command
    if c == "init":
        r = prof.init(a.name, a.country, a.university, a.programme, a.style, a.language, a.ui or "en", a.student_id)
        _emit(r, a.json, _ask_lines if r["status"] == "ask" else _kv)
        return 2 if r["status"] == "ask" else 0
    if c == "countries":
        rows = []
        for code in prof.countries():
            loc = prof.locale(code)
            rows.append({k: loc[k] for k in ("id", "country", "work_language", "page", "default_style", "styles",
                                             "plagiarism_tool")})
        _emit({"countries": rows}, a.json, lambda r: [
            f"{x['id']}  {x['country']:<16} {x['work_language']}  {x['page']:<6} {x['default_style']:<12} "
            f"(also {', '.join(s for s in x['styles'] if s != x['default_style'])})  {x['plagiarism_tool']}"
            for x in r["countries"]])
        return 0
    if c == "types":
        from . import document_types as dt
        if a.target:
            key = dt.resolve_type(a.target)
            if not key:
                raise ValueError(f"unknown type {a.target!r}: fieldkit academic types")
            spec = dt.DOCUMENT_TYPES[key]
            r = {"type": key, "label": spec["label"], "typical_words": spec["typical_words"],
                 "sections": dt.section_guide(key)}
            _emit(r, a.json, lambda r: [f"{r['label']} (about {r['typical_words']:,} words)"]
                  + [f"  {s}" for s in (r["sections"] if isinstance(r["sections"], list) else [r["sections"]])])
        else:
            r = {"types": dt.type_names()}
            _emit(r, a.json, lambda r: r["types"] + [f"{len(r['types'])} types"])
        return 0
    if c in ("setup", "next", "accessed"):
        from . import assignment
        if not a.target:
            raise ValueError(f"fieldkit academic {c} FOLDER")
        if c == "setup":
            r = assignment.setup(a.target, a.deadline, a.upload, a.type, a.target_words, a.module, a.title)
            _emit(r, a.json, _ask_lines if r["status"] == "ask" else _kv)
            return 0 if r["ok"] else 2
        if c == "next":
            r = assignment.next_step(a.target)
            _emit(r, a.json, _next_lines)
            return 0 if r["ok"] else 2
        if not a.extra:
            raise ValueError("fieldkit academic accessed FOLDER URL")
        r = assignment.accessed(a.target, a.extra)
        _emit(r, a.json, lambda r: [r["line"]])
        return 0
    if c == "extract":
        from .extract_library import build_library
        s = build_library(a.target, verbose=False)
        r = {"sources": s["sources"], "extracted": len(s["extracted"]), "reused": len(s["reused"]),
             "failed": [f"{rel}: {why[:110]}" for rel, why in s["failed"]], "index": s["index"],
             "next": f'fieldkit academic search "{a.target}" "WORDS"'}
        _emit(r, a.json, _kv)
        return 3 if r["failed"] else 0
    if c == "search":
        from .find_in_sources import search
        if not a.extra:
            raise ValueError('fieldkit academic search FOLDER "WORDS"')
        hits, total, files = search(a.target, a.extra, any_word=a.any)
        _emit({"hits": hits, "total": total, "files": files}, a.json, _search_lines)
        return 0
    path = _need_file(a.target)
    if c == "refs":
        from .reference_auditor import audit_references
        r = audit_references(path)
        _emit(r, a.json, _refs_lines)
        return 3 if r["issues"] else 0
    if c == "style":
        from . import styles
        from .reference_auditor import load_document
        r = styles.check(load_document(path)[0], a.style or p.get("style") or "apa7")
        _emit(r, a.json, styles.lines)
        return 3 if r["warnings"] else 0
    if c == "language":
        from . import language
        from .reference_auditor import load_document
        lang = a.language or p.get("work_language") or "en"
        spelling = a.spelling or (p.get("spelling") if lang == "en" else None)
        r = language.check(load_document(path)[0], lang, spelling)
        _emit(r, a.json, language.lines)
        return 0 if r["ok"] else 3
    if c == "words":
        from .reference_auditor import load_document
        from .word_counter import TOLERANCE, WORD_RE, extract_body
        n = len(WORD_RE.findall(extract_body(load_document(path)[0])))
        r = {"words": n, "target": a.target_words}
        if a.target_words:
            r.update(lower=int(a.target_words * (1 - TOLERANCE)), upper=int(a.target_words * (1 + TOLERANCE)))
            r["ok"] = r["lower"] <= n <= r["upper"]
        _emit(r, a.json, _words_lines)
        return 3 if r.get("ok") is False else 0
    if c == "dashes":
        from .dash_check import check_file
        faults, warnings = check_file(path)
        counts = p.get("avoid_long_dashes", True)
        r = {"faults": faults, "warnings": warnings, "counts": counts}
        _emit(r, a.json, _dash_lines)
        return 3 if faults and counts else 0
    if c == "plagiarism":
        from .plagiarism_precheck import is_serious, run_precheck
        if not a.sources or not os.path.isdir(a.sources):
            raise ValueError("fieldkit academic plagiarism FILE --sources FOLDER")
        matches = run_precheck(path, a.sources)
        for m in matches:
            m["serious"] = is_serious(m)
            m.pop("tokens", None)
        r = {"matches": matches, "serious": sum(m["serious"] for m in matches), "no_sources": not matches and
             not any(os.scandir(a.sources)), "tool": p.get("plagiarism_tool") or "the plagiarism checker"}
        _emit(r, a.json, _plag_lines)
        return 3 if r["serious"] or r["no_sources"] else 0
    if c == "structure":
        from . import document_types as dt
        from .reference_auditor import load_document
        key = dt.resolve_type(a.type or "")
        if not key:
            raise ValueError("fieldkit academic structure FILE --type T (fieldkit academic types)")
        r = dt.check_structure(load_document(path)[0], key)
        _emit(r, a.json, _structure_lines)
        return 0 if r["ok"] else 3
    raise ValueError(f"unknown command {c!r}")


COMMANDS = ["init", "setup", "next", "accessed", "extract", "search", "refs", "style", "language", "words", "dashes",
            "plagiarism", "structure", "types", "countries"]


def parser(prog="fieldkit academic"):
    ap = argparse.ArgumentParser(prog=prog, usage=USAGE, add_help=True)
    ap.add_argument("command", choices=COMMANDS, metavar="COMMAND")
    ap.add_argument("target", nargs="?", help="a FOLDER or a FILE")
    ap.add_argument("extra", nargs="?", help="a URL (accessed) or a QUERY (search)")
    ap.add_argument("--json", action="store_true")
    for opt in ("name", "country", "university", "programme", "style", "language", "ui", "student-id", "deadline",
                "upload", "type", "module", "title", "spelling", "sources"):
        ap.add_argument("--" + opt)
    ap.add_argument("--words", "--target", dest="target_words", type=int)
    ap.add_argument("--any", action="store_true", help="search: any of the words, not all")
    return ap


def main(argv=None, prog="fieldkit academic"):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        print(USAGE)
        return 2
    a = parser(prog).parse_args(argv)
    try:
        return run(a)
    except (prof.BadProfile, ValueError, FileNotFoundError, KeyError) as e:
        msg = str(e).strip("'\"")
        if a.json:
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(f"REFUSED: {msg}")
        return 2
