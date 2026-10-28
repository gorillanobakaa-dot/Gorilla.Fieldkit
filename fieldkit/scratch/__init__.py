# Version: 1.0.0 · updated 26-10-26
"""The scratch root: one short folder for test copies, fixtures, transfers and probe output, deleted fast and safely.

    fieldkit scratch new [JOB] [--purpose TEXT] [--keep] [--print-path] [--json]   a marked job folder; prints its path
    fieldkit scratch clean JOB [JOB ...] [--claim] [--dry-run]                     delete these jobs (yours)
    fieldkit scratch clean --mine | --stale [--dry-run]                            every job of yours / every stale job
    fieldkit scratch report [--json] [--no-size]                                   jobs, sizes (fieldkit size), ages, owners
    fieldkit scratch doctor [--json]                                               root, marker rules, Defender exclusion
    fieldkit scratch owner-script [--out DIR]                                      the owner's one-UAC-click exclusion script
    fieldkit scratch bench [--files N] [--recycle]                                 time clean (and the Recycle Bin) on N files

WHY (owner, 2026-10-26: "Defender exclusion folder for scratch. Go.")
    Deleting about a million test files through the Recycle Bin with Defender scanning each one took half an hour
    ("imagine a petabyte"), and two transfer test copies (956,000 files, 8.3 GB) could not go to the bin at all: their
    paths inside it would have been longer than 259 characters. Scratch is one short root (C:\\fk-scratch on Windows,
    ~/.cache/fieldkit-scratch elsewhere; fieldkit.local.json "scratch": {"root": ..., "stale_hours": 24}) that Defender
    is told to skip (by the owner's own elevated script, never by a session) and that `clean` empties directly.

THE DELIBERATE EXCEPTION TO THE RECYCLE RULE
    Everywhere else a session's leftovers go to the Recycle Bin (fieldkit size recycle). Inside the scratch root, and
    only there, `clean` deletes permanently, because:
      - a job folder is deleted only when it carries a valid marker (.fk-scratch.json: schema, job = its folder name,
        root = this root) AND it is the caller's (same owning session process: pid + creation time), or it is stale,
        or it is named with --claim and its owner is gone or it is a hand-over job (kind link);
      - stale = the owning process is gone AND the job is older than stale_hours AND it was not made with --keep;
      - the job folder must be a direct child of the root; the root and the job folder must be real folders (no
        junction, symlink or other reparse point); anything else is refused, never touched;
      - inside a job, a junction, symlink or reparse point is removed as a link and never followed;
      - paths are \\\\?\\ long paths on Windows (no 260 limit), read-only files are made writable first;
      - many threads at once; the marker goes last, so a half-done delete is still a marked job that clean finishes;
      - every delete is one line in state/scratch/clean.jsonl (job, owner, rule, files, bytes, seconds, errors).
    The cost the owner accepted with the Defender exclusion: nothing inside the scratch root is scanned, so nothing is
    ever executed from there, and whatever crosses into it (transfers) is verified by its sha256.

A folder in the root without a marker is listed by report as UNMARKED and never deleted by this tool.
"""
import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core import longpath

MARKER = ".fk-scratch.json"
SCHEMA = 1
JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
CLAIMABLE_KINDS = ("link",)         # hand-over data: arrived for whoever asked for it, owned by no session
STALE_HOURS = 24
HERE = Path(__file__).resolve().parent
OWNER_SCRIPT = HERE / "owner" / "scratch-exclusion.ps1"


class Refused(ValueError):
    pass


# ---- where -------------------------------------------------------------------------------------------------------
def _local():
    from ..core import settings
    try:
        s = settings.local_settings().get("scratch") or {}
        return settings.expand(s, strict=False) if isinstance(s, dict) else {}
    except Exception:
        return {}


def default_root(platform=None):
    platform = platform or sys.platform
    if platform.startswith("win"):
        return Path(r"C:\fk-scratch")
    if platform == "darwin":
        return Path.home() / "Library" / "Caches" / "fieldkit-scratch"
    return Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "fieldkit-scratch"


def root():
    """FIELDKIT_SCRATCH_ROOT, else fieldkit.local.json scratch.root, else the platform default."""
    env = os.environ.get("FIELDKIT_SCRATCH_ROOT")
    return Path(env or _local().get("root") or default_root())


def stale_hours():
    try:
        return float(os.environ.get("FIELDKIT_SCRATCH_STALE_HOURS") or _local().get("stale_hours") or STALE_HOURS)
    except ValueError:
        return float(STALE_HOURS)


def state_dir():
    from ..core import settings
    d = Path(os.environ.get("FIELDKIT_SCRATCH_STATE") or (settings.state_root() / "state" / "scratch"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _norm(p):
    return os.path.normcase(os.path.abspath(str(p)))


long_path = longpath.extended         # the shared helpers (fieldkit/core/longpath.py): every path tool uses them
is_link = longpath.is_link


# ---- who ---------------------------------------------------------------------------------------------------------
def _ct(x):
    return round(float(x), 2)


def whoami(owner_pid=None):
    """The owner of jobs made now: the agent session this runs under (the nearest claude/opencode/gemini ancestor),
    else the process that called us (a shell, a script), or owner_pid (a long-lived tool owning its own jobs)."""
    import psutil
    proc = None
    if owner_pid:
        proc = psutil.Process(int(owner_pid))
    else:
        try:
            from ..hog.registry import session_process
            proc = session_process()
        except Exception:
            proc = None
        if proc is None:
            me = psutil.Process()
            proc = me.parent() or me
    return {"pid": proc.pid, "ctime": _ct(proc.create_time()), "name": proc.name(), "host": socket.gethostname()}


def alive(owner):
    """True when the owner's pid still runs AND is the same process (creation time), on this host."""
    import psutil
    if not owner or owner.get("host") not in (None, socket.gethostname()):
        return True                 # another machine's job: never ours to call dead
    try:
        return _ct(psutil.Process(int(owner["pid"])).create_time()) == _ct(owner["ctime"])
    except (psutil.Error, TypeError, ValueError, KeyError):
        return False


def same_owner(a, b):
    return bool(a and b) and a.get("pid") == b.get("pid") and _ct(a.get("ctime", 0)) == _ct(b.get("ctime", -1)) \
        and a.get("host") == b.get("host")


# ---- jobs --------------------------------------------------------------------------------------------------------
def _check_root(r):
    if not r.is_dir():
        raise Refused(f"the scratch root {r} does not exist")
    if is_link(r):
        raise Refused(f"the scratch root {r} is a link or junction: refused")


def new(job=None, purpose="", keep=False, kind="job", owner_pid=None, r=None, now=None):
    """Make ROOT/JOB with its marker. -> (path, marker). A JOB that exists is refused (use ensure)."""
    r = Path(r or root())
    r.mkdir(parents=True, exist_ok=True)
    _check_root(r)
    job = job or time.strftime("job-%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    if not JOB_RE.match(job) or job in (".", ".."):
        raise Refused(f"job name {job!r}: letters, digits, . _ - only, at most 80, not starting with . _ -")
    d = r / job
    try:
        d.mkdir()
    except FileExistsError:
        raise Refused(f"job {job} already exists in {r}") from None
    m = {"schema": SCHEMA, "job": job, "root": _norm(r), "kind": kind, "purpose": purpose[:300], "keep": bool(keep),
         "owner": whoami(owner_pid), "created": now or time.time(), "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    (d / MARKER).write_text(json.dumps(m, indent=1), encoding="utf-8")
    return d, m


def ensure(job, purpose="", keep=False, kind="job", owner_pid=None, r=None):
    """ROOT/JOB, made when missing; an existing one must carry a valid marker (else Refused). -> path."""
    r = Path(r or root())
    d = r / job
    if d.exists() or is_link(d):
        marker(d, r)
        return d
    try:
        return new(job, purpose, keep, kind, owner_pid, r)[0]
    except Refused:
        if d.is_dir():              # made by a parallel call a moment ago
            marker(d, r)
            return d
        raise


def marker(d, r=None):
    """-> the marker of job folder d, or raise Refused saying why it is not a deletable job."""
    r = Path(r or root())
    d = Path(d)
    _check_root(r)
    if _norm(d.parent) != _norm(r) or not JOB_RE.match(d.name):
        raise Refused(f"{d} is not a job folder directly inside the scratch root {r}")
    if is_link(d):
        raise Refused(f"{d} is a link or junction: refused, never followed")
    if not d.is_dir():
        raise Refused(f"{d} is not a folder")
    mp = d / MARKER
    if is_link(mp) or not mp.is_file():
        raise Refused(f"{d}: no marker {MARKER}: not made by fieldkit scratch, never deleted by it")
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(f"{d}: unreadable marker ({e})") from None
    if not isinstance(m, dict) or m.get("schema") != SCHEMA or m.get("job") != d.name or m.get("root") != _norm(r):
        raise Refused(f"{d}: the marker does not name this job in this root: refused")
    return m


def age_h(m, now=None):
    return max(0.0, ((now or time.time()) - float(m.get("created") or 0)) / 3600.0)


def classify(m, me, now=None, hours=None, alive_fn=alive):
    """-> mine | live | stale | kept | young (a job whose owner is gone but that is not old enough yet)."""
    if same_owner(m.get("owner"), me):
        return "mine"
    if alive_fn(m.get("owner")):
        return "live"
    if m.get("keep"):
        return "kept"
    return "stale" if age_h(m, now) >= (stale_hours() if hours is None else hours) else "young"


def may_delete(m, state, how):
    """The ONE rule. how: mine (--mine), stale (--stale), named (JOB), claim (JOB --claim). -> (ok, rule)."""
    if state == "mine":
        return True, "mine"
    if how == "stale" and state == "stale":
        return True, f"stale (owner gone, older than {stale_hours():g} h)"
    if how == "claim" and (state in ("stale", "young", "kept") or m.get("kind") in CLAIMABLE_KINDS):
        return True, f"claimed ({'owner gone' if state != 'live' else 'kind ' + str(m.get('kind'))})"
    return False, f"{state}: not yours" + (" (--claim a job whose owner is gone, or a hand-over job)"
                                          if how == "named" else "")


def jobs(r=None):
    """-> [(path, marker or None, why-not)] for every entry of the root."""
    r = Path(r or root())
    out = []
    if not r.is_dir():
        return out
    for e in sorted(os.scandir(r), key=lambda e: e.name.lower()):
        p = Path(e.path)
        try:
            out.append((p, marker(p, r), ""))
        except Refused as x:
            out.append((p, None, str(x)))
    return out


# ---- delete ------------------------------------------------------------------------------------------------------
def wipe(d, workers=None, keep_top=(MARKER,)):
    """Everything inside job folder d except its marker, by the platform's standard direct delete (longpath.remove_tree:
    \\\\?\\ DeleteFileW/RemoveDirectoryW on Windows, unlinkat via shutil.rmtree elsewhere), links never followed."""
    try:
        return longpath.remove_tree(d, keep_top=keep_top, workers=workers)
    except longpath.Refused as e:
        raise Refused(str(e)) from None


def delete_job(d, m, rule, r=None, log=True, workers=None):
    """Wipe the job, then its marker, then its folder; one line in state/scratch/clean.jsonl. -> record."""
    t0 = time.time()
    m2 = marker(d, r)                                   # checked again right before the first delete
    if m2 != m:
        raise Refused(f"{d}: the marker changed since it was read: refused")
    res = wipe(d, workers)
    if not res["errors"]:
        try:
            longpath._unlink(long_path(Path(d) / MARKER))
            os.rmdir(long_path(d))
        except OSError as e:
            res["errors"].append(f"{d}: {e}")
            res["error_count"] += 1
    rec ={"t": time.time(), "at": time.strftime("%Y-%m-%d %H:%M:%S"), "job": m["job"], "root": m["root"],
           "kind": m.get("kind"), "owner": m.get("owner"), "by": _safe_me(), "rule": rule,
           "files": res["files"], "dirs": res["dirs"], "links": res["links"], "bytes": res["bytes"],
           "seconds": round(time.time() - t0, 3), "errors": res["errors"][:10],
           "error_count": res["error_count"], "ok": not res["errors"]}
    if log:
        with open(state_dir() / "clean.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    return rec


def _safe_me():
    try:
        return whoami()
    except Exception:
        return None


def clean(names=(), mine=False, stale=False, claim=False, dry=False, r=None, me=None, now=None, alive_fn=alive):
    """-> [{job, ok, rule, ...}] one row per job considered. Nothing outside the root, nothing unmarked."""
    r = Path(r or root())
    me = me or whoami()
    rows = []
    if names:
        targets = []
        for n in names:
            p = Path(n)
            d = p if p.is_absolute() else r / n
            try:
                targets.append((d, marker(d, r)))
            except Refused as e:
                rows.append({"job": str(n), "ok": False, "rule": "refused", "why": str(e)})
        how = "claim" if claim else "named"
    else:
        targets = [(p, m) for p, m, _ in jobs(r) if m]
        how = "stale" if stale else "mine"
    for d, m in targets:
        state = classify(m, me, now, alive_fn=alive_fn)
        if not names and state != how:              # --mine takes only mine, --stale only stale
            continue
        ok, rule = may_delete(m, state, how)
        if not ok:
            rows.append({"job": m["job"], "ok": False, "rule": "refused", "why": rule})
            continue
        if dry:
            rows.append({"job": m["job"], "ok": True, "rule": rule, "dry_run": True})
            continue
        try:
            rec = delete_job(d, m, rule, r)
        except Refused as e:
            rows.append({"job": m["job"], "ok": False, "rule": "refused", "why": str(e)})
            continue
        rows.append(rec)
    return rows


# ---- sizes, report, preflight ------------------------------------------------------------------------------------
def sizes(paths):
    """{path: (bytes, files)} by fieldkit size (gorilla-size --json); paths it cannot measure are left out."""
    paths = [str(p) for p in paths]
    if not paths:
        return {}
    try:
        from ..desk import size as size_cli
        exe = size_cli.locate()
    except Exception:
        exe = None
    if not exe:
        return {}
    try:
        r = subprocess.run([str(exe), "--json", *paths], capture_output=True, text=True, timeout=600)
        data = json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    out = {}
    for x in data.get("roots", []):
        t = x.get("totals") or {}
        out[_norm(x.get("path", ""))] = (int(t.get("bytes", 0)), int(t.get("files", 0)))
    return out


def report(r=None, with_size=True, me=None, now=None):
    r = Path(r or root())
    me = me or _safe_me()
    rows = []
    found = jobs(r)
    sz = sizes([p for p, _m, _w in found]) if with_size else {}
    for p, m, why in found:
        b, f = sz.get(_norm(p), (None, None))
        if m is None:
            rows.append({"job": p.name, "state": "UNMARKED", "why": why, "bytes": b, "files": f})
            continue
        o = m.get("owner") or {}
        rows.append({"job": m["job"], "state": classify(m, me, now), "kind": m.get("kind"), "keep": m.get("keep"),
                     "age_h": round(age_h(m, now), 1), "owner": f"{o.get('name')} {o.get('pid')}",
                     "purpose": m.get("purpose", ""), "bytes": b, "files": f})
    return rows


def preflight_check(ctx=None, r=None):
    """The run guard's check (fieldkit/core/preflight.py STEPS): stale scratch jobs reported, never a stop."""
    rows = [x for x in report(r, with_size=False) if x["state"] in ("stale", "UNMARKED")]
    if not rows:
        return "ok", f"scratch: {root()} clean"
    st = [x["job"] for x in rows if x["state"] == "stale"]
    um = [x["job"] for x in rows if x["state"] == "UNMARKED"]
    parts = []
    if st:
        parts.append(f"{len(st)} stale job(s) ({', '.join(st[:3])}): fieldkit scratch clean --stale")
    if um:
        parts.append(f"{len(um)} unmarked folder(s) ({', '.join(um[:3])}): not ours to delete, look at them")
    return "warn", "scratch: " + "; ".join(parts)


def hog_findings():
    """Stale scratch jobs as hog findings (reported, no action: the hog never deletes files)."""
    sev, line = preflight_check()
    if sev == "ok":
        return []
    return [{"check": "scratch", "severity": "warn", "line": line, "numbers": {}, "action": None}]


# ---- Defender exclusion ------------------------------------------------------------------------------------------
def exclusion(r=None, run=subprocess.run):
    """-> {present: True|False|None, how, detail}. Read-only: Get-MpPreference (lists exclusions only for an
    administrator; otherwise None), then the proof the owner's script wrote (state/scratch/exclusion.json)."""
    r = _norm(r or root())
    if os.name != "nt":
        return {"present": None, "how": "not Windows", "detail": "no Defender here: no exclusion needed"}
    proof = None
    try:
        proof = json.loads((state_dir() / "exclusion.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        pass
    try:
        p = run(["powershell", "-NoProfile", "-Command",
                 "(Get-MpPreference).ExclusionPath | ForEach-Object { $_ }"],
                capture_output=True, text=True, timeout=60)
        out = [x.strip() for x in (p.stdout or "").splitlines() if x.strip()]
    except (OSError, subprocess.SubprocessError) as e:
        out, p = [], None
        err = str(e)
    else:
        err = (p.stderr or "").strip()
    if out and not any("must be an administrator" in x.lower() for x in out):
        here = any(_norm(x) == r for x in out)
        return {"present": here, "how": "Get-MpPreference", "detail": f"{len(out)} exclusion path(s) listed"}
    if proof and _norm(proof.get("root", "")) == r:
        return {"present": bool(proof.get("present")), "how": "owner script proof",
                "detail": f"state/scratch/exclusion.json {proof.get('at')} (live list needs admin)"}
    return {"present": None, "how": "unknown", "detail": "Get-MpPreference lists exclusions only for an administrator "
            "and the owner's script has written no proof yet" + (f" ({err[:80]})" if err else "")}


def write_owner_script(out=None):
    """Copy the owner's elevated script into state/proposed/scratch-exclusion-<date>/ with a README. -> folder."""
    from ..core import settings
    out = Path(out or (settings.state_root() / "state" / "proposed" / "scratch-exclusion-2026-10-26"))
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OWNER_SCRIPT, out / OWNER_SCRIPT.name)
    q = lambda s: "'" + str(s).replace("'", "''") + "'"          # a PowerShell single-quoted literal
    for action in ("apply", "verify", "undo"):
        (out / f"{action}.ps1").write_text(
            f"# one UAC click: elevate.py run this file. It runs scratch-exclusion.ps1 -Action {action}.\r\n"
            f"& (Join-Path $PSScriptRoot 'scratch-exclusion.ps1') -Action {action} -Root {q(root())} "
            f"-Proof {q(state_dir() / 'exclusion.json')}\r\n", encoding="utf-8")
    return out


# ---- bench -------------------------------------------------------------------------------------------------------
def make_fixture(d, files, per_dir=1000, size=64, workers=16):
    d = Path(d)
    blob = b"x" * size

    def mk(i):
        sub = d / f"d{i:05d}"
        os.makedirs(long_path(sub), exist_ok=True)
        n = min(per_dir, files - i * per_dir)
        for k in range(n):
            with open(long_path(sub / f"f{k:05d}.txt"), "wb") as f:
                f.write(blob)
        return n
    with ThreadPoolExecutor(workers) as pool:
        return sum(pool.map(mk, range((files + per_dir - 1) // per_dir)))


def bench(files=100_000, recycle=False, say=print):
    """Time clean on N small files in a fresh job; with recycle, the same fixture through fieldkit size recycle."""
    out = {"files": files, "root": str(root()), "exclusion": exclusion().get("present")}
    d, m = new(purpose=f"scratch bench: {files} files, deleted by this bench")
    t = time.time()
    out["made"] = make_fixture(d, files)
    out["make_s"] = round(time.time() - t, 1)
    say(f"  fixture: {out['made']} files in {out['make_s']} s ({d})")
    rec = delete_job(d, m, "mine (bench)")
    out["clean_s"], out["clean_ok"] = rec["seconds"], rec["ok"]
    say(f"  clean: {rec['files']} files, {rec['dirs']} folders in {rec['seconds']} s (ok={rec['ok']})")
    if recycle:
        from ..desk import size as size_cli
        d2, _m2 = new(purpose=f"scratch bench: {files} files sent to the Recycle Bin by this bench")
        make_fixture(d2, files)
        exe = str(size_cli.locate())
        t = time.time()
        # over 1,000 files gorilla-size holds the recycle for a plan (exit 4); the bench applies that plan at once
        r1 = subprocess.run([exe, "recycle", "--whole-folder", "--plan", str(d2)], capture_output=True, text=True)
        plan = re.search(r"^Plan: (.+)$", r1.stdout + r1.stderr, re.M)
        rc = r1.returncode
        if plan and rc == 4:
            rc = subprocess.run([exe, "apply", plan.group(1).strip()], capture_output=True, text=True).returncode
        out["recycle_s"], out["recycle_rc"] = round(time.time() - t, 1), rc
        out["recycle_left"] = d2.exists() and any(d2.iterdir())
        say(f"  recycle (fieldkit size recycle --whole-folder, then apply its plan): exit {rc} in {out['recycle_s']} s")
    return out


# ---- the command -------------------------------------------------------------------------------------------------
def _gb(b):
    return "?" if b is None else f"{b / 2**30:.2f} GB"


def doctor(say=print):
    """-> (exit, rows). 0 OK, 1 something to look at."""
    rows = []
    r = root()
    rows.append(("root", r.is_dir() and not is_link(r), f"{r}" + ("" if r.is_dir() else " (missing: scratch new makes "
                                                                                    "it)") + (" LINK" if is_link(r) else "")))
    rows.append(("short", len(str(r)) <= 20, f"{len(str(r))} characters (deep job trees need a short root)"))
    if r.is_dir():
        try:
            probe = r / f".probe-{os.getpid()}"
            probe.write_bytes(b"")
            probe.unlink()
            rows.append(("writable", True, "yes"))
        except OSError as e:
            rows.append(("writable", False, str(e)))
    try:
        import psutil  # noqa: F401
        rows.append(("psutil", True, "owner checks (pid + creation time)"))
    except ImportError:
        rows.append(("psutil", False, "pip install psutil"))
    try:
        from ..desk import size as size_cli
        exe = size_cli.locate()
        rows.append(("fieldkit size", bool(exe), str(exe) if exe else "missing: fieldkit size doctor --install"))
    except Exception as e:
        rows.append(("fieldkit size", False, str(e)))
    ex = exclusion(r)
    rows.append(("defender exclusion", ex["present"] is not False,
                 {True: "PRESENT", False: "NOT PRESENT", None: "UNKNOWN"}[ex["present"]] + f" ({ex['how']}: "
                 f"{ex['detail']})" + ("" if ex["present"] or os.name != "nt" else
                                        "; the owner's script: fieldkit scratch owner-script")))
    rep = report(r, with_size=False)
    st = [x["job"] for x in rep if x["state"] == "stale"]
    um = [x["job"] for x in rep if x["state"] == "UNMARKED"]
    rows.append(("jobs", not st and not um, f"{len(rep)} in the root, {len(st)} stale, {len(um)} unmarked"))
    for name, ok, line in rows:
        say(f"  {'OK  ' if ok else 'WARN'} {name:<19} {line}")
    bad = [x for x in rows if not x[1]]
    say(f"VERDICT  scratch: {'OK' if not bad else f'{len(bad)} to look at'}")
    return (0 if not bad else 1), rows


def main(argv=None, prog="fieldkit scratch"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="a marked job folder")
    p.add_argument("job", nargs="?")
    p.add_argument("--purpose", default="")
    p.add_argument("--keep", action="store_true", help="never stale: only you or --claim delete it")
    p.add_argument("--print-path", action="store_true", help="print only the path (for scripts)")
    p.add_argument("--json", action="store_true")
    c = sub.add_parser("clean", help="delete your jobs, stale jobs, or named jobs (permanently, inside the root only)")
    c.add_argument("jobs", nargs="*")
    c.add_argument("--mine", action="store_true")
    c.add_argument("--stale", action="store_true")
    c.add_argument("--claim", action="store_true", help="named jobs whose owner is gone, or hand-over (link) jobs")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--json", action="store_true")
    rp = sub.add_parser("report", help="jobs, sizes, ages, owners")
    rp.add_argument("--json", action="store_true")
    rp.add_argument("--no-size", action="store_true")
    d = sub.add_parser("doctor", help="root, fieldkit size, the Defender exclusion, stale jobs")
    d.add_argument("--json", action="store_true")
    o = sub.add_parser("owner-script", help="write the owner's one-UAC-click exclusion script")
    o.add_argument("--out", default=None)
    b = sub.add_parser("bench", help="time clean (and the Recycle Bin) on N small files")
    b.add_argument("--files", type=int, default=100_000)
    b.add_argument("--recycle", action="store_true")
    b.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        return _run(a)
    except Refused as e:
        print(f"REFUSED  {e}")
        return 2


def _run(a):
    if a.cmd == "new":
        d, m = new(a.job, a.purpose, a.keep)
        if a.print_path:
            print(d)
        elif a.json:
            print(json.dumps({"path": str(d), **m}, indent=1))
        else:
            print(f"SCRATCH NEW  {d}  (owner {m['owner']['name']} {m['owner']['pid']}"
                  f"{', keep' if m['keep'] else ''}; delete with: fieldkit scratch clean {m['job']})")
        return 0
    if a.cmd == "clean":
        if bool(a.jobs) + a.mine + a.stale != 1:
            print("clean: name JOB(s), or --mine, or --stale (exactly one of them)")
            return 2
        rows = clean(a.jobs, mine=a.mine, stale=a.stale, claim=a.claim, dry=a.dry_run)
        if a.json:
            print(json.dumps(rows, indent=1))
        for x in rows if not a.json else []:
            if not x["ok"] and x.get("rule") == "refused":
                print(f"  REFUSED  {x['job']}: {x['why']}")
            elif x.get("dry_run"):
                print(f"  WOULD DELETE  {x['job']} ({x['rule']})")
            else:
                print(f"  {'DELETED' if x['ok'] else 'PARTLY'}  {x['job']}: {x['files']} files, {x['dirs']} folders, "
                      f"{x['links']} links, {_gb(x['bytes'])} in {x['seconds']} s ({x['rule']})"
                      + ("" if x["ok"] else f"; {x['error_count']} error(s): {x['errors'][:2]}"))
        bad = [x for x in rows if not x["ok"]]
        if not a.json:
            print(f"VERDICT  {len(rows) - len(bad)} of {len(rows)} job(s) "
                  f"{'would go' if a.dry_run else 'deleted'}" if rows else "VERDICT  nothing to clean")
        return 3 if bad else 0
    if a.cmd == "report":
        rows = report(with_size=not a.no_size)
        if a.json:
            print(json.dumps(rows, indent=1))
            return 0
        print(f"SCRATCH  {root()}  ({len(rows)} entr{'y' if len(rows) == 1 else 'ies'}; stale after "
              f"{stale_hours():g} h)")
        for x in rows:
            if x["state"] == "UNMARKED":
                print(f"  {x['job']:<34} UNMARKED  {_gb(x['bytes']):>9}  never deleted by this tool")
                continue
            print(f"  {x['job']:<34} {x['state']:<6} {_gb(x['bytes']):>9} {x['files'] if x['files'] is not None else '?':>9}"
                  f" files {x['age_h']:>7} h  {x['owner']:<22} {x['kind']}{' keep' if x['keep'] else ''}  "
                  f"{x['purpose'][:60]}")
        return 0
    if a.cmd == "doctor":
        rc, rows = doctor(say=(lambda *_: None) if a.json else print)
        if a.json:
            print(json.dumps([{"check": n, "ok": ok, "detail": l} for n, ok, l in rows], indent=1))
        return rc
    if a.cmd == "owner-script":
        out = write_owner_script(a.out)
        print(f"OWNER SCRIPT  {out}\n  one UAC click (PowerShell, as you; the UAC prompt is the only thing to answer):\n"
              f"  cd $HOME; & \"$HOME\\scoop\\apps\\python312\\current\\python.exe\" "
              f"\"$HOME\\Documents\\Scripts\\Fieldkit\\local\\kits\\Elevate\\elevate.py\" run \"{out}\\apply.ps1\"")
        return 0
    if a.cmd == "bench":
        res = bench(a.files, a.recycle)
        print(json.dumps(res, indent=1) if a.json else
              f"VERDICT  {res['files']} files: clean {res['clean_s']} s"
              + (f", Recycle Bin {res['recycle_s']} s" if "recycle_s" in res else "")
              + f" (Defender exclusion: {res['exclusion']})")
        return 0 if res["clean_ok"] else 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
