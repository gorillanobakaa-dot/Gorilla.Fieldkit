"""Focused tests for bounded, read-only PowerPoint XML checks."""
import json
import zipfile

from fieldkit.cli import main
from fieldkit.office import pptx_checks


def _pptx(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return path


def _chart_xml(stacked=False, bad_label=False, dead_axis=False):
    grouping = '<c:grouping val="stacked"/>' if stacked else '<c:grouping val="clustered"/>'
    labels = '<c:dLbls><c:dLbl><c:dLblPos val="outEnd"/></c:dLbl></c:dLbls>' if bad_label else ""
    axis_ids = '<c:axId val="1"/><c:axId val="2"/>' if not dead_axis else '<c:axId val="9"/><c:axId val="10"/>'
    live = '<c:catAx><c:axId val="1"/></c:catAx><c:valAx><c:axId val="2"/></c:valAx>'
    return (f'<c:chartSpace xmlns:c="{pptx_checks.CHART_NS["c"]}"><c:chart><c:plotArea>'
            f'<c:barChart>{grouping}<c:ser><c:dLbls>{labels}</c:dLbls></c:ser>{axis_ids}</c:barChart>'
            f'{live}</c:plotArea></c:chart></c:chartSpace>')


def test_chart_check_counts_known_findings_without_payload(tmp_path):
    file = _pptx(tmp_path / "chart.pptx", {
        "ppt/charts/chart1.xml": _chart_xml(stacked=True, bad_label=True, dead_axis=True),
    })
    result = pptx_checks.check_charts(file)
    assert result["chart_count"] == 1
    assert result["findings"] == {
        "stacked-bar-label-position": 1,
        "unresolved-chart-axis": 1,
    }
    assert "chart1.xml" not in json.dumps(result)


def test_chart_check_passes_clean_and_empty_presentations(tmp_path):
    clean = _pptx(tmp_path / "clean.pptx", {
        "ppt/charts/chart1.xml": _chart_xml(),
    })
    empty = _pptx(tmp_path / "empty.pptx", {})
    assert pptx_checks.check_charts(clean)["ok"] is True
    assert pptx_checks.check_charts(empty)["chart_count"] == 0


def _relationship(target):
    return (f'<Relationships xmlns="{pptx_checks.REL_NS}"><Relationship Type="{pptx_checks.THEME_REL_TYPE}" '
            f'Target="{target}" Id="rId1"/></Relationships>')


def test_master_check_detects_shared_theme_without_echoing_file_content(tmp_path):
    file = _pptx(tmp_path / "masters.pptx", {
        "ppt/presentation.xml": '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        "ppt/theme/theme1.xml": "<theme/>",
        "ppt/slideMasters/slideMaster1.xml": "<master/>",
        "ppt/slideMasters/slideMaster2.xml": "<master/>",
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _relationship("../theme/theme1.xml"),
        "ppt/slideMasters/_rels/slideMaster2.xml.rels": _relationship("../theme/theme1.xml"),
    })
    result = pptx_checks.check_masters(file)
    assert result["shared_theme_findings"] == 1
    assert "slideMaster" not in json.dumps(result)


def test_notes_master_sharing_is_ignored_when_presentation_order_is_inert(tmp_path):
    p_ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    file = _pptx(tmp_path / "notes.pptx", {
        "ppt/presentation.xml": (f'<p:presentation xmlns:p="{p_ns}"><p:sldIdLst/>'
                                 '<p:notesMasterIdLst/></p:presentation>'),
        "ppt/theme/theme1.xml": "<theme/>",
        "ppt/notesMasters/notesMaster1.xml": "<master/>",
        "ppt/notesMasters/notesMaster2.xml": "<master/>",
        "ppt/notesMasters/_rels/notesMaster1.xml.rels": _relationship("../theme/theme1.xml"),
        "ppt/notesMasters/_rels/notesMaster2.xml.rels": _relationship("../theme/theme1.xml"),
    })
    assert pptx_checks.check_masters(file)["shared_theme_findings"] == 0


def test_schema_verdict_classifies_only_known_diagnostics_and_does_not_echo(tmp_path):
    diagnostics = tmp_path / "diagnostics.txt"
    diagnostics.write_text(
        "Element {x}txBody': Missing child element at private-slide-path\n"
        "Element {x}other': an unknown condition\n",
        encoding="utf-8",
    )
    report = pptx_checks.check_schema_diagnostics(diagnostics)
    assert report["findings"] == {"empty-text-body": 1}
    assert "private-slide-path" not in json.dumps(report)


def test_xml_entities_and_archive_limits_are_refused(tmp_path, monkeypatch):
    xml = _pptx(tmp_path / "entity.pptx", {
        "ppt/charts/chart1.xml": '<!DOCTYPE x [<!ENTITY a "b">]><c:chartSpace/>',
    })
    try:
        pptx_checks.check_charts(xml)
    except pptx_checks.PptxCheckError as exc:
        assert "entities" in str(exc)
    else:
        raise AssertionError("DOCTYPE input should be rejected")

    monkeypatch.setattr(pptx_checks, "MAX_ARCHIVE_BYTES", 1)
    try:
        pptx_checks.check_charts(xml)
    except pptx_checks.PptxCheckError as exc:
        assert "50 MiB" in str(exc)
    else:
        raise AssertionError("oversized input should be rejected")


def test_corrupt_zip_member_is_a_safe_refusal(tmp_path, monkeypatch):
    file = _pptx(tmp_path / "corrupt.pptx", {"ppt/charts/chart1.xml": _chart_xml()})

    def corrupt_read(_archive, _member):
        raise zipfile.BadZipFile("private CRC failure detail")

    monkeypatch.setattr(zipfile.ZipFile, "read", corrupt_read)
    try:
        pptx_checks.check_charts(file)
    except pptx_checks.PptxCheckError as exc:
        assert str(exc) == "presentation part is corrupt or unreadable"
        assert "private" not in str(exc)
    else:
        raise AssertionError("corrupt member should be rejected")


def test_office_cli_exposes_schema_verdict_as_findings(tmp_path, capsys):
    file = tmp_path / "diagnostics.txt"
    file.write_text("Element {x}srgbClr', attribute 'val' is invalid\n", encoding="utf-8")
    assert main(["office", "pptx-schema-verdict", str(file), "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["findings"] == {"invalid-srgb-color": 1}


def test_office_cli_exposes_chart_and_master_checks(tmp_path, capsys):
    presentation = _pptx(tmp_path / "empty.pptx", {})
    assert main(["office", "pptx-chart-check", str(presentation), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["chart_count"] == 0
    assert main(["office", "pptx-master-check", str(presentation), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["master_count"] == 0


def test_corrupt_member_cli_returns_clean_failure_json(tmp_path, monkeypatch, capsys):
    file = _pptx(tmp_path / "corrupt-cli.pptx", {"ppt/charts/chart1.xml": _chart_xml()})

    def corrupt_read(_archive, _member):
        raise zipfile.BadZipFile("private CRC failure detail")

    monkeypatch.setattr(zipfile.ZipFile, "read", corrupt_read)
    assert main(["office", "pptx-chart-check", str(file), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"] == "presentation part is corrupt or unreadable"
    assert "private" not in json.dumps(report)
