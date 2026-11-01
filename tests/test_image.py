"""fieldkit image: tool discovery, metric parsing, squash decisions (fake tools), OCR parsing and redaction, and the
privacy check on a real picture holding an email address (it must FAIL there, and pass on a clean one).
Real-tool tests are skipped where magick or tesseract is absent."""
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from fieldkit.image import cli, measure, ocr, squash, tools

HAVE_MAGICK = bool(tools.find("magick"))
HAVE_TESS = bool(tools.find("tesseract"))
PIL = pytest.importorskip("PIL")


@pytest.fixture(autouse=True)
def scratch_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setenv("FIELDKIT_SCRATCH_STATE", str(tmp_path / "state"))
    measure._CONVENTION.clear()


# ---- tools

def test_find_prefers_the_configured_path_and_never_guesses():
    which = lambda p: p if p in ("/opt/cwebp", "magick") else None      # noqa: E731
    local = {"image": {"tools": {"cwebp": "/opt/cwebp", "avifenc": "/nowhere/avifenc"}}}
    assert tools.find("cwebp", local, which) == "/opt/cwebp"
    assert tools.find("avifenc", local, which) is None          # configured but not a program: absent, no PATH fallback
    assert tools.find("magick", {}, which) == "magick"


def test_report_lists_missing_tools_with_the_install_command_and_source():
    rows = tools.report(local={}, which=lambda p: "/bin/magick" if p == "magick" else None,
                        run=lambda *a, **k: SimpleNamespace(stdout="Version: 7\n", stderr="", returncode=0),
                        platform="win32")
    by = {r["tool"]: r for r in rows}
    assert by["magick"]["present"] and by["magick"]["version"] == "Version: 7"
    assert not by["cwebp"]["present"] and by["cwebp"]["install"] == "scoop install libwebp"
    assert by["cwebp"]["source"].startswith("https://")
    assert tools.report(local={}, which=lambda p: None, platform="linux")[0]["install"].startswith("micromamba")


def test_doctor_exit_is_3_when_a_required_tool_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(tools, "find", lambda name, *a, **k: None)
    assert cli.main(["doctor", "--json"]) == 3
    assert '"missing"' in capsys.readouterr().out


# ---- metrics

def _run(stderr, rc=1):
    return lambda cmd, **k: SimpleNamespace(stdout="", stderr=stderr, returncode=rc)


def test_metric_parses_value_and_bracket(monkeypatch):
    monkeypatch.setattr(tools, "find", lambda n, *a, **k: "magick")
    assert measure.metric("PSNR", "a", "b", _run("39.5653 (0.329711)")) == (39.5653, 0.329711)
    assert measure.metric("PSNR", "a", "b", _run("inf", 0)) == (float("inf"), None)
    with pytest.raises(measure.ImageError):
        measure.metric("SSIM", "a", "b", _run("what"))
    with pytest.raises(measure.ImageError):
        measure.metric("SSIM", "a", "b", _run("boom", 2))


def test_ssim_reads_both_conventions(monkeypatch):
    monkeypatch.setattr(tools, "find", lambda n, *a, **k: "magick")
    # 7.1.2 style: dissimilarity, 0 for identical; bracket = (1 - SSIM) / 2
    def new(cmd, **k):
        same = cmd[-3] == cmd[-2]
        return SimpleNamespace(stdout="", stderr="0 (0)" if same else "1859.19 (0.0283694)", returncode=1)
    assert measure.ssim("a", "b", new) == pytest.approx(1 - 2 * 0.0283694)
    measure._CONVENTION.clear()
    # older style: the similarity itself, 1 for identical
    def old(cmd, **k):
        same = cmd[-3] == cmd[-2]
        return SimpleNamespace(stdout="", stderr="1" if same else "0.93", returncode=1)
    assert measure.ssim("a", "b", old) == pytest.approx(0.93)


# ---- squash decisions with fake tools

def _fake_tools(monkeypatch, have):
    monkeypatch.setattr(tools, "find", lambda n, *a, **k: f"/x/{n}" if n in have else None)

    def run(cmd, **k):
        name = Path(cmd[0]).stem
        if name == "magick" and cmd[1] == "identify":
            return SimpleNamespace(stdout="8 8", stderr="", returncode=0)
        if name == "magick" and cmd[1] == "compare":
            a, b = cmd[-3], cmd[-2]
            if a == b:
                return SimpleNamespace(stdout="", stderr="0 (0)", returncode=0)
            if cmd[3] == "PSNR":
                return SimpleNamespace(stdout="", stderr="40 (0.3)", returncode=1)
            ssim = 0.5 + os.path.getsize(b) / 100 / 200          # a file of q*100 bytes was encoded at quality q
            d = (1 - ssim) / 2
            return SimpleNamespace(stdout="", stderr=f"{d * 65535:.2f} ({d})", returncode=1)
        q = int(cmd[cmd.index("-q") + 1]) if "-q" in cmd else int(cmd[cmd.index("-quality") + 1])
        dst = cmd[cmd.index("-o") + 1] if "-o" in cmd else cmd[-1]
        Path(dst).write_bytes(b"x" * (q * 100))
        return SimpleNamespace(stdout="", stderr="", returncode=0)
    return run


def _orig(tmp_path, size=100_000):
    p = tmp_path / "in" / "icon.png"
    p.parent.mkdir()
    p.write_bytes(b"\x89PNG" + b"0" * size)
    return p


def test_target_ssim_finds_the_smallest_quality_that_reaches_it(tmp_path, monkeypatch):
    run = _fake_tools(monkeypatch, {"magick", "cwebp"})
    src = _orig(tmp_path)
    r = squash.squash_one(src, "webp", tmp_path / "out", target=0.9, run=run)
    assert r["kept"] and r["codec"] == "cwebp" and "-q 80" in r["settings"]          # 0.5 + 80/200 = 0.9
    assert r["ssim"] >= 0.9 and (tmp_path / "out" / "icon.webp").stat().st_size == 8000
    assert src.read_bytes().startswith(b"\x89PNG") and src.stat().st_size == 100_004     # the original is untouched


def test_below_the_floor_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    run = _fake_tools(monkeypatch, {"magick", "cwebp"})
    r = squash.squash_one(_orig(tmp_path), "webp", tmp_path / "out", quality=40, run=run)
    assert not r["kept"] and "below 0.98" in r["why"] and not (tmp_path / "out" / "icon.webp").exists()


def test_unreachable_target_is_refused(tmp_path, monkeypatch):
    run = _fake_tools(monkeypatch, {"magick", "cwebp"})
    r = squash.squash_one(_orig(tmp_path), "webp", tmp_path / "out", target=0.99, run=run)    # q 95 only gives 0.975
    assert not r["kept"] and "no quality up to 95" in r["why"]


def test_a_result_not_smaller_is_refused(tmp_path, monkeypatch):
    run = _fake_tools(monkeypatch, {"magick", "cwebp"})
    r = squash.squash_one(_orig(tmp_path, 5000), "webp", tmp_path / "out", target=0.9, run=run)   # 8000 > 5004
    assert not r["kept"] and "not smaller" in r["why"]


def test_the_original_is_never_overwritten(tmp_path, monkeypatch):
    run = _fake_tools(monkeypatch, {"magick"})
    src = _orig(tmp_path)
    r = squash.squash_one(src, "png", src.parent, quality=80, run=run)
    assert not r["kept"] and "overwrite the original" in r["why"]


def test_magick_encodes_when_the_real_codec_is_absent(tmp_path, monkeypatch):
    run = _fake_tools(monkeypatch, {"magick"})
    r = squash.squash_one(_orig(tmp_path), "avif", tmp_path / "out", target=0.9, run=run)
    assert r["codec"] == "magick" and r["kept"]


# ---- OCR parsing, detectors, redaction

TSV = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
       "5\t1\t1\t1\t1\t1\t10\t10\t50\t20\t95\tMail\n"
       "5\t1\t1\t1\t1\t2\t70\t10\t200\t20\t93\tjane.roe@gmail.com\n"  # privacy-scan: allow
       "5\t1\t1\t1\t2\t1\t10\t50\t60\t20\t90\tHello\n")


def test_parse_tsv_gives_words_with_boxes_and_lines():
    res = ocr.parse_tsv(TSV, "x.png")
    assert res["text"] == "Mail jane.roe@gmail.com\nHello"  # privacy-scan: allow
    assert res["words"][1]["box"] == [70, 10, 200, 20]


def test_only_the_email_word_is_boxed_for_redaction():
    hits = ocr.findings(ocr.parse_tsv(TSV), terms=[])
    assert [h["kind"] for h in hits] == ["email"] and hits[0]["boxes"] == [[70, 10, 200, 20]]


def test_a_term_spanning_two_words_blacks_out_the_whole_line():
    tsv = TSV.replace("jane.roe@gmail.com", "Jane").replace("Hello", "Roe")  # privacy-scan: allow
    hits = ocr.findings(ocr.parse_tsv(tsv), terms=["mail jane"])
    assert hits and len(hits[0]["boxes"]) == 2


def test_redact_writes_a_copy_and_leaves_the_original(tmp_path):
    from PIL import Image
    src = tmp_path / "s.png"
    Image.new("RGB", (300, 100), "white").save(src)
    before = src.read_bytes()
    out = ocr.redact(src, [{"boxes": [[20, 20, 100, 30]]}])
    assert out.endswith("s.redacted.png") and src.read_bytes() == before
    assert Image.open(out).getpixel((50, 30)) == (0, 0, 0) and Image.open(out).getpixel((5, 5)) == (255, 255, 255)
    with pytest.raises(measure.ImageError):
        ocr.redact(src, [], out=str(src))


def test_an_unreadable_picture_is_a_finding_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "find", lambda n, *a, **k: None)
    hits, res = ocr.check(tmp_path / "x.png")
    assert res is None and hits[0]["kind"] == "not-scanned"


def _picture(path, text):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (900, 140), "white")
    ImageDraw.Draw(im).text((20, 40), text, fill="black", font=ImageFont.load_default(size=36))
    im.save(path)
    return path


@pytest.mark.skipif(not HAVE_TESS, reason="tesseract is not installed")
def test_privacy_fails_on_a_picture_with_an_email_and_passes_a_clean_one(tmp_path, capsys):
    bad = _picture(tmp_path / "bad.png", "Signed in as jane.roe@gmail.com")  # privacy-scan: allow
    ok = _picture(tmp_path / "ok.png", "Theme Gorilla Dark version 158")
    assert cli.main(["privacy", str(bad), "--json"]) == 3
    assert '"email"' in capsys.readouterr().out
    assert cli.main(["privacy", str(ok)]) == 0
    assert cli.main(["privacy", str(bad), "--redact"]) == 3
    assert cli.main(["privacy", str(tmp_path / "bad.redacted.png")]) == 0          # the redacted copy is clean
    from fieldkit.core import privacy
    assert privacy.scan_text("jane.roe@gmail.com")                                    # privacy-scan: allow (the detector itself)
    assert ocr.scan_images([str(bad)]) and not ocr.scan_images([str(ok)])


def test_privacy_scan_images_flag_is_off_by_default(tmp_path, monkeypatch):
    from fieldkit import cli as top
    _picture(tmp_path / "p.png", "x")
    called = []
    monkeypatch.setattr(ocr, "scan_images", lambda *a, **k: called.append(1) or {})
    assert top.main(["privacy", "scan", str(tmp_path)]) == 0 and not called
    assert top.main(["privacy", "scan", str(tmp_path), "--images"]) == 0 and called


@pytest.mark.skipif(not HAVE_MAGICK, reason="magick is not installed")
def test_real_squash_and_compare(tmp_path, capsys):
    from PIL import Image
    import random
    random.seed(1)
    src = tmp_path / "n.png"
    im = Image.new("RGB", (96, 96))
    im.putdata([(x * 2 % 256, y * 2 % 256, (x + y) % 256) for y in range(96) for x in range(96)])
    im.save(src)
    out = tmp_path / "o"
    assert cli.main(["squash", str(src), "--to", "webp", "--target-ssim", "0.9", "--out", str(out), "--json"]) in (0, 3)
    capsys.readouterr()
    if (out / "n.webp").exists():
        assert cli.main(["compare", str(src), str(out / "n.webp"), "--out", str(tmp_path / "c.png"), "--json"]) == 0
        assert '"ssim"' in capsys.readouterr().out and (tmp_path / "c.png").exists()
    assert src.stat().st_size > 0
