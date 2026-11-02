"""fieldkit install verify: every check can fail (the self-test seeds each break in a fake HOME)."""
import json
import sys

import pytest

from fieldkit.method import installverify as iv

linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads a Linux desktop")


def test_png_header_and_blocky_detection():
    good = iv._png(64, 1)
    info = iv.png_info(good)
    assert (info["w"], info["h"], info["depth"], info["ctype"]) == (64, 64, 8, 6)
    assert iv.blocky_upscale(good, info) is False
    up = iv._png(64, 1, repeat=2)
    assert iv.blocky_upscale(up, iv.png_info(up)) is True
    rgb = iv._png(64, 1, rgba=False)
    assert iv.blocky_upscale(rgb, iv.png_info(rgb)) is None
    assert iv.png_info(b"not a png at all, not at all, not at all, no") is None


@linux
def test_self_test_catches_every_break():
    res = iv.self_test()
    assert len(res) == len(iv.BREAKS) + 1
    assert [r for r in res if not r["ok"]] == []


@linux
def test_a_correct_install_has_no_failure(tmp_path):
    f = iv.Fake(tmp_path)
    rows, resolved = iv.verify(f.product, f.ref, f.env, session_start=0)
    assert [r["check"] for r in rows if not r["ok"]] == []
    assert set(resolved) == {f"{s}x{s}" for s in iv.REQUIRED_SIZES} | {"scalable"}
    assert any(r["check"] == "session" and r.get("info") for r in rows)


@linux
def test_a_size_the_release_does_not_ship_fails(tmp_path):
    f = iv.Fake(tmp_path)
    ref = f.ref
    del ref["icons"]["64x64"]
    rows, _ = iv.verify(f.product, ref, f.env)
    bad = [r for r in rows if not r["ok"]]
    assert [r["check"] for r in bad] == ["icon.64x64"] and "ships no 64x64" in bad[0]["line"]


@linux
def test_shadowing_names_the_release_copy_that_is_hidden(tmp_path):
    f = iv.Fake(tmp_path)
    iv._seed_old_icon(f)
    rows, _ = iv.verify(f.product, f.ref, f.env)
    r = next(r for r in rows if r["check"] == "icon.256x256")
    assert not r["ok"] and "shadowed" in r["line"] and "recycle" in r["fix"]


def test_reference_from_release_reads_sizes_and_binary(tmp_path):
    def fake_fetch(tag, repo, name, folder, run=None, urlopen=None):
        folder.mkdir(parents=True)
        (folder / name).write_bytes(b"deb")
        (folder / f"SHA256SUMS-{tag}.txt").write_text(f"{'a' * 64}  fakeprod-{tag}-linux-amd64\n")
        return folder / name, "x"

    def fake_run(cmd, timeout=60):
        d = tmp_path / "r" / "x" / "usr" / "share" / "icons" / "hicolor"
        for s in ("128x128", "scalable"):
            (d / s / "apps").mkdir(parents=True)
            (d / s / "apps" / ("fakeprod.svg" if s == "scalable" else "fakeprod.png")).write_bytes(b"i" + s.encode())
        return 0, "", ""
    ref = iv.reference_from_release("fakeprod", "v1.2.3", "o/r", tmp_path / "r", run=fake_run, fetch_fn=fake_fetch)
    assert ref["binary_sha"] == "a" * 64 and set(ref["icons"]) == {"128x128", "scalable"}


def test_other_platform_is_reported_not_passed(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    assert iv.main(["verify", "gorilla-opencode", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "not-this-platform"


def test_unknown_product_refused():
    assert iv.main(["verify", "nope"]) == 2
