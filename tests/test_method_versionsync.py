"""fieldkit version-sync: every place a project states its version agrees; a template is not a version; the built
program is held to the same answer (2026-10-19: TPFanControl's installer said gorilla.3, its program gorilla.4)."""
import sys

from fieldkit import cli
from fieldkit.method import versionsync as vs


def _rust(root, v="0.3.0", lock_v=None):
    (root / "Cargo.toml").write_text(f'[package]\nname = "tool"\nversion = "{v}"\n', encoding="utf-8")
    (root / "Cargo.lock").write_text(f'version = 3\n\n[[package]]\nname = "tool"\nversion = "{lock_v or v}"\n',
                                     encoding="utf-8")


def test_places_that_agree_pass_and_one_that_differs_is_named(tmp_path):
    _rust(tmp_path)
    assert vs.check(tmp_path)["ok"]
    (tmp_path / "packaging").mkdir()
    (tmp_path / "packaging" / "PKGBUILD").write_text("pkgname=tool\npkgver=0.2.9\n", encoding="utf-8")
    r = vs.check(tmp_path)
    assert not r["ok"] and r["disagree"]


def test_a_template_is_not_a_version_and_only_templates_is_not_a_pass(tmp_path):
    (tmp_path / "packaging").mkdir()
    (tmp_path / "packaging" / "changelog.in").write_text("tool (@VERSION@) stable; urgency=low\n", encoding="utf-8")
    assert not vs.check(tmp_path)["ok"]                            # nothing a person could check
    _rust(tmp_path)
    r = vs.check(tmp_path)
    assert r["ok"] and r["version"] == "0.3.0", r


def test_the_installer_define_and_a_header_constant_are_read(tmp_path):
    (tmp_path / "installer").mkdir()
    (tmp_path / "installer" / "Setup.iss").write_text('#define AppVersion   "2.5.1-gorilla.3"\n[Setup]\n'
                                                      'AppVersion={#AppVersion}\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.h").write_text('constexpr const char* APPVERSION = "2.5.1-gorilla.4 Single";\n',
                                            encoding="utf-8")
    r = vs.check(tmp_path)
    assert not r["ok"]
    got = {p["place"]: p["version"] for p in r["places"]}
    assert got["installer/Setup.iss".replace("/", "\\") if sys.platform == "win32" else "installer/Setup.iss"] \
        == "2.5.1-gorilla.3"
    assert got["src/app.h APPVERSION"] == "2.5.1-gorilla.4"


def test_expect_and_the_built_program(tmp_path):
    _rust(tmp_path, "0.3.0")
    stub = tmp_path / "prog.py"
    stub.write_text("print('tool 0.2.0')\n", encoding="utf-8")
    exe = tmp_path / ("prog.cmd" if sys.platform == "win32" else "prog")
    exe.write_text(f'@"{sys.executable}" "{stub}" %*\r\n' if sys.platform == "win32"
                   else f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n', encoding="utf-8")
    if sys.platform != "win32":
        exe.chmod(0o755)
    r = vs.check(tmp_path, binary=exe)
    assert not r["ok"] and any("--version" in p for p in r["disagree"]), r   # the program says 0.2.0
    assert not vs.check(tmp_path, expect="0.3.1")["ok"]
    assert vs.check(tmp_path, expect="0.3.0")["ok"]


def test_cli_exit_codes(tmp_path, capsys):
    _rust(tmp_path)
    assert cli.main(["version-sync", str(tmp_path)]) == 0
    assert "VERSION-SYNC OK" in capsys.readouterr().out
    assert cli.main(["version-sync", str(tmp_path), "--expect", "9.9.9"]) == 3
    assert cli.main(["version-sync", str(tmp_path / "missing")]) == 2
