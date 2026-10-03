"""Find and drive DualTrackAgent's dual_track.py (never edited from here).

Looked for, in order: the FIELDKIT_DUAL_TRACK environment variable,
"dual_track" in fieldkit.local.json, Documents/Scripts/DualTrackAgent/dual_track.py,
then the gathered copy in toolbox/dual-track-doc-generator/. dual_track.py
makes no network call in prep or render; it is run as a subprocess with the
Fieldkit folder as working directory, so every path it writes into a prep file
is relative (no home folder ends up in a working file).
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from ..core import settings


class DualTrackMissing(RuntimeError):
    pass


def candidates():
    out = []
    if os.environ.get("FIELDKIT_DUAL_TRACK"):
        out.append(Path(os.environ["FIELDKIT_DUAL_TRACK"]))
    local = settings.local_settings().get("dual_track")
    if local:
        out.append(Path(settings.expand(local, strict=False)))
    out.append(Path(settings.expand("${DOCUMENTS}")) / "Scripts" / "DualTrackAgent" / "dual_track.py")
    out.append(settings.ROOT / "toolbox" / "dual-track-doc-generator" / "dual_track.py")
    return out


def path():
    for c in candidates():
        if c.is_file():
            return c
    raise DualTrackMissing("dual_track.py not found; set FIELDKIT_DUAL_TRACK or \"dual_track\" in "
                           "fieldkit.local.json, or run: fieldkit gather --only dual-track-doc-generator")


_MOD = None


def module():
    """dual_track imported by path (for its schemas, validate_json and score_document)."""
    global _MOD
    if _MOD is None:
        p = path()
        spec = importlib.util.spec_from_file_location("fieldkit_gdocs_dual_track", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def run(*args, cwd=None, timeout=600):
    """-> (returncode, stdout+stderr)."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(path()), *map(str, args)], cwd=str(cwd or settings.ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")
