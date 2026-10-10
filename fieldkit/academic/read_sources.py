"""
READ SOURCE MATERIALS - Turns every study file into clean, page-referenced text.

Every file is read into UNITS - pages of a PDF, slides of a presentation,
sheets of a workbook - and every unit keeps its number. That number is what
lets a claim be cited as (Author, 2024, p. 14) or traced to slide 7, so it is
never thrown away.

What is handled, because real lecture material contains all of it:

  PDF          text layer page by page; any page with no usable text but an
               image on it is OCR'd on its own (a handout with one scanned
               page no longer loses that page); running headers, footers and
               bare page numbers repeated on every page are removed; words
               split across lines are rejoined.
  PowerPoint   titles, text boxes, text inside grouped shapes (to any depth),
               tables, charts, SmartArt, speaker notes, and text inside
               pictures (OCR, pictures large enough to hold text only); slide
               footers, dates and slide numbers are dropped; boilerplate
               repeated on most slides is removed; shapes are read top to
               bottom, left to right.
  Word         paragraphs, headings (marked as headings) and tables.
  Excel        each sheet as a unit.
  Images       OCR.
  Old formats  .ppt, .doc and .xls are converted first - by LibreOffice if it
               is installed, otherwise by Microsoft Office itself.

The cleaning is deliberate: every repeated footer or stray page number is a
token a language model pays for and learns nothing from.

Other scripts use:
    extract_units(path)          -> (units, error)
    units_to_text(units)         -> text with [Page n] / [Slide n: title] lines
    read_file(path)              -> the same text, or an [ERROR ...] marker
    read_all_sources(exam)       -> every file in the study-materials folder

Usage:
    python read_sources.py <exam_folder>
    python read_sources.py <exam_folder> --full
    python read_sources.py --file "lecture 3.pptx"
"""
import io
import os
import re
import sys
import shutil
import argparse
import tempfile
import subprocess
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ERROR_PREFIX = "[ERROR"
NOTICE_PREFIX = "[NOTICE"

# A page with fewer words than this but an image on it is treated as scanned.
SCANNED_PAGE_WORDS = 25
# Pictures smaller than this share of the slide are logos and icons.
PICTURE_MIN_SHARE = 0.08
# OCR output shorter than this is noise, not text.
OCR_MIN_WORDS = 4
OCR_DPI = 300


def is_error_text(text):
    """True if a reader returned a diagnostic marker instead of real text."""
    return (not text or text.startswith(ERROR_PREFIX)
            or text.startswith(NOTICE_PREFIX))


def _unit(kind, number=None, title="", text="", ocr=False, note=""):
    return {"kind": kind, "number": number, "title": title, "text": text,
            "ocr": ocr, "note": note}


# ==========================================================================
# Cleaning
# ==========================================================================

PAGE_NUMBER_LINE = re.compile(
    r"^\s*(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$", re.IGNORECASE)
BULLET_START = re.compile(u"^\\s*(?:[-*\u2022\u25aa\u25cf\u2013o]|\\d+[.)]|[a-z][.)])\\s+")


def tidy_text(text):
    """Rejoin broken lines and words; collapse whitespace.

    PDF text arrives as hard-wrapped lines. Words split with a hyphen at a
    line end are rejoined, and a line that stops mid-sentence is joined to the
    next when that one carries on in lower case. Bullets and headings keep
    their own lines.
    """
    if not text:
        return ""
    text = text.replace("\r", "")
    text = re.sub(u"[\u00ad]", "", text)                       # soft hyphens
    text = re.sub(r"(\w)-\n\s*([a-z])", r"\1\2", text)         # hyphen breaks
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    # OCR often puts a blank line inside a sentence; close it up when the
    # line before stops mid-sentence and the next carries on in lower case.
    text = re.sub(r"([^.:;!?\n ])[ ]*\n[ ]*\n[ ]*([a-z])", r"\1\n\2", text)

    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        if (out and out[-1] and not BULLET_START.match(line)
                and not re.search(r"[.:;!?]\s*$", out[-1])
                and re.match(r"^[a-z(\"'\u2018\u201c]", line)):
            out[-1] = out[-1] + " " + line
        else:
            out.append(line)
    return "\n".join(out).strip()


def _normalise_line(line):
    """A line reduced to its shape, so 'Page 3' and 'Page 4' compare equal."""
    return re.sub(r"\d+", "#", line.strip().lower())


def remove_repeated_lines(units, min_units=3, share=0.5, edge=3):
    """Drop running headers and footers repeated across most units.

    Only the first and last few lines of each unit are candidates - that is
    where headers and footers live - so a phrase genuinely repeated in the
    body is never removed.
    """
    texts = [u for u in units if u["text"].strip()]
    if len(texts) < min_units:
        return units
    counts = Counter()
    for u in texts:
        lines = [l for l in u["text"].split("\n") if l.strip()]
        candidates = set(_normalise_line(l) for l in lines[:edge] + lines[-edge:])
        counts.update(candidates)
    threshold = max(min_units, int(len(texts) * share + 0.999))
    repeated = {k for k, v in counts.items() if v >= threshold and k.strip("#")}
    if not repeated:
        return units
    for u in texts:
        lines = u["text"].split("\n")
        keep = []
        for idx, line in enumerate(lines):
            near_edge = idx < edge or idx >= len(lines) - edge
            if (near_edge and _normalise_line(line) in repeated
                    and not _looks_like_sentence(line)):
                continue
            keep.append(line)
        u["text"] = "\n".join(keep).strip()
    return units


def _looks_like_sentence(line):
    """A line ending like a sentence is content, never a header or footer.

    Headers and footers ("Module X | University", "Page 3") rarely end in a
    full stop. Keeping a repeated sentence costs a few tokens; deleting a
    real one loses content, so this errs towards keeping.
    """
    line = line.strip()
    return bool(re.search(r"[.!?]['\")”]?$", line)) and len(line.split()) >= 3


def strip_page_numbers(units):
    for u in units:
        lines = [l for l in u["text"].split("\n")
                 if not PAGE_NUMBER_LINE.match(l)]
        u["text"] = "\n".join(lines).strip()
    return units


# ==========================================================================
# OCR
# ==========================================================================

def _ocr_image(image):
    import pytesseract
    return pytesseract.image_to_string(image)


def ocr_available():
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        langs = pytesseract.get_languages(config="")
        return "eng" in langs
    except Exception:
        return False


# ==========================================================================
# PDF
# ==========================================================================

def _pdf_units(path):
    try:
        import pdfplumber
    except ImportError:
        return [], "[ERROR: pdfplumber is not installed]"

    units, scanned = [], []
    try:
        with pdfplumber.open(path) as pdf:
            for number, page in enumerate(pdf.pages, 1):
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                page_area = float(page.width * page.height) or 1.0
                image_area = 0.0
                for im in page.images or []:
                    try:
                        image_area += abs((im["x1"] - im["x0"]) *
                                          (im["bottom"] - im["top"]))
                    except Exception:
                        pass
                units.append(_unit("page", number, text=text))
                words = len(text.split())
                # No text but an image of any size: scanned. A little text
                # (a running header, say) beside a large image: also scanned.
                if (words == 0 and page.images) or (
                        words < SCANNED_PAGE_WORDS
                        and image_area / page_area > 0.15):
                    scanned.append(number)
                elif words == 0 and not page.images:
                    units[-1]["note"] = "blank page"
    except Exception as e:
        msg = str(e)
        if "password" in msg.lower() or "encrypt" in msg.lower():
            return [], ("[ERROR: %s is password-protected - save an unlocked "
                        "copy]" % os.path.basename(path))
        return [], "[ERROR reading %s: %s]" % (os.path.basename(path), e)

    if scanned:
        if ocr_available():
            try:
                from pdf2image import convert_from_path
                for number in scanned:
                    images = convert_from_path(path, dpi=OCR_DPI,
                                               first_page=number,
                                               last_page=number)
                    ocr_text = "\n".join(_ocr_image(im) for im in images)
                    u = units[number - 1]
                    if len(ocr_text.split()) >= OCR_MIN_WORDS:
                        u["text"] = (u["text"] + "\n" + ocr_text).strip()
                        u["ocr"] = True
                    elif not u["text"].strip():
                        u["note"] = "scanned page, OCR found no text"
            except Exception as e:
                for number in scanned:
                    if not units[number - 1]["text"].strip():
                        units[number - 1]["note"] = ("scanned page could not "
                                                     "be OCR'd (%s)" % e)
        else:
            for number in scanned:
                if not units[number - 1]["text"].strip():
                    units[number - 1]["note"] = ("scanned page - OCR is not "
                                                 "available on this machine")

    for u in units:
        u["text"] = tidy_text(u["text"])
    units = strip_page_numbers(units)
    units = remove_repeated_lines(units)
    return units, None


# ==========================================================================
# PowerPoint
# ==========================================================================

DIAGRAM_URI = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _walk_shapes(shapes):
    """Every shape, descending into groups to any depth."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            for inner in _walk_shapes(shape.shapes):
                yield inner
        else:
            yield shape


def _position(shape):
    return ((shape.top or 0) // 91440, (shape.left or 0))   # rows of 0.1in


def _is_furniture(shape):
    """Slide number, footer and date placeholders: not content."""
    try:
        from pptx.enum.shapes import PP_PLACEHOLDER
        if shape.is_placeholder:
            return shape.placeholder_format.type in (
                PP_PLACEHOLDER.SLIDE_NUMBER, PP_PLACEHOLDER.FOOTER,
                PP_PLACEHOLDER.DATE)
    except Exception:
        pass
    return False


def _smartart_text(shape, slide):
    """Text held in a SmartArt diagram's data part."""
    try:
        xml = shape._element
        rel = xml.find(".//{http://schemas.openxmlformats.org/drawingml/2006/"
                       "diagram}relIds")
        if rel is None:
            return ""
        rid = rel.get("{%s}dm" % R_NS)
        part = slide.part.related_part(rid)
        from lxml import etree
        root = etree.fromstring(part.blob)
        return "\n".join(t.text.strip() for t in root.iter("{%s}t" % A_NS)
                         if t.text and t.text.strip())
    except Exception:
        return ""


def _chart_text(shape):
    try:
        chart = shape.chart
        parts = []
        if chart.has_title and chart.chart_title.has_text_frame:
            title = chart.chart_title.text_frame.text.strip()
            if title:
                parts.append("Chart: " + title)
        for plot in chart.plots:
            cats = [str(c) for c in plot.categories if str(c).strip()]
            if cats:
                parts.append("Categories: " + ", ".join(cats[:30]))
            names = [s.name for s in plot.series if s.name]
            if names:
                parts.append("Series: " + ", ".join(names[:15]))
        return "\n".join(parts)
    except Exception:
        return ""


def _picture_text(shape, slide_area):
    """OCR a picture big enough to hold text; ignore logos and icons."""
    try:
        share = (shape.width * shape.height) / float(slide_area)
    except Exception:
        return ""
    if share < PICTURE_MIN_SHARE:
        return ""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(shape.image.blob)) as img:
            img = img.convert("RGB")
            if img.width < 600:
                factor = 600.0 / img.width
                img = img.resize((600, int(img.height * factor)))
            text = _ocr_image(img)
        return text if len(text.split()) >= OCR_MIN_WORDS else ""
    except Exception:
        return ""


def _pptx_units(path):
    try:
        from pptx import Presentation
    except ImportError:
        return [], "[ERROR: python-pptx is not installed]"
    try:
        prs = Presentation(path)
    except Exception as e:
        return [], "[ERROR reading %s: %s]" % (os.path.basename(path), e)

    slide_area = float(prs.slide_width * prs.slide_height) or 1.0
    can_ocr = ocr_available()
    units = []

    for number, slide in enumerate(prs.slides, 1):
        title = ""
        try:
            if slide.shapes.title is not None:
                title = slide.shapes.title.text_frame.text.strip()
        except Exception:
            pass

        blocks, used_ocr = [], False
        shapes = [s for s in _walk_shapes(slide.shapes) if not _is_furniture(s)]
        for shape in sorted(shapes, key=_position):
            text = ""
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if title and text == title:
                    continue
            elif getattr(shape, "has_table", False) and shape.has_table:
                rows = []
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        rows.append(" | ".join(cells))
                text = "\n".join(rows)
            elif getattr(shape, "has_chart", False) and shape.has_chart:
                text = _chart_text(shape)
            elif DIAGRAM_URI in shape._element.xml[:4000] \
                    if hasattr(shape, "_element") else False:
                text = _smartart_text(shape, slide)
            elif hasattr(shape, "image") and can_ocr:
                text = _picture_text(shape, slide_area)
                if text:
                    used_ocr = True
                    text = "[Text in a picture] " + text.strip()
            if text:
                blocks.append(text)

        notes = ""
        try:
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
        except Exception:
            pass
        body = "\n".join(blocks)
        if notes:
            body = (body + "\nSpeaker notes: " + notes).strip()
        units.append(_unit("slide", number, title, tidy_text(body), used_ocr))

    units = remove_repeated_lines(units, edge=50)
    return units, None


# ==========================================================================
# Word, Excel, text, images
# ==========================================================================

def _docx_units(path):
    try:
        from docx import Document
    except ImportError:
        return [], "[ERROR: python-docx is not installed]"
    try:
        doc = Document(path)
    except Exception as e:
        return [], "[ERROR reading %s: %s]" % (os.path.basename(path), e)

    lines = []
    try:
        blocks = list(doc.iter_inner_content())
    except AttributeError:
        blocks = list(doc.paragraphs) + list(doc.tables)
    for block in blocks:
        if block.__class__.__name__ == "Table":
            for row in block.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
            continue
        text = block.text.strip()
        if not text:
            continue
        style = (block.style.name if block.style is not None else "").lower()
        if style.startswith("heading") or style == "title":
            lines.append("")
            lines.append("### " + text)
        else:
            lines.append(text)
    return [_unit("document", None, text="\n".join(lines).strip())], None


def _xlsx_units(path):
    try:
        from openpyxl import load_workbook
    except ImportError:
        return [], "[ERROR: openpyxl is not installed]"
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        return [], "[ERROR reading %s: %s]" % (os.path.basename(path), e)
    units = []
    for number, ws in enumerate(wb.worksheets, 1):
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None and str(c).strip()]
            if cells:
                rows.append(" | ".join(cells))
        units.append(_unit("sheet", number, ws.title, "\n".join(rows)))
    wb.close()
    return units, None


def _text_units(path):
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return [_unit("text", None, text=f.read().strip())], None
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception as e:
            return [], "[ERROR reading %s: %s]" % (os.path.basename(path), e)
    return [], "[ERROR: could not decode %s]" % os.path.basename(path)


def _image_units(path):
    if not ocr_available():
        return [], ("[ERROR: OCR unavailable for %s - Tesseract with English "
                    "language data is needed]" % os.path.basename(path))
    try:
        from PIL import Image
        with Image.open(path) as img:
            text = _ocr_image(img.convert("RGB"))
    except Exception as e:
        return [], "[ERROR reading %s: %s]" % (os.path.basename(path), e)
    if len(text.split()) < OCR_MIN_WORDS:
        return [], "[NOTICE: no text detected in %s]" % os.path.basename(path)
    return [_unit("image", None, text=tidy_text(text), ocr=True)], None


# ==========================================================================
# Old Office formats
# ==========================================================================

LEGACY = {".ppt": ("pptx", 24, "PowerPoint.Application"),
          ".doc": ("docx", 16, "Word.Application"),
          ".xls": ("xlsx", 51, "Excel.Application")}


def _find_soffice():
    for name in ("soffice", "soffice.com", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                      r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                      os.path.expanduser(r"~\scoop\apps\libreoffice\current"
                                         r"\LibreOffice\program\soffice.exe")):
        if os.path.isfile(candidate):
            return candidate
    return None


def _ps_quote(value):
    return "'" + value.replace("'", "''") + "'"


def _convert_with_office(path, target, ext):
    """Save As through Microsoft Office itself, driven by PowerShell COM."""
    new_ext, fmt, app = LEGACY[ext]
    src, dst = _ps_quote(os.path.abspath(path)), _ps_quote(os.path.abspath(target))
    if ext == ".ppt":
        body = ("$d = $a.Presentations.Open(%s, $true, $false, $false); "
                "$d.SaveAs(%s, %d); $d.Close()" % (src, dst, fmt))
    elif ext == ".doc":
        body = ("$a.Visible = $false; $d = $a.Documents.Open(%s, $false, $true); "
                "$d.SaveAs2(%s, %d); $d.Close()" % (src, dst, fmt))
    else:
        body = ("$a.Visible = $false; $a.DisplayAlerts = $false; "
                "$d = $a.Workbooks.Open(%s); $d.SaveAs(%s, %d); $d.Close()"
                % (src, dst, fmt))
    script = ("$ErrorActionPreference = 'Stop'; "
              "$a = New-Object -ComObject %s; try { %s } finally { $a.Quit() }"
              % (app, body))
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-Command", script], check=True, timeout=180,
                       capture_output=True)
        return os.path.isfile(target)
    except Exception:
        return False


def convert_legacy(path, out_dir=None):
    """Convert .ppt/.doc/.xls to the modern format. Returns (path, how)."""
    ext = os.path.splitext(path)[1].lower()
    new_ext = LEGACY[ext][0]
    out_dir = out_dir or tempfile.mkdtemp(prefix="harness_convert_")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    target = os.path.join(out_dir, stem + "." + new_ext)

    if os.path.isfile(target) and os.path.getmtime(target) >= os.path.getmtime(path):
        return target, "cached conversion"

    soffice = _find_soffice()
    if soffice:
        try:
            subprocess.run([soffice, "--headless", "--convert-to", new_ext,
                            "--outdir", out_dir, path], check=True,
                           timeout=240, capture_output=True)
            if os.path.isfile(target):
                return target, "LibreOffice"
        except Exception:
            pass

    if os.name == "nt" and _convert_with_office(path, target, ext):
        return target, "Microsoft Office"

    return None, ("the old %s format needs converting, and neither "
                  "LibreOffice nor Microsoft Office could do it. Open the "
                  "file and Save As %s." % (ext, new_ext.upper()))


def _legacy_units(path, convert_dir=None):
    converted, how = convert_legacy(path, convert_dir)
    if converted is None:
        return [], "[ERROR: %s - %s]" % (os.path.basename(path), how)
    units, error = extract_units(converted)
    for u in units:
        u["note"] = (u["note"] + "; " if u["note"] else "") + \
            "converted from %s by %s" % (os.path.splitext(path)[1], how)
    return units, error


# ==========================================================================
# Public interface
# ==========================================================================

READERS = {
    ".pdf": _pdf_units,
    ".pptx": _pptx_units,
    ".docx": _docx_units,
    ".xlsx": _xlsx_units,
    ".xlsm": _xlsx_units,
    ".txt": _text_units,
    ".md": _text_units,
    ".csv": _text_units,
    ".rtf": _text_units,
    ".png": _image_units,
    ".jpg": _image_units,
    ".jpeg": _image_units,
    ".tiff": _image_units,
    ".tif": _image_units,
    ".bmp": _image_units,
    ".webp": _image_units,
    ".ppt": _legacy_units,
    ".doc": _legacy_units,
    ".xls": _legacy_units,
}

SUPPORTED_EXTENSIONS = tuple(sorted(READERS.keys()))


def extract_units(path, convert_dir=None):
    """Read one file into units. Returns (units, error_or_None)."""
    ext = os.path.splitext(path)[1].lower()
    reader = READERS.get(ext)
    if reader is None:
        return [], "[ERROR: %s format is not supported]" % ext
    if ext in LEGACY:
        return reader(path, convert_dir)
    return reader(path)


def unit_label(unit):
    """'[Page 14]', '[Slide 7: Title]', '[Sheet 2: Data]' - or '' for one-part files."""
    if unit["number"] is None:
        return ""
    name = {"page": "Page", "slide": "Slide", "sheet": "Sheet"}.get(
        unit["kind"], unit["kind"].title())
    label = "%s %d" % (name, unit["number"])
    if unit.get("title"):
        label += ": " + unit["title"]
    if unit.get("ocr"):
        label += " (OCR)"
    return "[" + label + "]"


def units_to_text(units, markers=True):
    parts = []
    for u in units:
        if not u["text"].strip():
            continue
        if markers and u["number"] is not None:
            parts.append(unit_label(u))
        parts.append(u["text"])
        parts.append("")
    return "\n".join(parts).strip()


MARKER_LINE = re.compile(r"^\[(?:Page|Slide|Sheet) \d+[^\]]*\]$", re.MULTILINE)


def strip_markers(text):
    return MARKER_LINE.sub("", text)


def read_file(path, markers=True, convert_dir=None):
    """Read any supported file to text; None if the format is unsupported."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in READERS:
        return None
    units, error = extract_units(path, convert_dir)
    if error:
        return error
    text = units_to_text(units, markers)
    if not text.strip():
        return "[NOTICE: no text found in %s]" % os.path.basename(path)
    return text


SCAFFOLD_FILES = ("README.MD", "README.TXT")


def _role(path):
    """'brief', 'notes' or 'source', from the folder a file sits in."""
    try:
        from .workspace import BRIEF, NOTES
    except ImportError:
        return "source"
    parts = [p.lower() for p in os.path.normpath(path).split(os.sep)]
    if BRIEF.lower() in parts:
        return "brief"
    if NOTES.lower() in parts:
        return "notes"
    return "source"


def _skip(fname):
    """Office lock files, hidden files, and the harness's own folder READMEs.

    The READMEs matter: every folder the harness creates holds a README.txt
    explaining it, and reading those as study sources would feed the
    harness's own words into the plagiarism check.
    """
    return (fname.startswith("~") or fname.startswith(".")
            or fname.upper() in SCAFFOLD_FILES)


def materials_folder(exam_path, verbose=True):
    """The study-materials folder of an exam, or a plain folder of sources."""
    try:
        from .workspace import Workspace
        ws = Workspace(exam_path)
        if os.path.isdir(ws.materials):
            return ws.materials
    except ImportError:
        pass
    if os.path.isfile(os.path.join(exam_path, "EXAM_CONFIG.md")):
        # An exam folder with no materials folder yet. Never fall back to
        # reading the whole exam folder, drafts and output included.
        if verbose:
            print("No study materials folder yet in %s" % exam_path)
        return None
    if os.path.isdir(exam_path):
        return exam_path
    print("ERROR: %s does not exist." % exam_path)
    return None


def read_all_sources(exam_path, verbose=True):
    """Read every supported file in the exam's study-materials folder.

    Prefer extract_library.build_library() for anything repeated: it reads
    each file once and caches the result. This reads everything afresh.
    """
    source_dir = materials_folder(exam_path, verbose)
    if source_dir is None:
        return {}

    results, unsupported, failed = {}, [], []
    for root, dirs, files in os.walk(source_dir):
        for fname in sorted(files):
            if _skip(fname):
                continue
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext not in READERS:
                unsupported.append(fname)
                if verbose:
                    print("  Skipped: %s (unsupported format: %s)" % (fname, ext))
                continue
            if verbose:
                print("  Reading: %s (%s)" % (fname, ext))
            units, error = extract_units(fpath)
            text = error or units_to_text(units)
            if not error and not text.strip():
                text = "[NOTICE: no text found in %s]" % fname
            if is_error_text(text):
                failed.append((fname, text))
                if verbose:
                    print("    %s" % text)
            rel = os.path.relpath(fpath, source_dir).replace(os.sep, "/")
            results[rel] = {
                "path": fpath,
                "type": ext,
                "text": text,
                "units": units,
                "word_count": len(strip_markers(text).split()) if text else 0,
                "ok": not is_error_text(text),
                "role": _role(fpath),
            }

    if verbose:
        total_words = sum(r["word_count"] for r in results.values() if r["ok"])
        print("\nFiles read: %d  (%s words extracted)"
              % (len(results), format(total_words, ",")))
        if failed:
            print("Files that could NOT be read: %d" % len(failed))
            for fname, why in failed:
                print("  !! %s -> %s" % (fname, why))
            print("These files are invisible to the plagiarism check and to")
            print("the AI. Convert or replace them before drafting.")
        if unsupported:
            print("Unsupported files ignored: %s" % ", ".join(unsupported))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read study materials")
    parser.add_argument("exam_path", nargs="?",
                        help="Exam folder (or a folder of sources)")
    parser.add_argument("--full", action="store_true",
                        help="Print the entire extracted text")
    parser.add_argument("--file", default=None, help="Read one file only")
    args = parser.parse_args()

    if args.file:
        print(read_file(args.file))
        sys.exit(0)
    if not args.exam_path:
        parser.error("give an exam folder, or --file")
    for fname, data in read_all_sources(args.exam_path).items():
        print("\n" + "=" * 60)
        print("FILE: %s (%s words)" % (fname, format(data["word_count"], ",")))
        print("=" * 60)
        text = data["text"]
        print(text if args.full else text[:500] + ("..." if len(text) > 500 else ""))
