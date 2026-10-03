"""Raster measurements: is a picture a real downsample of its master, does an .ico carry every frame, does a size
slot hold the size its name claims.

The measurement is the Crisp Icon Doctrine's (Gorilla.firefox/doctrine/ICON-DOCTRINE-WINDOWS.md, sections 2-4):

    edge energy = mean of FIND_EDGES over the luminance
    ICON-002    got >= 0.75 x energy(master Lanczos-downsampled to the raster's size)

and its second-order trap, the vacuous pass: a reference built from a soft master confirms a soft asset. So a
verdict is PASS only when the master (a) out-resolves the raster and (b) passes the self-control: degraded through
a quarter of the raster's size and back, the master must lose clearly more than 1.25x of its edge energy, which
proves it carries real detail AT the raster's size. Either failing gives UNVERIFIABLE, never PASS.

Artwork that is not the master's (installer art, a tinted variant, a placeholder) is told apart by colour (doctrine
6c: mean RGB on an 8x8 grid; grayscale signatures gave a false pass on firefox.ico). Tinted variants (distance
between TINTED and FOREIGN, like pbmode.ico) are compared on histogram-equalised luminance, which cancels the
tint's contrast change, and on the outline (alpha); artwork further away needs its own master (allowlist file,
`masters:`), else UNVERIFIABLE.

The .ico frame reader mirrors IconKit (Documents/Scripts/IconKit/iconkit.py, `_frames_from_ico`, `check_ico`):
mirrored, not imported, because IconKit is a loose script outside any package. ICO_RECOMMENDED is iconkit's
DEFAULT_SIZES; a test checks the two agree whenever iconkit.py is on this machine.
"""
import base64
import hashlib
import io
import re
from pathlib import Path

PROVENANCE_RATIO = 0.75       # ICON-002 (doctrine section 2)
SEPARATION = 1.25             # self-control (doctrine section 3)
OUTRESOLVE = 1.0              # the master's squared side must be strictly larger than the raster's
TINTED = 8.0                  # colour distance up to this: the same artwork (correct icons measure 0.1-3.1)
FOREIGN = 55.0                # beyond this: different artwork (doctrine 6c threshold for the tinted pbmode icon)
HEADROOM = 2.0                # ASSET-002
# doctrine 6d: the Windows ladder; Explorer draws 96 px in "Large icons", 256 is the format's ceiling
ICO_REQUIRED = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
# IconKit's DEFAULT_SIZES (iconkit.py, 2026-09-29): reported when missing, not failed (the dense desktop ladder)
ICO_RECOMMENDED = (16, 20, 24, 32, 40, 48, 56, 64, 72, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224, 240, 256)
RASTER_EXT = (".png", ".ico", ".bmp", ".jpg", ".jpeg", ".gif", ".webp")
EMBEDDED = re.compile(r"data:image/(png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/=\s]+)")


def _pil():
    from PIL import Image, ImageFilter, ImageStat
    return Image, ImageFilter, ImageStat


def energy(im):
    """The doctrine's sharpness proxy."""
    _, ImageFilter, ImageStat = _pil()
    return ImageStat.Stat(im.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]


def eq_energy(im):
    """Edge energy of the histogram-equalised luminance over mid-grey: insensitive to a tint's contrast change."""
    Image, _, _ = _pil()
    from PIL import ImageOps
    rgba = im.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (128, 128, 128, 255))
    bg.alpha_composite(rgba)
    return energy(ImageOps.equalize(bg.convert("L")))


def alpha_energy(im):
    _, ImageFilter, ImageStat = _pil()
    return ImageStat.Stat(im.convert("RGBA").split()[3].filter(ImageFilter.FIND_EDGES)).mean[0]


def squarify(im):
    """Crop to the alpha bounding box and centre on a square canvas (doctrine gate_icon_002)."""
    Image, _, _ = _pil()
    im = im.convert("RGBA")
    box = im.split()[3].point(lambda v: 255 if v > 8 else 0).getbbox()
    if box:
        im = im.crop(box)
    w, h = im.size
    side = max(w, h, 1)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(im, ((side - w) // 2, (side - h) // 2))
    return out


def _grid(im):
    Image, _, _ = _pil()
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im.convert("RGBA"))
    small = bg.convert("RGB").resize((8, 8), Image.LANCZOS)
    return [small.getpixel((x, y)) for y in range(8) for x in range(8)]


def colour_distance(a, b):
    """Mean per-channel distance of the 8x8 colour grids of two (squared) pictures (doctrine 6c)."""
    Image, _, _ = _pil()
    pa, pb = _grid(a), _grid(b.resize(a.size, Image.LANCZOS))
    return sum(sum(abs(x - y) for x, y in zip(p, q)) / 3 for p, q in zip(pa, pb)) / 64


def is_alpha_trivial(im):
    lo, hi = im.convert("RGBA").split()[3].getextrema()
    return lo == hi


def provenance(raster, master, ratio=PROVENANCE_RATIO, master_squared=False, cache=None):
    """ICON-002 with the vacuity guard. -> (verdict, evidence, numbers). Both arguments are PIL images; pass
    master_squared=True when `master` is already squarify()'d (saves re-cropping a 2600 px master per raster), and
    one `cache` dict per run (and per master object) to reuse the master's downsample per size."""
    Image, _, _ = _pil()
    got_sq, ref_sq = squarify(raster), (master if master_squared else squarify(master))
    n = got_sq.size[0]
    nums = {"raster": list(raster.size), "squared": n, "master_squared": ref_sq.size[0]}
    if n < 4:
        return "UNVERIFIABLE", "the picture is empty or under 4 px after cropping", nums
    if ref_sq.size[0] <= n * OUTRESOLVE:
        return ("UNVERIFIABLE", f"master {ref_sq.size[0]} px does not out-resolve the raster ({n} px): a reference "
                f"built from it proves nothing", nums)
    key = (id(ref_sq), n)
    if cache is not None and key in cache:
        ref, e_ref, e_deg = cache[key]
    else:
        ref = ref_sq.resize(got_sq.size, Image.LANCZOS)
        q = max(4, n // 4)
        degraded = ref_sq.resize((q, q), Image.LANCZOS).resize(got_sq.size, Image.LANCZOS)
        e_ref, e_deg = energy(ref), energy(degraded)
        if cache is not None:
            cache[key] = (ref, e_ref, e_deg)
    sep = e_ref / e_deg if e_deg else 0.0
    nums.update(reference=round(e_ref, 2), degraded=round(e_deg, 2), separation=round(sep, 2))
    if sep <= SEPARATION:
        return ("UNVERIFIABLE", f"self-control failed: the master at {n} px separates only {sep:.2f}x from its own "
                f"degraded copy (need >{SEPARATION}); it is soft too, a pass would be vacuous", nums)
    dist = colour_distance(got_sq, ref_sq)
    nums["colour_distance"] = round(dist, 1)
    if dist > FOREIGN:
        return ("UNVERIFIABLE", f"different artwork from the master (colour distance {dist:.1f} > {FOREIGN}): it "
                f"needs its own master (allowlist file, masters:)", nums)
    if dist > TINTED:
        # a tint changes luminance contrast, so the plain ratio is biased (pbmode measured 0.80 while crisp).
        # Equalised luminance removes the tint's contrast change (crisp tinted icons measure 0.97-1.04, the same
        # icons softened 0.42-0.69, 2026-10-02); the outline (alpha) must hold too where there is one.
        got, want = eq_energy(got_sq), eq_energy(ref)
        nums.update(channel="equalised", got=round(got, 2), want=round(want, 2))
        ok = want > 0 and got >= ratio * want
        what = f"recoloured variant (colour {dist:.1f}): equalised edge energy {got:.2f} vs {want:.2f}"
        if not is_alpha_trivial(raster):
            ga, wa = alpha_energy(got_sq), alpha_energy(ref)
            nums.update(outline_got=round(ga, 2), outline_want=round(wa, 2))
            ok = ok and wa > 0 and ga >= ratio * wa
            what += f", outline {ga:.2f} vs {wa:.2f}"
        return ("PASS" if ok else "FAIL"), what + f" from the master (need {ratio:.0%} of both)", nums
    got = energy(got_sq)
    nums.update(channel="luminance", got=round(got, 2), want=round(e_ref, 2))
    ok = e_ref > 0 and got >= ratio * e_ref
    return (("PASS" if ok else "FAIL"),
            f"edge energy {got:.2f} vs {e_ref:.2f} for a true downsample of the master ({got / e_ref if e_ref else 0:.2f}, "
            f"need {ratio:.2f}; self-control {sep:.2f}x)", nums)


def open_raster(path):
    """A file -> PIL image (largest frame of an .ico). Raises OSError/ValueError on unreadable files."""
    Image, _, _ = _pil()
    im = Image.open(path)
    im.load()
    return im


def ico_frames(path):
    """[(size, PIL image)] of every frame in an .ico, smallest first (IconKit's _frames_from_ico)."""
    from PIL import IcoImagePlugin
    with open(path, "rb") as f:
        ico = IcoImagePlugin.IcoFile(f)
        out = []
        for size in sorted(ico.sizes()):
            out.append((size[0], ico.getimage(size).convert("RGBA")))
    return out


def ico_ladder(sizes):
    """-> (verdict, evidence, missing_recommended)."""
    have = set(sizes)
    missing = [s for s in ICO_REQUIRED if s not in have]
    extra = [s for s in ICO_RECOMMENDED if s not in have]
    if missing:
        return "FAIL", f"frames {sorted(have)}; missing {missing} (doctrine 6d: Windows rescales a missing size)", extra
    return "PASS", f"frames {sorted(have)}: the full Windows ladder", extra


def svg_rasters(path):
    """[(index, PIL image)] of every raster embedded in an SVG as a data: URI."""
    Image, _, _ = _pil()
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    out = []
    for i, m in enumerate(EMBEDDED.finditer(text)):
        im = Image.open(io.BytesIO(base64.b64decode(re.sub(r"\s", "", m.group(2)))))
        im.load()
        out.append((i, im))
    return out


SLOT = re.compile(r"(?:^|[^0-9])(\d{2,4})(?:x\d{2,4})?(?:@(\d)x)?\.(?:png|bmp|jpg)$", re.I)


def nominal_size(name):
    """'icon48.png' -> 48, 'default256.png' -> 256, 'x@2x.png' -> None (a scale, not a size)."""  # privacy-scan: allow (a file name or a fake address, not an email)
    if re.search(r"(scale-|targetsize-|_\d+\.png$)", name, re.I):
        return None                 # MSIX qualifiers and tile sizes are not pixel sizes
    m = SLOT.search(name)
    if not m or m.group(2):
        return None
    return int(m.group(1))


def slot_rows(files):
    """ICON-001: a size slot's real pixels match its name, and no two different size slots are the same picture.
    `files` [(relative path, PIL image)]. -> [(item, verdict, evidence)]"""
    rows, by_hash = [], {}
    for rel, im in files:
        n = nominal_size(Path(rel).name)
        if n is None:
            continue
        ok = im.size == (n, n)
        rows.append((rel, "PASS" if ok else "FAIL",
                     f"named {n} px, is {im.size[0]}x{im.size[1]}" + ("" if ok else ": the slot lies about its size")))
        h = hashlib.sha256(im.convert("RGBA").tobytes()).hexdigest()
        by_hash.setdefault(h, set()).add((n, rel))
    for h, slots in by_hash.items():
        sizes = {s for s, _ in slots}
        if len(sizes) > 1:          # doctrine 6j: two NAMES for one size may be identical; two SIZES may not
            for _, rel in sorted(slots):
                rows.append((rel + "#distinct", "FAIL",
                             f"the same picture fills slots {sorted(sizes)}: copied into every slot"))
    return rows
