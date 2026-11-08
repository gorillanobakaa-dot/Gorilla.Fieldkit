"""Read the text in a picture (Tesseract, word boxes) and find personal text in it with the detectors of
`fieldkit privacy scan`. Fail closed: a picture that could not be read is a finding, not a pass."""
import os
import subprocess
from pathlib import Path

from ..core import privacy
from . import measure, tools

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MIN_CONF = 30           # below this a "word" is noise; the line text still goes through the detectors


def read(path, lang="eng", run=subprocess.run, timeout=180):
    """-> {'file','lang','text','words':[{'text','conf','box':[x,y,w,h],'line'}]}. Raises ImageError."""
    exe = tools.find("tesseract")
    if not exe:
        raise measure.ImageError("tesseract is not installed: run `fieldkit image doctor`")
    if not os.path.isfile(path):
        raise measure.ImageError(f"{path} does not exist")
    r = run([exe, str(path), "stdout", "-l", lang, "tsv"], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise measure.ImageError(f"tesseract failed: {(r.stderr or '').strip()[:200]}")
    return parse_tsv(r.stdout, path, lang)


def parse_tsv(tsv, path="", lang="eng"):
    words, lines = [], {}
    for row in tsv.splitlines()[1:]:
        c = row.split("\t")
        if len(c) < 12 or c[0] != "5" or not c[11].strip():
            continue
        try:
            conf = float(c[10])
            box = [int(c[6]), int(c[7]), int(c[8]), int(c[9])]
        except ValueError:
            continue
        key = (c[2], c[3], c[4])
        words.append({"text": c[11].strip(), "conf": conf, "box": box, "line": key})
        lines.setdefault(key, []).append(c[11].strip())
    text = "\n".join(" ".join(w) for w in lines.values())
    for w in words:
        w["line"] = "-".join(w["line"])
    return {"file": str(path), "lang": lang, "text": text, "words": words}


def findings(res, terms=None):
    """Personal text in one OCR result -> [{'kind','excerpt','line','boxes'}]. The boxes are the words to black out:
    the words that are findings on their own, else (a term spanning words) the whole line."""
    terms = privacy.private_terms() if terms is None else terms
    by_line = {}
    for w in res["words"]:
        by_line.setdefault(w["line"], []).append(w)
    out = []
    for n, ws in enumerate(by_line.values(), 1):
        hits = privacy.scan_text(" ".join(w["text"] for w in ws), terms)
        if not hits:
            continue
        single = [w for w in ws if privacy.scan_text(w["text"], terms)]
        for h in hits:
            out.append({"kind": h["kind"], "excerpt": h["excerpt"], "line": n,
                        "boxes": [w["box"] for w in (single or ws)]})
    return out


def check(path, lang="eng", terms=None, run=subprocess.run):
    """-> (findings, ocr result). A picture that cannot be read gives one not-scanned finding."""
    try:
        res = read(path, lang, run)
    except (measure.ImageError, OSError, subprocess.SubprocessError) as e:
        return [{**privacy.not_scanned(str(e)), "boxes": []}], None
    return findings(res, terms), res


def redact(path, hits, out=None):
    """A copy with the boxes of HITS blacked out. Never the original. -> the copy's path."""
    from ..shot import look
    look.need_pil()
    from PIL import Image, ImageDraw
    src = Path(path)
    dst = Path(out) if out else src.with_name(src.stem + ".redacted.png")
    if dst.resolve() == src.resolve():
        raise measure.ImageError("the redacted copy would overwrite the original")
    im = Image.open(src).convert("RGB")
    dr = ImageDraw.Draw(im)
    for h in hits:
        for x, y, w, hh in h["boxes"]:
            dr.rectangle([x - 3, y - 3, x + w + 3, y + hh + 3], fill=(0, 0, 0))
    im.save(dst)
    return str(dst)


def image_files(path, git_only=False):
    p = Path(path)
    if p.is_file():
        files = [p]
    elif git_only:
        files = [f for f in (privacy.git_publishable(p) or []) if f.is_file()]
    else:
        files = [Path(r) / n for r, ds, ns in os.walk(p) for n in sorted(ns)
                 if not set(Path(r).relative_to(p).parts) & privacy.SKIP_DIRS]
    return [f for f in files if f.suffix.lower() in IMAGE_EXT]


def scan_images(paths, git_only=False, lang="eng", run=subprocess.run):
    """For `fieldkit privacy scan --images`: {file: [findings]} in the shape privacy.scan_path returns."""
    rep = {}
    for p in paths:
        for f in image_files(p, git_only):
            hits, _ = check(f, lang, run=run)
            if hits:
                rep[str(f)] = [{"kind": h["kind"], "line": h.get("line", 0), "excerpt": h["excerpt"]} for h in hits]
    return rep
