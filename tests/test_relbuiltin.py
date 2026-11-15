"""The release page's "What is built in" table (fieldkit/build/relbuiltin.py, plan W6): generated from the tree's
internal/toolbox/SHA256SUMS + helpers.json and checked against third_party/PINS.md; plain words first on the page; a
pinned helper nobody described, or a version PINS.md does not name, is refused; a tree with no toolbox adds nothing.
"""
import json

import pytest

from fieldkit.build import relbuiltin, relpage

SUM = "a" * 64


def _tree(tmp_path, pins, helpers, pins_md="sd 1.1.0, ffmpeg 7.0.2 and 8.0\n"):
    tb = tmp_path / "internal" / "toolbox"
    tb.mkdir(parents=True)
    (tb / "SHA256SUMS").write_text("# c\n" + "".join(f"{SUM}  {p}\n" for p in pins), encoding="utf-8")
    (tb / "helpers.json").write_text(json.dumps(helpers), encoding="utf-8")
    (tmp_path / "third_party").mkdir()
    (tmp_path / "third_party" / "PINS.md").write_text(pins_md, encoding="utf-8")
    return tmp_path


HELPERS = [
    {"name": "sd", "version": "1.1.0", "purpose": "Find and replace text."},
    {"name": "ffmpeg", "version": "7.0.2", "version_darwin": "8.0", "purpose": "Converts video.", "mac_min": "11.0"},
]
PINS = ["windows-amd64/sd.exe", "linux-amd64/sd", "darwin-amd64/sd", "windows-amd64/ffmpeg.exe",
        "linux-amd64/ffmpeg", "darwin-amd64/ffmpeg"]


def test_one_row_per_pinned_helper_with_version_purpose_and_each_system(tmp_path):
    out = relbuiltin.table(_tree(tmp_path, PINS, HELPERS))
    assert "### What is built in" in out and "nothing to install" in out
    assert "| `sd` | 1.1.0 | Find and replace text. | yes | yes | yes |" in out
    assert "| `ffmpeg` | 7.0.2 (macOS: 8.0) | Converts video. | yes | yes | yes, needs macOS 11.0 |" in out


def test_a_helper_missing_on_one_system_says_no(tmp_path):
    out = relbuiltin.table(_tree(tmp_path, PINS[:2], HELPERS[:1]))
    assert "| `sd` | 1.1.0 | Find and replace text. | yes | yes | no |" in out


def test_a_pinned_helper_with_no_description_is_refused(tmp_path):
    with pytest.raises(ValueError, match="ffmpeg"):
        relbuiltin.table(_tree(tmp_path, PINS, HELPERS[:1]))


def test_a_version_pins_md_does_not_name_is_refused(tmp_path):
    with pytest.raises(ValueError, match="PINS.md"):
        relbuiltin.table(_tree(tmp_path, PINS, HELPERS, pins_md="nothing here\n"))


def test_a_tree_without_a_toolbox_adds_nothing(tmp_path):
    assert relbuiltin.table(tmp_path) == ""


def test_the_page_carries_the_table_after_the_plain_track_and_before_the_developer_one(tmp_path):
    src = _tree(tmp_path / "repo", PINS, HELPERS)
    files = tmp_path / "dist"
    files.mkdir()
    (src / "ReleaseNotes" / "openings").mkdir(parents=True)
    (src / "Changelogs").mkdir()
    (src / "ReleaseNotes" / "openings" / "v9.md").write_text("# App\n\nOpening.\n", encoding="utf-8")
    (src / "Changelogs" / "v9-release-notes.layman.md").write_text("# t\n\n---\n\n## Why\n\nBecause.\n", encoding="utf-8")
    (src / "Changelogs" / "v9-release-notes.developer.md").write_text("# t\n\n---\n\n## Summary\n\nS.\n", encoding="utf-8")
    (files / "a.bin").write_bytes(b"x")
    import hashlib
    (files / "SHA256SUMS-v9.txt").write_text(hashlib.sha256(b"x").hexdigest() + "  a.bin\n", encoding="utf-8")
    page = relpage.compose(src, "v9", files)
    order = [page.index(x) for x in ("# In plain language", "### Why", "### What is built in", "# For developers")]
    assert order == sorted(order)
