# Version: 1.0.0 · updated 26-10-22-18-50
"""fieldkit shot grid|crop|zoom|stack|sheet|text|text-compare, and fieldkit mac shot. Born 2026-10-22 from the owner's
question about thin Linux text against thick Mac text (measured that day: Mac strokes 1.40x). The pictures are
drawn here, so the expected numbers are known exactly."""
import os
import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from fieldkit.mac import macssh as M  # noqa: E402
from fieldkit.shot import look as L  # noqa: E402

BG, FG = (20, 20, 22), (230, 230, 230)


def bars(width_px, colour=FG, n=6, w=200, h=40):
    """Vertical bars of a known stroke width on a dark background: stand-ins for letter stems."""
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    for i in range(n):
        x = 10 + i * 30
        d.rectangle([x, 8, x + width_px - 1, 31], fill=colour)
    return im


def test_stroke_width_is_measured_and_bolder_is_named(tmp_path):
    thin, thick = L.text_metrics(bars(1)), L.text_metrics(bars(3))
    assert thin["stroke_px"] < thick["stroke_px"]
    assert 2.5 <= thick["stroke_px"] <= 3.5
    lines = L.compare(thin, thick)
    assert lines[0].startswith("strokes: B is") and "B is clearly bolder" in lines[0]
    assert "clearly" not in L.compare(thin, L.text_metrics(bars(1)))[0]          # same text, same verdict


def test_coloured_lcd_smoothing_and_halftone_are_counted():
    im = bars(2)
    d = ImageDraw.Draw(im)
    for i in range(6):                                           # red and blue fringes either side, as LCD smoothing does
        x = 10 + i * 30
        d.line([(x - 1, 8), (x - 1, 31)], fill=(200, 60, 40))
        d.line([(x + 2, 8), (x + 2, 31)], fill=(40, 60, 200))
    m = L.text_metrics(im)
    assert m["colour_pct"] > 30 and m["halftone_pct"] > 30
    assert L.text_metrics(bars(2))["colour_pct"] == 0


def test_zoom_keeps_every_pixel_exact():
    im = Image.new("RGB", (2, 1))
    im.putpixel((0, 0), (255, 0, 0))
    im.putpixel((1, 0), (0, 0, 255))
    z = L.zoom(im, 3)
    assert z.size == (6, 3) and {z.getpixel((x, y)) for x in range(3) for y in range(3)} == {(255, 0, 0)}


def test_crop_past_the_edge_is_refused_and_box_parsing_says_why():
    with pytest.raises(SystemExit, match="goes past the picture"):
        L.crop(Image.new("RGB", (100, 50)), (90, 0, 20, 10))
    assert L.box("525,170,330,75") == (525, 170, 330, 75)
    with pytest.raises(Exception):
        L.box("525,170,0,75")


def test_stack_and_sheet_and_grid_shapes(tmp_path):
    a, b = Image.new("RGB", (40, 10)), Image.new("RGB", (30, 20))
    assert L.stack(a, b).size == (40, 36) and L.stack(a, b, side=True).size == (76, 20)
    st = L.stack(Image.new("RGB", (40, 10), (0, 255, 0)), Image.new("RGB", (40, 20), (0, 0, 255)))
    assert st.getpixel((5, 12)) == (255, 0, 255) and st.getpixel((5, 16)) == (0, 0, 255)   # the divider, then B
    p = []
    for i in range(5):
        f = tmp_path / f"s{i}.png"
        Image.new("RGB", (800, 450), (i * 40, 0, 0)).save(f)
        p.append(str(f))
    assert L.sheet(p, cols=4, width=200).width == 4 * 204
    assert L.grid(Image.new("RGB", (300, 200))).getpixel((100, 50)) == (255, 0, 255)


def test_cli_writes_next_to_the_input_and_never_changes_it(tmp_path, capsys):
    f = tmp_path / "shot.png"
    bars(2).save(f)
    before = f.read_bytes()
    assert L.main(["crop", str(f), "0,0,50,40", "--zoom", "2"]) == 0
    assert (tmp_path / "shot.crop.png").exists() and Image.open(tmp_path / "shot.crop.png").size == (100, 80)
    assert L.main(["text-compare", str(f), "0,0,200,40", str(f), "0,0,200,40", "--json"]) == 0
    assert '"stroke_px"' in capsys.readouterr().out
    assert f.read_bytes() == before


def test_mac_shot_retries_a_dropped_connection_then_cleans_up():
    calls = []
    fail_once = {"capture": 1}

    def prog(c):                       # Windows runs its own OpenSSH by full path (C:\Windows\System32\OpenSSH\ssh.exe)
        return os.path.basename(c[0]).lower().removesuffix(".exe")

    def runner(argv):
        calls.append(argv)
        if prog(argv) == "ssh" and "screencapture" in argv[-1] and fail_once["capture"]:
            fail_once["capture"] -= 1
            return 255, "ssh: connect to host port 22: Connection timed out"
        return 0, ""
    mac = {"user": "u", "key": "~/.ssh/k", "host_key_alias": "old-mac.local"}
    assert M.take_shot(mac, "192.0.2.5", "/tmp/x.png", "out.png", runner=runner) == 0
    kinds = [("capture" if "screencapture" in c[-1] else "rm" if c[-1].startswith("rm ") else "")
             if prog(c) == "ssh" else prog(c) for c in calls]
    assert kinds == ["capture", "capture", "scp", "rm"]
    assert M.take_shot(mac, "192.0.2.5", "/tmp/x.png", "out.png", runner=lambda a: (255, "down")) == 1
