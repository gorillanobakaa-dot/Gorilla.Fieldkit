"""fieldkit backup: a complete, dated, verified zip of the Fieldkit folder, into the owner's backup folder.

    fieldkit backup [--to FOLDER] [--json]

Born 2026-10-10 from the owner's brief: back the harness up before changing it and again after, in the folder where
the backups already live, and prove the archive is sound. The folder is never guessed: it comes from --to, or from
"backup": {"dir": ...} in fieldkit.local.json. With neither, this lists the folders on the machine that look like
backup folders and stops (exit 2), so the person names one.

The archive is harness_backup_YYYY-MM-DD_HHMM.zip (never overwritten: _2, _3 ...). It holds everything in the Fieldkit
folder, .git and local/ included, except what is rebuilt or downloaded again: caches, toolbox/ (fieldkit gather),
vault/ (pristine sources, fetched again). Verified by re-opening it: every file is in it, every CRC checks out, and
the byte total matches what was read.
"""
import datetime as dt
import os
import re
import zipfile
from pathlib import Path

from . import settings

SKIP_DIRS = {"__pycache__", ".pytest_cache", "toolbox", "vault", "harvest", ".mypy_cache"}
SKIP_WHY = "caches, and what fieldkit gather or the pipelines fetch again (toolbox/, vault/, harvest/)"
LOOKS_LIKE = re.compile(r"(backup|back-up|copii|copie|salvari|arhiv)", re.I)


def candidates(home=None, extra=()):
    """Folders that look like backup folders: names with 'backup' (and Romanian words) near home, and drive roots."""
    home = Path(home or Path.home())
    roots = [home, home / "Documents", home / "Desktop", home / "OneDrive", *map(Path, extra)]
    if os.name == "nt":
        roots += [Path(f"{d}:/") for d in "DEFGHIJ" if Path(f"{d}:/").exists()]
    found = []
    for r in roots:
        if not r.is_dir():
            continue
        for p in sorted(r.iterdir()):
            try:
                if p.is_dir() and LOOKS_LIKE.search(p.name):
                    found.append(p)
                    found += [q for q in sorted(p.iterdir()) if q.is_dir() and LOOKS_LIKE.search(q.name)]
            except OSError:
                continue
    return list(dict.fromkeys(str(p) for p in found))


def _files(root):
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            if f.endswith((".pyc", ".pyo")):
                continue
            p = Path(d) / f
            if p.is_file() and not p.is_symlink():
                out.append(p)
    return out


def verify(zpath, expect_count, expect_bytes):
    with zipfile.ZipFile(zpath) as z:
        bad = z.testzip()
        infos = [i for i in z.infolist() if not i.is_dir()]
        total = sum(i.file_size for i in infos)
    problems = []
    if bad:
        problems.append(f"CRC error in {bad}")
    if len(infos) != expect_count:
        problems.append(f"{len(infos)} files in the archive, {expect_count} read")
    if total != expect_bytes:
        problems.append(f"{total} bytes in the archive, {expect_bytes} read")
    return {"ok": not problems, "files": len(infos), "bytes": total, "problems": problems}


def backup(to=None, root=None, now=None, local=None, home=None):
    root = Path(root or settings.ROOT).resolve()
    local = settings.local_settings() if local is None else local
    dest = to or (local.get("backup") or {}).get("dir")
    if not dest:
        c = candidates(home)
        return {"ok": False, "status": "ask", "candidates": c,
                "next": ("which folder holds the backups? then: fieldkit backup --to \"FOLDER\" "
                         "(or put {\"backup\": {\"dir\": \"FOLDER\"}} in fieldkit.local.json)") if c else
                        "no backup folder found: ask the person where backups go, then fieldkit backup --to \"FOLDER\""}
    dest = Path(settings.expand(str(dest))).expanduser().resolve()
    if dest == root or root in dest.parents:
        return {"ok": False, "status": "refused", "next": f"{dest} is inside the Fieldkit folder; choose a folder outside it"}
    dest.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now()).strftime("%Y-%m-%d_%H%M")
    name, n = f"harness_backup_{stamp}", 1
    while (dest / f"{name}.zip").exists():
        n += 1
        name = f"harness_backup_{stamp}_{n}"
    zpath = dest / f"{name}.zip"
    files = _files(root)
    total = 0
    tmp = zpath.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            z.write(p, Path(root.name) / p.relative_to(root))
            total += p.stat().st_size
    v = verify(tmp, len(files), total)
    if not v["ok"]:
        tmp.unlink()
        return {"ok": False, "status": "failed", "problems": v["problems"], "next": "run fieldkit backup again; "
                "if it fails twice, check the disk (space, errors) before anything else"}
    tmp.replace(zpath)
    out = {"ok": True, "status": "done", "archive": str(zpath), "size": zpath.stat().st_size, "files": v["files"],
           "bytes": v["bytes"], "skipped": SKIP_WHY, "verified": "re-opened: every file present, every CRC good, "
           "byte total matches", "next": "done"}
    if (local.get("backup") or {}).get("reminder"):
        out["reminder"] = local["backup"]["reminder"]
    return out


def lines(r):
    if r["status"] == "ask":
        out = ["backup: which folder? (the folder is never guessed)"]
        out += [f"  {c}" for c in r["candidates"]] or ["  (no folder that looks like a backup folder was found)"]
    elif r["ok"]:
        out = [f"backup: {r['archive']}", f"  {r['files']} files, {r['bytes']:,} bytes read, "
               f"{r['size']:,} bytes on disk", f"  verified: {r['verified']}", f"  not included: {r['skipped']}"]
        if r.get("reminder"):
            out.append(f"  {r['reminder']}")
    else:
        out = [f"backup: {r['status'].upper()}"] + [f"  {p}" for p in r.get("problems", [])]
    return out + [f"NEXT: {r['next']}"]
