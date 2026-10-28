"""fieldkit backup usb: the newest backup, copied to a USB stick and proven there. Nothing on the stick is ever
deleted, moved or overwritten.

    fieldkit backup usb [--drive G | --stick FOLDER] [--from FOLDER]    copy the newest backup zip, then verify it
    fieldkit backup usb --check [--drive G | --stick FOLDER]            what is on the stick; changes nothing
    fieldkit backup remind [--quiet]                                     how old the last copy on a stick is

Born 2026-10-11 from the academic harness this generalises (usb_backup.py), with one rule changed on purpose. The
original kept the two newest copies and moved the rest off the stick, and when the stick was full it made room by
moving the OLDEST copies away, hand-made copies included. A backup can be the only one there is ("este unic"), so
here nothing already on the stick is touched: a full stick stops the copy and says how much room is needed, and the
person decides what to delete.

The copy: <stick>/Fieldkit Backups/<the zip's own name>, written as .part, renamed only after it is complete, then
read back: the SHA-256 must equal the original's and every member's CRC must check (zipfile.testzip). A copy that
fails is removed (only that file: it was made by this run a moment before) and the run says so. A name already on
the stick is never written over: an identical file counts as done, a different one gets _2, _3 ...

The reminder: the time of the last verified copy is kept in state/backup-usb.json, and a connected stick's newest
copy counts too. Due after backup.usb_remind_days (fieldkit.local.json, default 3). With a voice (the herald's,
offline) it is said aloud at most once a day.
"""
import datetime as dt
import hashlib
import json
import os
import re
import string
import zipfile
from pathlib import Path

from . import settings

FOLDER = "Fieldkit Backups"
REMIND_DAYS = 3
SPEAK_EVERY_H = 24
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})")


def _state_file():
    return settings.state_root() / "state" / "backup-usb.json"


def _read(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(p)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def removable_roots():
    """Folders where a USB stick is mounted: removable drive letters on Windows; /media and /run/media on Linux."""
    if os.name == "nt":
        import ctypes
        out = []
        for letter in string.ascii_uppercase:
            root = letter + ":\\"
            try:
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 2 and os.path.isdir(root):   # DRIVE_REMOVABLE
                    out.append(root)
            except OSError:
                continue
        return out
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    out = []
    for base in (Path("/media") / user, Path("/run/media") / user, Path("/media")):
        if base.is_dir():
            for p in sorted(base.iterdir()):
                if p.is_dir() and os.path.ismount(p) and str(p) not in out:
                    out.append(str(p))
    return out


def pick_stick(drive=None, stick=None, roots=None):
    """-> (folder, None) or (None, why). A named folder or drive wins; with several sticks, the one already holding
    a Fieldkit Backups folder; otherwise the person is asked."""
    if stick:
        p = Path(stick)
        return (p, None) if p.is_dir() else (None, f"{p} is not a folder: is the stick plugged in?")
    if drive:
        p = Path(drive.rstrip(":\\/") + ":\\") if os.name == "nt" else Path(drive)
        return (p, None) if p.is_dir() else (None, f"no drive {drive}: is the stick plugged in?")
    found = roots if roots is not None else removable_roots()
    if not found:
        return None, "no USB stick found: plug it in and run this again (or name it: --drive G / --stick FOLDER)"
    if len(found) == 1:
        return Path(found[0]), None
    known = [r for r in found if (Path(r) / FOLDER).is_dir()]
    if len(known) == 1:
        return Path(known[0]), None
    return None, f"several sticks ({', '.join(found)}): name one with --drive or --stick"


def newest_backup(source=None, local=None):
    local = settings.local_settings() if local is None else local
    src = source or (local.get("backup") or {}).get("dir")
    if not src:
        return None, "no backup folder: fieldkit backup --to FOLDER first (or --from FOLDER)"
    d = Path(settings.expand(str(src))).expanduser()
    zips = sorted(d.glob("harness_backup_*.zip"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
    if not zips:
        return None, f"no harness_backup_*.zip in {d}: run fieldkit backup first"
    return zips[-1], None


def copies(stick):
    """Every copy in <stick>/Fieldkit Backups, newest first: [{name, when, size, verified}] (verified: the .sha256
    beside it matches). Other files and folders on the stick are not listed and not read."""
    folder = Path(stick) / FOLDER
    out = []
    for p in sorted(folder.glob("*.zip")) if folder.is_dir() else []:
        m = STAMP.search(p.name)
        when = dt.datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M") if m else \
            dt.datetime.fromtimestamp(p.stat().st_mtime)
        side = p.with_name(p.name + ".sha256")
        out.append({"name": p.name, "path": str(p), "when": when.strftime("%Y-%m-%d %H:%M"), "size": p.stat().st_size,
                    "has_checksum": side.is_file()})
    return sorted(out, key=lambda c: c["when"], reverse=True)


def _free(path):
    import shutil
    return shutil.disk_usage(path).free


def copy(drive=None, stick=None, source=None, local=None, roots=None, now=None, free=None):
    src, why = newest_backup(source, local)
    if not src:
        return {"ok": False, "status": "no-backup", "next": why}
    root, why = pick_stick(drive, stick, roots)
    if not root:
        return {"ok": False, "status": "ask", "next": why}
    folder = root / FOLDER
    digest = sha256(src)
    size = src.stat().st_size
    dest, n = folder / src.name, 1
    while dest.exists():
        if dest.stat().st_size == size and sha256(dest) == digest:
            return _done(src, dest, digest, root, now, already=True)
        n += 1
        dest = folder / f"{src.stem}_{n}{src.suffix}"
    room = _free(root) if free is None else free
    need = size + (1 << 20)
    if room < need:
        return {"ok": False, "status": "full", "need": need, "free": room,
                "on_stick": [c["name"] for c in copies(root)],
                "next": (f"the stick has {room / 2 ** 20:,.0f} MB free and the copy needs {need / 2 ** 20:,.0f} MB. "
                         "Nothing was deleted and nothing will be: choose yourself what to remove from the stick "
                         "(fieldkit backup usb --check lists the Fieldkit copies), then run this again")}
    folder.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with open(src, "rb") as fi, open(part, "wb") as fo:
        for block in iter(lambda: fi.read(1 << 20), b""):
            fo.write(block)
        fo.flush()
        os.fsync(fo.fileno())
    part.replace(dest)
    problems = []
    if sha256(dest) != digest:
        problems.append("the copy's SHA-256 is not the original's")
    else:
        try:
            with zipfile.ZipFile(dest) as z:
                bad = z.testzip()
            if bad:
                problems.append(f"CRC error in {bad}")
        except zipfile.BadZipFile as e:
            problems.append(f"not a readable zip on the stick: {e}")
    if problems:
        dest.unlink()                         # this run's own file, written a moment ago; nothing else is touched
        return {"ok": False, "status": "failed", "problems": problems,
                "next": "the stick may be failing: try another stick, or run this again"}
    dest.with_name(dest.name + ".sha256").write_text(f"{digest}  {dest.name}\n", encoding="ascii")
    return _done(src, dest, digest, root, now)


def _done(src, dest, digest, root, now, already=False):
    when = (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    st = _read(_state_file())
    st.update({"last": when, "where": str(dest), "sha256": digest})
    _write(_state_file(), st)
    return {"ok": True, "status": "already-there" if already else "copied", "source": str(src), "copy": str(dest),
            "sha256": digest, "verified": "read back from the stick: SHA-256 equal, every CRC good",
            "on_stick": [c["name"] for c in copies(root)],
            "next": "done; nothing else on the stick was touched"}


def check(drive=None, stick=None, roots=None):
    root, why = pick_stick(drive, stick, roots)
    if not root:
        return {"ok": False, "status": "ask", "next": why}
    return {"ok": True, "status": "listed", "stick": str(root), "copies": copies(root),
            "next": "nothing was changed"}


def last_copy(roots=None):
    """The time of the last verified copy: the record, or a newer copy on a connected stick."""
    st = _read(_state_file())
    best = dt.datetime.strptime(st["last"], "%Y-%m-%d %H:%M:%S") if st.get("last") else None
    for r in (roots if roots is not None else removable_roots()):
        for c in copies(r)[:1]:
            when = dt.datetime.strptime(c["when"], "%Y-%m-%d %H:%M")
            if best is None or when > best:
                best = when
    return best


def remind(speak=True, now=None, roots=None, local=None, say=None):
    local = settings.local_settings() if local is None else local
    days_due = int((local.get("backup") or {}).get("usb_remind_days") or REMIND_DAYS)
    now = now or dt.datetime.now()
    when = last_copy(roots)
    days = None if when is None else max(0.0, (now - when).total_seconds() / 86400)
    due = days is None or days >= days_due
    text = ("No copy of the backup on a USB stick is on record. Plug in the stick and run: fieldkit backup usb"
            if days is None else
            f"The last copy on a USB stick is {int(days)} day(s) old. Plug in the stick and run: fieldkit backup usb")
    spoken = False
    if due and speak:
        st = _read(_state_file())
        if now.timestamp() - float(st.get("spoken", 0)) >= SPEAK_EVERY_H * 3600:
            if say is None:
                from ..buildh import herald
                say = lambda s: herald.say(s)
            say(text)
            st["spoken"] = now.timestamp()
            _write(_state_file(), st)
            spoken = True
    return {"ok": not due, "status": "due" if due else "fine", "last": when.strftime("%Y-%m-%d %H:%M") if when else None,
            "days": None if days is None else int(days), "due_after_days": days_due, "spoken": spoken,
            "next": text if due else "nothing to do"}


def lines(r):
    out = []
    if r.get("status") == "listed":
        out.append(f"stick: {r['stick']}")
        out += [f"  {c['when']}  {c['name']}  {c['size'] / 2 ** 20:,.1f} MB"
                f"{'' if c['has_checksum'] else '  (no .sha256 beside it)'}" for c in r["copies"]] or \
               [f"  no {FOLDER} folder yet"]
    elif r.get("ok") and r.get("copy"):
        out += [f"backup usb: {r['copy']} ({r['status']})", f"  {r['verified']}"]
        out += [f"  on the stick: {', '.join(r['on_stick'])}"]
    elif r.get("status") in ("due", "fine"):
        days = "" if r["days"] is None else f" ({r['days']} day(s) ago)"
        out.append(f"backup on a stick: {'last ' + r['last'] + days if r['last'] else 'never recorded'}")
    else:
        out.append(f"backup usb: {r.get('status', '').upper()}")
        out += [f"  {p}" for p in r.get("problems", [])]
    return out + [f"NEXT: {r['next']}"]
