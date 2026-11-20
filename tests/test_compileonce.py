from __future__ import annotations

import json
from pathlib import Path

from fieldkit import compileonce


def test_compileonce_build_and_check(tmp_path: Path):
    pkg = tmp_path / "fieldkit"
    pkg.mkdir()
    mod = pkg / "sample.py"
    mod.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    kits = tmp_path / "kits" / "demo"
    kits.mkdir(parents=True)
    kmod = kits / "tool.py"
    kmod.write_text("X = 42\n", encoding="utf-8")

    # Initially stale (no .pyc yet)
    chk0 = compileonce.check(tmp_path, dirs=("fieldkit", "kits"))
    assert chk0["ok"] is False
    assert chk0["stale_count"] == 2

    # Build once
    res1 = compileonce.build(tmp_path, dirs=("fieldkit", "kits"))
    assert res1["ok"] is True
    assert res1["total"] == 2
    assert res1["compiled_now"] == 2
    assert res1["errors"] == []

    # Check after build -> fresh
    chk1 = compileonce.check(tmp_path, dirs=("fieldkit", "kits"))
    assert chk1["ok"] is True
    assert chk1["fresh"] == 2
    assert chk1["stale_count"] == 0

    # Second build without changes compiles 0 files
    res2 = compileonce.build(tmp_path, dirs=("fieldkit", "kits"))
    assert res2["ok"] is True
    assert res2["compiled_now"] == 0

    # Touching source marks it stale until rebuilt
    mod.write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
    chk_stale = compileonce.check(tmp_path, dirs=("fieldkit", "kits"))
    assert chk_stale["ok"] is False
    assert any(s["file"].replace("\\", "/") == "fieldkit/sample.py" for s in chk_stale["stale"])

    res3 = compileonce.build(tmp_path, dirs=("fieldkit", "kits"))
    assert res3["ok"] is True
    assert res3["compiled_now"] >= 1
    assert compileonce.check(tmp_path, dirs=("fieldkit", "kits"))["ok"] is True


def test_compileonce_cli_json(tmp_path: Path, capsys):
    pkg = tmp_path / "fieldkit"
    pkg.mkdir()
    (pkg / "a.py").write_text("VAL = 1\n", encoding="utf-8")

    rc_b = compileonce.main(["build", "--root", str(tmp_path), "--json"])
    out_b = json.loads(capsys.readouterr().out)
    assert rc_b == 0
    assert out_b["ok"] is True
    assert out_b["compiled_now"] == 1

    rc_c = compileonce.main(["check", "--root", str(tmp_path), "--json"])
    out_c = json.loads(capsys.readouterr().out)
    assert rc_c == 0
    assert out_c["ok"] is True
    assert out_c["fresh"] == 1


def test_pfind_help_is_available_before_gather(tmp_path, monkeypatch, capsys):
    from fieldkit.desk import pfind

    monkeypatch.setattr(pfind.settings, "ROOT", tmp_path)
    assert pfind.main(["--help"]) == 0
    assert "fieldkit gather --only pfind" in capsys.readouterr().out
    assert pfind.main(["needle"]) == 2
    assert "tool source missing" in capsys.readouterr().err


def test_pfind_launcher_refuses_missing_and_same_size_stale_bytecode(tmp_path, monkeypatch, capsys):
    import os

    from fieldkit.desk import pfind

    source = tmp_path / compileonce.PFIND_FILE
    source.parent.mkdir(parents=True)
    source.write_text('def main():\n    print("found")\n    return 0\n', encoding="utf-8")
    monkeypatch.setattr(pfind.settings, "ROOT", tmp_path)
    monkeypatch.setattr(pfind.sys, "argv", ["pfind"])
    monkeypatch.setattr(pfind.sys, "path", list(pfind.sys.path))
    assert pfind.main() == 3
    assert "compileonce build" in capsys.readouterr().err
    assert compileonce.build(tmp_path)["compiled_now"] == 1
    _, bytecode, _ = compileonce.pyc_is_fresh(source)
    before = bytecode.stat().st_mtime_ns
    assert pfind.main() == pfind.main() == 0
    assert capsys.readouterr().out == "found\nfound\n"
    assert bytecode.stat().st_mtime_ns == before
    mtime = source.stat().st_mtime_ns
    source.write_text('def main():\n    print("other")\n    return 0\n', encoding="utf-8")
    os.utime(source, ns=(mtime, mtime))
    assert pfind.main() == 3
    assert "stale pfind bytecode" in capsys.readouterr().err
    assert bytecode.stat().st_mtime_ns == before
    assert compileonce.build(tmp_path)["compiled_now"] == 1
    assert pfind.main() == 0
    assert capsys.readouterr().out == "other\n"
