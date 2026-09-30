"""Generic stage checks any pipeline can use (preflight-style, from Gorilla.firefox).

Each takes the pipeline Context first and returns {"ok": bool, "detail": str}.
A check tests the thing the job depends on, not a proxy for it.
"""
import shutil
from pathlib import Path

from .host import find_tool


def disk_free(ctx, path, gb):
    """At least `gb` GB free on the drive holding `path` (a Firefox objdir needs ~40)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    free = shutil.disk_usage(p).free / 1e9
    return {"ok": free >= float(gb), "detail": f"{free:.1f} GB free at {p}, need {gb}"}


def tools_present(ctx, names):
    """Every named program is findable (PATH or known Windows locations)."""
    missing = [n for n in names if not find_tool(n)]
    return {"ok": not missing, "detail": "all present" if not missing else "missing: " + ", ".join(missing)}


def always_ok(ctx, detail="ok"):
    """A no-op stage (placeholders, tests)."""
    return {"ok": True, "detail": detail}
