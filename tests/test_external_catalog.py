"""External cards are inspected as data and never executed during discovery."""
import json
import subprocess
import sys
from pathlib import Path

from fieldkit.desk import external

ROOT = Path(__file__).resolve().parents[1]


def _write_card(folder, *, effects=None, entry=None, script_path=None, input_flag=None):
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "tool.py"
    script.write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_name('executed').write_text('NO')\n",
        encoding="utf-8",
    )
    test = folder / "test_tool.py"
    test.write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
    card = {
        "id": folder.name,
        "title": "External sample tool",
        "path": str(script_path or script),
        "entry": entry or ["python", str(script)],
        "inputs": [{"name": "source", "flag": input_flag, "type": "path", "required": True}],
        "effects": effects if effects is not None else ["reads-files"],
        "safety": "read-only",
        "modes": {},
        "tests": [["python", "-m", "pytest", "-q", str(test)]],
        "platforms": ["windows", "linux"],
        "draft": False,
        "reviewed": True,
    }
    (folder / f"{folder.name}.card.json").write_text(json.dumps(card), encoding="utf-8")
    return script


def test_inspection_is_read_only_and_never_trusts_external_review(tmp_path):
    script = _write_card(tmp_path / "sample")
    result = external.inspect(tmp_path)
    assert result["counts"]["tools"] == 1
    assert result["counts"]["compatible_untrusted"] == 1
    assert result["counts"]["runnable"] == 0
    assert result["tools"][0]["status"] == "untrusted"
    assert result["tools"][0]["runnable"] is False
    assert not script.with_name("executed").exists()


def test_incompatible_entry_paths_effects_and_positional_inputs_are_reported(tmp_path):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    outside = tmp_path / "outside-tool.py"
    outside.write_text("pass\n", encoding="utf-8")
    _write_card(
        catalog / "unsafe",
        effects=["reads-capture-store", "processes"],
        entry=["powershell", "-File", str(outside)],
        script_path=outside,
        input_flag="source",
    )
    row = external.inspect(catalog)["tools"][0]
    assert row["status"] == "incompatible"
    assert row["runnable"] is False
    assert any("outside the catalog root" in issue for issue in row["issues"])
    assert any("entry must be exactly" in issue for issue in row["issues"])
    assert any("effects outside Fieldkit schema" in issue for issue in row["issues"])
    assert any("positional inputs require null" in issue for issue in row["issues"])


def test_unknown_workflow_files_are_listed_but_not_activated(tmp_path):
    workflow = tmp_path / "workflow-example.yaml"
    workflow.write_text("steps: []\n", encoding="utf-8")
    result = external.inspect(tmp_path)
    assert result["counts"]["workflows"] == 1
    assert result["workflows"][0]["path"] == str(workflow.resolve())
    assert result["workflows"][0]["runnable"] is False


def test_catalog_scan_and_card_size_are_bounded(tmp_path, monkeypatch):
    _write_card(tmp_path / "first")
    _write_card(tmp_path / "second")
    monkeypatch.setattr(external, "MAX_CARDS", 1)
    result = external.inspect(tmp_path)
    assert result["scan_limited"] is True
    assert result["counts"]["tools"] == 1

    monkeypatch.setattr(external, "MAX_CARDS", 500)
    monkeypatch.setattr(external, "MAX_CARD_BYTES", 32)
    result = external.inspect(tmp_path)
    assert result["counts"]["incompatible"] == 2
    assert all(row["status"] == "invalid" for row in result["tools"])


def test_tools_external_cli_emits_catalog_findings_without_execution(tmp_path):
    script = _write_card(tmp_path / "sample", effects=["reads-capture-store"])
    completed = subprocess.run(
        [sys.executable, "-m", "fieldkit", "tools", "external", str(tmp_path), "--json"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert completed.returncode == 3
    result = json.loads(completed.stdout)
    assert result["counts"]["tools"] == 1
    assert result["counts"]["incompatible"] == 1
    assert not script.with_name("executed").exists()
