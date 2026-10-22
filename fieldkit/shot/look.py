#!/usr/bin/env python3
# Version: 1.0.0 · updated 26-10-22-18-30
"""look.py - look at screenshots the way a person compares two screens: find the place, cut it out, enlarge it
without smoothing, put two side by side, and MEASURE how thick and how smoothed the text is.

    fieldkit shot grid FILE [--step 100]                       the picture with a labelled pixel grid, to find a box
    fieldkit shot crop FILE X,Y,W,H [--zoom 3]                 cut a box out; --zoom enlarges with no smoothing
    fieldkit shot zoom FILE [--zoom 3]                         enlarge a whole picture with no smoothing
    fieldkit shot stack A B [--zoom 3] [--side]                two pictures one above the other (or side by side)
    fieldkit shot sheet FILE... [--cols 4] [--width 400]       a contact sheet, each picture labelled with its name
    fieldkit shot text FILE X,Y,W,H [--json]                   text measurements inside a box (see below)
    fieldkit shot text-compare A X,Y,W,H B X,Y,W,H [--json]    the same for two boxes, and how they differ

Every command that makes a picture writes it next to the first input (or --out) and prints the path. Nothing
changes the inputs.

Born 2026-10-22. The owner compared about:about on the VAIO (Linux, thin and crisp) with the MacBook Air (macOS 10.13,
"oversmoothed thick blurry almost") and asked: anti-aliasing or serif? The answer came from cropping the same
sentence from both screenshots, enlarging 3x with no smoothing, and stacking them: the Mac drew macOS's own
interface font (not Segoe UI) with its stroke-widening font smoothing. The owner: "teach the harness what you are
doing now, all the commands that allow you to compare the screenshots and the fonts and to crop and zoom in".

WHAT `text` MEASURES (light text on dark, or dark on light: the background is the box's most common colour)
  ink %          share of pixels that differ clearly from the background: more ink at the same text = bolder strokes
  stroke px      2 x ink pixels / ink edge pixels: the average stroke width in pixels (a thin 1 px stem reads ~1-1.5)
  half-tone %    ink pixels that are neither background nor full text colour: the blur of the anti-aliasing
  colour %       ink pixels whose red, green and blue differ by more than 40: coloured (LCD subpixel) smoothing
  extent         width and height of the inked area: the same sentence in two fonts has two extents
Numbers are relative: compare two boxes holding the SAME text at the SAME size. Model inference, not a standard.
"""
import argparse
import json
import os
import sys
from collections import Counter

try:
    from PIL import Image, ImageDraw
except ImportError:                          # doctor says what to install; nothing works without it
    Image = ImageDraw = None


def need_pil():
    if Image is None:
        raise SystemExit("REFUSED: Pillow is not installed (python3 -m pip install Pillow)")


def box(s):
    try:
        x, y, w, h = (int(v) for v in s.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{s!r} is not X,Y,W,H in pixels (for example 525,170,330,75)")
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        raise argparse.ArgumentTypeError(f"{s!r}: width and height must be above 0, x and y not below 0")
    return x, y, w, h


def out_path(src, tag, out=None):
    if out:
        return out
    root, _ = os.path.splitext(src)
    return f"{root}.{tag}.png"


def crop(img, b):
    x, y, w, h = b
    if x + w > img.width or y + h > img.height:
        raise SystemExit(f"REFUSED: box {x},{y},{w},{h} goes past the picture ({img.width}x{img.height}); "
                         f"fieldkit shot grid shows where things are")
    return img.crop((x, y, x + w, y + h))


def zoom(img, n):
    """Enlarge with NEAREST: every pixel becomes an n x n block, so what is seen is what was on the screen."""
    return img.resize((img.width * n, img.height * n), Image.NEAREST) if n > 1 else img


def stack(a, b, side=False, gap=6, colour=(255, 0, 255)):
    if side:
        out = Image.new("RGB", (a.width + gap + b.width, max(a.height, b.height)), colour)
        out.paste(a.convert("RGB"), (0, 0))
        out.paste(b.convert("RGB"), (a.width + gap, 0))
    else:
        out = Image.new("RGB", (max(a.width, b.width), a.height + gap + b.height), colour)
        out.paste(a.convert("RGB"), (0, 0))
        out.paste(b.convert("RGB"), (0, a.height + gap))
    return out


def grid(img, step=100):
    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    for x in range(0, out.width, step):
        d.line([(x, 0), (x, out.height)], fill=(255, 0, 255), width=1)
        d.text((x + 2, 2), str(x), fill=(255, 0, 255))
    for y in range(0, out.height, step):
        d.line([(0, y), (out.width, y)], fill=(255, 0, 255), width=1)
        d.text((2, y + 2), str(y), fill=(255, 0, 255))
    return out


def sheet(paths, cols=4, width=400):
    tiles = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        h = max(1, round(im.height * width / im.width))
        t = Image.new("RGB", (width, h + 16), (255, 255, 255))
        t.paste(im.resize((width, h), Image.LANCZOS), (0, 16))
        ImageDraw.Draw(t).text((2, 2), os.path.basename(p)[:60], fill=(0, 0, 0))
        tiles.append(t)
    rows = [tiles[i:i + cols] for i in range(0, len(tiles), cols)]
    H = sum(max(t.height for t in r) + 4 for r in rows)
    out = Image.new("RGB", (cols * (width + 4), H), (200, 200, 200))
    y = 0
    for r in rows:
        for i, t in enumerate(r):
            out.paste(t, (i * (width + 4), y))
        y += max(t.height for t in r) + 4
    return out


def text_metrics(img, b=None):
    """Measurements of the text inside a box. -> dict (see the module text)."""
    im = (crop(img, b) if b else img).convert("RGB")
    px = list(im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata())
    W, H = im.size
    bg = Counter(px).most_common(1)[0][0]
    lum = lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    bl = lum(bg)
    diffs = [abs(lum(c) - bl) for c in px]
    full = max(diffs) or 1
    ink = [d > 0.25 * full for d in diffs]
    n_ink = sum(ink)
    if not n_ink:
        return {"ink_pct": 0.0, "stroke_px": 0.0, "halftone_pct": 0.0, "colour_pct": 0.0, "extent": [0, 0],
                "background": list(bg), "note": "no text found in the box"}
    edges = 0
    for i, on in enumerate(ink):
        if not on:
            continue
        x, y = i % W, i // W
        if (x == 0 or not ink[i - 1] or x == W - 1 or not ink[i + 1] or
                y == 0 or not ink[i - W] or y == H - 1 or not ink[i + W]):
            edges += 1
    half = sum(1 for d, on in zip(diffs, ink) if on and d < 0.85 * full)
    colour = sum(1 for c, on in zip(px, ink) if on and max(c) - min(c) > 40)
    xs = [i % W for i, on in enumerate(ink) if on]
    ys = [i // W for i, on in enumerate(ink) if on]
    return {"ink_pct": round(100 * n_ink / len(px), 2), "stroke_px": round(2 * n_ink / max(edges, 1), 2),
            "halftone_pct": round(100 * half / n_ink, 1), "colour_pct": round(100 * colour / n_ink, 1),
            "extent": [max(xs) - min(xs) + 1, max(ys) - min(ys) + 1], "background": list(bg)}


def compare(ma, mb):
    """Plain-words differences, B against A."""
    lines = []
    if ma["stroke_px"] and mb["stroke_px"]:
        r = mb["stroke_px"] / ma["stroke_px"]
        lines.append(f"strokes: B is {r:.2f}x A ({mb['stroke_px']} vs {ma['stroke_px']} px)" +
                     (" - B is clearly bolder" if r >= 1.25 else " - B is clearly thinner" if r <= 0.8 else ""))
    if ma["ink_pct"]:
        lines.append(f"ink: B {mb['ink_pct']}% vs A {ma['ink_pct']}% of the box")
    lines.append(f"blur (half-tone pixels): B {mb['halftone_pct']}% vs A {ma['halftone_pct']}% of the ink")
    lines.append(f"coloured (LCD) smoothing: B {mb['colour_pct']}% vs A {ma['colour_pct']}% of the ink")
    if ma["extent"] != mb["extent"]:
        lines.append(f"extent: B {mb['extent'][0]}x{mb['extent'][1]} vs A {ma['extent'][0]}x{ma['extent'][1]} px "
                     f"(different widths for the same text = different fonts or sizes)")
    return lines


def main(argv=None, prog="fieldkit shot"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0],
                                 epilog="\n\n".join(__doc__.split("\n\n")[1:2]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("grid"); g.add_argument("file"); g.add_argument("--step", type=int, default=100); g.add_argument("--out")
    c = sp.add_parser("crop"); c.add_argument("file"); c.add_argument("box", type=box)
    c.add_argument("--zoom", type=int, default=1); c.add_argument("--out")
    z = sp.add_parser("zoom"); z.add_argument("file"); z.add_argument("--zoom", type=int, default=3); z.add_argument("--out")
    s = sp.add_parser("stack"); s.add_argument("a"); s.add_argument("b"); s.add_argument("--zoom", type=int, default=1)
    s.add_argument("--side", action="store_true"); s.add_argument("--out")
    h = sp.add_parser("sheet"); h.add_argument("files", nargs="+"); h.add_argument("--cols", type=int, default=4)
    h.add_argument("--width", type=int, default=400); h.add_argument("--out")
    t = sp.add_parser("text"); t.add_argument("file"); t.add_argument("box", type=box); t.add_argument("--json", action="store_true")
    tc = sp.add_parser("text-compare"); tc.add_argument("a"); tc.add_argument("box_a", type=box)
    tc.add_argument("b"); tc.add_argument("box_b", type=box); tc.add_argument("--json", action="store_true")
    tc.add_argument("--out", help="also write the two boxes stacked at 3x")
    o = ap.parse_args(argv)
    need_pil()
    if o.cmd in ("grid", "crop", "zoom"):
        im = Image.open(o.file)
        res = grid(im, o.step) if o.cmd == "grid" else zoom(crop(im, o.box), o.zoom) if o.cmd == "crop" else zoom(im, o.zoom)
        p = out_path(o.file, o.cmd, o.out)
        res.save(p)
        print(f"{p}  ({res.width}x{res.height})")
        return 0
    if o.cmd == "stack":
        res = stack(zoom(Image.open(o.a), o.zoom), zoom(Image.open(o.b), o.zoom), o.side)
        p = out_path(o.a, "stack", o.out)
        res.save(p)
        print(f"{p}  ({res.width}x{res.height}; A {'left' if o.side else 'top'}, B {'right' if o.side else 'bottom'})")
        return 0
    if o.cmd == "sheet":
        res = sheet(o.files, o.cols, o.width)
        p = out_path(o.files[0], "sheet", o.out)
        res.save(p)
        print(f"{p}  ({len(o.files)} pictures)")
        return 0
    if o.cmd == "text":
        m = text_metrics(Image.open(o.file), o.box)
        print(json.dumps(m) if o.json else "\n".join(f"  {k}: {v}" for k, v in m.items()))
        return 0
    ia, ib = Image.open(o.a), Image.open(o.b)
    ma, mb = text_metrics(ia, o.box_a), text_metrics(ib, o.box_b)
    if o.out:
        stack(zoom(crop(ia, o.box_a), 3), zoom(crop(ib, o.box_b), 3)).save(o.out)
    if o.json:
        print(json.dumps({"a": ma, "b": mb, "differences": compare(ma, mb)}))
    else:
        print(f"A {os.path.basename(o.a)} {o.box_a}\n  " + "  ".join(f"{k}={v}" for k, v in ma.items() if k != "background"))
        print(f"B {os.path.basename(o.b)} {o.box_b}\n  " + "  ".join(f"{k}={v}" for k, v in mb.items() if k != "background"))
        print("\n".join(compare(ma, mb)))
        if o.out:
            print(f"stacked at 3x: {o.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
