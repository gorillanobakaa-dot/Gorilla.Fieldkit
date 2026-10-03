"""Office: create -> check -> read round trips, broken files caught, names scrubbed."""
import shutil
import zipfile

import pytest

from fieldkit.office import check, create, read, scrub


@pytest.fixture(autouse=True)
def _backups(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_OFFICE_BACKUPS", str(tmp_path / "backups"))

DOCX = {"type": "docx", "title": "Field report", "blocks": [
    {"heading": "Findings", "level": 1},
    {"paragraph": "The pump failed at 14:00."},
    {"bullets": ["check the seal", "order parts"]},
    {"table": [["Part", "Qty"], ["seal", "2"]]},
]}
XLSX = {"type": "xlsx", "sheets": [{"name": "Stock", "rows": [["Item", "Qty", "Price", "Total"],
                                                               ["seal", 2, 3.5, "=B2*C2"]], "freeze": "A2"}]}
PPTX = {"type": "pptx", "slides": [{"title": "Plan", "bullets": ["one", "two"], "notes": "say hello"}]}
PDF = {"type": "pdf", "title": "Summary", "blocks": [{"heading": "Result"}, {"paragraph": "All good & tested."},
                                                    {"bullets": ["a", "b"]}]}


@pytest.mark.parametrize("spec,ext,must", [
    (DOCX, "docx", ["# Findings", "The pump failed", "- check the seal", "| seal | 2 |"]),
    (XLSX, "xlsx", ["## Stock", "| seal | 2 | 3.5 |"]),
    (PPTX, "pptx", ["## Slide 1: Plan", "- one", "> Notes: say hello"]),
    (PDF, "pdf", ["Summary", "All good & tested."]),
])
def test_create_check_read_round_trip(tmp_path, spec, ext, must):
    out = tmp_path / f"x.{ext}"
    rep = create.create(spec, out)
    assert rep["problems"] == []
    text = read.read(out)
    for m in must:
        assert m in text, (m, text[:500])


def test_created_files_carry_no_author(tmp_path):
    out = tmp_path / "a.docx"
    create.create(DOCX, out)
    assert scrub.inspect(out, terms=[]) == []


def test_pdf_is_byte_identical_across_runs(tmp_path):
    create.create(PDF, tmp_path / "a.pdf")
    create.create(PDF, tmp_path / "b.pdf")
    assert (tmp_path / "a.pdf").read_bytes() == (tmp_path / "b.pdf").read_bytes()


def test_xlsx_flags_formulas_without_cached_values(tmp_path):
    out = tmp_path / "f.xlsx"
    create.create(XLSX, out)
    info = check.check(out)["info"]
    assert info["formulas"] == 1 and info["formulas_without_cached_value"] == 1


def _rewrite(src, dst, change):
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for item in zi.infolist():
            data = change(item.filename, zi.read(item.filename))
            if data is not None:
                zo.writestr(item, data)


def test_check_catches_malformed_xml(tmp_path):
    good = tmp_path / "g.docx"
    create.create(DOCX, good)
    bad = tmp_path / "bad.docx"
    _rewrite(good, bad, lambda n, d: d.replace(b"</w:body>", b"</w:bodyy>") if n == "word/document.xml" else d)
    probs = check.check(bad)["problems"]
    assert any("malformed XML in word/document.xml" in p for p in probs)


def test_check_catches_missing_part(tmp_path):
    good = tmp_path / "g.pptx"
    create.create(PPTX, good)
    bad = tmp_path / "bad.pptx"
    _rewrite(good, bad, lambda n, d: None if n == "ppt/slides/slide1.xml" else d)
    probs = check.check(bad)["problems"]
    assert any("points at missing part ppt/slides/slide1.xml" in p for p in probs)


def test_check_catches_not_a_zip_and_truncated_pdf(tmp_path):
    fake = tmp_path / "fake.docx"
    fake.write_bytes(b"\x89PNG not a document")
    assert check.check(fake)["problems"]
    good = tmp_path / "g.pdf"
    create.create(PDF, good)
    cut = tmp_path / "cut.pdf"
    cut.write_bytes(good.read_bytes()[:-200])
    assert check.check(cut)["problems"]


def test_scrub_removes_names_but_not_text(tmp_path):
    import docx
    f = tmp_path / "essay.docx"
    create.create({**DOCX, "author": "Jane Public"}, f)
    d = docx.Document(f)
    d.core_properties.title = "Essay by Jane Public"
    d.core_properties.subject = "History"
    d.paragraphs[0].text = "Jane Public wrote this line."        # body text must survive
    d.save(f)
    found = scrub.inspect(f, ["Jane Public"])
    fields = {x[1] for x in found}
    assert {"creator", "lastModifiedBy", "title"} <= fields and "subject" not in fields
    res = scrub.scrub(f, ["Jane Public"])
    assert res["removed"] and not (tmp_path / "essay.docx.bak").exists()      # never beside the file
    assert res["backup"] and str(tmp_path / "backups") in res["backup"]
    assert scrub.inspect(f, ["Jane Public"]) == []
    assert "Jane Public wrote this line." in read.read(f)
    assert check.check(f)["problems"] == []


def test_scrub_review_authors(tmp_path):
    f = tmp_path / "c.docx"
    create.create(DOCX, f)
    tracked = tmp_path / "tracked.docx"
    _rewrite(f, tracked, lambda n, d: d.replace(b"<w:body>", b'<w:body><w:ins w:id="1" w:author="Jane Public" '
                                                               b'w:initials="JP" w:date="2026-01-01T00:00:00Z"/>')
             if n == "word/document.xml" else d)
    assert ("review marks", "author", "Jane Public") in scrub.inspect(tracked, [])
    scrub.scrub(tracked, [], backup=False)
    x = zipfile.ZipFile(tracked).read("word/document.xml").decode()
    assert "Jane Public" not in x and 'w:author="Author"' in x and "JP" not in x


def test_unsupported_types_refused(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("hi")
    with pytest.raises(ValueError):
        read.read(p)
    with pytest.raises(ValueError):
        create.create({"type": "odt"}, tmp_path / "x.odt")


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="Poppler not installed; pypdfium2 path used")
def test_pdf_read_via_poppler(tmp_path):
    f = tmp_path / "p.pdf"
    create.create(PDF, f)
    assert "Summary" in read.read(f)
