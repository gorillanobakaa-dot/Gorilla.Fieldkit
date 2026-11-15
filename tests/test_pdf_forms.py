"""Read-only local checks for PDF form geometry and fillability."""
import json

import pytest

from fieldkit.office import forms


def _field(label, entry, page=1, **extra):
    return {"page_number": page, "label_bounding_box": label,
            "entry_bounding_box": entry, **extra}


def test_bbox_clear_boxes_and_touching_edges_pass():
    findings, truncated = forms.inspect_fields({
        "form_fields": [
            _field([0, 0, 10, 10], [10, 0, 20, 10], entry_text={"font_size": 10}),
            _field([20, 0, 30, 10], [30, 0, 40, 10], page=2, entry_text={"font_size": 10}),
        ]
    })
    assert findings == []
    assert truncated is False


def test_bbox_reports_overlap_and_small_entry_without_echoing_labels(tmp_path, capsys):
    payload = {"form_fields": [
        _field([0, 0, 10, 10], [10, 0, 20, 8], description="private label",
               entry_text={"font_size": 9}),
        _field([5, 5, 15, 15], [20, 0, 30, 10], description="other private label",
               entry_text={"font_size": 9}),
    ]}
    path = tmp_path / "fields.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = forms.check_bbox_file(path)
    assert {finding["kind"] for finding in report["findings"]} == {"overlap", "height"}
    forms.show_bbox_report(report)
    output = capsys.readouterr().out
    assert "private label" not in output
    assert "other private label" not in output
    assert "NEXT:" in output


@pytest.mark.parametrize("rect", [
    [0, 0, 1], [0, 0, 2, 1, 3], [0, 0, float("nan"), 1], [2, 0, 1, 1]
])
def test_bbox_rejects_malformed_rectangles(rect):
    with pytest.raises(ValueError):
        forms.inspect_fields({"form_fields": [_field(rect, [0, 0, 1, 1])]})


def test_bbox_enforces_field_and_input_size_limits(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="safety limit"):
        forms.inspect_fields({"form_fields": [_field([0, 0, 1, 1], [1, 1, 2, 2])] *
                              (forms.MAX_FIELDS + 1)})
    path = tmp_path / "large.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(forms, "MAX_JSON_BYTES", 1)
    assert "safety limit" in forms.check_bbox_file(path)["error"]


def test_fillable_pdf_reports_count_without_names_or_values(tmp_path, monkeypatch, capsys):
    class FakeReader:
        is_encrypted = False

        def __init__(self, _path, *, strict):
            assert strict is False

        def get_fields(self):
            return {"sensitive-name": {"value": "private value"}}

    monkeypatch.setattr(forms, "PdfReader", FakeReader)
    pdf = tmp_path / "form.pdf"
    pdf.write_bytes(b"pdf placeholder")
    report = forms.check_fillable_pdf(pdf)
    forms.show_fillable_report(report)
    output = capsys.readouterr().out
    assert report["field_count"] == 1
    assert "sensitive-name" not in output and "private value" not in output


def test_fillable_pdf_refuses_encryption_and_no_fields(tmp_path, monkeypatch):
    pdf = tmp_path / "form.pdf"
    pdf.write_bytes(b"pdf placeholder")

    class EncryptedReader:
        is_encrypted = True

        def __init__(self, _path, *, strict):
            pass

    monkeypatch.setattr(forms, "PdfReader", EncryptedReader)
    assert "encrypted PDF" in forms.check_fillable_pdf(pdf)["error"]

    class EmptyReader:
        is_encrypted = False

        def __init__(self, _path, *, strict):
            pass

        def get_fields(self):
            return None

    monkeypatch.setattr(forms, "PdfReader", EmptyReader)
    assert forms.check_fillable_pdf(pdf)["field_count"] == 0


def test_fillable_pdf_enforces_field_limit(tmp_path, monkeypatch):
    class TooManyFieldsReader:
        is_encrypted = False

        def __init__(self, _path, *, strict):
            pass

        def get_fields(self):
            return {f"field-{i}": {} for i in range(forms.MAX_FIELDS + 1)}

    monkeypatch.setattr(forms, "PdfReader", TooManyFieldsReader)
    pdf = tmp_path / "form.pdf"
    pdf.write_bytes(b"pdf placeholder")
    report = forms.check_fillable_pdf(pdf)
    assert "safety limit" in report["error"]
    assert report["field_count"] is None


def test_office_cli_registers_both_local_checks(tmp_path, capsys, monkeypatch):
    from fieldkit.cli import main

    fields = tmp_path / "fields.json"
    fields.write_text('{"form_fields": []}', encoding="utf-8")
    assert main(["office", "form-bbox-check", str(fields), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"placeholder")

    class EmptyReader:
        is_encrypted = False

        def __init__(self, _path, *, strict):
            pass

        def get_fields(self):
            return None

    monkeypatch.setattr(forms, "PdfReader", EmptyReader)
    assert main(["office", "fillable-check", str(pdf), "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["field_count"] == 0
