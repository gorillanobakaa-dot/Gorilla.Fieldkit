"""Measured picture quality: SSIM and PSNR from ImageMagick's `magick compare`, and the side-by-side picture."""
import os
import re
import subprocess

from . import tools


class ImageError(RuntimeError):
    pass


def magick(args, run=subprocess.run, timeout=300):
    exe = tools.find("magick")
    if not exe:
        raise ImageError("magick (ImageMagick) is not installed: run `fieldkit image doctor`")
    return run([exe, *args], capture_output=True, text=True, timeout=timeout)


def metric(name, a, b, run=subprocess.run):
    """-> (value, normalised) from `magick compare -metric NAME`, which prints 'VALUE (NORMALISED)' on stderr
    (exit 1 just means 'not identical'). NORMALISED is None when the build prints no bracket."""
    r = magick(["compare", "-metric", name, a, b, "null:"], run)
    if r.returncode not in (0, 1):
        raise ImageError(f"magick compare {name} failed: {(r.stderr or '').strip()[:200]}")
    text = (r.stderr or r.stdout or "").strip()
    m = re.match(r"^(\S+)(?:\s+\(([^)]*)\))?", text)
    try:
        return float(m.group(1)), (float(m.group(2)) if m.group(2) else None)
    except (AttributeError, ValueError):
        raise ImageError(f"magick compare {name} printed {text[:60]!r}, not a number") from None


_CONVENTION = {}


def ssim(a, b, run=subprocess.run):
    """Structural similarity, 1.0 = identical. ImageMagick 7.1.2 prints the metric as a DISSIMILARITY (0 for identical,
    the bracket is (1 - SSIM) / 2); older builds print the similarity itself. Decided per build by comparing a picture
    with itself, never assumed. The number is ImageMagick's, not scikit-image's: they differ by a few hundredths."""
    value, norm = metric("SSIM", a, b, run)
    if "dissimilarity" not in _CONVENTION:
        same, _ = metric("SSIM", a, a, run)
        _CONVENTION["dissimilarity"] = same < 0.5
    if _CONVENTION["dissimilarity"]:
        return 1.0 - 2.0 * (norm if norm is not None else value)
    return value


def to_png(src, dst, run=subprocess.run):
    r = magick([src + "[0]", "-auto-orient", dst], run)
    if r.returncode != 0 or not os.path.isfile(dst):
        raise ImageError(f"could not read {src}: {(r.stderr or '').strip()[:200]}")
    return dst


def size_of(p, run=subprocess.run):
    r = magick(["identify", "-format", "%w %h", p + "[0]"], run)
    if r.returncode != 0:
        raise ImageError(f"could not read {p}: {(r.stderr or '').strip()[:200]}")
    return tuple(int(v) for v in r.stdout.split())


def scores(a, b, run=subprocess.run):
    """{'ssim','psnr'}; pictures of different size are an error, never a guess."""
    sa, sb = size_of(a, run), size_of(b, run)
    if sa != sb:
        raise ImageError(f"different sizes {sa[0]}x{sa[1]} and {sb[0]}x{sb[1]}: compare needs the same size "
                         "(squash --max-px compares against the resized original)")
    return {"ssim": ssim(a, b, run), "psnr": metric("PSNR", a, b, run)[0]}


def side_by_side(a_png, b_png, out):
    """A | B | difference (amplified x4), with the shot-look stacking."""
    from ..shot import look
    look.need_pil()
    from PIL import Image, ImageChops
    ia, ib = Image.open(a_png).convert("RGB"), Image.open(b_png).convert("RGB")
    diff = ImageChops.difference(ia, ib).point(lambda v: min(255, v * 4))
    look.stack(look.stack(ia, ib, side=True), diff, side=True).save(out)
    return out
