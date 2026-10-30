"""fieldkit release page (fieldkit/build/relpage.py): one tool for every Gorilla OpenCode release page, so no session
writes the page composer again (owner, 2026-10-27). What is held: opening, plain-language track, developer track in
that order, each in full; notes headings one level down; the table measured from the files (a file that does not
match SHA256SUMS is refused); the table at the marker, else before "Test coverage and CI"; a missing notes file named.
"""
import hashlib

import pytest

from fieldkit.build import relpage
from fieldkit.cli import main


def _release(tmp_path, dev_extra=""):
    src, files = tmp_path / "repo", tmp_path / "dist"
    (src / "ReleaseNotes" / "openings").mkdir(parents=True)
    (src / "Changelogs").mkdir()
    files.mkdir()
    (src / "ReleaseNotes" / "openings" / "v1.2.3.md").write_text("# App 1.2.3\n\n## What is this program?\n\nIt is.\n",
                                                                  encoding="utf-8")
    (src / "Changelogs" / "v1.2.3-release-notes.layman.md").write_text(
        "# v1.2.3: long title\n\n**Date:** today\n\n---\n\n## Why\n\nBecause.\n\n### Detail\n\nMore.\n", encoding="utf-8")
    (src / "Changelogs" / "v1.2.3-release-notes.developer.md").write_text(
        "# v1.2.3: dev title\n\n**Commit range:** a..b\n\n---\n\n## Summary\n\nS.\n" + dev_extra +
        "\n## Test coverage and CI\n\nCI.\n", encoding="utf-8")
    (files / "app.exe").write_bytes(b"exe bytes")
    sha = hashlib.sha256(b"exe bytes").hexdigest()
    (files / "SHA256SUMS-v1.2.3.txt").write_text(f"{sha}  app.exe\n", encoding="utf-8")
    return src, files, sha


def test_the_page_is_opening_then_both_tracks_with_the_measured_table(tmp_path):
    src, files, sha = _release(tmp_path)
    page = relpage.compose(src, "v1.2.3", files, commit="abc1234")
    order = [page.index(x) for x in ("# App 1.2.3", "# In plain language", "### Why", "#### Detail",
                                     "# For developers", "### Summary", "| `app.exe` | 9 | `" + sha + "` |",
                                     "### Test coverage and CI")]
    assert order == sorted(order)
    assert "long title" not in page and "dev title" not in page and "**Date:**" not in page
    assert "every file built from abc1234" in page


def test_the_table_goes_at_the_marker_when_there_is_one(tmp_path):
    src, files, _ = _release(tmp_path, dev_extra="\n## Toolchain\n\n<!-- files table -->\n")
    page = relpage.compose(src, "1.2.3", files)
    assert page.index("| `app.exe`") < page.index("### Test coverage") and relpage.TABLE_MARK not in page
    assert page.index("### Toolchain") < page.index("| `app.exe`")


def test_a_file_that_is_not_its_checksum_or_a_missing_note_is_refused(tmp_path):
    src, files, _ = _release(tmp_path)
    (files / "app.exe").write_bytes(b"changed")
    with pytest.raises(ValueError, match="does not match"):
        relpage.compose(src, "v1.2.3", files)
    (src / "Changelogs" / "v1.2.3-release-notes.layman.md").unlink()
    with pytest.raises(ValueError, match="layman"):
        relpage.compose(src, "v1.2.3", files)


def test_the_command_writes_the_page_and_says_the_next_step(tmp_path, capsys):
    src, files, _ = _release(tmp_path)
    out = tmp_path / "page.md"
    assert main(["release", "page", "v1.2.3", "--src", str(src), "--dir", str(files), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# App 1.2.3")
    assert "fieldkit ship gh-release v1.2.3" in capsys.readouterr().out
    assert main(["release", "page", "v1.2.3", "--src", str(src)]) == 2
