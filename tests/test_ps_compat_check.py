"""Tests for fieldkit repair ps-compat-check: detects and fixes PowerShell 5.1/7 incompatibilities."""
import sys
from pathlib import Path

import pytest

from fieldkit.repair import ps_compat_check as pcc

# --- unit: check_file -----------------------------------------------------------

def test_expand_archive_is_flagged(tmp_path):
    p = tmp_path / "install.ps1"
    p.write_text("Expand-Archive -LiteralPath $zip -DestinationPath $dest\n", encoding="utf-8")
    findings, err = pcc.check_file(p)
    assert err is None
    assert len(findings) == 1
    assert findings[0]["rule"] == "expand-archive-ps7-broken"
    assert findings[0]["line"] == 1


def test_commented_expand_archive_is_not_flagged(tmp_path):
    p = tmp_path / "s.ps1"
    p.write_text("# Expand-Archive would go here\n", encoding="utf-8")
    findings, err = pcc.check_file(p)
    assert err is None
    assert findings == []


def test_get_filehash_is_flagged(tmp_path):
    p = tmp_path / "s.ps1"
    p.write_text("$h = Get-FileHash $f -Algorithm SHA256\n", encoding="utf-8")
    findings, err = pcc.check_file(p)
    assert err is None
    assert any(f["rule"] == "get-filehash-ps7-module-path" for f in findings)


def test_convertfromjson_ashashtable_is_flagged(tmp_path):
    p = tmp_path / "s.ps1"
    p.write_text("$d = Get-Content f.json | ConvertFrom-Json -AsHashtable\n", encoding="utf-8")
    findings, err = pcc.check_file(p)
    assert err is None
    assert any(f["rule"] == "convertfromjson-ashashtable-ps6" for f in findings)


def test_clean_file_has_no_findings(tmp_path):
    p = tmp_path / "s.ps1"
    p.write_text("[IO.Compression.ZipFile]::ExtractToDirectory($zip, $dest)\n", encoding="utf-8")
    findings, err = pcc.check_file(p)
    assert err is None
    assert findings == []


def test_non_ps1_file_is_skipped(tmp_path, capsys):
    p = tmp_path / "script.sh"
    p.write_text("Expand-Archive foo bar\n", encoding="utf-8")
    rc = pcc.main([str(p)])
    out = capsys.readouterr().out
    assert rc == 0 and "PASS" in out


# --- apply: auto-fix -----------------------------------------------------------

def test_apply_fixes_expand_archive(tmp_path):
    p = tmp_path / "install.ps1"
    p.write_text("    Expand-Archive -LiteralPath $zipPath -DestinationPath $stage\n", encoding="utf-8")
    rc = pcc.main([str(p), "--apply"])
    new_text = p.read_text(encoding="utf-8")
    assert "ExtractToDirectory" in new_text
    assert "Expand-Archive" not in new_text


# --- integration: the real install.ps1 is clean after the fix ------------------

def test_install_ps1_has_no_compat_issues():
    root = Path(__file__).resolve().parents[1]
    ps1 = root / "install.ps1"
    if not ps1.is_file():
        pytest.skip("install.ps1 not found")
    findings, err = pcc.check_file(ps1)
    assert err is None
    assert findings == [], f"install.ps1 has PS compat issues: {findings}"


# --- CLI exit codes ------------------------------------------------------------

def test_cli_exit_3_on_findings(tmp_path):
    p = tmp_path / "bad.ps1"
    p.write_text("Expand-Archive -LiteralPath $a -DestinationPath $b\n", encoding="utf-8")
    assert pcc.main([str(p)]) == 3


def test_cli_exit_0_on_clean(tmp_path):
    p = tmp_path / "good.ps1"
    p.write_text("[IO.Compression.ZipFile]::ExtractToDirectory($a, $b)\n", encoding="utf-8")
    assert pcc.main([str(p)]) == 0