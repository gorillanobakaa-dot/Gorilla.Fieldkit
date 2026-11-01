"""Acceptance check that REQUIRES Tesseract and ImageMagick: draw a picture holding known text, read it back with
tesseract, and run magick identify and convert on it. A missing binary is a failure; the model's own vision never
counts. `fieldkit image accept` exits 0 only when every step passed."""
import os
import subprocess
import tempfile

from . import tools

PHRASE = "GORILLA 4271"


def make_picture(path, text=PHRASE):
    """A large black-on-white PNG of TEXT (Pillow's default font, scaled up so Tesseract reads it)."""
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:                                  # Pillow < 10.1 has a fixed small bitmap font
        font = ImageFont.load_default()
    im = Image.new("RGB", (640, 160), "white")
    ImageDraw.Draw(im).text((24, 40), text, fill="black", font=font)
    im.save(path)
    return path


def _step(name, ok, detail):
    return {"step": name, "ok": bool(ok), "detail": detail}


def _run(argv, run, timeout=120):
    r = run(argv, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def check(workdir=None, local=None, which=None, run=subprocess.run):
    """-> {'ok', 'steps': [{'step','ok','detail'}]}. Absent binaries fail their steps; nothing is skipped."""
    kw = {} if which is None else {"which": which}
    magick, tess = tools.find("magick", local, **kw), tools.find("tesseract", local, **kw)
    steps = [_step("magick present", magick, magick or "magick not found: " + tools.SPECS["magick"][3][tools.platform_key()]),
             _step("tesseract present", tess, tess or "tesseract not found: " + tools.SPECS["tesseract"][3][tools.platform_key()])]
    with tempfile.TemporaryDirectory(prefix="fk-accept-", dir=workdir) as d:
        png = make_picture(os.path.join(d, "known.png"))
        if tess:
            try:
                rc, out, err = _run([tess, png, "stdout", "-l", "eng"], run)
                got = " ".join(out.split())
                steps.append(_step("tesseract reads the known text", rc == 0 and PHRASE in got, f"read {got!r}, wanted {PHRASE!r}"))
            except (OSError, subprocess.SubprocessError) as e:
                steps.append(_step("tesseract reads the known text", False, str(e)))
        if magick:
            try:
                rc, out, err = _run([magick, "identify", "-format", "%w %h %m", png], run)
                steps.append(_step("magick identify", rc == 0 and out.strip() == "640 160 PNG", f"printed {out.strip()!r}"))
                jpg = os.path.join(d, "known.jpg")
                rc, out, err = _run([magick, "convert", png, jpg] if tools.platform_key() != "windows" else [magick, png, jpg], run)
                steps.append(_step("magick convert", rc == 0 and os.path.isfile(jpg) and os.path.getsize(jpg) > 0,
                                   f"exit {rc} {err.strip()[:120]}"))
            except (OSError, subprocess.SubprocessError) as e:
                steps.append(_step("magick identify/convert", False, str(e)))
    return {"ok": all(s["ok"] for s in steps), "steps": steps}
