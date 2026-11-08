"""Read-only checks for PDF form geometry and fillable fields."""
import json
import math
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_FIELDS = 2_000
MAX_FINDINGS = 20
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_PDF_BYTES = 100 * 1024 * 1024


def _rectangle(value, field_index, name):
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"field {field_index} {name} must be a four-number rectangle")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in value):
        raise ValueError(f"field {field_index} {name} must contain finite numbers")
    x0, y0, x1, y1 = value
    if x1 < x0 or y1 < y0:
        raise ValueError(f"field {field_index} {name} has inverted coordinates")
    return tuple(float(v) for v in value)


def _intersects(left, right):
    return not (left[0] >= right[2] or left[2] <= right[0]
                or left[1] >= right[3] or left[3] <= right[1])


def inspect_fields(payload):
    """Find overlapping form boxes and entries too short for their font; never include labels."""
    if not isinstance(payload, dict) or not isinstance(payload.get("form_fields"), list):
        raise ValueError("input must be an object with a form_fields list")
    fields = payload["form_fields"]
    if len(fields) > MAX_FIELDS:
        raise ValueError(f"form_fields exceeds the {MAX_FIELDS}-field safety limit")

    boxes, findings = [], []
    for index, field in enumerate(fields, 1):
        if not isinstance(field, dict):
            raise ValueError(f"field {index} must be an object")
        page = field.get("page_number")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError(f"field {index} page_number must be a positive integer")
        label = _rectangle(field.get("label_bounding_box"), index, "label_bounding_box")
        entry = _rectangle(field.get("entry_bounding_box"), index, "entry_bounding_box")
        boxes.extend(((page, index, "label", label), (page, index, "entry", entry)))

        entry_text = field.get("entry_text", {})
        if not isinstance(entry_text, dict):
            raise ValueError(f"field {index} entry_text must be an object")
        font_size = entry_text.get("font_size", 14)
        if (isinstance(font_size, bool) or not isinstance(font_size, (int, float))
                or not math.isfinite(font_size) or font_size < 0):
            raise ValueError(f"field {index} entry_text.font_size must be a finite nonnegative number")
        height = entry[3] - entry[1]
        if height < font_size:
            findings.append({"kind": "height", "field": index, "page": page,
                             "height": height, "font_size": font_size})
            if len(findings) >= MAX_FINDINGS:
                return findings, True

    for index, left in enumerate(boxes):
        for right in boxes[index + 1:]:
            if left[0] == right[0] and _intersects(left[3], right[3]):
                findings.append({"kind": "overlap", "field": left[1], "box": left[2],
                                 "other_field": right[1], "other_box": right[2], "page": left[0]})
                if len(findings) >= MAX_FINDINGS:
                    return findings, True
    return findings, False


def check_bbox_file(path):
    """Read bounded UTF-8 JSON and check its form geometry without echoing descriptions."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise ValueError(f"JSON file exceeds the {MAX_JSON_BYTES}-byte safety limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
        findings, truncated = inspect_fields(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "findings": [], "truncated": False,
                "next": "provide valid UTF-8 JSON with page numbers and four-coordinate boxes"}
    return {"ok": not findings, "findings": findings, "truncated": truncated,
            "next": "visually review the rendered form before using it" if not findings
            else "adjust the reported rectangles and rerun the check"}


def check_fillable_pdf(path):
    """Count form fields with pypdf, refusing encrypted or oversized documents."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_PDF_BYTES:
            raise ValueError(f"PDF exceeds the {MAX_PDF_BYTES}-byte safety limit")
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            raise ValueError("encrypted PDF cannot be inspected without credentials")
        fields = reader.get_fields() or {}
        if not isinstance(fields, dict):
            raise ValueError("PDF form field table has an unsupported structure")
        if len(fields) > MAX_FIELDS:
            raise ValueError(f"PDF exceeds the {MAX_FIELDS}-field safety limit")
    except (OSError, PdfReadError, ValueError) as exc:
        return {"ok": False, "error": "PDF could not be safely read" if isinstance(exc, PdfReadError) else str(exc),
                "field_count": None, "next": "provide a readable, unencrypted PDF within the size limit"}
    count = len(fields)
    return {"ok": bool(count), "field_count": count,
            "next": "inspect field geometry before filling" if count else
            "use a reviewed visual form-filling workflow"}


def show_bbox_report(report):
    if report.get("error"):
        print(f"FAIL: {report['error']}")
        print(f"NEXT: {report['next']}")
        return
    for finding in report["findings"]:
        if finding["kind"] == "height":
            print(f"FAIL: field {finding['field']} on page {finding['page']} has entry height "
                  f"{finding['height']:g}, smaller than font size {finding['font_size']:g}")
        else:
            print(f"FAIL: field {finding['field']} {finding['box']} box overlaps field "
                  f"{finding['other_field']} {finding['other_box']} box on page {finding['page']}")
    if report["truncated"]:
        print(f"NOTE: stopped after {MAX_FINDINGS} findings")
    if report["ok"]:
        print("PASS: all form-field rectangles are valid")
    else:
        print("NEXT: " + report["next"])


def show_fillable_report(report):
    if report.get("error"):
        print(f"FAIL: {report['error']}")
    elif report["field_count"]:
        print(f"PASS: PDF has {report['field_count']} fillable form field(s)")
    else:
        print("FAIL: PDF has no fillable form fields")
    print(f"NEXT: {report['next']}")
