"""Look at the rendered file, not the code that wrote it (docs/METHODS.md #3).

    fieldkit office render FILE [--out DIR] [--scale 0.6] [--pages 6] [--sheet]

A Word, PowerPoint or Excel file is turned into a PDF by LibreOffice (headless, in a private profile so an open
LibreOffice is not disturbed), and each page into a PNG by pdfium. --sheet puts the first pages side by side in one
image (contact-sheet.png), which is what a model with vision, or a person, reads at a glance. PDFs are rendered
directly. Layout faults that pass every check - an empty slide, a page break in the wrong place, a table off the
page, unsorted references - are visible here.

Nothing is changed: the input is copied into the output folder first.
Exit 0 with the pages written, 2 for bad input, 4 when LibreOffice is needed and missing (the install line is given).
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

INSTALL = {"windows": "winget install --id TheDocumentFoundation.LibreOffice -e",
           "linux": "sudo apt-get install -y libreoffice-core libreoffice-writer libreoffice-impress libreoffice-calc"}


def find_soffice():
    from ..academic.read_sources import _find_soffice
    return _find_soffice()


def to_pdf(src, out_dir, timeout=180):
    src, out_dir = Path(src), Path(out_dir)
    if src.suffix.lower() == ".pdf":
        dest = out_dir / src.name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        return dest
    soffice = find_soffice()
    if not soffice:
        raise LookupError("LibreOffice is needed to render Office files")
    work = out_dir / src.name
    shutil.copy2(src, work)
    with tempfile.TemporaryDirectory() as profile:
        r = subprocess.run([soffice, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless",
                            "--convert-to", "pdf", "--outdir", str(out_dir), str(work)],
                           capture_output=True, text=True, timeout=timeout)
    pdf = out_dir / (src.stem + ".pdf")
    if not pdf.is_file():
        raise RuntimeError(f"LibreOffice made no PDF: {(r.stderr or r.stdout).strip()[-300:]}")
    return pdf


def render(src, out=None, scale=0.6, pages=6, sheet=False):
    import pypdfium2 as pdfium
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"no such file: {src}")
    out = Path(out) if out else src.parent / f"{src.stem}.render"
    out.mkdir(parents=True, exist_ok=True)
    pdf = to_pdf(src, out)
    doc = pdfium.PdfDocument(str(pdf))
    total = len(doc)
    pngs = []
    for i in range(min(total, pages)):
        p = out / f"page-{i + 1:02d}.png"
        doc[i].render(scale=scale).to_pil().save(p)
        pngs.append(str(p))
    doc.close()
    rep = {"ok": True, "file": str(src), "pdf": str(pdf), "pages": total, "rendered": pngs}
    if sheet and pngs:
        from PIL import Image
        ims = [Image.open(p) for p in pngs]
        w, h = sum(i.width for i in ims), max(i.height for i in ims)
        board = Image.new("RGB", (w, h), "white")
        x = 0
        for im in ims:
            board.paste(im, (x, 0))
            x += im.width
        rep["sheet"] = str(out / "contact-sheet.png")
        board.save(rep["sheet"])
    rep["next"] = f"look at {rep.get('sheet') or pngs[0]}" if pngs else "the PDF has no pages"
    return rep
