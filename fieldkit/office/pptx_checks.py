"""Bounded, read-only checks for known PowerPoint XML failure patterns."""
import posixpath
import re
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

CHART_PART = re.compile(r"ppt/charts/chart\d+\.xml\Z")
MASTER_PART = re.compile(
    r"ppt/(?P<group>slideMasters|notesMasters|handoutMasters)/"
    r"(?:slide|notes|handout)Master\d+\.xml\Z"
)
THEME_PART = re.compile(r"ppt/theme/theme\d+\.xml\Z")
CHART_NS = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
AXIS_TAGS = ("catAx", "dateAx", "valAx", "serAx")
AXID_MINIMUM = {
    "barChart": 2, "lineChart": 2, "areaChart": 2, "scatterChart": 2,
    "bubbleChart": 2, "radarChart": 2, "stockChart": 2, "bar3DChart": 2,
    "area3DChart": 2, "surfaceChart": 2, "line3DChart": 3, "surface3DChart": 3,
}
KNOWN_ERRORS = (
    (re.compile(r"\}tableStyleId': This element is not expected"), "duplicate-table-style-id"),
    (re.compile(r"\}srgbClr', attribute 'val'"), "invalid-srgb-color"),
    (re.compile(r"\}txBody': Missing child element"), "empty-text-body"),
    (re.compile(r"\}miter', attribute 'lim'"), "invalid-miter-limit"),
    (re.compile(r"\}uLnTx': This element is not expected"), "unexpected-underlined-line"),
    (re.compile(r"\}overrideClrMapping': This element is not expected"), "unexpected-color-mapping"),
    (re.compile(r"\}nvGrpSpPr': Missing child element"), "empty-nonvisual-group-properties"),
)
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
THEME_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_XML_BYTES = 2 * 1024 * 1024
MAX_TOTAL_XML_BYTES = 20 * 1024 * 1024
MAX_PARTS = 256
MAX_ZIP_ENTRIES = 10_000
MAX_DIAGNOSTIC_BYTES = 2 * 1024 * 1024
MAX_DIAGNOSTIC_LINES = 100_000


class PptxCheckError(ValueError):
    """Input is malformed or exceeds a local inspection limit."""


def _open_archive(path):
    path = Path(path)
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PptxCheckError("presentation exceeds the 50 MiB input limit")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PptxCheckError("presentation is not a readable ZIP-based PPTX file") from exc
    entries = archive.infolist()
    if len(entries) > MAX_ZIP_ENTRIES:
        archive.close()
        raise PptxCheckError("presentation exceeds the 10,000-entry limit")
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)):
        archive.close()
        raise PptxCheckError("presentation contains duplicate ZIP entry names")
    return archive


def _read_xml(archive, name, budget):
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.flag_bits & 1:
        raise PptxCheckError("encrypted presentation parts are not supported")
    if info.file_size > MAX_XML_BYTES:
        raise PptxCheckError("an inspected XML part exceeds the 2 MiB limit")
    budget[0] += info.file_size
    if budget[0] > MAX_TOTAL_XML_BYTES:
        raise PptxCheckError("inspected XML exceeds the 20 MiB total limit")
    try:
        raw = archive.read(info)
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zlib.error, NotImplementedError) as exc:
        raise PptxCheckError("presentation part is corrupt or unreadable") from exc
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        raise PptxCheckError("XML declarations with document types or entities are not supported")
    try:
        return ElementTree.fromstring(raw)
    except (ElementTree.ParseError, RuntimeError) as exc:
        raise PptxCheckError("presentation XML is malformed or unreadable") from exc


def _chart_findings(root):
    findings = []
    plot_area = root.find(".//c:plotArea", CHART_NS)
    if plot_area is None:
        return findings
    declared = set()
    for axis_kind in AXIS_TAGS:
        for axis in root.findall(f".//c:{axis_kind}/c:axId", CHART_NS):
            value = axis.get("val")
            if value is not None:
                declared.add(value)
    for chart in list(plot_area):
        kind = chart.tag.rsplit("}", 1)[-1]
        if kind in {"barChart", "bar3DChart"}:
            grouping = chart.find("c:grouping", CHART_NS)
            if grouping is not None and grouping.get("val") in {"stacked", "percentStacked"}:
                if any(node.get("val") == "outEnd"
                       for node in chart.findall(".//c:dLblPos", CHART_NS)):
                    findings.append("stacked-bar-label-position")
        minimum = AXID_MINIMUM.get(kind)
        if minimum is not None:
            refs = [node.get("val") for node in chart.findall("c:axId", CHART_NS)
                    if node.get("val") is not None]
            live = sum(value in declared for value in refs)
            if live < minimum:
                findings.append("unresolved-chart-axis")
    return findings


def check_charts(path):
    """Return checked chart count and category counts; never return chart payload text."""
    archive = _open_archive(path)
    try:
        entries = sorted((entry for entry in archive.infolist()
                          if CHART_PART.fullmatch(entry.filename)), key=lambda entry: entry.filename)
        if len(entries) > MAX_PARTS:
            raise PptxCheckError("presentation has more than 256 chart parts")
        budget, counts = [0], {}
        for entry in entries:
            root = _read_xml(archive, entry.filename, budget)
            for kind in _chart_findings(root):
                counts[kind] = counts.get(kind, 0) + 1
        return {"ok": not counts, "chart_count": len(entries), "findings": counts,
                "next": "validate the whole presentation in its target viewer" if not counts
                else "review and correct the chart structure in the authoring workflow"}
    finally:
        archive.close()


def _theme_target(archive, master, budget):
    rels = posixpath.join(posixpath.dirname(master), "_rels",
                          posixpath.basename(master) + ".rels")
    root = _read_xml(archive, rels, budget)
    if root is None:
        return None
    for relation in root.findall(f"{{{REL_NS}}}Relationship"):
        if relation.get("Type") != THEME_REL_TYPE:
            continue
        target = relation.get("Target")
        if not target:
            continue
        target = (target.lstrip("/") if target.startswith("/") else
                  posixpath.normpath(posixpath.join(posixpath.dirname(master), target)))
        return target if THEME_PART.fullmatch(target) else None
    return None


def _notes_master_share_is_inert(archive, budget):
    root = _read_xml(archive, "ppt/presentation.xml", budget)
    if root is None:
        return False
    tags = [child.tag.rsplit("}", 1)[-1] for child in list(root)]
    try:
        slide_ids = tags.index("sldIdLst")
    except ValueError:
        return False
    return slide_ids + 1 < len(tags) and tags[slide_ids + 1] == "notesMasterIdLst"


def check_masters(path):
    """Find masters that share one theme part, excluding inert notes-master sharing."""
    archive = _open_archive(path)
    try:
        masters = sorted(name for name in archive.namelist() if MASTER_PART.fullmatch(name))
        if len(masters) > MAX_PARTS:
            raise PptxCheckError("presentation has more than 256 master parts")
        budget = [0]
        inert_notes = _notes_master_share_is_inert(archive, budget)
        owners, shared = {}, 0
        for master in masters:
            theme = _theme_target(archive, master, budget)
            if theme is None or theme not in archive.namelist():
                continue
            first = owners.setdefault(theme, master)
            if first != master and not (inert_notes and master.startswith("ppt/notesMasters/")):
                shared += 1
        return {"ok": shared == 0, "master_count": len(masters), "shared_theme_findings": shared,
                "next": "validate the whole presentation in its target viewer" if not shared
                else "review theme relationships and presentation part order"}
    finally:
        archive.close()


def check_schema_diagnostics(path):
    """Count known fatal diagnostic classes without returning diagnostic text or paths."""
    path = Path(path)
    if path.stat().st_size > MAX_DIAGNOSTIC_BYTES:
        raise PptxCheckError("diagnostic input exceeds the 2 MiB limit")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeError as exc:
        raise PptxCheckError("diagnostic input must be UTF-8 text") from exc
    if len(lines) > MAX_DIAGNOSTIC_LINES:
        raise PptxCheckError("diagnostic input exceeds the 100,000-line limit")
    counts = {}
    for line in lines:
        if line.startswith("Element "):
            for pattern, category in KNOWN_ERRORS:
                if pattern.search(line):
                    counts[category] = counts.get(category, 0) + 1
                    break
    return {"ok": not counts, "findings": counts,
            "next": "inspect unrecognized diagnostics and validate the presentation separately" if not counts
            else "correct the reported slide XML issue classes and rerun validation"}
