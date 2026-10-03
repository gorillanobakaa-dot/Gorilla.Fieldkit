"""Pipeline stages for sending a document safely (pipelines/office-deliver.yaml).

Harvested from a document harness's finalising step ("makes a Word or
PowerPoint file safe to submit") and its metadata scrubber ("the LAST step,
after the last save").

    fieldkit pipeline run office-deliver --var file=report.docx [--var no_backup=1]

Stages: check (not broken) -> scrub (names out of the properties; the backup goes
to Fieldkit's state folder, never beside the file; verified by recheck: still
sound, no names left) -> privacy (no secrets, paths or private words anywhere
inside the file, every part of it actually read, and no stale backup or lock
file lying next to it). Each returns {"ok", "detail"}.

PDF: the document properties (Info dictionary, XMP) are inspected; Fieldkit
cannot clean them, so a PDF whose properties hold a name stops at scrub with
instructions to clean it elsewhere.
"""
from pathlib import Path

from ..core import privacy
from . import check as chk
from . import scrub as scr

_TRUE = {"1", "true", "yes", "on"}


def _file(ctx, file=None):
    if not (file or ctx.vars.get("file")):
        raise ValueError("no file given: --var file=PATH")
    f = Path(file or ctx.vars["file"])
    if not f.is_file():
        raise FileNotFoundError(f"{f} not found")
    return f


def _no_backup(ctx):
    return str((getattr(ctx, "vars", None) or {}).get("no_backup", "")).strip().lower() in _TRUE


def stage_check(ctx, file=None):
    rep = chk.check(_file(ctx, file))
    return {"ok": not rep["problems"], "detail": "sound" if not rep["problems"] else "; ".join(rep["problems"][:3])}


def stage_scrub(ctx, file=None):
    f = _file(ctx, file)
    terms = privacy.private_terms()
    if f.suffix.lower() == ".pdf":
        left = scr.inspect(f, terms)
        if left:
            return {"ok": False, "detail": "PDF properties hold names: "
                    + ", ".join(f"{k}" for _, k, _ in left[:5]) + ". " + scr.PDF_CANNOT_SCRUB}
        return {"ok": True, "detail": "PDF: no names in its properties (Author, Title, Subject, Keywords, "
                                      "Creator, Producer, XMP)"}
    res = scr.scrub(f, terms, backup=not _no_backup(ctx))
    detail = f"removed {len(res['removed'])} item(s)"
    if res["backup"]:
        detail += f"; backup kept outside the document's folder: {res['backup']}"
    elif res["removed"]:
        detail += "; no backup kept (no_backup)"
    return {"ok": True, "detail": detail}


def stage_recheck(ctx, file=None):
    f = _file(ctx, file)
    probs = chk.check(f)["problems"]
    left = scr.inspect(f, privacy.private_terms())
    ok = not probs and not left
    return {"ok": ok, "detail": "sound and no names in its properties" if ok else
            "; ".join(probs[:2] + [f"{w} {k}" for w, k, _ in left[:3]])}


def stage_privacy(ctx, file=None):
    f = _file(ctx, file)
    found = privacy.scan_path(f)
    stale = scr.stale_backups(f)
    n = sum(len(v) for v in found.values())
    parts = []
    if found:
        parts.append(f"{n} finding(s): " + ", ".join(sorted({h['kind'] for v in found.values() for h in v})))
        skipped = [h["excerpt"] for v in found.values() for h in v if h["kind"] == "not-scanned"]
        if skipped:
            parts.append(f"{len(skipped)} part(s) could not be read, so cannot be called clean: "
                         + "; ".join(skipped[:3]))
    if stale:
        parts.append("stale backup or lock file next to it (may hold removed names; delete or move it before "
                     "sharing the folder): " + ", ".join(p.name for p in stale[:5]))
    ok = not found and not stale
    return {"ok": ok, "detail": "nothing private inside, no stale backup beside it" if ok else " | ".join(parts)}
