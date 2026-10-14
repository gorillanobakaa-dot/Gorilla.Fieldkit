"""Read numbers off a screenshot, and reason about what they mean.

    fieldkit shot read FILE...          the text, as OCR sees it
    fieldkit shot speedtest FILE...     download, upload, ping, per shot
    fieldkit shot verdict FILE...       WHICH LAYER is limiting, from several runs

# WHY THIS EXISTS

On 2026-10-13 the owner sent four speedtest screenshots and asked why wifi gave
44 Mbps when he expected 417. The answer was visible in the pictures before any
probe was run, and it is the reason this module exists:

    shot      download     upload
    1           44.31       21.89
    2           44.25       16.07
    3        (mid-test, no final numbers)
    4          417.32       18.81

Download moved by a factor of 9.4. Upload sat between 16 and 22 in every single
run. A bottleneck in the link throttles BOTH directions; this one touched only
the download. So the download was hitting a ceiling the upload never reached,
which pointed at the radio rather than at the line, and the hardware probe then
confirmed a card with no 5 GHz radio.

That inference is `verdict()` below. It is the whole point of the module: the
numbers are easy, the comparison is what tells you where to look.
"""

# ============================================================================
# TRAPS, from doing this for real
# ============================================================================
#
# 1. OCR OF A DARK UI IS NOISY AND THAT IS FINE. tesseract returns things like
#    "Pingms @15 @O100 @90" and "31.94.7258". Do not try to clean the whole
#    page: find the LABELS, then take the numbers near them.
#
# 2. A SCREENSHOT OF A TEST IN PROGRESS HAS NO RESULT. Shot 3 above was taken
#    mid-run: the download figure is on a dial, the upload is a dash. A parser
#    that returns a number for it is inventing data. Return `complete: False`
#    and say so.
#
# 3. NEVER GUESS A MISSING NUMBER FROM THE OTHERS. Reporting "about 400" for an
#    unfinished test is the same class of error as a check that reports ok
#    because it found a program.
#
# 4. tesseract IS ENOUGH. pytesseract is a thin wrapper around the same binary;
#    calling it directly avoids a pip dependency the harness does not need.
#    PIL and ImageMagick are present for preprocessing if a shot ever needs it.
# ============================================================================

from .read import read_text, speedtest, verdict, lines, exit_code  # noqa: E402

__all__ = ["read_text", "speedtest", "verdict", "lines", "exit_code"]
