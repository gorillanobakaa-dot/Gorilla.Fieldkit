"""build-harness images (2026-10-07): finds byte copies, the same picture re-encoded at other sizes, and SVGs that
only wrap a raster, in a fake install built here (two omni archives and a loose file)."""
import base64
import io
import zipfile

from PIL import Image, ImageDraw

from fieldkit.buildh import images


def _png(size, seed=0):
    im = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((40, 40, 360, 360), fill=(200, 120, 40, 255))
    d.rectangle((150 + seed, 100, 250, 300), fill=(20, 20, 200, 255))
    d.polygon([(60, 380), (200, 220), (340, 380)], fill=(240, 240, 240, 255))
    b = io.BytesIO()
    im.resize((size, size), Image.LANCZOS).save(b, "PNG")
    return b.getvalue()


def _other(size):
    im = Image.new("RGB", (size, size), (0, 0, 0))
    d = ImageDraw.Draw(im)
    for i in range(0, size, 8):
        d.line((i, 0, size - i, size), fill=(255, 255, 255), width=3)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _install(tmp_path):
    big = _png(1400)
    wrapper = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1400 1400"><image width="1400" height="1400" '
               b'href="data:image/png;base64,' + base64.b64encode(big) + b'"/></svg>')
    with zipfile.ZipFile(tmp_path / "omni.ja", "w") as z:
        z.writestr("chrome/toolkit/a.png", _png(500))
        z.writestr("chrome/toolkit/a-copy.png", _png(500))
        z.writestr("chrome/toolkit/lines.png", _other(300))
    (tmp_path / "browser").mkdir()
    with zipfile.ZipFile(tmp_path / "browser" / "omni.ja", "w") as z:
        z.writestr("chrome/branding/about-logo.svg", wrapper)
        z.writestr("chrome/branding/about-logo-2x.png", _png(1000))
        z.writestr("chrome/branding/tiny.png", _png(16))
    (tmp_path / "browser" / "logo.png").write_bytes(_png(256))
    return tmp_path


def test_copies_the_same_picture_and_wrappers_are_found(tmp_path):
    r = images.scan(_install(tmp_path))
    assert r["count"] == 7
    assert [sorted(e["copies"]) for e in r["exact"]] == [["omni.ja:chrome/toolkit/a-copy.png", "omni.ja:chrome/toolkit/a.png"]]
    gorilla = r["same"][0]
    names = {f[0] for f in gorilla["files"]}
    assert names == {"browser/omni.ja:chrome/branding/about-logo.svg", "browser/omni.ja:chrome/branding/about-logo-2x.png",
                     "omni.ja:chrome/toolkit/a.png", "omni.ja:chrome/toolkit/a-copy.png", "loose:browser/logo.png"}
    assert not any("lines.png" in f[0] for s in r["same"] for f in s["files"])        # a different picture stays apart
    assert not any("tiny.png" in f[0] for s in r["same"] for f in s["files"])         # under 64 px: not hashed
    assert r["wrappers"] == [{"file": "browser/omni.ja:chrome/branding/about-logo.svg", "bytes": r["wrappers"][0]["bytes"],
                              "raster": "1400x1400", "decoded_bytes": 1400 * 1400 * 4}]
    text = "\n".join(images.lines(r))
    assert "wraps 1400x1400" in text and "the same picture in several files: 1 group(s)" in text


def test_an_unreadable_image_is_counted_not_fatal(tmp_path):
    with zipfile.ZipFile(tmp_path / "omni.ja", "w") as z:
        z.writestr("x/broken.png", b"not a png")
    r = images.scan(tmp_path)
    assert r["count"] == 1 and r["same"] == [] and r["wrappers"] == []
