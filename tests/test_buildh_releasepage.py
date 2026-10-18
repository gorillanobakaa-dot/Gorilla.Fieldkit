"""The release page assembled from PAGE.parts (2026-10-19): order, demoted track headings, claim lists left out."""
import pytest

from fieldkit.buildh import releasepage as rp


def test_parts_are_joined_in_order_with_their_treatment(tmp_path):
    (tmp_path / "HEAD.md").write_text("**Title**\n", encoding="utf-8")
    (tmp_path / "LAY.md").write_text("# Track title\n\n## Why\n\nBecause.\n\n## Claim sources\n\n- x\n", encoding="utf-8")
    (tmp_path / "PAGE.parts").write_text("# order\nHEAD.md\nLAY.md demote no-claims heading=## In plain words\n", encoding="utf-8")
    text, missing = rp.assemble(tmp_path)
    assert missing == []
    assert text.index("**Title**") < text.index("## In plain words") < text.index("### Why")
    assert "Track title" not in text and "Claim sources" not in text


def test_a_missing_part_writes_nothing(tmp_path):
    (tmp_path / "PAGE.parts").write_text("GONE.md\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        rp.write(tmp_path)
    assert not (tmp_path / "RELEASE-BODY.md").exists()
