"""Tesseract and ImageMagick are declared dependencies on every system and the acceptance check REQUIRES them:
a generated picture with known text is read by tesseract, magick identify/convert run on it. An absent binary fails."""
import json
import shutil
from pathlib import Path

import pytest

from fieldkit.core import doctor
from fieldkit.image import acceptance, tools

REQ = json.loads((Path(doctor.__file__).with_name("requirements.json")).read_text(encoding="utf-8"))
pytest.importorskip("PIL")


@pytest.mark.parametrize("name", ["tesseract", "magick"])
def test_declared_for_every_system_per_user(name):
    t = REQ["tools"][name]
    assert "image" in t["for"]
    assert {"debian", "fedora", "windows", "mac"} <= set(t["install"])
    for fam in ("debian", "fedora"):
        assert "sudo" not in t["install"][fam]            # per user, no sudo
    assert "sudo" not in json.dumps(tools.SPECS[name][3])


def test_doctor_asks_for_both_when_missing():
    r = doctor.check(pipeline="image", which=lambda n: None, have_dist=lambda n: "1")
    missing = {x["name"] for x in r["rows"] if x["kind"] == "tool" and not x["ok"]}
    assert {"tesseract", "magick"} <= missing and not r["ok"]


def test_absent_binaries_fail_and_nothing_is_skipped(tmp_path):
    r = acceptance.check(workdir=str(tmp_path), local={}, which=lambda n: None)
    assert not r["ok"]
    assert [s["ok"] for s in r["steps"]] == [False, False]
    assert "micromamba" in r["steps"][0]["detail"] or "scoop" in r["steps"][0]["detail"] or "brew" in r["steps"][0]["detail"]


def test_wrong_text_fails(tmp_path):
    class R:
        returncode, stdout, stderr = 0, "SOMETHING ELSE", ""
    r = acceptance.check(workdir=str(tmp_path), local={}, which=lambda n: n, run=lambda *a, **k: R())
    assert not r["ok"]
    assert not next(s for s in r["steps"] if s["step"].startswith("tesseract reads"))["ok"]


@pytest.mark.skipif(not (tools.find("tesseract") and tools.find("magick")), reason="needs the real programs; the failing case is above")
def test_real_programs_pass(tmp_path):
    r = acceptance.check(workdir=str(tmp_path))
    assert r["ok"], r
    assert not list(tmp_path.iterdir())                   # scratch recycled
