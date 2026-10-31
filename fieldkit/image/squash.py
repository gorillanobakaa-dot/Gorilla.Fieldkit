"""Compress and convert pictures with the real codecs, and refuse what falls below the measured quality."""
import os
import shutil
import subprocess
from pathlib import Path

from .. import scratch
from . import measure, tools

FORMATS = {"webp": ".webp", "avif": ".avif", "jpeg": ".jpg", "png": ".png"}
DEFAULT_QUALITY = 80
DEFAULT_FLOOR = 0.98            # the lowest SSIM a kept file may have when the caller gave no --target-ssim
READABLE = {".png", ".jpg", ".jpeg"}   # what cwebp, avifenc and oxipng read directly; anything else is converted first


def _run(cmd, run, timeout=600):
    r = run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise measure.ImageError(f"{Path(cmd[0]).stem} failed: {(r.stderr or r.stdout or '').strip()[:200]}")


def encode(fmt, src, dst, q, tmp, run=subprocess.run):
    """-> (codec, settings). The real codec when it is installed, else ImageMagick's own encoder."""
    mg = tools.find("magick")
    if fmt == "webp" and (t := tools.find("cwebp")):
        _run([t, "-q", str(q), "-m", "6", "-mt", "-quiet", src, "-o", dst], run)
        return "cwebp", f"-q {q} -m 6"
    if fmt == "avif" and (t := tools.find("avifenc")):
        _run([t, "-q", str(q), "-s", "6", "-j", "all", src, dst], run)
        return "avifenc", f"-q {q} -s 6"
    if fmt == "jpeg" and (t := tools.find("cjpeg")):
        bmp = os.path.join(tmp, "in.bmp")
        _run([mg, src, "-background", "white", "-alpha", "remove", "-alpha", "off", bmp], run)
        _run([t, "-quality", str(q), "-outfile", dst, bmp], run)
        return "cjpeg(mozjpeg)", f"-quality {q}"
    if fmt == "png" and (t := tools.find("oxipng")):
        _run([t, "-o", "4", "--strip", "safe", "--out", dst, src], run)
        return "oxipng", "-o 4 --strip safe"
    if not mg:
        raise measure.ImageError("magick (ImageMagick) is not installed: run `fieldkit image doctor`")
    if fmt == "png":
        _run([mg, src, "-strip", "-define", "png:compression-level=9", dst], run)
        return "magick", "png:compression-level=9 -strip"
    flat = ["-background", "white", "-alpha", "remove", "-alpha", "off"] if fmt == "jpeg" else []
    _run([mg, src, *flat, "-strip", "-quality", str(q), dst], run)
    return "magick", f"-quality {q}"


def _search(fmt, src, ref, target, tmp, run):
    """Smallest quality whose SSIM reaches TARGET. -> (q, dst, codec, settings, scores) or None."""
    lo, hi, best = 30, 95, None
    while lo <= hi:
        mid = (lo + hi) // 2
        dst = os.path.join(tmp, f"try-{mid}{FORMATS[fmt]}")
        codec, st = encode(fmt, src, dst, mid, tmp, run)
        sc = measure.scores(ref, dst, run)
        if sc["ssim"] >= target:
            best, hi = (mid, dst, codec, st, sc), mid - 1
        else:
            lo = mid + 1
    return best


def squash_one(path, fmt, out_dir, quality=None, target=None, max_px=None, run=subprocess.run):
    path = Path(path)
    row = {"file": str(path), "out": "", "to": fmt, "kept": False, "why": ""}
    if not path.is_file():
        row["why"] = "does not exist"
        return row
    dst_final = Path(out_dir) / (path.stem + FORMATS[fmt])
    if dst_final.resolve() == path.resolve():
        row["why"] = "refused: the output would overwrite the original, choose another --out"
        return row
    before = path.stat().st_size
    d, m = scratch.new(purpose="fieldkit image squash", kind="image")
    try:
        src = ref = str(path)
        if max_px or path.suffix.lower() not in READABLE:
            ref = src = os.path.join(d, "ref.png")
            args = [str(path) + "[0]", "-auto-orient"] + (["-resize", f"{max_px}x{max_px}>"] if max_px else [])
            r = measure.magick([*args, src], run)
            if r.returncode != 0:
                row["why"] = f"could not read: {(r.stderr or '').strip()[:150]}"
                return row
        floor = target if target is not None else DEFAULT_FLOOR
        if target is not None and fmt != "png":
            hit = _search(fmt, src, ref, target, str(d), run)
            if not hit:
                row["why"] = f"refused: no quality up to 95 reaches SSIM {target}"
                return row
            q, dst, codec, st, sc = hit
        else:
            q = quality or DEFAULT_QUALITY
            dst = os.path.join(d, "out" + FORMATS[fmt])
            codec, st = encode(fmt, src, dst, q, str(d), run)
            sc = measure.scores(ref, dst, run)
        after = os.path.getsize(dst)
        row.update(codec=codec, settings=st, before=before, after=after, ssim=sc["ssim"], psnr=sc["psnr"],
                   ratio=round(after / before, 3) if before else None)
        if sc["ssim"] < floor:
            row["why"] = f"refused: SSIM {sc['ssim']:.4f} is below {floor}"
        elif after >= before and not max_px:
            row["why"] = f"refused: {after} bytes is not smaller than the original's {before}"
        else:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            shutil.move(dst, dst_final)
            row.update(out=str(dst_final), kept=True)
        return row
    finally:
        scratch.delete_job(d, m, "image-squash-own-job")
