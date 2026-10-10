"""
EXTRACT LIBRARY - Extracts every study file ONCE into plain, page-referenced
markdown, so a language model reads a small clean text file instead of
sending a whole PDF or slide deck to a server again and again.

    Exam_1/Extracted PDF.PowerPoint/
        INDEX.md                         read this first: every source, its
                                         size, main topics and slide titles
        Lecture 3 - Safeguarding.pptx.md one file per source, with
        Care Act guidance.pdf.md         "## Page 14" / "## Slide 7: Title"
        ...                              headings to jump straight to
        _manifest.json                   what was extracted, and when
        _converted/                      modern copies of old .ppt/.doc files

Why this matters for cheap or limited models: a model asked about a lecture
would otherwise upload the whole file, or re-read it on every question. Here
it reads INDEX.md (small), runs find_in_sources.py for the topic it needs,
and opens only the page or slide the search points to. Tokens are spent on
content, not on repeated uploads, running headers or page numbers.

Nothing is re-extracted unless its source changed. A file is re-read when its
size or modification time changes, when the extractor itself is improved, or
when OCR was missing last time and is available now. Outputs for deleted
sources are removed.

Every file also carries an estimate of its size in tokens (about 1.3 per
word) so the cost of opening it is known in advance.

Usage:
    python extract_library.py <exam_folder>            build or refresh
    python extract_library.py <exam_folder> --force    re-extract everything
"""
import os
import re
import sys
import json
import time
import math
import argparse
from collections import Counter

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
if HARNESS_DIR not in sys.path:
    sys.path.insert(0, HARNESS_DIR)

from .workspace import Workspace
from .read_sources import (READERS, extract_units, _skip, _role,
                          ocr_available, strip_markers)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Bump when extraction improves, so cached files are rebuilt.
EXTRACTOR_VERSION = 2
MANIFEST = "_manifest.json"
CONVERTED = "_converted"
INDEX = "INDEX.md"
TOKENS_PER_WORD = 1.33

KIND_NAMES = {"page": "Page", "slide": "Slide", "sheet": "Sheet"}
TYPE_NAMES = {".pdf": "PDF", ".pptx": "PowerPoint", ".ppt": "PowerPoint (old)",
              ".docx": "Word", ".doc": "Word (old)", ".xlsx": "Excel",
              ".xlsm": "Excel", ".xls": "Excel (old)", ".txt": "Text",
              ".md": "Text", ".csv": "CSV", ".rtf": "Text"}
ROLE_NAMES = {"source": "study material", "brief": "assignment brief",
              "notes": "own notes"}

STOPWORDS = set("""
a about above after again against all also am an and any are as at be because
been before being below between both but by can could did do does doing down
during each few for from further had has have having he her here hers him his
how i if in into is it its itself just me more most my no nor not now of off on
once only or other our ours out over own same she should so some such than that
the their theirs them then there these they this those through to too under
until up very was we were what when where which while who whom why will with
would you your yours may might must shall also however therefore thus within
without upon via per e g ie eg etc et al use used using one two three first
second new page slide figure table see also including include includes well
make made many much often part people person p pp s t can't don't
""".split())


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def estimate_tokens(words):
    return int(math.ceil(words * TOKENS_PER_WORD))


def top_terms(text, n=8):
    """The most characteristic words and two-word phrases in a text."""
    words = [w for w in re.findall(r"[a-z][a-z'\-]{2,}", text.lower())
             if w not in STOPWORDS]
    counts = Counter(words)
    bigrams = Counter()
    for a, b in zip(words, words[1:]):
        bigrams[a + " " + b] += 1
    ranked = [(p, c * 2.2) for p, c in bigrams.items() if c >= 3]
    ranked += [(w, float(c)) for w, c in counts.items() if c >= 2]
    ranked.sort(key=lambda x: -x[1])
    chosen = []
    for term, _ in ranked:
        # Skip a single word already covered by a chosen phrase.
        if any(term in c.split() or term == c for c in chosen):
            continue
        chosen.append(term)
        if len(chosen) >= n:
            break
    return chosen


def safe_name(rel_path):
    """'Assignment brief/brief.docx' -> 'Assignment brief - brief.docx.md'."""
    name = rel_path.replace("\\", "/").replace("/", " - ")
    name = re.sub(r'[<>:"|?*]', "_", name)
    return name + ".md"


def unit_heading(u):
    if u["number"] is None:
        return None
    label = "%s %d" % (KIND_NAMES.get(u["kind"], u["kind"].title()), u["number"])
    if u.get("title"):
        label += ": " + u["title"]
    if u.get("ocr"):
        label += " (OCR)"
    return "## " + label


def cite_label(u):
    """How a unit is cited: 'p. 14', 'slide 7', 'sheet 2'."""
    if u["number"] is None:
        return ""
    return {"page": "p. %d", "slide": "slide %d", "sheet": "sheet %d"}.get(
        u["kind"], "part %d") % u["number"]


def _library_paths(ws):
    return (ws.extracted, os.path.join(ws.extracted, MANIFEST),
            os.path.join(ws.extracted, CONVERTED))


def load_manifest(ws):
    _, manifest_path, _ = _library_paths(ws)
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {"version": EXTRACTOR_VERSION, "files": {}}


def _fingerprint(path):
    st = os.stat(path)
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def _is_fresh(entry, path, ocr_now):
    if not entry or entry.get("version") != EXTRACTOR_VERSION:
        return False
    fp = _fingerprint(path)
    if entry.get("size") != fp["size"] or entry.get("mtime") != fp["mtime"]:
        return False
    if entry.get("ocr_was_missing") and ocr_now:
        return False
    return os.path.isfile(entry.get("output_path", ""))


# --------------------------------------------------------------------------
# Writing one source
# --------------------------------------------------------------------------

def render_source(rel, path, units, error):
    """The markdown file for one source, and the facts for the index."""
    ext = os.path.splitext(path)[1].lower()
    kinds = [u["kind"] for u in units if u["number"] is not None]
    unit_kind = kinds[0] if kinds else None
    body_text = "\n".join(u["text"] for u in units)
    words = len(body_text.split())
    ocr_units = [cite_label(u) for u in units if u.get("ocr")]
    problems = [("%s: %s" % (cite_label(u) or "file", u["note"]))
                for u in units if u.get("note")
                and "converted from" not in u["note"]
                and u["note"] != "blank page"]
    if error:
        problems.insert(0, error.strip("[]"))
    empty = [cite_label(u) for u in units
             if u["number"] is not None and not u["text"].strip()]
    converted = next((u["note"] for u in units
                      if "converted from" in u.get("note", "")), "")

    info = {
        "file": rel,
        "type": TYPE_NAMES.get(ext, ext.lstrip(".").upper()),
        "role": _role(path),
        "units": len([u for u in units if u["number"] is not None]),
        "unit_kind": unit_kind,
        "words": words,
        "tokens": estimate_tokens(words),
        "terms": top_terms(body_text),
        "titles": [(u["number"], u["title"]) for u in units
                   if u["kind"] == "slide" and u.get("title")],
        "ocr_units": ocr_units,
        "empty_units": empty,
        "problems": problems,
        "ok": not error and words > 0,
    }

    lines = ["# %s" % os.path.basename(rel), ""]
    count = ""
    if info["units"]:
        count = "%d %s%s | " % (info["units"],
                                KIND_NAMES.get(unit_kind, "part").lower(),
                                "s" if info["units"] != 1 else "")
    lines.append("Source: %s | %s | %s%s words | ~%s tokens"
                 % (rel, info["type"], count, format(words, ","),
                    format(info["tokens"], ",")))
    lines.append("Role: %s" % ROLE_NAMES.get(info["role"], info["role"]))
    if info["terms"]:
        lines.append("Main topics: %s" % ", ".join(info["terms"]))
    if ocr_units:
        lines.append("Read by OCR (check spellings): %s" % ", ".join(ocr_units))
    if converted:
        lines.append("Note: %s" % converted)
    if problems:
        lines.append("Problems: %s" % "; ".join(problems))
    lines += ["", "---", ""]

    if error:
        lines.append("_Nothing could be extracted: %s_" % error.strip("[]"))
    for u in units:
        heading = unit_heading(u)
        if heading:
            lines.append(heading)
            lines.append("")
        text = u["text"].strip()
        if text:
            lines.append(text)
        elif heading:
            lines.append("_(no text%s)_" % ("; " + u["note"] if u.get("note")
                                             and "converted" not in u["note"]
                                             else ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", info


# --------------------------------------------------------------------------
# The index
# --------------------------------------------------------------------------

def render_index(ws, infos):
    materials = os.path.relpath(ws.materials, ws.root)
    total_words = sum(i["words"] for i in infos)
    lines = [
        "# Study materials index",
        "",
        "Generated by extract_library.py - do not edit. %d source(s), "
        "%s words, ~%s tokens in total."
        % (len(infos), format(total_words, ","),
           format(estimate_tokens(total_words), ",")),
        "",
        "**For the AI - read this before anything else, and do not open the "
        "original PDFs or slide decks.**",
        "1. Use this index to see what exists and how large each file is.",
        "2. To find a topic, run `python find_in_sources.py \"<exam folder>\" "
        "\"<words>\"` - it returns only the matching passages, each with its "
        "page or slide number.",
        "3. Open a source's `.md` file only when you need more than the "
        "search shows, and read only the `## Page` / `## Slide` section you "
        "need.",
        "4. Cite page numbers from these headings: (Author, Year, p. 14). A "
        "slide is cited by the lecture, not by the page.",
        "",
        "| Source | Type | Size | Words | ~Tokens | Main topics |",
        "| :--- | :--- | :--- | ---: | ---: | :--- |",
    ]
    order = {"brief": 0, "source": 1, "notes": 2}
    for i in sorted(infos, key=lambda x: (order.get(x["role"], 1),
                                          x["file"].lower())):
        size = ""
        if i["units"]:
            size = "%d %s%s" % (i["units"], KIND_NAMES.get(
                i["unit_kind"], "part").lower(), "s" if i["units"] != 1 else "")
        name = "[%s](%s)" % (i["file"], safe_name(i["file"]).replace(" ", "%20"))
        if i["role"] != "source":
            name += " *(%s)*" % ROLE_NAMES.get(i["role"], i["role"])
        lines.append("| %s | %s | %s | %s | %s | %s |"
                     % (name, i["type"], size, format(i["words"], ","),
                        format(i["tokens"], ","),
                        ", ".join(i["terms"][:6]) or "-"))

    decks = [i for i in infos if i["titles"]]
    if decks:
        lines += ["", "## Slide titles", ""]
        for i in decks:
            titles = "; ".join("%d %s" % (n, t) for n, t in i["titles"][:60])
            lines.append("- **%s**: %s" % (i["file"], titles))

    trouble = [i for i in infos if i["problems"] or i["ocr_units"]
               or not i["ok"]]
    if trouble:
        lines += ["", "## Read with care", ""]
        for i in trouble:
            bits = []
            if not i["ok"]:
                bits.append("NOTHING EXTRACTED")
            if i["ocr_units"]:
                bits.append("OCR on %s" % ", ".join(i["ocr_units"][:12]))
            bits += i["problems"][:5]
            lines.append("- **%s**: %s" % (i["file"], "; ".join(bits)))

    lines += ["", "Materials folder: `%s`" % materials, ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------

def build_library(exam_path, force=False, verbose=True):
    """Extract every study file that is new or changed; rebuild the index.

    Returns a summary dict: sources, extracted, reused, removed, failed,
    infos, index.
    """
    ws = Workspace(exam_path)
    folder, manifest_path, convert_dir = _library_paths(ws)
    from .workspace import refuse_cloud_save
    refuse_cloud_save(folder, "the text extracted from the study materials")
    os.makedirs(folder, exist_ok=True)
    manifest = load_manifest(ws)
    old_files = manifest.get("files", {})
    ocr_now = ocr_available()

    summary = {"sources": 0, "extracted": [], "reused": [], "removed": [],
               "failed": [], "infos": [], "index": os.path.join(folder, INDEX),
               "folder": folder}

    if not os.path.isdir(ws.materials):
        if verbose:
            print("No study materials folder yet: %s" % ws.materials)
        return summary

    new_files = {}
    for root, dirs, files in os.walk(ws.materials):
        for fname in sorted(files):
            if _skip(fname):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in READERS:
                continue
            path = os.path.join(root, fname)
            rel = os.path.relpath(path, ws.materials).replace(os.sep, "/")
            summary["sources"] += 1
            entry = old_files.get(rel)

            if not force and _is_fresh(entry, path, ocr_now):
                new_files[rel] = entry
                summary["reused"].append(rel)
                summary["infos"].append(entry["info"])
                if not entry["info"]["ok"]:
                    summary["failed"].append((rel, "; ".join(
                        entry["info"]["problems"]) or "no text"))
                continue

            if verbose:
                print("  Extracting: %s" % rel)
            started = time.time()
            units, error = extract_units(path, convert_dir)
            markdown, info = render_source(rel, path, units, error)
            output_path = os.path.join(folder, safe_name(rel))
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(markdown)

            ocr_missing = any("OCR is not available" in (u.get("note") or "")
                              for u in units) or (bool(error) and "OCR" in error)
            fp = _fingerprint(path)
            new_files[rel] = {
                "version": EXTRACTOR_VERSION, "size": fp["size"],
                "mtime": fp["mtime"], "output": safe_name(rel),
                "output_path": output_path, "ocr_was_missing": ocr_missing,
                "seconds": round(time.time() - started, 1), "info": info,
            }
            summary["extracted"].append(rel)
            summary["infos"].append(info)
            if not info["ok"]:
                summary["failed"].append((rel, "; ".join(info["problems"])
                                          or "no text found"))

    # Remove outputs whose source has gone.
    for rel, entry in old_files.items():
        if rel not in new_files:
            try:
                os.remove(entry.get("output_path", ""))
            except OSError:
                pass
            summary["removed"].append(rel)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"version": EXTRACTOR_VERSION, "files": new_files}, f,
                  indent=1, ensure_ascii=False)
    with open(summary["index"], "w", encoding="utf-8") as f:
        f.write(render_index(ws, summary["infos"]))

    readme = os.path.join(folder, "README.txt")
    if not os.path.exists(readme):
        from .workspace import FOLDER_HELP
        with open(readme, "w", encoding="utf-8") as f:
            f.write(FOLDER_HELP["extracted"] + "\n")

    if verbose:
        print_summary(summary)
    return summary


def print_summary(summary):
    total_words = sum(i["words"] for i in summary["infos"])
    print("\n  Extracted text: %s" % summary["folder"])
    print("  %d source(s): %d extracted now, %d unchanged (reused), "
          "%d removed" % (summary["sources"], len(summary["extracted"]),
                          len(summary["reused"]), len(summary["removed"])))
    print("  %s words, ~%s tokens in total"
          % (format(total_words, ","),
             format(estimate_tokens(total_words), ",")))
    ocr = [i for i in summary["infos"] if i["ocr_units"]]
    if ocr:
        print("  OCR used on: %s" % ", ".join(i["file"] for i in ocr))
    if summary["failed"]:
        print("  !! Nothing could be extracted from %d file(s):"
              % len(summary["failed"]))
        for rel, why in summary["failed"]:
            print("     - %s: %s" % (rel, why[:110]))
    partial = [i for i in summary["infos"] if i["ok"] and i["problems"]]
    for i in partial:
        print("  !! %s: %s" % (i["file"], "; ".join(i["problems"])[:120]))
    print("  Index (start here): %s" % summary["index"])


# --------------------------------------------------------------------------
# Reading the library back
# --------------------------------------------------------------------------

HEADING = re.compile(r"^## (Page|Slide|Sheet) (\d+)(?::\s*(.*?))?(?: \(OCR\))?\s*$")


def library_units(md_path):
    """Read one extracted file back into (label, citation, text) sections."""
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()
    body = content.split("\n---\n", 1)[1] if "\n---\n" in content else content
    sections, label, cite, buf = [], "", "", []
    for line in body.split("\n"):
        m = HEADING.match(line)
        if m:
            if buf and "".join(buf).strip():
                sections.append((label, cite, "\n".join(buf).strip()))
            kind, number, title = m.group(1), int(m.group(2)), m.group(3)
            cite = {"Page": "p. %d", "Slide": "slide %d",
                    "Sheet": "sheet %d"}[kind] % number
            label = cite + (" (%s)" % title if title else "")
            buf = []
            continue
        buf.append(line)
    if buf and "".join(buf).strip():
        sections.append((label, cite, "\n".join(buf).strip()))
    return [(l, c, t) for l, c, t in sections
            if not re.match(r"^_\(no text", t)]


def cached_text(path):
    """Plain text of a study file from the library, if it is up to date.

    Used by the plagiarism check so a scanned PDF is OCR'd once, not on
    every run. Returns None when there is no fresh copy.
    """
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    exam = None
    for _ in range(6):
        if os.path.isfile(os.path.join(folder, "EXAM_CONFIG.md")):
            exam = folder
            break
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    if exam is None:
        return None
    ws = Workspace(exam)
    if not path.startswith(os.path.abspath(ws.materials) + os.sep):
        return None
    rel = os.path.relpath(path, ws.materials).replace(os.sep, "/")
    entry = load_manifest(ws).get("files", {}).get(rel)
    if not _is_fresh(entry, path, ocr_available()):
        return None
    return "\n\n".join(t for _, _, t in library_units(entry["output_path"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract every study file once into page-referenced text")
    parser.add_argument("exam_folder")
    parser.add_argument("--force", action="store_true",
                        help="Re-extract everything, even unchanged files")
    args = parser.parse_args()
    result = build_library(args.exam_folder, force=args.force)
    sys.exit(1 if result["failed"] else 0)
