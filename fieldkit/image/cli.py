"""Pictures, offline: doctor, squash, compare, ocr, privacy (fieldkit/image/__init__.py)."""
import argparse
import os
from pathlib import Path

from ..method import emit
from . import acceptance, measure, ocr, squash, tools


def _doctor(a):
    rows = tools.report()
    missing = [r for r in rows if not r["present"]]

    def lines(d):
        for r in d["tools"]:
            if r["present"]:
                yield f"[ok]      {r['tool']:<10} {r['version']}  ({r['path']})"
            else:
                yield f"[missing] {r['tool']:<10} {r['for']}: {r['install']}   source {r['source']}"
        yield "magick and tesseract are required; the other codecs are used when present, else magick encodes."
    emit({"tools": rows, "missing": [r["tool"] for r in missing],
          "next": "a person installs the missing ones (nothing is installed by this command)" if missing else ""},
         a.json, lines)
    return 0 if not {"magick", "tesseract"} & {r["tool"] for r in missing} else 3


def _squash(a):
    fmt = "jpeg" if a.to == "jpg" else a.to
    rows = []
    for p in a.paths:
        out = a.out or str(Path(p).resolve().parent / "squashed")
        try:
            rows.append(squash.squash_one(p, fmt, out, a.quality, a.target_ssim, a.max_px))
        except measure.ImageError as e:
            rows.append({"file": p, "kept": False, "why": f"refused: {e}", "to": fmt})
    refused = [r for r in rows if not r["kept"]]

    def lines(d):
        yield f"{'file':<28} {'codec':<15} {'settings':<22} {'before':>9} {'after':>9} {'ratio':>6} {'SSIM':>7}  result"
        for r in d["rows"]:
            if "after" in r:
                yield (f"{Path(r['file']).name[:28]:<28} {r['codec']:<15} {r['settings'][:22]:<22} {r['before']:>9} "
                       f"{r['after']:>9} {r['ratio']:>6} {r['ssim']:>7.4f}  " + ("kept" if r["kept"] else r["why"]))
            else:
                yield f"{Path(r['file']).name[:28]:<28} {r['why']}"
        kept = [r for r in d["rows"] if r["kept"]]
        yield f"{len(kept)} kept, {len(d['rows']) - len(kept)} refused; {sum(r['before'] for r in kept)} -> " \
              f"{sum(r['after'] for r in kept)} bytes. Originals are never touched."
    emit({"rows": rows, "next": "a refused file keeps its original; raise --quality or lower --target-ssim"
          if refused else ""}, a.json, lines)
    return 3 if refused else 0


def _compare(a):
    from .. import scratch
    try:
        d, m = scratch.new(purpose="fieldkit image compare", kind="image")
    except scratch.Refused as e:
        print(f"REFUSED: {e}")
        return 2
    try:
        sc = measure.scores(a.a, a.b)
        out = a.out or str(Path(a.b).with_suffix("")) + ".compare.png"
        pa, pb = (measure.to_png(x, os.path.join(d, n)) for x, n in ((a.a, "a.png"), (a.b, "b.png")))
        measure.side_by_side(pa, pb, out)
    except measure.ImageError as e:
        print(f"REFUSED: {e}")
        return 2
    finally:
        scratch.delete_job(d, m, "image-compare-own-job")
    emit({**sc, "a": a.a, "b": a.b, "picture": out}, a.json,
         lambda r: [f"SSIM {r['ssim']:.6f}  PSNR {r['psnr']} dB", f"picture (A | B | difference x4): {r['picture']}"])
    return 0


def _ocr(a):
    bad = 0
    for p in a.paths:
        try:
            res = ocr.read(p, a.lang)
        except measure.ImageError as e:
            res = {"file": p, "text": "", "words": [], "error": str(e)}
            bad = 1
        emit(res, a.json, lambda r: [r.get("text") or f"  {r.get('error', '(no text found)')}"])
    return 3 if bad else 0


def _privacy(a):
    report, redacted = {}, {}
    for p in a.paths:
        for f in ocr.image_files(p, a.git):
            hits, _ = ocr.check(f, a.lang)
            if not hits:
                continue
            report[str(f)] = hits
            if a.redact and not any(h["kind"] == "not-scanned" for h in hits):
                redacted[str(f)] = ocr.redact(f, hits, a.out)
    examined = sum(len(ocr.image_files(p, a.git)) for p in a.paths)

    def lines(d):
        for f, hits in d["findings"].items():
            yield f
            for h in hits:
                yield f"   {h['kind']:<26} {h['excerpt']}  {len(h['boxes'])} box(es)"
            if f in d["redacted"]:
                yield f"   redacted copy: {d['redacted'][f]}  (the original is untouched)"
        yield f"{sum(len(h) for h in d['findings'].values())} finding(s) in {len(d['findings'])} picture(s), " \
              f"out of {d['examined']} picture(s) read"
        if not d["examined"]:
            yield "NOTHING WAS EXAMINED. That is not a pass: check the path."
    emit({"findings": report, "redacted": redacted, "examined": examined,
          "next": "publish the redacted copy, not the original" if report else ""}, a.json, lines)
    return 3 if report or not examined else 0


def _accept(a):
    r = acceptance.check()

    def lines(d):
        for s in d["steps"]:
            yield f"[{'ok' if s['ok'] else 'FAIL'}] {s['step']}: {s['detail']}"
    emit({**r, "next": "" if r["ok"] else "install the missing program (`fieldkit image doctor`); a model's vision does not count"},
         a.json, lines)
    return 0 if r["ok"] else 3


def main(argv):
    p = argparse.ArgumentParser(prog="fieldkit image", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="which picture tools are present, and how to install the missing ones")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=_doctor)
    x = sub.add_parser("accept", help="acceptance: tesseract reads a generated picture, magick identify/convert; fails when absent")
    x.add_argument("--json", action="store_true")
    x.set_defaults(fn=_accept)
    s = sub.add_parser("squash", help="compress or convert; the original is never touched; below target is refused")
    s.add_argument("paths", nargs="+")
    s.add_argument("--to", choices=["webp", "avif", "jpeg", "jpg", "png"], default="webp")
    s.add_argument("--quality", type=int, help=f"codec quality 1-100 (default {squash.DEFAULT_QUALITY}); png is lossless")
    s.add_argument("--target-ssim", type=float, help="search the smallest quality that reaches this SSIM; also the floor "
                   f"(default floor {squash.DEFAULT_FLOOR})")
    s.add_argument("--max-px", type=int, help="shrink so neither side exceeds N pixels (never enlarges)")
    s.add_argument("--out", help="output folder (default: a 'squashed' folder next to each file)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=_squash)
    c = sub.add_parser("compare", help="SSIM, PSNR and a side-by-side picture with the difference")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--out")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=_compare)
    o = sub.add_parser("ocr", help="the text in each picture, with word boxes in --json")
    o.add_argument("paths", nargs="+")
    o.add_argument("--lang", default="eng")
    o.add_argument("--json", action="store_true")
    o.set_defaults(fn=_ocr)
    v = sub.add_parser("privacy", help="OCR, then the privacy-scan detectors; exit 3 on a finding")
    v.add_argument("paths", nargs="+")
    v.add_argument("--lang", default="eng")
    v.add_argument("--redact", action="store_true", help="write NAME.redacted.png with those boxes blacked out")
    v.add_argument("--out", help="redacted copy path (single picture)")
    v.add_argument("--git", action="store_true", help="in a git folder, only the pictures git would publish")
    v.add_argument("--json", action="store_true")
    v.set_defaults(fn=_privacy)
    a = p.parse_args(argv)
    return a.fn(a)
