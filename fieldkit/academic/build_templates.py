"""
BUILD TEMPLATES - Ready-to-write Word and PowerPoint templates, one per
document type, and the reader that turns a filled-in template back into
markdown so the pipeline can check it.

A Word template is already in the student's layout (layout.py: the
country's page and margins, the style's line spacing, the anonymous Student
ID header and page numbers, headings in the work language) and uses
Word's real Heading styles, so the navigation pane and the contents page
work. Under each heading sits grey guidance text saying what the section is
for and roughly how many words it should take.

The guidance is written in its own paragraph style, "Guidance". That does
two jobs:
  * the student can delete it all at once (Home > Select > Select Text with
    Similar Formatting), and
  * the pipeline can tell when guidance has been left in, and refuses to
    call a document ready while it has.

Word in, Word out: a student can write straight into the template and give
the .docx to the checks. docx_to_markdown() reads it back - headings from
the Heading styles, lists, tables, bold and italic - so every check runs on
exactly what was written.

Posters and presentations get PowerPoint templates instead.

Usage:
    fieldkit academic template TYPE [--to FOLDER]      one template
    fieldkit academic template all --to FOLDER         all of them
"""
import os
import re
import sys
import argparse

from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_LINE_SPACING
from docx.oxml.ns import qn

from . import build_word as bw
from .document_types import (DOCUMENT_TYPES, resolve_type, section_guide,
                            output_format, builder_kind, _section_key_for)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

GUIDANCE_STYLE = "Guidance"
GUIDANCE_COLOUR = RGBColor(0x76, 0x76, 0x76)
TEMPLATE_PREFIX = "TEMPLATE - "


def template_filename(type_key):
    spec = DOCUMENT_TYPES[resolve_type(type_key)]
    # "Systematic / scoping review" -> "Systematic or scoping review".
    label = re.sub(r"\s*/\s*", " or ", spec["label"])
    label = re.sub(r'[<>:"/\\|?*]', "-", label).strip()
    return "%s%s.%s" % (TEMPLATE_PREFIX, label, output_format(type_key))


# ==========================================================================
# Word templates
# ==========================================================================

def _add_guidance_style(doc):
    styles = doc.styles
    try:
        return styles[GUIDANCE_STYLE]
    except KeyError:
        pass
    style = styles.add_style(GUIDANCE_STYLE, WD_STYLE_TYPE.PARAGRAPH)
    style.base_style = styles["Normal"]
    style.font.name = bw.BODY_FONT
    style.font.size = Pt(11)
    style.font.italic = True
    style.font.color.rgb = GUIDANCE_COLOUR
    style.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    style.paragraph_format.space_after = Pt(6)
    bw._force_font(style.element)
    return style


def _guidance(doc, text):
    return doc.add_paragraph(text, style=GUIDANCE_STYLE)


def build_word_template(type_key, output_path, title=None,
                        student_id="[Student ID]", module="[Module Code]",
                        target_words=None, verbose=True, guidance=False):
    """A template holding the structure and nothing else.

    'guidance' adds the old explanatory notes under every heading and the
    checklist page at the end. It is off by default: the template is the
    document he submits, so anything he would have to find and delete does
    not belong in it. What each section needs: fieldkit academic types TYPE.
    """
    canonical = resolve_type(type_key)
    if canonical is None:
        raise ValueError("unknown document type %r" % type_key)
    spec = DOCUMENT_TYPES[canonical]
    target = target_words or spec["typical_words"]

    doc = Document()
    bw._set_base_style(doc)
    bw._set_margins(doc)
    bw._set_header(doc, student_id, module)
    bw._add_page_number_footer(doc)
    bw._set_heading_styles(doc)
    _add_guidance_style(doc)

    from .layout import heading as _h
    lab = bw._lay()["labels"]
    lang = bw._lay()["language"]
    p_title = bw._heading(doc, title or lab["title"], 1, centred=True)
    p_title.paragraph_format.space_before = Pt(36)
    p_title.paragraph_format.space_after = Pt(24)

    # Vertical spacing to position the metadata table in the lower half of page 1
    for _ in range(6):
        doc.add_paragraph("")

    # Metadata table on cover page
    meta_rows = [
        ["%s:" % lab["student_id"], student_id],
        ["%s:" % lab["date"], "[...]"],
        ["%s:" % lab["words"], "[...] (%s: %s +/- 10%%)" % (lab["target"], format(target, ","))]
    ]
    bw._add_table(doc, meta_rows)

    # Standalone cover page ends here
    bw._page_break(doc)

    has_contents_heading = any(_section_key_for(h) == "contents"
                               for h in spec["skeleton"])
    if spec.get("contents_page") and not has_contents_heading:
        bw._add_contents_page(doc)

    for heading, words, share, text in section_guide(canonical, target):
        key = _section_key_for(heading)
        if key == "contents":
            bw._add_contents_page(doc)
            continue
        if key == "references":
            bw._page_break(doc)
        bw._heading(doc, _h(heading, lang), 2)
        if guidance:
            if words:
                _guidance(doc, "About %s words (%d%%). %s"
                          % (format(words, ","), round(share), text))
            elif text:
                _guidance(doc, text)
            if key == "references":
                from . import styles
                from .profile import load
                st = (load() or {}).get("style") or "harvard-ctr"
                if st in styles.names():
                    _guidance(doc, "Format (%s): %s" % (styles.get(st)["label"],
                                                         styles.get(st)["entry_example"]))
        doc.add_paragraph("")

    # Checklist at the very end, as guidance.
    if guidance:
        bw._page_break(doc)
        _guidance(doc, "BEFORE YOU SUBMIT - delete this page, then check:")
        for item in spec.get("checklist", []):
            _guidance(doc, u"\u2610  " + item)

    from .workspace import refuse_cloud_save
    refuse_cloud_save(output_path, "the template")
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    doc.save(output_path)
    # No author: Word fills in whoever saves it. The language is the work
    # language from the start, so Word spell-checks what the student types
    # in the right language.
    from .office_finalise import finalise
    finalise(output_path)
    if verbose:
        print("Word template saved: %s" % output_path)
    return output_path


# ==========================================================================
# PowerPoint templates
# ==========================================================================

def build_pptx_template(type_key, output_path, title=None,
                        student_id="[Student ID]", module="[Module Code]",
                        target_words=None, size="A1",
                        orientation="landscape", columns=3, verbose=True):
    from .build_pptx import AcademicPoster, AcademicPresentation
    from .layout import current, heading as _h
    lay = current()
    lab, lang = lay["labels"], lay["language"]

    canonical = resolve_type(type_key)
    spec = DOCUMENT_TYPES[canonical]
    target = target_words or spec["typical_words"]
    rows = section_guide(canonical, target)

    if builder_kind(canonical) == "poster":
        poster = AcademicPoster(title or lab["title"], student_id,
                                module, size, orientation, columns)
        refs = None
        for heading, words, share, text in rows:
            if _section_key_for(heading) == "references":
                refs = [lab["references_placeholder"]]
                continue
            poster.add_section(_h(heading, lang), "[~%s] %s"
                               % (format(words, ","), text) if words else text)
        if refs:
            poster.add_references(refs)
        poster.save(output_path, verbose=False)
    else:
        pres = AcademicPresentation(title or lab["title"],
                                    student_id, module)
        pres.add_title_slide(subtitle=lab["template_subtitle"])
        for heading, words, share, text in rows:
            if _section_key_for(heading) == "references":
                pres.add_reference_slide([lab["references_placeholder"]])
                continue
            pres.add_content_slide(
                _h(heading, lang),
                [lab["key_point"]] * 3,
                notes="GUIDANCE: %s Put the detail you will say aloud in "
                      "these speaker notes." % text)
        pres.save(output_path, verbose=False)

    if verbose:
        print("PowerPoint template saved: %s" % output_path)
    return output_path


def build_template(type_key, output_dir, title=None,
                   student_id="[Student ID]", module="[Module Code]",
                   target_words=None, verbose=True, guidance=False):
    """Build the right kind of template for a type into output_dir."""
    canonical = resolve_type(type_key)
    if canonical is None:
        raise ValueError("unknown document type %r" % type_key)
    path = os.path.join(output_dir, template_filename(canonical))
    if output_format(canonical) == "pptx":
        return build_pptx_template(canonical, path, title, student_id, module,
                                   target_words, verbose=verbose)
    return build_word_template(canonical, path, title, student_id, module,
                               target_words, verbose=verbose,
                               guidance=guidance)


def build_all_templates(output_dir, student_id="[Student ID]",
                        module="[Module Code]", verbose=True):
    paths = []
    for name in sorted(DOCUMENT_TYPES):
        paths.append(build_template(name, output_dir, None, student_id,
                                    module, None, verbose=False))
    if verbose:
        print("%d templates written to %s" % (len(paths), output_dir))
    return paths


# ==========================================================================
# Reading a filled-in Word document back
# ==========================================================================

HEADING_LEVELS = {"title": 1, "heading 1": 2, "heading 2": 3,
                  "heading 3": 4, "heading 4": 5}


def _runs_to_markdown(paragraph):
    """Paragraph text with bold and italic kept as markdown emphasis."""
    parts = []
    for run in paragraph.runs:
        text = run.text
        if not text:
            continue
        # Keep emphasis outside surrounding whitespace, or markdown breaks.
        lead = text[:len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        core = text.strip()
        if not core:
            parts.append(text)
            continue
        if run.bold and run.italic:
            core = "***%s***" % core
        elif run.bold:
            core = "**%s**" % core
        elif run.italic:
            core = "*%s*" % core
        parts.append(lead + core + trail)
    md = "".join(parts)
    # Merge adjacent identical markers left by run splitting: "**a****b**".
    md = md.replace("******", "").replace("****", "")
    return md


_CONTENTS_WORDS = ("contents", "table of contents", "índice", "indice", "inhaltsverzeichnis", "cuprins")


def _is_toc_paragraph(paragraph):
    style = (paragraph.style.name if paragraph.style is not None else "").lower()
    if style.startswith("toc") or "table of figures" in style:
        return True
    text = paragraph.text.strip().lower()
    if text in _CONTENTS_WORDS:
        return True
    xml = paragraph._p.xml
    return "TOC \\o" in xml or ("instrText" in xml and "TOC" in xml)


def docx_to_markdown(docx_path):
    """Read a Word document back into markdown for the pipeline.

    Returns (markdown, info) where info reports the guidance paragraphs still
    present and anything the conversion could not carry (images).
    """
    doc = Document(docx_path)
    lines = []
    info = {"guidance_left": 0, "guidance_samples": [], "images": 0,
            "headings": 0}

    try:
        blocks = list(doc.iter_inner_content())
    except AttributeError:
        blocks = list(doc.paragraphs)

    for block in blocks:
        # Tables.
        if block.__class__.__name__ == "Table":
            rows = []
            for row in block.rows:
                cells = [c.text.strip().replace("|", "/") for c in row.cells]
                rows.append("| " + " | ".join(cells) + " |")
            if rows:
                width = rows[0].count("|") - 1
                lines.append("")
                lines.append(rows[0])
                lines.append("|" + "---|" * width)
                lines.extend(rows[1:])
                lines.append("")
            continue

        p = block
        style = (p.style.name if p.style is not None else "").lower()
        text = p.text.strip()

        if "graphicData" in p._p.xml or "w:drawing" in p._p.xml:
            info["images"] += 1

        if style == GUIDANCE_STYLE.lower():
            if text:
                info["guidance_left"] += 1
                if len(info["guidance_samples"]) < 3:
                    info["guidance_samples"].append(text[:70])
            continue

        if _is_toc_paragraph(p):
            continue
        if not text:
            lines.append("")
            continue
        if text.lower() in _CONTENTS_WORDS:
            continue

        level = HEADING_LEVELS.get(style)
        if level is None and style.startswith("heading"):
            level = 4
        if level is None and len(text.split()) <= 12 and not text.endswith(".") \
                and p.runs and all(r.bold for r in p.runs if r.text.strip()):
            # A bold one-line paragraph used as a heading without the style.
            level = 2
        if level:
            info["headings"] += 1
            lines.append("")
            lines.append("#" * level + " " + text)
            lines.append("")
            continue

        body = _runs_to_markdown(p)
        if "list" in style or text.startswith(u"\u2022"):
            body = re.sub(u"^\\s*\u2022\\s*", "", body)
            lines.append("- " + body)
            continue
        lines.append(body)
        lines.append("")

    md = "\n".join(lines)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    return md, info
