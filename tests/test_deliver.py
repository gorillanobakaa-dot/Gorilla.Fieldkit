"""office-deliver: a named document comes out clean, a leaky one is stopped, a broken one is never touched."""
from pathlib import Path

import pytest

from fieldkit.core import privacy
from fieldkit.core.pipeline import Pipeline
from fieldkit.office import create, scrub

PIPE = Path(__file__).resolve().parents[1] / "fieldkit" / "build" / "pipelines" / "office-deliver.yaml"
DOC = {"type": "docx", "title": "Field report", "author": "Jane Public",
       "blocks": [{"heading": "Findings", "level": 1}, {"paragraph": "The pump failed at 14:00."}]}


@pytest.fixture(autouse=True)
def _terms(monkeypatch):
    monkeypatch.setattr(privacy, "private_terms", lambda local=None: ["Jane Public"])


def _run(tmp_path, f):
    return Pipeline.load(PIPE, overrides={"file": str(f)}, state_dir=tmp_path / "state").run()


def _status(rep):
    return {s["id"]: s["status"] for s in rep["stages"]}


def test_named_document_comes_out_clean(tmp_path):
    f = tmp_path / "report.docx"
    create.create(DOC, f)
    assert scrub.inspect(f, ["Jane Public"])                     # the name is really there first
    rep = _run(tmp_path, f)
    assert rep["ok"] and _status(rep) == {"check": "done", "scrub": "done", "privacy": "done"}, rep
    assert scrub.inspect(f, ["Jane Public"]) == []
    assert (tmp_path / "report.docx.bak").exists()


def test_private_text_inside_stops_delivery(tmp_path):
    f = tmp_path / "notes.docx"
    home = "\\".join(["C:", "Users", "jane" + "public", "Desktop", "pump.xlsx"])   # built, so this file stays clean
    create.create({**DOC, "blocks": [{"paragraph": f"Figures are in {home}"}]}, f)
    rep = _run(tmp_path, f)
    assert not rep["ok"] and rep["stopped_at"] == "privacy"
    assert "windows-user-path" in rep["stages"][-1]["result"]["detail"]


def test_broken_file_is_not_touched(tmp_path):
    f = tmp_path / "broken.docx"
    f.write_bytes(b"not a zip at all")
    rep = _run(tmp_path, f)
    assert not rep["ok"] and rep["stopped_at"] == "check"
    assert [s["id"] for s in rep["stages"]] == ["check"]
    assert f.read_bytes() == b"not a zip at all" and not (tmp_path / "broken.docx.bak").exists()


def test_no_file_given_fails_clearly(tmp_path):
    rep = Pipeline.load(PIPE, state_dir=tmp_path / "state").run()
    assert not rep["ok"] and "no file given" in rep["stages"][0]["result"]["detail"]


def test_second_run_rechecks_instead_of_trusting_state(tmp_path):
    f = tmp_path / "report.docx"
    create.create(DOC, f)
    assert _run(tmp_path, f)["ok"]
    import docx                                                    # re-saved: Word writes the name back
    d = docx.Document(f)
    d.core_properties.author = "Jane Public"
    d.save(f)
    assert scrub.inspect(f, ["Jane Public"])
    rep = _run(tmp_path, f)
    assert rep["ok"] and scrub.inspect(f, ["Jane Public"]) == []
