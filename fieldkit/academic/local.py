"""Assignment folders sitting in OneDrive, Dropbox or Google Drive, copied onto this computer.

    fieldkit academic local [--from FOLDER]            look: which assignments are in a cloud folder (changes nothing)
    fieldkit academic local --apply [--from FOLDER]    copy them to the local work folder, prove the copy, then mark
                                                       the cloud original as moved

Why: Windows' "Known Folder Move" makes Documents mean OneDrive\\Documents. The path looks local and is not; when the
internet went down, a student's work was unreachable for three hours (the academic harness this generalises,
move_work_local.py, 2026-10-10).

An assignment folder is one holding EXAM_CONFIG.md. Nothing is deleted, here or in the cloud: every file is copied
and read back (size and SHA-256), and only when the whole folder is proven is the cloud original renamed to
"<name> (moved to this computer <date>)", so nobody goes on working in it. A destination that already exists is
never written over: that folder is skipped and named. No Windows or OneDrive setting is changed; turning Known
Folder Move off is a person's step, in the OneDrive app, and the output says where.
"""
import datetime as _dt
import hashlib
import os
import shutil

from .workspace import HOME, cloud_service, documents_folder, local_work_root

MARKERS = ("EXAM_CONFIG.md",)
SKIP_DIRS = {"__pycache__", ".git", "node_modules", "AppData", "Windows"}
MAX_DEPTH = 6
HOW_TO_STOP = ("so it does not happen again, stop OneDrive backing up Documents: the blue cloud by the clock > "
               "Settings (gear) > Sync and backup > Manage backup > turn off Documents, Desktop and Pictures")


def search_roots(extra=None):
    if extra:
        return [extra] if os.path.isdir(extra) else []
    roots = [documents_folder(), os.path.join(HOME, "Desktop")]
    for name in os.listdir(HOME) if os.path.isdir(HOME) else []:
        full = os.path.join(HOME, name)
        if os.path.isdir(full) and cloud_service(full):
            roots.append(full)
    seen, out = set(), []
    for r in roots:
        key = os.path.normcase(os.path.abspath(r))
        if key not in seen and os.path.isdir(r):
            seen.add(key)
            out.append(r)
    return out


def find_assignments(root, max_depth=MAX_DEPTH):
    found = []
    root = os.path.abspath(root)
    base = root.rstrip(os.sep).count(os.sep)
    for current, dirs, files in os.walk(root):
        if current.rstrip(os.sep).count(os.sep) - base >= max_depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        if any(m in files for m in MARKERS):
            found.append(current)
            dirs[:] = []
    return found


def plan(extra=None, dest_root=None):
    dest_root = dest_root or local_work_root()
    moves, seen = [], set()
    for root in search_roots(extra):
        for folder in find_assignments(root):
            svc = cloud_service(folder)
            key = os.path.normcase(os.path.realpath(folder))
            if not svc or key in seen:            # Documents inside OneDrive is found from both roots
                continue
            seen.add(key)
            target = os.path.join(dest_root, os.path.basename(folder))
            moves.append({"from": folder, "to": target, "service": svc,
                          "skip": "already exists on this computer: compare by hand" if os.path.exists(target) else ""})
    return moves


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _copy_proven(src, dst):
    """Copy a folder and read every file back. -> list of problems (empty when proven)."""
    part = dst + ".part"
    if os.path.exists(part):
        shutil.rmtree(part)                       # this tool's own unfinished copy from an earlier run
    shutil.copytree(src, part)
    problems = []
    for d, _, files in os.walk(src):
        for f in files:
            a = os.path.join(d, f)
            b = os.path.join(part, os.path.relpath(a, src))
            if not os.path.isfile(b) or os.path.getsize(a) != os.path.getsize(b) or _sha(a) != _sha(b):
                problems.append(os.path.relpath(a, src))
    if problems:
        return problems
    os.replace(part, dst)
    return []


def apply(moves, today=None):
    stamp = (today or _dt.date.today()).isoformat()
    done, skipped = [], []
    for m in moves:
        if m["skip"]:
            skipped.append({**m})
            continue
        os.makedirs(os.path.dirname(m["to"]), exist_ok=True)
        try:
            bad = _copy_proven(m["from"], m["to"])
        except OSError as e:
            bad = [str(e)]
        if bad:
            skipped.append({**m, "skip": "the copy did not read back the same, nothing moved: "
                                         + ", ".join(bad[:3])})
            continue
        old = f"{m['from']} (moved to this computer {stamp})"
        try:
            os.rename(m["from"], old)
        except OSError as e:
            old = None
            m = {**m, "note": f"copied and proven; the cloud folder could not be renamed ({e}): do not work in it"}
        done.append({**m, "cloud_copy_now": old})
    return done, skipped


def run(extra=None, do=False, dest_root=None, today=None):
    moves = plan(extra, dest_root)
    docs = documents_folder()
    out = {"documents": docs, "documents_in": cloud_service(docs), "work_root": dest_root or local_work_root(),
           "in_cloud": moves}
    if not moves:
        return {**out, "ok": True, "status": "all-local", "next": "nothing to do"}
    if not do:
        return {**out, "ok": False, "status": "found",
                "next": "fieldkit academic local --apply   (copies, proves the copy, renames the cloud original)"}
    done, skipped = apply(moves, today)
    return {**out, "ok": not skipped, "status": "copied", "done": done, "skipped": skipped, "next": HOW_TO_STOP}


def lines(r):
    out = [f"Documents: {r['documents']}" + (f"  (in {r['documents_in']})" if r["documents_in"] else ""),
           f"work folder: {r['work_root']}"]
    if r["status"] == "all-local":
        out.append("no assignment is in a cloud folder")
    for m in r.get("done") or []:
        out.append(f"COPIED  {m['from']} -> {m['to']}")
        out.append(f"        the cloud folder is now: {m['cloud_copy_now'] or m.get('note')}")
    for m in r.get("skipped") or []:
        out.append(f"SKIPPED {m['from']}: {m['skip']}")
    if r["status"] == "found":
        for m in r["in_cloud"]:
            out.append(f"IN {m['service'].upper()}: {m['from']}\n   -> {m['to']}" + (f"  ({m['skip']})" if m["skip"] else ""))
    return out
