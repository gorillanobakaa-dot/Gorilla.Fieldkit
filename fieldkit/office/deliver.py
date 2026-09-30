"""Pipeline stages for sending a document safely (pipelines/office-deliver.yaml).

Harvested from a document harness's finalising step ("makes a Word or
PowerPoint file safe to submit") and its metadata scrubber ("the LAST step,
after the last save").

    fieldkit pipeline run office-deliver --var file=report.docx

Stages: check (not broken) -> scrub (names out of the properties, .bak kept;
verified by recheck: still sound, no names left) -> privacy (no secrets, paths or private
words anywhere inside the file). Each returns {"ok", "detail"}.
"""
from pathlib import Path

from ..core import privacy
from . import check as chk
from . import scrub as scr


def _file(ctx, file=None):
    if not (file or ctx.vars.get("file")):
        raise ValueError("no file given: --var file=PATH")
    f = Path(file or ctx.vars["file"])
    if not f.is_file():
        raise FileNotFoundError(f"{f} not found")
    return f


def stage_check(ctx, file=None):
    rep = chk.check(_file(ctx, file))
    return {"ok": not rep["problems"], "detail": "sound" if not rep["problems"] else "; ".join(rep["problems"][:3])}


def stage_scrub(ctx, file=None):
    f = _file(ctx, file)
    if f.suffix.lower() == ".pdf":
        return {"ok": True, "detail": "PDF: no Office properties to scrub"}
    res = scr.scrub(f, privacy.private_terms())
    return {"ok": True, "detail": f"removed {len(res['removed'])} item(s)" + (f"; backup {res['backup']}" if res["backup"] else "")}


def stage_recheck(ctx, file=None):
    f = _file(ctx, file)
    probs = chk.check(f)["problems"]
    left = [] if f.suffix.lower() == ".pdf" else scr.inspect(f, privacy.private_terms())
    ok = not probs and not left
    return {"ok": ok, "detail": "sound and no names in its properties" if ok else
            "; ".join(probs[:2] + [f"{w} {k}" for w, k, _ in left[:3]])}


def stage_privacy(ctx, file=None):
    found = privacy.scan_path(_file(ctx, file))
    n = sum(len(v) for v in found.values())
    return {"ok": not found, "detail": "nothing private inside" if not found else
            f"{n} finding(s): " + ", ".join(sorted({h['kind'] for v in found.values() for h in v}))}
