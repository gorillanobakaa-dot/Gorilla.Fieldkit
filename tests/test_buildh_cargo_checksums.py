"""D-158-02's check (2026-10-18): every hash in a vendored crate's .cargo-checksum.json equals its file, which is what
Cargo checks; the old check only asked that the json existed."""
import hashlib
import json

import pytest

from fieldkit.buildh import decisions


def _crate(tmp_path, text, recorded):
    d = tmp_path / "third_party" / "rust" / "c"
    d.mkdir(parents=True)
    (d / "src.rs").write_bytes(text.encode())
    (d / ".cargo-checksum.json").write_text(json.dumps({"files": {"src.rs": recorded}, "package": "x"}))


def test_a_recomputed_checksum_passes_and_a_stale_one_fails(tmp_path):
    _crate(tmp_path, "edited\n", hashlib.sha256(b"edited\n").hexdigest())
    assert decisions.cargo_checksums(tmp_path, ["third_party/rust/c"])[0]
    (tmp_path / "third_party" / "rust" / "c" / "src.rs").write_text("edited again\n", encoding="utf-8")
    ok, ev = decisions.cargo_checksums(tmp_path, ["third_party/rust/c"])
    assert not ok and "src.rs" in ev


def test_a_crate_the_tree_lacks_is_unreadable_not_passed(tmp_path):
    with pytest.raises(decisions.Unreadable):
        decisions.cargo_checksums(tmp_path, ["third_party/rust/none"])
