"""A release ships a checksum file that matches what it ships (2026-10-18: build 1's SHA256SUMS.txt sat beside build 2)."""
from fieldkit.autopilot import inbox


def test_the_sums_file_must_match_every_uploaded_file(tmp_path):
    a, b = tmp_path / "setup.exe", tmp_path / "app.zip"
    a.write_bytes(b"build 2 installer")
    b.write_bytes(b"build 2 zip")
    sums = tmp_path / "SHA256SUMS.txt"
    inbox.write_sums([a, b], sums)
    assert inbox.sums_blockers([a, b, sums]) == []
    a.write_bytes(b"build 3 installer")                                   # the file changed after the sums were made
    assert any("wrong hash for setup.exe" in x for x in inbox.sums_blockers([a, b, sums]))
    assert inbox.sums_blockers([a, b]) == ["no SHA256SUMS.txt among the assets (fieldkit publish --sums OUT writes it from the assets)"]
    assert any("not uploaded" in x for x in inbox.sums_blockers([a, sums]))
