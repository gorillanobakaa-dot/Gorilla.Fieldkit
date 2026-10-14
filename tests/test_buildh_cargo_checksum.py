"""D-158-02: a vendored crate's .cargo-checksum.json is derived data, recomputed from the crate's files."""
import hashlib
import json
import subprocess
from pathlib import Path

from fieldkit.buildh import firefox, task

CRATE = "third_party/rust/fake-crate"
OTHER = "third_party/rust/other-crate"
PACKAGE = "0" * 64


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _crate(root, rel, files):
    d = Path(root) / rel
    for name, body in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(body)
    data = {"files": {n: _sha(b) for n, b in files.items()}, "package": PACKAGE}
    (d / ".cargo-checksum.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8", newline="")
    return d


def _tree(tmp_path):
    w = tmp_path / "work"
    _crate(w, CRATE, {"src/lib.rs": b"fn a() {}\n", "src/gone.rs": b"x\n", "Cargo.toml": b"[package]\n"})
    _crate(w, OTHER, {"src/lib.rs": b"fn b() {}\n"})
    _git(w.parent, "init", "-q", str(w))
    _git(w, "add", "-A")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "pristine")
    return w


def test_recompute_rewrites_hashes_drops_missing_keeps_package(tmp_path):
    w = _tree(tmp_path)
    crate = w / CRATE
    (crate / "src/lib.rs").write_bytes(b"fn a() { gorilla_off(); }\n")
    (crate / "src/gone.rs").unlink()
    assert firefox.cargo_checksum_problems(crate)                 # stale before
    assert firefox.recompute_cargo_checksum(crate) is True
    raw = (crate / ".cargo-checksum.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["files"] == {"src/lib.rs": _sha(b"fn a() { gorilla_off(); }\n"), "Cargo.toml": _sha(b"[package]\n")}
    assert data["package"] == PACKAGE
    assert "\n" not in raw and list(data) == ["files", "package"]  # one line, no trailing newline, key order kept
    assert firefox.cargo_checksum_problems(crate) == []
    assert firefox.recompute_cargo_checksum(crate) is False        # a second run changes nothing


def test_an_untouched_crate_is_left_byte_identical(tmp_path):
    w = _tree(tmp_path)
    before = (w / OTHER / ".cargo-checksum.json").read_bytes()
    assert firefox.recompute_cargo_checksum(w / OTHER) is False
    assert (w / OTHER / ".cargo-checksum.json").read_bytes() == before


def test_auto_port_does_not_port_a_checksum_hunk(tmp_path):
    w = _tree(tmp_path)
    before = (w / CRATE / ".cargo-checksum.json").read_bytes()
    hunk = {"header": "@@ -1 +1 @@", "lines": ['-{"files":{}}', '+{"files":{"src/lib.rs":"' + "1" * 64 + '"}}']}
    res = firefox.auto_port({"workdir": str(w)}, {"id": "s"}, "p", f"{CRATE}/.cargo-checksum.json", hunk)
    assert res["ok"] and "recomputed" in res["notes"][0]
    assert (w / CRATE / ".cargo-checksum.json").read_bytes() == before


def test_check_port_accepts_unchanged_or_consistent_and_refuses_stale(tmp_path):
    w = _tree(tmp_path)
    t, f, hunk = {"workdir": str(w)}, f"{CRATE}/.cargo-checksum.json", {"header": "@@ -1 +1 @@", "lines": ["-a", "+b"]}
    assert firefox.check_port(t, {"id": "s"}, "p", f, hunk)["ok"]                 # unchanged from HEAD
    (w / CRATE / "src/lib.rs").write_bytes(b"edited\n")
    assert firefox.check_port(t, {"id": "s"}, "p", f, hunk)["ok"]                 # still unchanged: recomputed later
    data = json.loads((w / f).read_text())
    data["files"]["Cargo.toml"] = "f" * 64                                         # changed and wrong
    (w / f).write_text(json.dumps(data, separators=(",", ":")))
    res = firefox.check_port(t, {"id": "s"}, "p", f, hunk)
    assert not res["ok"] and any("Cargo.toml" in why for why in res["why"])
    firefox.recompute_cargo_checksum(w / CRATE)                                   # changed and consistent
    assert firefox.check_port(t, {"id": "s"}, "p", f, hunk)["ok"]
    (w / f).write_text("{not json")
    assert not firefox.check_port(t, {"id": "s"}, "p", f, hunk)["ok"]


def test_export_recomputes_changed_crates_and_the_patch_carries_the_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = _tree(tmp_path)
    other_before = (w / OTHER / ".cargo-checksum.json").read_bytes()
    base = subprocess.run(["git", "-C", str(w), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (w / CRATE / "src/lib.rs").write_bytes(b"fn a() { gorilla_off(); }\n")      # a group's committed source hunk
    _git(w, "add", "-A")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "hunk")
    t = {"workdir": str(w), "meta": {"group_base": {"13.G": base}, "upstream": {"version": "158.0"}}, "checkpoints": []}
    res = firefox.step_export_group(t, tmp_path, "13.G", out_dir=tmp_path / "out")
    assert res["ok"] and res["checksums_recomputed"] == [CRATE]
    assert firefox.cargo_checksum_problems(w / CRATE) == []
    assert (w / OTHER / ".cargo-checksum.json").read_bytes() == other_before
    status = subprocess.run(["git", "-C", str(w), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert status == ""                                                            # the recomputed file is committed
    patch = (tmp_path / "out" / "13.G.patch").read_text()
    assert f"{CRATE}/.cargo-checksum.json" in patch and _sha(b"fn a() { gorilla_off(); }\n") in patch
    assert OTHER not in patch


def test_the_final_recheck_does_not_judge_a_checksum_by_its_hunk():
    assert firefox.still_holds({}, f"{CRATE}/.cargo-checksum.json", {"lines": ["-a", "+b"]}, ["a"], ["c"]) == []
