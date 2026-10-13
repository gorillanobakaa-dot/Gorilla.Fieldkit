"""OCR a screenshot and pull the numbers out, honestly."""
import os
import re
import shutil
import subprocess

NUM = r"[0-9]{1,5}(?:[.,][0-9]{1,2})?"


def _tesseract():
    p = shutil.which("tesseract")
    if p:
        return p
    for d in ("/usr/sbin", "/sbin", "/usr/local/sbin"):
        c = os.path.join(d, "tesseract")
        if os.access(c, os.X_OK):
            return c
    return None


def read_text(path, timeout=120):
    """-> the raw OCR text, or None with a reason."""
    t = _tesseract()
    if not t:
        return None, "tesseract is not installed (sudo apt install tesseract-ocr)"
    if not os.path.exists(path):
        return None, f"{path} does not exist"
    try:
        r = subprocess.run([t, path, "-"], capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return None, str(e)
    if r.returncode != 0:
        return None, (r.stderr or "tesseract failed").strip()[:200]
    return r.stdout, None


def _num(s):
    try:
        return float(s.replace(",", "."))
    except (TypeError, ValueError):
        return None


def speedtest(path):
    """Pull download, upload and ping off an Ookla-style result screenshot.

    Returns complete=False when the picture shows a test still running. It does
    NOT guess the missing figure from the others (trap 3).
    """
    text, err = read_text(path)
    if text is None:
        return {"file": os.path.basename(path), "complete": False, "error": err}

    out = {"file": os.path.basename(path), "download": None, "upload": None,
           "ping": None, "complete": False, "error": None}

    lines_ = [l.strip() for l in text.splitlines() if l.strip()]

    # The result pair sits on the line AFTER the DOWNLOAD/UPLOAD labels. Find the
    # label line, then the first following line holding two numbers.
    label_at = None
    for i, l in enumerate(lines_):
        if re.search(r"DOWNLOAD", l, re.I) and re.search(r"UPLOAD", l, re.I):
            label_at = i
            break
    if label_at is not None:
        for l in lines_[label_at + 1: label_at + 4]:
            pair = re.findall(NUM, l)
            # two plain numbers and nothing else of substance: the result pair
            if len(pair) == 2 and not re.search(r"ping", l, re.I):
                out["download"], out["upload"] = _num(pair[0]), _num(pair[1])
                break

    for l in lines_:
        if re.search(r"ping", l, re.I):
            # "Pingms @15 @O100 @90" -> the FIRST number is the idle ping; the
            # other two are loaded latency and are not what people mean by ping.
            nums = re.findall(r"[0-9]{1,4}", l)
            if nums:
                out["ping"] = _num(nums[0])
            break

    out["complete"] = out["download"] is not None and out["upload"] is not None
    if not out["complete"]:
        out["error"] = ("no final figures in this picture: it looks like a test still "
                        "running. Not guessing (see trap 2).")
    return out


def verdict(results, wan=None):
    """From several runs, say WHICH LAYER is limiting. The reasoning, not the numbers.

    The rule: a bottleneck in the link throttles BOTH directions. If download
    swings wildly while upload barely moves, the download was above a ceiling the
    upload never reached, and that ceiling is the radio or the cable.

    THAT RULE IS ONLY VALID ON A FIXED LINE, and getting this wrong is the
    mistake this function exists to stop repeating.

    CORRECTION, 2026-10-13, measured not argued. The rule was applied to a
    connection behind an EE 5G hub and gave a confident wrong answer. On a
    cellular WAN:

      * the DOWNLINK is shared and rescheduled continuously, so it varies by
        10x or more within minutes with no fault at all. Measured on one
        evening, same cable, unchanged: 25.7, 29.8, 80.2, 122, 170, 204, 220,
        235 and 417 Mbps;
      * the UPLINK is a separate and much smaller allocation, so it stays flat
        ANYWAY.

    So "download swings, upload flat" is the normal shape of mobile broadband,
    not evidence of a link bottleneck. The rule found a real pattern and blamed
    the wrong thing.

    `wan` must therefore be "fixed" or "cellular". Without it this refuses to
    conclude rather than guessing, because a confident wrong answer sends
    somebody to change router settings for an evening.
    Get it from `fieldkit net kind`.
    """
    done = [r for r in results if r.get("complete")]
    skipped = [r["file"] for r in results if not r.get("complete")]
    if len(done) < 2:
        return {"verdict": "need at least two completed tests to compare",
                "usable": len(done), "ignored": skipped}

    dl = [r["download"] for r in done]
    ul = [r["upload"] for r in done]
    dl_spread = max(dl) / min(dl) if min(dl) else 0
    ul_spread = max(ul) / min(ul) if min(ul) else 0

    if wan is None:
        return {"verdict": ("CANNOT SAY YET. The same numbers mean opposite things on a fixed "
                            "line and on a mobile connection: on cellular a 10x swing with a "
                            "flat upload is normal, not a fault. Establish which this is "
                            "first."),
                "next": "fieldkit net kind", "usable": len(done), "ignored": skipped,
                "download_min": min(dl), "download_max": max(dl),
                "download_spread": round(dl_spread, 2),
                "upload_min": min(ul), "upload_max": max(ul),
                "upload_spread": round(ul_spread, 2)}

    if wan == "cellular":
        v = (f"NOT A FAULT, PROBABLY. This is a mobile connection, and download varying by "
             f"{dl_spread:.1f}x ({min(dl):.0f} to {max(dl):.0f} Mbps) with a flat upload is "
             f"what mobile broadband looks like: you share the cell, the tower reschedules you "
             f"constantly, and the uplink is a separate smaller allocation that stays flat "
             f"anyway. Judge this connection by its WORST usable moment, never by one reading. "
             f"Your own equipment only matters when it caps you BELOW the cell's good moments.")
        return {"verdict": v, "next": "fieldkit wifi doctor   (to find your own ceiling)",
                "usable": len(done), "ignored": skipped, "wan": wan,
                "download_min": min(dl), "download_max": max(dl),
                "download_spread": round(dl_spread, 2),
                "upload_min": min(ul), "upload_max": max(ul),
                "upload_spread": round(ul_spread, 2)}

    if dl_spread >= 2 and ul_spread < 1.6:
        v = ("THE LINK IS THE LIMIT, NOT THE LINE. Download varies by "
             f"{dl_spread:.1f}x ({min(dl):.0f} to {max(dl):.0f} Mbps) while upload stays "
             f"within {ul_spread:.1f}x ({min(ul):.0f} to {max(ul):.0f}). A congested or slow "
             "line would drag both down together. Only the download met a ceiling, so look "
             "at the radio or the cable: band, card capability, channel.")
        nxt = "fieldkit wifi doctor"
    elif dl_spread < 1.6 and ul_spread < 1.6:
        v = ("CONSISTENT. Both directions are steady across runs, so whatever you are "
             "getting is what this connection gives. If it is below what you pay for, the "
             "question is for the provider, not the hardware.")
        nxt = "check the service you are paying for"
    else:
        v = ("BOTH DIRECTIONS VARY. That points at the line or at congestion rather than at "
             "a fixed ceiling in your own equipment. Test at different times before blaming "
             "hardware.")
        nxt = "repeat the tests at a quiet hour"

    return {"verdict": v, "next": nxt, "usable": len(done), "ignored": skipped, "wan": wan,
            "download_min": min(dl), "download_max": max(dl), "download_spread": round(dl_spread, 2),
            "upload_min": min(ul), "upload_max": max(ul), "upload_spread": round(ul_spread, 2)}


def lines(d):
    if "verdict" in d and "usable" in d:
        out = [f"{d['usable']} completed test(s) compared"]
        if d.get("ignored"):
            out.append(f"ignored (no final figures): {', '.join(d['ignored'])}")
        if "download_spread" in d:
            out += ["",
                    f"  download  {d['download_min']:.2f} to {d['download_max']:.2f} Mbps"
                    f"   spread {d['download_spread']}x",
                    f"  upload    {d['upload_min']:.2f} to {d['upload_max']:.2f} Mbps"
                    f"   spread {d['upload_spread']}x", ""]
        out += [d["verdict"], "", f"NEXT: {d.get('next', '')}"]
        return out
    rows = [f"{d['file']:<14} ", ]
    return rows


def exit_code(d):
    return 0 if d.get("usable", 0) >= 2 or d.get("complete") else 3
