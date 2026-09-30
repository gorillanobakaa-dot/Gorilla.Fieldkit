"""gather: selection rules, provenance, drift detection; discover: per-file facts."""
import json

import pytest

from fieldkit import gather
from fieldkit.desk import discover


def test_select_include_exclude(tmp_path):
    for rel in ["a.py", "sub/b.py", "data/secret.json", "tests/test_a.py", "x.png", "__pycache__/a.pyc"]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    got = gather.select(tmp_path, ["*.py", "tests/**"], ["data/**", "**/__pycache__/**", "**/*.png"])
    assert got == ["a.py", "sub/b.py", "tests/test_a.py"]
    assert gather.select(tmp_path, ["./*.py"], []) == ["a.py"]          # top level only


@pytest.fixture
def fake_source(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "data").mkdir(parents=True)
    (src / "tool.py").write_text('"""Does a thing."""\nimport argparse\nif __name__ == "__main__":\n    pass\n')
    (src / "data" / "private.json").write_text('{"name": "someone"}')
    monkeypatch.setattr(gather, "TOOLBOX", tmp_path / "toolbox")
    return {"name": "demo", "local": str(src), "include": ["**/*"], "exclude": ["data/**"],
            "what": "demo tool"}


def test_gather_one_copies_records_and_excludes(fake_source, tmp_path):
    r = gather.gather_one(fake_source)
    dest = tmp_path / "toolbox" / "demo"
    assert r["files"] == 1 and (dest / "tool.py").is_file() and not (dest / "data").exists()
    prov = json.loads((dest / "PROVENANCE.json").read_text())
    assert list(prov["files"]) == ["tool.py"] and len(prov["files"]["tool.py"]) == 64
    assert r["compile_errors"] == []


def test_check_detects_drift_both_ways(fake_source, tmp_path):
    gather.gather_one(fake_source)
    assert gather.check_one(fake_source)["status"] == "in-sync"
    (tmp_path / "src" / "tool.py").write_text('"""Changed upstream."""\n')
    (tmp_path / "src" / "new.py").write_text("x = 1\n")
    r = gather.check_one(fake_source)
    assert r["status"] == "drift" and r["changed_at_source"] == ["tool.py"] and r["added_at_source"] == ["new.py"]
    gather.gather_one(fake_source)
    (tmp_path / "toolbox" / "demo" / "tool.py").write_text("edited by hand\n")
    assert gather.check_one(fake_source)["edited_in_toolbox"] == ["tool.py"]


def test_gather_reports_code_that_does_not_compile(fake_source, tmp_path):
    (tmp_path / "src" / "broken.py").write_text("def (:\n")
    r = gather.gather_one(fake_source)
    assert any(e.startswith("broken.py") for e in r["compile_errors"])


def test_test_one_runs_commands_where_told(fake_source, tmp_path):
    gather.gather_one(fake_source)
    ok = dict(fake_source, tests=[["python", "-c", "import pathlib; assert pathlib.Path('tool.py').exists()"]])
    assert gather.test_one(ok)["ok"] is True
    bad = dict(fake_source, tests=[["python", "-c", "raise SystemExit(1)"]])
    assert gather.test_one(bad)["ok"] is False
    none = dict(fake_source)
    assert gather.test_one(none)["ok"] is None


def test_manifest_is_valid():
    srcs = gather.load_manifest()
    public = [s for s in srcs if s.get("github")]
    assert len(public) >= 12 and all(s.get("what") for s in srcs)


def test_discover_facts(tmp_path, monkeypatch):
    tb = tmp_path / "toolbox" / "demo"
    tb.mkdir(parents=True)
    home = "C:" + "\\" + "Users" + "\\" + "someone" + "\\" + "x"    # built up so no real-looking path sits in this file
    (tb / "win.py").write_text('"""Windows thing."""\nimport winreg\nP = r"' + home + '"\n')
    (tb / "run.sh").write_text("#!/usr/bin/env bash\n# Installs the theme.\necho hi\n")
    (tb / "test_win.py").write_text("def test_x():\n    pass\n")
    monkeypatch.setattr(gather, "load_manifest", lambda: [])
    got = {t["id"]: t for t in discover.discover(tmp_path / "toolbox")}
    assert set(got) == {"demo/win.py", "demo/run.sh"}                 # tests are not tools
    assert got["demo/win.py"]["platforms"] == ["windows"] and got["demo/win.py"]["portable"] is False
    assert got["demo/run.sh"]["platforms"] == ["linux"] and got["demo/run.sh"]["title"] == "Installs the theme."
    assert got["demo/win.py"]["tests_present"] == ["test_win.py"]
