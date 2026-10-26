"""Small python stages recipes share: checksums, a byte comparison, the telemetry scan. Each returns {ok, detail}."""
from pathlib import Path

from .recipe import files_of, sha256_file


def sha256sums(ctx, files, out):
    """SHA256SUMS over the files the globs match (name relative to its folder), written to `out`."""
    rows = [(sha256_file(f), Path(f).name) for _b, f in files_of(files)]
    if not rows:
        return {"ok": False, "detail": f"no file matches {files}"}
    if not ctx.dry_run:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes("".join(f"{h}  {n}\n" for h, n in rows).encode("utf-8"))
    return {"ok": True, "detail": f"{len(rows)} file(s) in {out}"}


def same_bytes(ctx, a, b):
    """Two builds of one input must be the same bytes (the reproducibility check)."""
    pa, pb = Path(a), Path(b)
    if not pa.is_file() or not pb.is_file():
        return {"ok": False, "detail": f"missing: {[str(p) for p in (pa, pb) if not p.is_file()]}"}
    ha, hb = sha256_file(pa), sha256_file(pb)
    if ha == hb:
        return {"ok": True, "detail": f"same bytes: sha256 {ha[:16]} ({pa.stat().st_size} bytes)"}
    return {"ok": False, "detail": f"NOT reproducible: {pa.name} {ha[:16]} vs {hb[:16]} "
                                   f"({pa.stat().st_size} vs {pb.stat().st_size} bytes)"}


def telemetry_scan(ctx, path):
    """fieldkit telemetry scan on the source tree: zero findings or the stage fails."""
    from ..core import telemetry
    r = telemetry.scan(path)
    bad = r["findings"]
    return {"ok": not bad, "detail": f"{len(bad)} finding(s) in {r['files']} file(s)"
                                     + "".join(f"; {f['file']}:{f['line']} {f['match']}" for f in bad[:5])}
