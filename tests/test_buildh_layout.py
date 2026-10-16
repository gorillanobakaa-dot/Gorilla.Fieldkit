# Version: 1.0.0 · updated 26-10-15-19-50
"""layout.py: one install read the same way packed (omni.ja) or unpacked (loose files), on any platform."""
import sys
import zipfile

import pytest

from fieldkit.buildh import layout, proof


def _unpacked(root):
    (root / "browser" / "modules").mkdir(parents=True)
    (root / "chrome.manifest").write_text("manifest browser/chrome.manifest\n")
    (root / "browser" / "chrome.manifest").write_text("")
    (root / "modules").mkdir()
    (root / "modules" / "Kept.sys.mjs").write_text("x")
    (root / "browser" / "modules" / "Gone.sys.mjs").write_text("x")
    return root


def test_unpacked_members_split_like_the_archives(tmp_path):
    root = _unpacked(tmp_path)
    assert "modules/Kept.sys.mjs" in layout.members(root, "omni.ja")
    assert not any(m.startswith("browser/") for m in layout.members(root, "omni.ja"))
    assert "modules/Gone.sys.mjs" in layout.members(root, "browser/omni.ja")
    assert layout.read(root, "browser/omni.ja", "modules/Gone.sys.mjs") == b"x"
    with pytest.raises(KeyError):
        layout.read(root, "browser/omni.ja", "modules/Absent.sys.mjs")


def test_packed_wins_when_the_archive_exists(tmp_path):
    (tmp_path / "browser").mkdir()
    with zipfile.ZipFile(tmp_path / "browser" / "omni.ja", "w") as z:
        z.writestr("modules/InZip.sys.mjs", "z")
    assert layout.members(tmp_path, "browser/omni.ja") == ["modules/InZip.sys.mjs"]
    assert layout.read(tmp_path, "browser/omni.ja", "modules/InZip.sys.mjs") == b"z"


def test_neither_archive_nor_folder_refuses(tmp_path):
    with pytest.raises(FileNotFoundError):
        layout.members(tmp_path, "omni.ja")


def test_exe_follows_the_platform(tmp_path):
    assert layout.exe(tmp_path).name == ("firefox.exe" if sys.platform == "win32" else "firefox")


def test_excised_row_reads_an_unpacked_install(tmp_path):
    """2026-10-15: on the unpacked Linux build this row said 'unreadable' and checked nothing."""
    root = _unpacked(tmp_path)
    r = proof.excised_row(root, ["browser/components/x/Gone.sys.mjs"])
    assert not r["ok"] and r["bad"] == ["browser/omni.ja:modules/Gone.sys.mjs"]
