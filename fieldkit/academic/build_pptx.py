"""
POWERPOINT BUILDER - Academic presentations and academic posters.

Two classes, both writing .pptx:

  AcademicPresentation - a 16:9 slide deck. Key points on the slide, the full
      prose in the speaker notes, so nothing written in the draft is lost
      when it is condensed for display.

  AcademicPoster - a single-page conference poster at A0, A1 or A2, portrait
      or landscape, laid out in columns. Font sizes follow the usual
      legibility guidance: a poster is read standing a metre away, so body
      text is 24pt or larger, never 12pt.

Both can be built straight from the report markdown, which is what
run_pipeline.py does for the `presentation` and `poster` document types:

    build_presentation_from_markdown("report.md", "deck.pptx", "12345678")
    build_poster_from_markdown("report.md", "poster.pptx", "12345678",
                               size="A1", orientation="landscape")

Or driven directly:

    from .build_pptx import AcademicPresentation, AcademicPoster
    pres = AcademicPresentation("My Title", "12345678", "HWSC4005")
    pres.add_title_slide()
    pres.add_content_slide("Findings", ["Point one", "Point two"])
    pres.save("deck.pptx")

Anonymous marking applies to both: the Student ID goes on the
title slide or poster banner, never a name.
"""
import os
import re
import sys
import math
import argparse

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FONT = "Arial"

# ISO 216 sizes in inches. PowerPoint accepts up to 56in, so A0 fits.
PAGE_SIZES = {
    "A0": (33.11, 46.81),
    "A1": (23.39, 33.11),
    "A2": (16.54, 23.39),
    "A3": (11.69, 16.54),
}

# Poster type scale: read from about a metre away.
POSTER_FONTS = {
    "A0": {"title": 96, "subtitle": 32, "heading": 48, "body": 28, "refs": 18},
    "A1": {"title": 72, "subtitle": 26, "heading": 36, "body": 24, "refs": 14},
    "A2": {"title": 54, "subtitle": 20, "heading": 28, "body": 18, "refs": 11},
    "A3": {"title": 40, "subtitle": 16, "heading": 22, "body": 14, "refs": 9},
}

ACCENT = RGBColor(0x1F, 0x3D, 0x6E)      # dark academic blue
INK = RGBColor(0, 0, 0)
MUTED = RGBColor(0x44, 0x44, 0x44)
BANNER_TEXT = RGBColor(0xFF, 0xFF, 0xFF)

from .reference_auditor import REF_HEADING                 # every language's heading

# Set by layout.apply(): labels and the references heading in the work language.
LAYOUT = None


def _labels():
    from .layout import current, heading
    lay = LAYOUT or current({})
    return dict(lay["labels"], references=heading("References", lay["language"]))

MAX_BULLETS_PER_SLIDE = 6


# ==========================================================================
# Shared helpers
# ==========================================================================

def _set_font(run, size=18, bold=False, color=INK, italic=False):
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color


def _strip_markdown(text):
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"[*_`]", "", text)


def _ensure_dir(path):
    from .workspace import refuse_cloud_save
    refuse_cloud_save(path, "the presentation")
    out_dir = os.path.dirname(os.path.abspath(path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)


def _estimate_height_in(text, font_pt, width_in):
    """Roughly how tall a block of text will be, for column flow."""
    avg_char_in = font_pt * 0.5 / 72.0
    chars_per_line = max(8, int(width_in / avg_char_in))
    lines = 0
    for para in text.split("\n"):
        para = para.strip()
        lines += max(1, int(math.ceil(len(para) / float(chars_per_line))))
    return lines * (font_pt * 1.35 / 72.0)


# ==========================================================================
# Markdown parsing, shared by the deck and the poster
# ==========================================================================

def parse_markdown_sections(md_text):
    """Split a report into (title, sections, references).

    sections is a list of dicts: {heading, prose, bullets, tables, images}.
    """
    md_text = re.sub(r"<!--.*?-->", "", md_text, flags=re.DOTALL)

    title = "[Title]"
    m = re.search(r"^#\s+(.*\S)\s*$", md_text, flags=re.MULTILINE)
    if m:
        title = _strip_markdown(m.group(1)).strip()

    # Separate the reference list.
    refs = []
    split = re.search(r"(?:^|\n)[ \t]*#*[ \t]*" + REF_HEADING +
                      r"[ \t]*:?[ \t]*\n", md_text, flags=re.IGNORECASE)
    body = md_text
    if split:
        body = md_text[:split.start()]
        ref_text = md_text[split.end():]
        current = []
        for raw in ref_text.splitlines():
            if not raw.strip():
                if current:
                    refs.append(" ".join(current))
                    current = []
                continue
            if re.match(r"^[ \t]{2,}", raw) and current:
                current.append(_strip_markdown(raw).strip())
            else:
                if current:
                    refs.append(" ".join(current))
                current = [_strip_markdown(raw).strip()]
        if current:
            refs.append(" ".join(current))

    sections = []
    current = None
    table_buffer = []

    def flush_table():
        if current is not None and table_buffer:
            current["tables"].append(list(table_buffer))
        table_buffer[:] = []

    for raw in body.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        heading = re.match(r"^(#{2,6})\s+(.*\S)\s*$", stripped)
        if heading:
            flush_table()
            current = {
                "heading": re.sub(r"^\s*\d+(?:\.\d+)*[.)]\s*", "",
                                  _strip_markdown(heading.group(2)).strip()),
                "prose": [],
                "bullets": [],
                "tables": [],
                "images": [],
            }
            sections.append(current)
            continue

        if current is None:
            continue

        if not stripped:
            flush_table()
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            if not re.match(r"^\s*\|[\s:\-|]+\|\s*$", stripped):
                table_buffer.append([c.strip() for c in
                                     _strip_markdown(stripped).strip("|").split("|")])
            continue
        flush_table()

        img = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)", stripped)
        if img:
            current["images"].append({"caption": img.group(1),
                                      "path": img.group(2)})
            continue

        bullet = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", line)
        if bullet:
            current["bullets"].append(_strip_markdown(bullet.group(1)).strip())
            continue

        if stripped.startswith(">"):
            current["prose"].append(_strip_markdown(
                stripped.lstrip("> ")).strip())
            continue

        current["prose"].append(_strip_markdown(stripped).strip())

    flush_table()
    from .reference_auditor import sort_key           # the reference check's own order
    return title, sections, sorted(refs, key=sort_key)


def _finalise(path, deck):
    """The work language's tags and honest file properties (see office_finalise)."""
    from .office_finalise import finalise

    def real(value):
        return "" if not value or "[" in value else value
    # No name and no Student ID in the file properties (see build_word.py).
    finalise(path, author="", title=real(deck.title),
             subject=real(deck.module))


def _sentences(text):
    """Split prose into sentences, keeping citation brackets intact."""
    if not text:
        return []
    protected = re.sub(r"\b(pp?)\.\s", r"\1<DOT> ", text)
    protected = re.sub(r"\b(et al|e\.g|i\.e|cf|vs|ed|eds|no)\.\s",
                       r"\1<DOT> ", protected)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", protected)
    return [p.replace("<DOT>", ".").strip() for p in parts if p.strip()]


def _condense(section, limit=MAX_BULLETS_PER_SLIDE):
    """Bullets for display: the section's own bullets, else its sentences."""
    if section["bullets"]:
        return list(section["bullets"])
    prose = " ".join(section["prose"])
    return _sentences(prose)[:max(limit * 3, 12)]


# ==========================================================================
# Slide deck
# ==========================================================================

class AcademicPresentation(object):
    """A 16:9 academic slide deck."""

    def __init__(self, title="Academic Presentation",
                 student_id="[Student ID]", module="[Module]"):
        self.prs = Presentation()
        self.prs.slide_width = Inches(13.333)
        self.prs.slide_height = Inches(7.5)
        self.title = title
        self.student_id = student_id
        self.module = module

    def _blank(self):
        return self.prs.slides.add_slide(self.prs.slide_layouts[6])

    def _set_notes(self, slide, text):
        if not text:
            return
        # Touching notes_slide creates it; has_notes_slide is False until then.
        slide.notes_slide.notes_text_frame.text = text

    def add_title_slide(self, subtitle=None):
        slide = self._blank()
        box = slide.shapes.add_textbox(Inches(1), Inches(2.1),
                                       Inches(11.333), Inches(2.4))
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        _set_font(p.add_run(), 36, bold=True)
        p.runs[0].text = self.title

        p2 = tf.add_paragraph()
        p2.alignment = PP_ALIGN.CENTER
        run = p2.add_run()
        lab = _labels()
        run.text = "\n%s: %s\n%s: %s" % (lab["student_id"], self.student_id,
                                         lab["module"], self.module)
        _set_font(run, 18, color=MUTED)

        if subtitle:
            p3 = tf.add_paragraph()
            p3.alignment = PP_ALIGN.CENTER
            run = p3.add_run()
            run.text = subtitle
            _set_font(run, 16, color=MUTED, italic=True)
        return slide

    def add_content_slide(self, heading, bullet_points, notes=None):
        slide = self._blank()
        box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3),
                                       Inches(12.3), Inches(1))
        p = box.text_frame.paragraphs[0]
        run = p.add_run()
        run.text = heading
        _set_font(run, 28, bold=True, color=ACCENT)

        box2 = slide.shapes.add_textbox(Inches(0.8), Inches(1.5),
                                        Inches(11.5), Inches(5.5))
        tf = box2.text_frame
        tf.word_wrap = True
        for i, point in enumerate(bullet_points):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.space_after = Pt(10)
            run = p.add_run()
            run.text = u"\u2022  " + point
            _set_font(run, 18)

        self._set_notes(slide, notes)
        return slide

    def add_table_slide(self, heading, rows, notes=None):
        slide = self._blank()
        box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3),
                                       Inches(12.3), Inches(1))
        run = box.text_frame.paragraphs[0].add_run()
        run.text = heading
        _set_font(run, 28, bold=True, color=ACCENT)

        if rows:
            cols = max(len(r) for r in rows)
            shape = slide.shapes.add_table(len(rows), cols, Inches(0.8),
                                           Inches(1.6), Inches(11.5),
                                           Inches(0.5 * len(rows)))
            for r, row in enumerate(rows):
                for c in range(cols):
                    cell = shape.table.cell(r, c)
                    cell.text = row[c] if c < len(row) else ""
                    for p in cell.text_frame.paragraphs:
                        for run in p.runs:
                            _set_font(run, 14, bold=(r == 0))
        self._set_notes(slide, notes)
        return slide

    def add_reference_slide(self, references):
        """References, split across as many slides as they need."""
        slides = []
        per_slide = 8
        chunks = [references[i:i + per_slide]
                  for i in range(0, len(references), per_slide)] or [[]]
        for index, chunk in enumerate(chunks):
            slide = self._blank()
            box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3),
                                           Inches(12.3), Inches(1))
            run = box.text_frame.paragraphs[0].add_run()
            run.text = _labels()["references"] + (" (%d)" % (index + 1) if index else "")
            _set_font(run, 28, bold=True, color=ACCENT)

            box2 = slide.shapes.add_textbox(Inches(0.5), Inches(1.3),
                                            Inches(12.3), Inches(5.7))
            tf = box2.text_frame
            tf.word_wrap = True
            for i, ref in enumerate(chunk):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.space_after = Pt(6)
                run = p.add_run()
                run.text = ref
                _set_font(run, 12)
            slides.append(slide)
        return slides

    def save(self, output_path, verbose=True):
        _ensure_dir(output_path)
        self.prs.save(output_path)
        _finalise(output_path, self)
        if verbose:
            print("PowerPoint saved: %s" % output_path)
            print("  Slides: %d" % len(self.prs.slides._sldIdLst))
        return output_path


def build_presentation_from_markdown(md_path, output_path,
                                     student_id="[Student ID]",
                                     module="[Module Code]", verbose=True):
    """Build a deck from the report markdown: one slide per section.

    The slide carries the key points; the section's full prose goes into the
    speaker notes, so condensing for display never loses what was written.
    """
    with open(md_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    title, sections, refs = parse_markdown_sections(text)
    pres = AcademicPresentation(title, student_id, module)
    pres.add_title_slide()

    for section in sections:
        prose = " ".join(section["prose"]).strip()
        points = _condense(section)

        if not points and not section["tables"] and not section["images"]:
            continue

        chunks = [points[i:i + MAX_BULLETS_PER_SLIDE]
                  for i in range(0, len(points), MAX_BULLETS_PER_SLIDE)]
        if not chunks and not section["tables"]:
            chunks = [[]]                  # an image-only section keeps its slide
        for index, chunk in enumerate(chunks):
            heading = section["heading"] + (" (%d)" % (index + 1) if index else "")
            pres.add_content_slide(heading, chunk,
                                   notes=prose if index == 0 else None)

        for rows in section["tables"]:
            pres.add_table_slide(section["heading"], rows,
                                 notes=prose if not chunks else None)

    if refs:
        pres.add_reference_slide(refs)

    pres.save(output_path, verbose=False)
    if verbose:
        print("Presentation saved: %s" % output_path)
        print("  Slides: %d (title + %d section slide(s) + references)"
              % (len(pres.prs.slides._sldIdLst), len(sections)))
        print("  Full prose preserved in the speaker notes.")
        print("  Student ID on the title slide, no name.")
    return output_path


# ==========================================================================
# Academic poster
# ==========================================================================

class AcademicPoster(object):
    """A single-page conference poster in columns, at A0/A1/A2/A3."""

    def __init__(self, title="Academic Poster", student_id="[Student ID]",
                 module="[Module]", size="A1", orientation="landscape",
                 columns=3):
        size = (size or "A1").upper()
        if size not in PAGE_SIZES:
            raise ValueError("unknown poster size %r - choose from %s"
                             % (size, ", ".join(sorted(PAGE_SIZES))))
        orientation = (orientation or "landscape").lower()
        if orientation not in ("portrait", "landscape"):
            raise ValueError("orientation must be 'portrait' or 'landscape'")

        short_in, long_in = PAGE_SIZES[size]
        if orientation == "landscape":
            width_in, height_in = long_in, short_in
        else:
            width_in, height_in = short_in, long_in

        self.prs = Presentation()
        self.prs.slide_width = Inches(width_in)
        self.prs.slide_height = Inches(height_in)
        self.slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])

        self.size = size
        self.orientation = orientation
        self.width_in = width_in
        self.height_in = height_in
        self.fonts = POSTER_FONTS[size]
        self.title = title
        self.student_id = student_id
        self.module = module

        self.margin_in = max(0.8, width_in * 0.025)
        self.gutter_in = max(0.4, width_in * 0.015)
        self.columns = max(1, int(columns))

        usable = width_in - 2 * self.margin_in - self.gutter_in * (self.columns - 1)
        self.col_width_in = usable / float(self.columns)

        self._banner_height_in = 0.0
        self._add_banner()
        self._cursor = [self._banner_height_in + self.gutter_in] * self.columns
        self._current_column = 0
        self.sections_placed = 0
        self.overflow = []

    # -- layout ----------------------------------------------------------

    def _add_banner(self):
        """Title band across the top: title, Student ID, module. No name."""
        height_in = self.height_in * 0.10
        band = self.slide.shapes.add_textbox(
            Inches(0), Inches(0), Inches(self.width_in), Inches(height_in))
        fill = band.fill
        fill.solid()
        fill.fore_color.rgb = ACCENT
        band.line.fill.background()

        tf = band.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf.margin_left = Inches(self.margin_in)
        tf.margin_right = Inches(self.margin_in)

        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = self.title
        _set_font(run, self.fonts["title"], bold=True, color=BANNER_TEXT)

        p2 = tf.add_paragraph()
        p2.alignment = PP_ALIGN.CENTER
        run = p2.add_run()
        lab = _labels()
        run.text = "%s: %s   |   %s: %s" % (lab["student_id"], self.student_id,
                                             lab["module"], self.module)
        _set_font(run, self.fonts["subtitle"], color=BANNER_TEXT)

        self._banner_height_in = height_in

    def _column_left_in(self, index):
        return self.margin_in + index * (self.col_width_in + self.gutter_in)

    def _remaining_in(self, column):
        return (self.height_in - self.margin_in) - self._cursor[column]

    def _place(self, needed_in):
        """Find a column with room. Returns (column, top_in) or None."""
        for _ in range(self.columns):
            if self._remaining_in(self._current_column) >= needed_in:
                return self._current_column, self._cursor[self._current_column]
            self._current_column += 1
            if self._current_column >= self.columns:
                self._current_column = self.columns - 1
                break
        column = self._current_column
        if self._remaining_in(column) >= needed_in * 0.5:
            return column, self._cursor[column]
        return None

    # -- content ---------------------------------------------------------

    def add_section(self, heading, body="", bullets=None):
        """Add a poster section, flowing into the next column when full."""
        bullets = bullets or []
        head_h = _estimate_height_in(heading, self.fonts["heading"],
                                     self.col_width_in)
        body_text = body or ""
        bullet_text = "\n".join(u"\u2022  " + b for b in bullets)
        combined = ("\n".join(x for x in (body_text, bullet_text) if x)).strip()
        body_h = _estimate_height_in(combined, self.fonts["body"],
                                     self.col_width_in) if combined else 0.0
        needed = head_h + body_h + self.gutter_in

        placement = self._place(needed)
        if placement is None:
            self.overflow.append(heading)
            return None
        column, top = placement

        box = self.slide.shapes.add_textbox(
            Inches(self._column_left_in(column)), Inches(top),
            Inches(self.col_width_in), Inches(min(needed, self._remaining_in(column))))
        tf = box.text_frame
        tf.word_wrap = True

        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = heading
        _set_font(run, self.fonts["heading"], bold=True, color=ACCENT)
        p.space_after = Pt(self.fonts["body"] * 0.4)

        if body_text:
            for para in [x for x in body_text.split("\n") if x.strip()]:
                bp = tf.add_paragraph()
                run = bp.add_run()
                run.text = para.strip()
                _set_font(run, self.fonts["body"])
                bp.space_after = Pt(self.fonts["body"] * 0.5)

        for bullet in bullets:
            bp = tf.add_paragraph()
            run = bp.add_run()
            run.text = u"\u2022  " + bullet
            _set_font(run, self.fonts["body"])
            bp.space_after = Pt(self.fonts["body"] * 0.35)

        self._cursor[column] = top + needed
        self.sections_placed += 1
        return box

    def add_table(self, rows, caption=None):
        """A data table, which is what makes a poster a poster."""
        if not rows:
            return None
        cols = max(len(r) for r in rows)
        row_h = self.fonts["body"] * 1.9 / 72.0
        needed = row_h * len(rows) + self.gutter_in
        if caption:
            needed += _estimate_height_in(caption, self.fonts["refs"],
                                          self.col_width_in)

        placement = self._place(needed)
        if placement is None:
            self.overflow.append(caption or "table")
            return None
        column, top = placement

        shape = self.slide.shapes.add_table(
            len(rows), cols, Inches(self._column_left_in(column)),
            Inches(top), Inches(self.col_width_in),
            Inches(row_h * len(rows)))
        for r, row in enumerate(rows):
            for c in range(cols):
                cell = shape.table.cell(r, c)
                cell.text = row[c] if c < len(row) else ""
                for p in cell.text_frame.paragraphs:
                    for run in p.runs:
                        _set_font(run, int(self.fonts["body"] * 0.8),
                                  bold=(r == 0))

        bottom = top + row_h * len(rows)
        if caption:
            box = self.slide.shapes.add_textbox(
                Inches(self._column_left_in(column)), Inches(bottom),
                Inches(self.col_width_in), Inches(0.4))
            run = box.text_frame.paragraphs[0].add_run()
            run.text = caption
            _set_font(run, self.fonts["refs"], italic=True, color=MUTED)

        self._cursor[column] = top + needed
        return shape

    def add_image(self, image_path, caption=None):
        if not os.path.isfile(image_path):
            return None
        width_in = self.col_width_in
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                ratio = img.height / float(img.width)
        except Exception:
            ratio = 0.7
        height_in = width_in * ratio
        needed = height_in + self.gutter_in + (0.5 if caption else 0)

        placement = self._place(needed)
        if placement is None:
            self.overflow.append(caption or os.path.basename(image_path))
            return None
        column, top = placement

        pic = self.slide.shapes.add_picture(
            image_path, Inches(self._column_left_in(column)), Inches(top),
            width=Inches(width_in))
        if caption:
            box = self.slide.shapes.add_textbox(
                Inches(self._column_left_in(column)),
                Inches(top + height_in + 0.1),
                Inches(width_in), Inches(0.4))
            run = box.text_frame.paragraphs[0].add_run()
            run.text = caption
            _set_font(run, self.fonts["refs"], italic=True, color=MUTED)

        self._cursor[column] = top + needed
        return pic

    def add_references(self, references):
        """References in small type, in the last column or a footer band."""
        if not references:
            return None
        text = "\n".join(references)
        needed = _estimate_height_in(text, self.fonts["refs"],
                                     self.col_width_in) + self.gutter_in

        placement = self._place(needed)
        if placement is None:
            # Footer band across the full width, as posters commonly do.
            top = self.height_in - self.margin_in - min(needed, 2.0)
            box = self.slide.shapes.add_textbox(
                Inches(self.margin_in), Inches(top),
                Inches(self.width_in - 2 * self.margin_in),
                Inches(min(needed, 2.0)))
            column = None
        else:
            column, top = placement
            box = self.slide.shapes.add_textbox(
                Inches(self._column_left_in(column)), Inches(top),
                Inches(self.col_width_in), Inches(needed))

        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = _labels()["references"]
        _set_font(run, self.fonts["heading"], bold=True, color=ACCENT)

        for ref in references:
            rp = tf.add_paragraph()
            run = rp.add_run()
            run.text = ref
            _set_font(run, self.fonts["refs"])
            rp.space_after = Pt(self.fonts["refs"] * 0.3)

        if column is not None:
            self._cursor[column] = top + needed
        return box

    def save(self, output_path, verbose=True):
        _ensure_dir(output_path)
        self.prs.save(output_path)
        _finalise(output_path, self)
        if verbose:
            print("Poster saved: %s" % output_path)
            print("  %s %s, %.1f x %.1f in, %d column(s)"
                  % (self.size, self.orientation, self.width_in,
                     self.height_in, self.columns))
            print("  Body text %dpt, headings %dpt (readable at 1m)"
                  % (self.fonts["body"], self.fonts["heading"]))
            print("  Student ID in the banner, no name")
            if self.overflow:
                print("  [WARNING] %d block(s) did not fit and were left out:"
                      % len(self.overflow))
                for item in self.overflow:
                    print("    - %s" % item)
                print("  Cut the text, or use a larger size (--size A0).")
        return output_path


def build_poster_from_markdown(md_path, output_path,
                               student_id="[Student ID]",
                               module="[Module Code]", size="A1",
                               orientation="landscape", columns=3,
                               verbose=True):
    """Build a conference poster from the report markdown."""
    with open(md_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    title, sections, refs = parse_markdown_sections(text)
    poster = AcademicPoster(title, student_id, module, size, orientation,
                            columns)

    for section in sections:
        prose = "\n".join(p for p in section["prose"] if p.strip())
        poster.add_section(section["heading"], prose, section["bullets"])
        for rows in section["tables"]:
            poster.add_table(rows, caption=None)
        for image in section["images"]:
            poster.add_image(image["path"], image["caption"] or None)

    poster.add_references(refs)
    poster.save(output_path, verbose=False)

    if verbose:
        print("Poster saved: %s" % output_path)
        print("  %s %s, %.1f x %.1f in, %d column(s)"
              % (poster.size, poster.orientation, poster.width_in,
                 poster.height_in, poster.columns))
        print("  Sections placed: %d" % poster.sections_placed)
        print("  Body text %dpt, headings %dpt (readable at 1m)"
              % (poster.fonts["body"], poster.fonts["heading"]))
        print("  Student ID in the banner, no name")
        if poster.overflow:
            print("  [WARNING] %d block(s) did not fit and were left out:"
                  % len(poster.overflow))
            for item in poster.overflow:
                print("    - %s" % item)
            print("  Cut the text, or build at a larger size (--size A0).")
    return output_path


# ==========================================================================
