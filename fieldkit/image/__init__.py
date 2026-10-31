"""Pictures, offline (plan item P4, idea I-04): compress, convert, resize, compare, read the text in them, and find
personal text before they are published.

    fieldkit image doctor [--json]                     which tools are present, their versions and paths; what is missing and how to install it
    fieldkit image squash PATH... [--to webp|avif|jpeg|png] [--quality Q | --target-ssim S] [--max-px N] [--out DIR]
    fieldkit image compare A B [--out FILE]            SSIM, PSNR and a side-by-side picture with the difference
    fieldkit image ocr PATH... [--lang eng]            the text per picture, with word boxes
    fieldkit image privacy PATH... [--lang eng] [--redact]   OCR, then the detectors of `fieldkit privacy scan`; exit 3 on a finding

Squoosh (squoosh.app, github.com/GoogleChromeLabs/squoosh) is a web page over open codecs. Checked 2026-10-31: its
command line and libsquoosh were removed from the repository on 2023-01-03 (commit "Remove CLI / libsquoosh (#1321)",
no `cli/` folder is left; the repository itself is still pushed to). So the codecs are driven directly: mozjpeg
(cjpeg), libwebp (cwebp), libavif (avifenc), oxipng; ImageMagick (`magick`) is the measuring tool and the fallback
encoder; Tesseract reads the text. No tool here phones home; nothing is installed or downloaded by this module.
Tool paths: fieldkit.local.json {"image": {"tools": {"cwebp": "PATH"}}}, else PATH. Never guessed.
"""
