"""Office tools and privacy scan: the privacy and safety holes found in review (2026-10-02) stay shut.

1 backups never beside the document; deliver refuses a folder with a stale backup in it
2 nothing too large or unreadable is ever skipped silently (fail closed)
3 text in compressed PDF streams is found
4 PDF document properties are inspected
5 scrub re-checks its own output and puts the original back on failure
6 create never overwrites by accident and never leaves a bad file
7 deliver honours no_backup
8 read keeps Markdown table rows together
"""
import zipfile
import zlib
from pathlib import Path

import pytest

from fieldkit.core import privacy
from fieldkit.core.pipeline import Pipeline
from fieldkit.office import check, create, read, scrub

PIPE = Path(__file__).resolve().parents[1] / "fieldkit" / "build" / "pipelines" / "office-deliver.yaml"
NAME = "Jane Public"
DOC = {"type": "docx", "title": "Field report", "author": NAME,
       "blocks": [{"heading": "Findings", "level": 1}, {"paragraph": "The pump failed at 14:00."}]}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "private_terms", lambda local=None: [NAME])
    monkeypatch.setenv("FIELDKIT_OFFICE_BACKUPS", str(tmp_path / "backups"))


def _deliver(tmp_path, f, **extra):
    return Pipeline.load(PIPE, overrides={"file": str(f), **extra}, state_dir=tmp_path / "state").run()


def _docs(tmp_path):
    d = tmp_path / "out"
    d.mkdir(exist_ok=True)
    return d


# -- 1 backups ---------------------------------------------------------------------------------
def test_scrub_backup_is_kept_outside_the_documents_folder_and_restores(tmp_path):
    folder = _docs(tmp_path)
    f = folder / "report.docx"
    create.create(DOC, f)
    res = scrub.scrub(f, [NAME])
    assert res["removed"] and res["backup"]
    assert sorted(p.name for p in folder.iterdir()) == ["report.docx"]          # nothing else beside it
    bak = Path(res["backup"])
    assert bak.is_file() and (tmp_path / "backups") in bak.parents
    assert ("properties", "creator", NAME) in scrub.inspect(bak, [NAME])        # the backup still holds the name
    assert scrub.inspect(f, [NAME]) == []
    assert scrub.restore(bak) == f.resolve()                                     # restore finds its way home
    assert ("properties", "creator", NAME) in scrub.inspect(f, [NAME])


def test_two_scrubs_in_the_same_second_keep_both_backups(tmp_path):
    folder = _docs(tmp_path)
    a, b = folder / "a.docx", folder / "b" / "a.docx"
    b.parent.mkdir()
    create.create(DOC, a)
    create.create(DOC, b)
    ba, bb = scrub.scrub(a, [NAME])["backup"], scrub.scrub(b, [NAME])["backup"]
    assert ba != bb and Path(ba).is_file() and Path(bb).is_file()


@pytest.mark.parametrize("stale", ["report.docx.bak", "report.bak", "report.docx.bak-2", "~$port.docx",
                                   "Backup of report.wbk"])
def test_deliver_is_not_safe_with_a_stale_backup_beside_the_file(tmp_path, stale):
    folder = _docs(tmp_path)
    f = folder / "report.docx"
    create.create(DOC, f)
    (folder / stale).write_bytes(f.read_bytes())
    rep = _deliver(tmp_path, f)
    assert not rep["ok"] and rep["stopped_at"] == "privacy", rep
    assert "stale backup" in rep["stages"][-1]["result"]["detail"] and stale in rep["stages"][-1]["result"]["detail"]
    assert scrub.stale_backups(f) == [folder / stale]


def test_unrelated_neighbours_are_not_stale_backups(tmp_path):
    folder = _docs(tmp_path)
    f = folder / "report.docx"
    create.create(DOC, f)
    (folder / "report-final.docx").write_bytes(b"x")
    (folder / "other.docx.bak").write_bytes(b"x")
    assert scrub.stale_backups(f) == []


# -- 2 fail closed -----------------------------------------------------------------------------
def test_privacy_reports_a_file_too_large_to_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "TEXT_LIMIT", 100)
    (tmp_path / "big.txt").write_text("x" * 101)
    (tmp_path / "small.txt").write_text("fine")
    rep = privacy.scan_path(tmp_path, terms=[])
    assert list(rep) == [str(tmp_path / "big.txt")]
    assert rep[str(tmp_path / "big.txt")][0]["kind"] == "not-scanned"
    assert "too large" in rep[str(tmp_path / "big.txt")][0]["excerpt"]


def test_privacy_reports_an_archive_member_too_large_to_scan(tmp_path, monkeypatch):
    z = tmp_path / "hand.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("big.txt", "y" * 500)
        zf.writestr("ok.txt", "fine")
    monkeypatch.setattr(privacy, "TEXT_LIMIT", 100)
    rep = privacy.scan_path(z, terms=[])
    assert list(rep) == [f"{z}!big.txt"] and rep[f"{z}!big.txt"][0]["kind"] == "not-scanned"


def test_privacy_reports_an_unreadable_file(tmp_path, monkeypatch):
    f = tmp_path / "locked.txt"
    f.write_text("secret?")
    real = Path.read_bytes

    def boom(self):
        if self.name == "locked.txt":
            raise PermissionError(13, "Permission denied")
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", boom)
    rep = privacy.scan_path(tmp_path, terms=[])
    assert rep[str(f)][0]["kind"] == "not-scanned" and "unreadable" in rep[str(f)][0]["excerpt"]


def test_privacy_reports_a_corrupt_archive_member(tmp_path):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.txt", "hello world " * 50)
    data = bytearray(z.read_bytes())
    i = data.find(b"doc.txt") + len(b"doc.txt") + 5                  # inside the compressed data
    data[i:i + 8] = b"\xff" * 8
    z.write_bytes(bytes(data))
    rep = privacy.scan_path(z, terms=[])
    assert rep and all(h["kind"] == "not-scanned" for v in rep.values() for h in v)


def test_privacy_looks_inside_an_office_file_inside_a_zip(tmp_path):
    inner = _docs(tmp_path) / "cv.docx"
    create.create({**DOC, "blocks": [{"paragraph": f"by {NAME}"}]}, inner)
    z = tmp_path / "handover.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(inner, "cv.docx")
    rep = privacy.scan_path(z, terms=[NAME])
    assert any(k.startswith(f"{z}!cv.docx!") for k in rep), rep


def test_cli_privacy_scan_exits_3_when_something_was_not_scanned(tmp_path, monkeypatch, capsys):
    from fieldkit import cli
    monkeypatch.setattr(privacy, "TEXT_LIMIT", 10)
    (tmp_path / "big.txt").write_text("nothing private, just long")
    assert cli.main(["privacy", "scan", str(tmp_path)]) == 3
    assert "not-scanned" in capsys.readouterr().out


def test_deliver_is_not_safe_when_parts_could_not_be_scanned(tmp_path, monkeypatch):
    f = _docs(tmp_path) / "report.docx"
    create.create(DOC, f)
    monkeypatch.setattr(privacy, "TEXT_LIMIT", 50)                   # every XML part is now "too large"
    rep = _deliver(tmp_path, f)
    assert not rep["ok"] and rep["stopped_at"] == "privacy"
    assert "could not be read" in rep["stages"][-1]["result"]["detail"]


# -- 3 compressed PDF text -----------------------------------------------------------------------
def _minimal_pdf(stream_dict, stream_bytes):
    """A small hand-made PDF with one extra stream object (pdfium repairs the cross-reference table)."""
    body = (b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
            b"4 0 obj << " + stream_dict + b" /Length " + str(len(stream_bytes)).encode() + b" >>\nstream\n"
            + stream_bytes + b"\nendstream\nendobj\n")
    return body + b"trailer << /Root 1 0 R >>\n%%EOF\n"


def test_privacy_finds_a_name_inside_a_compressed_pdf_stream(tmp_path):
    f = _docs(tmp_path) / "letter.pdf"
    create.create({"type": "pdf", "title": "Letter", "blocks": [{"paragraph": f"Signed, {NAME}"}]}, f)
    data = f.read_bytes()
    assert NAME.encode() not in data                                   # the raw bytes hide it
    rep = privacy.scan_path(f, terms=[NAME])
    assert any("!stream@" in k for k in rep), rep
    rep = _deliver(tmp_path, f)
    assert not rep["ok"] and rep["stopped_at"] == "privacy"


def test_pdf_flate_stream_that_cannot_be_decoded_is_not_scanned(tmp_path):
    f = tmp_path / "bad.pdf"
    f.write_bytes(_minimal_pdf(b"/Filter /FlateDecode", b"this is not zlib data at all"))
    rep = privacy.scan_path(f, terms=[])
    hits = [h for k, v in rep.items() if "!stream@" in k for h in v]
    assert hits and hits[0]["kind"] == "not-scanned" and "FlateDecode" in hits[0]["excerpt"]


def test_pdf_stream_with_an_unknown_filter_is_not_scanned(tmp_path):
    f = tmp_path / "lzw.pdf"
    f.write_bytes(_minimal_pdf(b"/Filter /LZWDecode", b"\x80\x0b\x60\x50"))
    rep = privacy.scan_path(f, terms=[])
    assert any(h["kind"] == "not-scanned" and "LZWDecode" in h["excerpt"] for v in rep.values() for h in v)


def test_pdf_flate_and_ascii85_chain_is_decoded(tmp_path):
    import base64
    payload = base64.a85encode(zlib.compress(f"BT ({NAME}) Tj ET".encode())) + b"~>"
    f = tmp_path / "chain.pdf"
    f.write_bytes(_minimal_pdf(b"/Filter [/ASCII85Decode /FlateDecode]", payload))
    rep = privacy.scan_path(f, terms=[NAME])
    assert any(h["kind"] == "private-term" for k, v in rep.items() if "!stream@" in k for h in v), rep


# -- 4 PDF properties ----------------------------------------------------------------------------
def test_pdf_author_is_reported_and_deliver_says_not_safe(tmp_path):
    f = _docs(tmp_path) / "named.pdf"
    create.create({"type": "pdf", "title": "Plain title", "author": NAME, "blocks": [{"paragraph": "Body."}]}, f)
    found = scrub.inspect(f, [NAME])
    assert ("pdf properties", "Author", NAME) in found
    rep = _deliver(tmp_path, f)
    assert not rep["ok"] and rep["stopped_at"] == "scrub"
    assert "cannot be cleaned by Fieldkit" in rep["stages"][-1]["result"]["detail"]
    with pytest.raises(ValueError, match="cannot be cleaned"):
        scrub.scrub(f, [NAME])


def test_pdf_title_with_a_private_term_is_reported(tmp_path):
    f = _docs(tmp_path) / "t.pdf"
    create.create({"type": "pdf", "title": f"CV of {NAME}", "blocks": [{"paragraph": "Body."}]}, f)
    assert ("pdf properties", "Title", f"CV of {NAME}") in scrub.inspect(f, [NAME])


def test_pdf_xmp_creator_is_reported(tmp_path):
    xmp = (b'<?xpacket begin=""?><x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description>'
           b'<dc:creator><rdf:Seq><rdf:li>' + NAME.encode() + b'</rdf:li></rdf:Seq></dc:creator>'
           b'</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')
    f = tmp_path / "xmp.pdf"
    f.write_bytes(_minimal_pdf(b"/Type /Metadata /Subtype /XML /Filter /FlateDecode", zlib.compress(xmp)))
    assert ("pdf xmp", "dc:creator", NAME) in scrub.inspect(f, [NAME])


def test_clean_pdf_is_safe_to_send(tmp_path):
    f = _docs(tmp_path) / "clean.pdf"
    create.create({"type": "pdf", "title": "Pump report", "blocks": [{"paragraph": "All good."}]}, f)
    assert scrub.inspect(f, [NAME]) == []
    rep = _deliver(tmp_path, f)
    assert rep["ok"], rep


# -- 5 scrub re-checks ---------------------------------------------------------------------------
def test_scrub_refuses_a_cleaned_copy_that_fails_its_check(tmp_path, monkeypatch):
    folder = _docs(tmp_path)
    f = folder / "report.docx"
    create.create(DOC, f)
    before = f.read_bytes()
    monkeypatch.setattr(scrub, "_rewrite", lambda path, tmp, terms: Path(tmp).write_bytes(b"broken"))
    with pytest.raises(RuntimeError, match="failed its check"):
        scrub.scrub(f, [NAME])
    assert f.read_bytes() == before
    assert sorted(p.name for p in folder.iterdir()) == ["report.docx"]          # no temporary left behind


def test_scrub_puts_the_original_back_if_the_file_in_place_fails(tmp_path, monkeypatch):
    f = _docs(tmp_path) / "report.docx"
    create.create(DOC, f)
    before = f.read_bytes()
    real, calls = scrub._left_over, []

    def second_check_fails(path, terms):
        calls.append(path)
        return real(path, terms) if len(calls) == 1 else ["simulated: file in place is damaged"]
    monkeypatch.setattr(scrub, "_left_over", second_check_fails)
    with pytest.raises(RuntimeError, match="original was put back"):
        scrub.scrub(f, [NAME])
    assert f.read_bytes() == before and len(calls) == 2


def test_scrub_checks_the_file_after_rewriting(tmp_path, monkeypatch):
    f = _docs(tmp_path) / "report.docx"
    create.create(DOC, f)
    seen = []
    real = scrub._left_over
    monkeypatch.setattr(scrub, "_left_over", lambda p, t: seen.append(Path(p)) or real(p, t))
    scrub.scrub(f, [NAME])
    assert seen[-1] == f                                                # last check is on the real file


# -- 6 create ------------------------------------------------------------------------------------
def test_create_refuses_to_overwrite_without_force(tmp_path):
    f = _docs(tmp_path) / "r.docx"
    f.write_bytes(b"precious")
    with pytest.raises(FileExistsError, match="--force"):
        create.create(DOC, f)
    assert f.read_bytes() == b"precious"
    create.create(DOC, f, force=True)
    assert check.check(f)["problems"] == []


def test_create_type_that_does_not_match_the_extension_leaves_nothing(tmp_path):
    folder = _docs(tmp_path)
    with pytest.raises(ValueError, match="ends .pdf"):
        create.create(DOC, folder / "r.pdf")                            # type docx, name .pdf
    assert list(folder.iterdir()) == []


def test_create_failed_check_deletes_the_bad_output(tmp_path, monkeypatch):
    folder = _docs(tmp_path)
    monkeypatch.setattr(check, "check", lambda p: {"file": str(p), "problems": ["simulated"], "info": {}})
    with pytest.raises(RuntimeError, match="failed its own check"):
        create.create(DOC, folder / "r.docx")
    assert list(folder.iterdir()) == []


def test_create_failed_check_with_force_keeps_the_old_file(tmp_path, monkeypatch):
    f = _docs(tmp_path) / "r.docx"
    f.write_bytes(b"precious")
    monkeypatch.setattr(check, "check", lambda p: {"file": str(p), "problems": ["simulated"], "info": {}})
    with pytest.raises(RuntimeError):
        create.create(DOC, f, force=True)
    assert f.read_bytes() == b"precious" and sorted(p.name for p in f.parent.iterdir()) == ["r.docx"]


# -- 7 deliver no_backup -------------------------------------------------------------------------
def test_deliver_honours_no_backup(tmp_path):
    f = _docs(tmp_path) / "report.docx"
    create.create(DOC, f)
    rep = _deliver(tmp_path, f, no_backup="1")
    assert rep["ok"], rep
    assert not (tmp_path / "backups").exists()
    assert "no backup kept" in rep["stages"][1]["result"]["detail"]


def test_deliver_keeps_a_backup_by_default_and_says_where(tmp_path):
    f = _docs(tmp_path) / "report.docx"
    create.create(DOC, f)
    rep = _deliver(tmp_path, f)
    detail = rep["stages"][1]["result"]["detail"]
    assert rep["ok"] and "backup kept outside the document's folder" in detail
    assert str(tmp_path / "backups") in detail


# -- 8 read: tables stay tables ------------------------------------------------------------------
TABLE = [["Part", "Qty", "Note"], ["seal", "2", "a|b"], ["pump", "1", ""], ["valve", "4", "spare"]]


def test_read_docx_table_rows_are_consecutive(tmp_path):
    f = _docs(tmp_path) / "t.docx"
    create.create({"type": "docx", "blocks": [{"paragraph": "Before."}, {"table": TABLE},
                                              {"paragraph": "After."}]}, f)
    text = read.read(f)
    table = "\n".join(["| Part | Qty | Note |", "|---|---|---|", "| seal | 2 | a\\|b |", "| pump | 1 |  |",
                       "| valve | 4 | spare |"])
    assert "Before.\n\n" + table + "\n\nAfter." in text, text


def test_read_xlsx_table_rows_are_consecutive(tmp_path):
    f = _docs(tmp_path) / "t.xlsx"
    create.create({"type": "xlsx", "sheets": [{"name": "Stock", "rows": TABLE}]}, f)
    text = read.read(f)
    assert "## Stock\n\n| Part | Qty | Note |\n|---|---|---|\n| seal | 2 | a\\|b |\n| pump | 1 |  |\n" \
           "| valve | 4 | spare |" in text, text
