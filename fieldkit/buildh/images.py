"""What pictures an installed build carries, and which of them are the same picture more than once.

    fieldkit build-harness images <task> [--install-dir DIR] [--json]

Born 2026-10-07 (owner: "our gorilla firefox icon has been duplicated countless of times ... we are carrying a lot of
extra weight"; the inventory "becomes part of the harness"). The Gorilla theme was designed around ONE logo
(--gorilla-master-icon, chrome://branding/content/about-logo.svg); byte-identical copies are rare, the weight is the
same artwork re-encoded: SVGs that only wrap a photo (Firefox must decode the whole photo, whatever size it is drawn
at), PNGs at 1x and 2x, a 768px about.svg, an .ico. Measured that day: 9.8 MB of the build's 16.7 MB of images sat in
branding/, and the new tab held the 1400x1400 logo as 12-21 MB of decoded memory per process (probe image-memory).

What it reports, from omni.ja, browser/omni.ja and loose files:
  exact     byte-identical images (sha256)
  same      the same picture in different encodings or sizes: a 16x16 difference hash of every raster of 64 px or more
            (embedded rasters inside SVGs included), grouped when at most MAX_BITS of 256 bits differ
            and the shape (width/height) is the same within 5 %
  wrappers  SVGs whose content is a raster in a data: URI, with the raster's real size
  biggest   the largest image files
Read-only: nothing in the install is changed.
"""
import base64
import collections
import hashlib
import io
import re
import zipfile
from pathlib import Path

IMG = re.compile(r"\.(png|jpe?g|webp|gif|avif|ico|bmp|svg)$", re.I)
DATA = re.compile(rb"data:image/(png|jpe?g|webp|gif)(;base64)?,([A-Za-z0-9+/=]{200,})")
MAX_BITS = 16
MIN_PX = 64


def files(install_dir):
    """[(where, path, bytes)] of every image in the install: the two omni archives and loose files."""
    app = Path(install_dir)
    out = []
    for jar in ("omni.ja", "browser/omni.ja"):
        if (app / jar).is_file():
            with zipfile.ZipFile(app / jar) as z:
                out += [(jar, n, z.read(n)) for n in z.namelist() if IMG.search(n)]
    for p in sorted(app.rglob("*")):
        if p.is_file() and IMG.search(p.name):
            out.append(("loose", p.relative_to(app).as_posix(), p.read_bytes()))
    return out


def embedded(data):
    """The rasters an SVG carries as data: URIs -> [bytes]."""
    out = []
    for m in DATA.finditer(data):
        try:
            out.append(base64.b64decode(m.group(3)) if m.group(2) else m.group(3))
        except ValueError:
            continue
    return out


def dhash(raw):
    """16x16 difference hash of a raster (alpha composited on black, as the dark theme shows it) -> (int, (w, h)),
    or None when the image cannot be read or is smaller than MIN_PX."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(raw))
        im.seek(0)
        size = im.size
        if max(size) < MIN_PX:
            return None
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
        g = Image.alpha_composite(bg, im).convert("L").resize((17, 16), Image.LANCZOS)
    except Exception:            # a picture PIL cannot read is reported by size only, never fatal
        return None
    px = list(g.getdata())
    bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (px[y * 17 + x] > px[y * 17 + x + 1])
    return bits, size


def scan(install_dir):
    imgs = files(install_dir)
    total = sum(len(b) for _, _, b in imgs)
    by_sha = collections.defaultdict(list)
    for w, n, b in imgs:
        by_sha[hashlib.sha256(b).hexdigest()].append(f"{w}:{n}")
    exact = [{"bytes": len(next(b for w, n, b in imgs if f"{w}:{n}" == v[0])), "copies": v}
             for v in by_sha.values() if len(v) > 1]
    wrappers, hashed = [], []
    for w, n, b in imgs:
        name = f"{w}:{n}"
        rasters = embedded(b) if n.lower().endswith(".svg") else [b]
        for raw in rasters:
            h = dhash(raw)
            if h:
                hashed.append((name, len(b), h[0], h[1]))
            if n.lower().endswith(".svg") and h:
                wrappers.append({"file": name, "bytes": len(b), "raster": f"{h[1][0]}x{h[1][1]}",
                                 "decoded_bytes": h[1][0] * h[1][1] * 4})
    # group the same picture: union of every pair within MAX_BITS (n is about 1600 rasters of 64 px or more)
    parent = list(range(len(hashed)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(hashed)):
        for j in range(i + 1, len(hashed)):
            (wi, hi), (wj, hj) = hashed[i][3], hashed[j][3]
            if abs(wi / hi - wj / hj) > 0.05 * (wi / hi):
                continue                          # a different shape is a different picture (flat images chained)
            if bin(hashed[i][2] ^ hashed[j][2]).count("1") <= MAX_BITS:
                parent[root(i)] = root(j)
    groups = collections.defaultdict(list)
    for i, h in enumerate(hashed):
        groups[root(i)].append(h)
    same = []
    for g in groups.values():
        names = {x[0] for x in g}
        if len(names) > 1:
            same.append({"files": sorted({(x[0], x[1], f"{x[3][0]}x{x[3][1]}") for x in g}, key=lambda t: -t[1]),
                         "bytes": sum({x[0]: x[1] for x in g}.values())})
    same.sort(key=lambda s: -s["bytes"])
    biggest = sorted(((f"{w}:{n}", len(b)) for w, n, b in imgs), key=lambda t: -t[1])[:20]
    return {"install": str(install_dir), "count": len(imgs), "bytes": total, "exact": exact,
            "same": same, "wrappers": sorted(wrappers, key=lambda x: -x["bytes"]), "biggest": biggest}


def lines(r):
    mb = lambda n: f"{n / 1048576:.2f} MB"
    out = [f"images in {r['install']}: {r['count']} files, {mb(r['bytes'])}"]
    waste = sum(e["bytes"] * (len(e["copies"]) - 1) for e in r["exact"])
    out.append(f"exact copies: {len(r['exact'])} group(s), {mb(waste)} duplicated")
    for e in sorted(r["exact"], key=lambda e: -e["bytes"])[:8]:
        out.append(f"  {e['bytes'] // 1024:6d} KB x{len(e['copies'])}: {', '.join(e['copies'])[:200]}")
    out.append(f"the same picture in several files: {len(r['same'])} group(s)")
    for s in r["same"][:10]:
        out.append(f"  {mb(s['bytes'])} in {len(s['files'])} file(s):")
        for name, size, px in s["files"][:12]:
            out.append(f"     {size // 1024:6d} KB  {px:>9}  {name}")
    out.append(f"SVGs that only wrap a raster: {len(r['wrappers'])}")
    for w in r["wrappers"][:10]:
        out.append(f"  {w['bytes'] // 1024:6d} KB  wraps {w['raster']:>9} ({mb(w['decoded_bytes'])} decoded)  {w['file']}")
    out.append("biggest image files:")
    out += [f"  {size // 1024:6d} KB  {name}" for name, size in r["biggest"][:12]]
    return out
