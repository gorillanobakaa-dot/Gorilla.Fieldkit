"""refcheck: each rule finds exactly what is missing, and nothing that exists."""
from fieldkit.build import refcheck
from fieldkit.exam import fixture


def test_manifest_on_the_exam_fixture(tmp_path):
    root = fixture.build(tmp_path / "p")
    r = refcheck.check_manifest(root / "MANIFEST.txt")
    assert sorted(r["missing"]) == sorted(fixture.MISSING_FROM_MANIFEST) and r["checked"] == 12
    out = refcheck.lines(r)
    assert out[0] == "3 of 12 missing (manifest):" and out[-1].startswith("NEXT:")


def test_manifest_ignores_comments_and_blank_lines(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "M").write_text("# comment\n\na.txt\n")
    r = refcheck.check_manifest(tmp_path / "M")
    assert r["ok"] and r["checked"] == 1
    assert refcheck.lines(r) == ["all 1 references exist (manifest).", "NEXT: nothing is missing."]


def test_markdown_links(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "real.md").write_text("x")
    (tmp_path / "README.md").write_text("[ok](docs/real.md) [gone](docs/missing.md) [web](https://x.org) "
                                        "[anchor](#top) [sec](docs/real.md#part) [mail](mailto:a@example.com)\n")
    r = refcheck.check_markdown(tmp_path)
    assert r["missing"] == ["README.md -> docs/missing.md"] and r["checked"] == 3


def test_python_imports(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "core.py").write_text("")
    (tmp_path / "main.py").write_text("import os\nimport requests\nfrom app import core\nfrom app.core import x\n"
                                      "import app.gone\nfrom app.missing_mod import y\n")
    r = refcheck.check_python(tmp_path)
    assert sorted(r["missing"]) == ["main.py -> app.gone", "main.py -> app.missing_mod"]


def test_regex_rule(tmp_path):
    (tmp_path / "img.png").write_bytes(b"x")
    (tmp_path / "page.html").write_text('<img src="img.png"><img src="nope.png">')
    r = refcheck.check_regex(tmp_path, r'src="([^"]+)"', "*.html")
    assert r["missing"] == ["page.html -> nope.png"]
