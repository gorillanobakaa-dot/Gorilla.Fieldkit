"""fieldkit image fetch: a pinned download is installed only when its sha256 matches; a changed byte installs nothing."""
import hashlib
import io
import json
import zipfile

import pytest

from fieldkit.image import cli, fetch


def _zip(member="cwebp.exe", data=b"MZ-program"):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("libwebp/bin/" + member, data)
    return b.getvalue()


def _table(blob):
    return {"cwebp": {"version": "9", "member": "cwebp",
                      "windows": {"url": "https://x/a.zip", "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)}}}


def test_matching_hash_installs_the_program(tmp_path):
    blob = _zip()
    r = fetch.fetch_one("cwebp", "win32", dl=lambda u: blob, table=_table(blob), into=tmp_path)
    assert (tmp_path / "cwebp.exe").read_bytes() == b"MZ-program" and r["version"] == "9"


def test_one_changed_byte_installs_nothing(tmp_path):
    blob = _zip()
    with pytest.raises(fetch.FetchError, match="sha256 mismatch"):
        fetch.fetch_one("cwebp", "win32", dl=lambda u: blob + b"\0", table=_table(blob), into=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_unpinned_tool_or_system_is_refused(tmp_path):
    blob = _zip()
    with pytest.raises(fetch.FetchError, match="no pinned download"):
        fetch.fetch_one("cjpeg", "win32", table=_table(blob), into=tmp_path)
    with pytest.raises(fetch.FetchError, match="this system"):
        fetch.fetch_one("cwebp", "linux", table=_table(blob), into=tmp_path)


def test_pins_file_is_https_hex_and_covers_both_systems():
    for name, e in fetch.pins().items():
        for system in ("windows", "linux"):
            p = e[system]
            assert p["url"].startswith("https://") and len(p["sha256"]) == 64 and int(p["sha256"], 16) >= 0


def test_cli_reports_a_mismatch_with_exit_3(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fetch, "download", lambda u: b"not the pinned file")
    monkeypatch.setenv("FIELDKIT_IMAGE_TOOLS", str(tmp_path))
    assert cli.main(["fetch", "oxipng", "--json"]) == 3
    assert "mismatch" in json.loads(capsys.readouterr().out)["rows"][0]["error"]
    assert list(tmp_path.iterdir()) == []
