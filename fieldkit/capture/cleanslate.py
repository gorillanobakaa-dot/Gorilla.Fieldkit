"""P40 scratch cleanup and the capture start gate.

    fieldkit capture clean (--all | --keep-last AGE) [--apply]
    fieldkit capture start --project NAME --lesson WHY [--workspace DIR --agents LIST] [--dry-run]

Before capture switches on, a digest check runs:
Start checks the previous capture ledger, archives and verifies it, recycles eligible scratch and starts a new lesson.
Standalone clean uses the same scratch gate. Only selected, disposed scratch may enter the platform Trash.
An owner at a real terminal can pass an open ledger with --force-undigested. The archive and scratch checks still apply.
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from . import lesson as lessons, retention, store as st

HERE = Path(__file__).resolve().parent
DISPOSITIONS = "dispositions.jsonl"
SHOW = 10
AGENT_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SESSION", "GEMINI_CLI", "FIELDKIT_AGENT")


def _policy():
    return yaml.safe_load((HERE / "keep.yaml").read_text(encoding="utf-8")) or {}


def keep_list():
    return [str(x).lower() for x in _policy().get("keep", [])]


def scratch_roots(*, include_missing=False):
    """Expand the platform scratch policy, optionally keeping unmatched entries."""
    import glob
    out = []
    for x in _policy().get("scratch_roots", []):
        if os.name != "nt" and Path(str(x)).drive == "" and str(x)[1:3] == ":/":
            continue
        if os.name == "nt" and str(x).startswith("/tmp/"):
            continue
        expanded = _expand(x)
        matches = sorted(glob.glob(str(expanded)))
        for g in matches or ([expanded] if include_missing else []):
            path = Path(g).absolute()
            if (include_missing or os.path.lexists(path)) and path not in out:
                out.append(path)
    for path in (_crew_root(), *_temp_item_roots()):
        if (include_missing or os.path.lexists(path)) and path not in out:
            out.append(path)
    return out


def _crew_root():
    return _expand(_policy()["kiro_crew_root"])


def _temp_item_roots():
    rule = _policy()["temp_item_roots"].get(sys.platform)
    return {_expand(rule["path"]): re.compile(rule["pattern"])} if rule else {}


def _protected(workspace=None):
    paths = [_expand(p) for p in _policy().get("protected_roots", [])]
    if codex_home := os.getenv("CODEX_HOME"):
        paths.append(Path(codex_home).absolute())
    live = st.capture_dir().absolute()
    paths.extend((live, live.parent / "capture-archive", HERE.parents[1]))
    if workspace:
        paths.append(workspace)
    return paths


def _expand(path):
    return Path(os.path.expandvars(os.path.expanduser(path))).absolute()


def scan_items(roots=None, before=None, keep=None, protected=None, *, visit=None):
    """Read scratch metadata and produce non-overlapping recycle candidates."""
    keep = keep_list() if keep is None else keep
    protected = _protected() if protected is None else protected
    roots = sorted({Path(p).absolute() for p in (scratch_roots() if roots is None else roots)})
    temp_items = _temp_item_roots()
    crew_root = _crew_root()
    unfiltered = set(roots) - temp_items.keys()
    protected = [*protected, *retention.active_paths(roots)]
    mounts = retention.mount_paths()
    rows = []
    for root in roots:
        if any(retention.within(root, p) for p in (*protected, *(p for p in unfiltered if p != root and len(p.parts) < len(root.parts)))):
            continue
        try:
            device = retention.check_root(root).st_dev
            if str(root) in mounts or os.path.ismount(root):
                raise OSError(f"scratch root is a mount: {root}")
            pattern = temp_items.get(root)
            crew = root == crew_root
            def kept(path, base):
                return is_kept(path, keep, base, crew=crew)

            for child in sorted(root.iterdir()):
                if visit is not None:
                    visit()
                if pattern and not (pattern.fullmatch(child.name) and stat.S_ISDIR(child.lstat().st_mode)):
                    continue
                snapshot = retention.inspect(child, root, before, kept, protected, device, mounts, crew=crew, visit=visit)
                for row in retention.rows_to_check(snapshot, whole=crew or pattern is not None):
                    row["top"] = str(child)
                    rows.append(row)
        except OSError as exc:
            rows.append({"root": str(root), "path": str(root), "name": root.name, "files": 0,
                         "status": "unreadable", "reason": str(exc)})
    return rows


def counts(rows):
    """-> {root: {items, files}}: the read-only count shown first."""
    out = {}
    for r in rows:
        c = out.setdefault(r["root"], {"items": 0, "files": 0})
        if not r.get("top") or r["path"] == r["top"]:
            c["items"] += 1
            c["files"] += r.get("files", 1)
    return out


def is_kept(path, keep=None, root=None, *, crew=False):
    """The keep list is matched below the scratch root only: /AppData/Local/ above it is not the owner's local/ folder."""
    p = str(path).replace("\\", "/").lower()
    r = str(root or "").replace("\\", "/").lower().rstrip("/")
    if r and p.startswith(r + "/"):
        p = p[len(r):]
    p = "/" + p.strip("/") + "/"
    if crew and ("/.quarantine/" in p or "relay" in p):
        return True
    return any(k in p for k in (keep if keep is not None else keep_list()))


def real_terminal():
    """The owner-only check (like `decide`): a person at a terminal, not an agent's pipe or tool call."""
    return sys.stdin.isatty() and sys.stdout.isatty() and not any(os.environ.get(k) for k in AGENT_ENV)


def dispositions():
    p = st.capture_dir() / DISPOSITIONS
    out = {}
    for history in lessons.archived_history():
        out.update(history.get("dispositions", {}))
    if os.path.lexists(p):
        if retention.link(p.lstat()) or not p.is_file():
            raise OSError("unsafe disposition file")
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                if d.get("path") and d.get("because"):
                    out[os.path.normcase(str(Path(d["path"])))] = d
            except ValueError:
                pass
    return out


def _disposed(path, disp):
    import fnmatch
    n = os.path.normcase(str(Path(path)))
    return n in disp or any(fnmatch.fnmatch(n, k) for k in disp if any(c in k for c in "*?["))


def record_disposition(path, because):
    if not (because or "").strip():
        raise ValueError("a disposition needs a reason: not worth keeping, because ...")
    p = st.capture_dir() / DISPOSITIONS
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"path": str(path), "because": because.strip(), "time": _now()}, ensure_ascii=False) + "\n")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check(store=None, rows=None, disp=None, keep=None, before=None, protected=None, check_store=True):
    """-> {digested, store: {outstanding, lost, unaccounted, stuck}, scripts: [rows to digest], wipeable: [rows], next}.
    rows: scratch items (injected by tests). An item is digested when it has a disposition (exact path, or a glob such as
    <root>/link-*) with a reason; digested items are the wipeable ones. Nothing outside scratch_roots is ever looked at."""
    keep = keep_list() if keep is None else keep
    open_store = {}
    if check_store and (store is not None or st.capture_dir().is_dir()):
        if store is None:
            for name in ("journal.jsonl", "index.json", "blobs"):
                p = st.capture_dir() / name
                if os.path.lexists(p) and retention.link(p.lstat()):
                    raise OSError(f"unsafe capture store path: {name}")
        led = (store or st.Store()).ledger()
        open_store = {k: led[k] for k in ("outstanding", "lost", "unaccounted", "stuck") if led[k]}
    if rows is None:
        rows = scan_items(before=before, keep=keep, protected=protected)
    disp = dispositions() if disp is None else disp
    undigested, wipeable, retained, errors = [], [], [], []
    for r in rows:
        if r["status"] == "unreadable":
            errors.append(r)
        elif r.get("reason") or is_kept(r["path"], keep, r.get("root")) or r["status"] in ("ok", "stale", "untested"):
            retained.append({**r, "reason": r.get("reason") or "protected or carded"})
        elif any(c in r["path"] for c in "*?"):
            errors.append({**r, "reason": "path would be interpreted as a recycle pattern"})
        elif _disposed(r["path"], disp) or (r.get("top") and _disposed(r["top"], disp)):
            wipeable.append(r)
        else:
            undigested.append(r)
    ok = not open_store and not undigested and not errors
    if errors:
        nxt = "resolve the unreadable or unsafe scratch paths, then preview again"
    elif open_store:
        nxt = "inspect with fieldkit capture ledger --closed; resolve the queue separately with fieldkit capture classify"
    elif undigested:
        nxt = ("adopt what is worth keeping (fieldkit adopt ...), then record why the rest goes: "
               "fieldkit capture start --dispose 'ROOT/link-*' --because WHY")
    else:
        nxt = "nothing to digest"
    return {"digested": ok, "counts": counts(rows), "store": open_store, "scripts": undigested, "wipeable": wipeable,
            "retained": retained, "errors": errors, "cutoff_ns": before, "next": nxt}


def _archive_files(src):
    root = retention.check_root(src)
    mounts = retention.mount_paths()
    paths = []
    for directory, children, names in os.walk(src, onerror=lambda exc: _raise(exc)):
        for name in sorted(children + names):
            path = Path(directory) / name
            info = path.lstat()
            if (retention.link(info) or info.st_dev != root.st_dev or str(path) in mounts or os.path.ismount(path)
                    or not (path.is_file() or path.is_dir()) or is_kept(path, root=src)):
                raise OSError(f"unsafe archive member: {path}")
            paths.append((path.relative_to(src).as_posix(), path, info))
    return sorted(paths)


def _raise(exc):
    raise exc


def _archive_snapshot(files):
    return [(name, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            for name, _, info in files]


def verify_archive(bundle, chain, head, directories):
    expected = iter(chain)
    prev = st.GENESIS
    seen = set()
    with tarfile.open(bundle, "r:gz") as archived:
        for member in archived:
            if member.isdir() and member.name in directories and member.name not in seen:
                seen.add(member.name)
                continue
            row = next(expected, None)
            if not member.isfile() or row is None or member.name != f"capture/{row['file']}":
                raise OSError("capture archive has unexpected entries")
            with archived.extractfile(member) as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            prev = st.sha(prev + row["file"] + digest)
            if digest != row["sha256"] or prev != row["chain"]:
                raise OSError("capture archive member failed verification")
    if prev != head or next(expected, None) is not None or seen != directories:
        raise OSError("capture archive is incomplete")


def archive(project, store_dir=None, out_dir=None, history=None):
    """Tar the capture store into a bundle with a hash-chained manifest (each file's hash includes the one before).
    -> {path, sha256, files, head}. The raw record stays; only the live copy is recycled afterwards."""
    src = Path(store_dir or st.capture_dir()).absolute()
    if not src.exists():
        return None
    out_dir = Path(out_dir or src.parent / "capture-archive")
    out_dir.mkdir(parents=True, exist_ok=True)
    retention.check_root(out_dir.absolute())
    stem = f"capture-{uuid4().hex}"
    bundle = out_dir / f"{stem}.tar.gz"
    manifest = out_dir / f"{stem}.manifest.json"
    prev, chain = st.GENESIS, []
    directories = {"capture"}
    files = _archive_files(src)
    with tarfile.open(bundle, "x:gz") as tar:
        tar.add(src, arcname="capture", recursive=False)
        for name, path, _ in files:
            if path.is_dir():
                directories.add(f"capture/{name}")
            else:
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                prev = st.sha(prev + name + digest)
                chain.append({"file": name, "sha256": digest, "chain": prev})
            tar.add(path, arcname=f"capture/{name}", recursive=False)
    verify_archive(bundle, chain, prev, directories)
    if _archive_snapshot(files) != _archive_snapshot(_archive_files(src)):
        raise OSError("capture store changed during archive verification")
    with bundle.open("rb+") as stream:
        os.fsync(stream.fileno())
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    data = {"project": project, "time": _now(), "head": prev, "files": chain,
            "sha256": digest, "history": history or {}}
    temp = manifest.with_suffix(".tmp")
    with temp.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(manifest)
    return {"path": str(bundle), "manifest": str(manifest), "sha256": digest, "files": len(chain), "head": prev}


def recycle(path, run=subprocess.run):
    r = run([sys.executable, "-m", "fieldkit", "size", "recycle", str(path), "--whole-folder", "--json",
             "--plan-files", "100000000", "--plan-bytes", "100T"],
            capture_output=True, text=True)
    return {"ok": r.returncode == 0 and not os.path.lexists(path), "exit": r.returncode,
            "output": r.stdout.strip(), "error": r.stderr.strip()}


def _recycle_one(path, recycle_fn):
    if any(c in str(path) for c in "*?"):
        return {"ok": False, "error": "path would be interpreted as a recycle pattern"}
    try:
        result = recycle_fn(path)
        return result if isinstance(result, dict) else {"ok": bool(result)}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}


def _unchanged(row, before, keep, protected, roots, mounts, temp_items, crew_root):
    if "identity" not in row:
        return Path(row["path"]).exists()
    root, top, path = (Path(row[key]) for key in ("root", "top", "path"))
    if root not in roots or str(root) in mounts or top.parent != root or not path.is_relative_to(top):
        return False
    pattern = temp_items.get(root)
    crew = root == crew_root
    if (pattern or crew) and path != top:
        return False
    if pattern and not pattern.fullmatch(path.name):
        return False
    def kept(candidate, base):
        return is_kept(candidate, keep, base, crew=crew)

    try:
        device = retention.check_root(root).st_dev
        for parent in reversed(path.parents):
            if not parent.is_relative_to(top):
                continue
            info = parent.lstat()
            if (retention.link(info) or info.st_dev != device or str(parent) in mounts or os.path.ismount(parent) or kept(parent, root)
                    or any(retention.within(parent, p) for p in protected)):
                return False
            with os.scandir(parent) as children:
                if retention.protected_directory(parent, root, (p.name for p in children)):
                    return False
        candidate = retention.inspect(path, root, before, kept, protected, device, mounts, crew=crew)
        return candidate["identity"] == row["identity"] and not candidate["reason"]
    except OSError:
        return False


def _report_path(out):
    root = st.capture_dir().absolute().parent / "capture-maintenance"
    root.mkdir(parents=True, exist_ok=True)
    retention.check_root(root)
    path = root / f"cleanup-{uuid4().hex}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(out, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def _apply(out, keep, protected, recycle_fn):
    failed = None
    roots = scratch_roots()
    protected = [*protected, *retention.active_paths(roots)]
    mounts = retention.mount_paths()
    temp_items = _temp_item_roots()
    crew_root = _crew_root()
    for row in out["check"]["wipeable"]:
        if not _unchanged(row, out["check"]["cutoff_ns"], keep, protected, roots, mounts, temp_items, crew_root):
            failed = {"path": row["path"], "error": "scratch item changed since inspection"}
            break
        receipt = _recycle_one(Path(row["path"]), recycle_fn)
        if not receipt["ok"]:
            failed = {"path": row["path"], **receipt}
            break
        out["recycled"].append(row["path"])
    if failed:
        out.update(refused=f"could not recycle {failed['path']}", failure=failed,
                   next="inspect with fieldkit size trash, then preview again")
        out["recovery_report"] = str(_report_path(out))
    return out


def _retire_sessions(out, live, recycle_fn):
    from . import session_history

    before = out["footprint"]["cutoff_ns"]
    active = set()
    for candidate in out["footprint"]["session_candidates"]:
        path, root = Path(candidate["path"]), Path(candidate["root"])
        agent = candidate["agent"]
        try:
            if agent in active or not session_history.quiescent(agent):
                active.add(agent)
                raise ValueError(f"a {agent} process is still running or cannot be ruled out")
            evidence = session_history.archive(candidate, before, live)
            session_history.source_info(path, root, candidate["fingerprint"], agent)
            if not session_history.quiescent(agent):
                active.add(agent)
                raise ValueError(f"a {agent} process started during archive verification")
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            out["sessions_retained"].append({"path": str(path), "agent": agent, "reason": str(exc)})
            continue
        except OSError as exc:
            out.update(refused="native transcript archive could not be verified", failure={"path": str(path), "error": str(exc)},
                       next="inspect the local session archive and preview again")
            out["recovery_report"] = str(_report_path(out))
            return
        receipt = _recycle_one(path, recycle_fn)
        if not receipt["ok"]:
            out.update(refused=f"could not recycle {path}", failure={"path": str(path), **receipt},
                       next="inspect with fieldkit size trash, then preview again")
            out["recovery_report"] = str(_report_path(out))
            return
        out["sessions_recycled"].append({"path": str(path), **evidence})


def clean(seconds=None, apply=False, *, recycle_fn=None, inventory=False, retire_sessions=False):
    from . import switch

    with lessons.lock() if apply else nullcontext():
        live = st.capture_dir()
        if os.path.lexists(live):
            retention.check_root(live.absolute())
        if apply and switch.is_on() and live.exists():
            return {"refused": "capture is active", "next": "finish the current capture first"}
        keep, protected = keep_list(), _protected()
        before = retention.cutoff(seconds)
        disp = dispositions()
        chk = check(keep=keep, before=before, protected=protected, disp=disp, check_store=False)
        out = {"check": chk, "recycled": [], "preview": not apply, "next": chk["next"]}
        if inventory:
            from . import footprint

            out["footprint"] = footprint.inventory(_policy(), before)
            out.update(sessions_recycled=[], sessions_retained=[])
        if chk["errors"]:
            out["refused"] = "scratch could not be inspected safely"
        elif apply:
            _apply(out, keep, protected, recycle_fn or recycle)
            if retire_sessions and not out.get("refused"):
                _retire_sessions(out, live, recycle_fn or recycle)
        elif chk["wipeable"]:
            out["next"] = "repeat this cleanup with --apply to recycle eligible items"
        return out


def start(project, lesson, minutes=None, force=False, dry_run=False, store=None, rows=None, disp=None, keep=None,
          recycle_fn=None, turn_on=None, archive_fn=None, terminal=None, seconds=None, workspace=None, agents=None,
          sessions=(), lookback=None, retire_sessions=False):
    """Gate manual lessons and existing build callers through the same cleanup checks."""
    from . import switch

    if not (project or "").strip() or not (lesson or "").strip():
        raise ValueError("--project and --lesson are required")
    if minutes is not None and not 1 <= minutes <= switch.MAX_MINUTES:
        raise ValueError("--for is outside the supported capture duration")
    if force and not (real_terminal() if terminal is None else terminal):
        return {"started": False, "refused": "--force-undigested is the owner's: type it at a real terminal", "next": "digest it: fieldkit capture start --project " + project + " (shows what)"}
    if bool(workspace) != bool(agents):
        raise ValueError("--workspace and --agents are required together for a scoped lesson")
    if (sessions or lookback) and not workspace:
        raise ValueError("--session and --lookback require --workspace and --agents")
    scope = None
    if workspace:
        workspace = Path(workspace).resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("--workspace must be an existing directory")
        scope = {"lesson_id": uuid4().hex, "workspace": str(workspace), "agents": sorted(set(agents))}
        if sessions or lookback or "codex" in agents:
            scope["sessions"] = lessons.session_selection(agents, sessions, lookback)
        if lookback:
            scope["retrospective_since"] = lessons.since_for(lookback)
    with lessons.lock() if not dry_run else nullcontext():
        return _start(project, lesson, minutes, force, dry_run, store, rows, disp, keep,
                      recycle_fn or recycle, turn_on, archive_fn, seconds, scope, retire_sessions)


def _start(project, lesson, minutes, force, dry_run, store, rows, disp, keep,
           recycle_fn, turn_on, archive_fn, seconds, scope, retire_sessions):
    from . import switch

    live = st.capture_dir().absolute()
    if os.path.lexists(live):
        retention.check_root(live)
    current = switch.read()
    if switch.is_on(current) and not current.get("legacy"):
        return {"started": False, "refused": "capture is active", "next": "finish the current capture first"}
    if current.get("error"):
        return {"started": False, "refused": current["error"], "next": "restore the capture switch"}
    if scope:
        from . import scan

        errors = switch.check_routes(scope["agents"])
        errors.update(scan.verify_sessions(scope))
        if errors:
            return {"started": False, "refused": "selected capture routes are unavailable", "agents": errors,
                    "next": "select verified agents or configure their capture routes"}
    keep = keep_list() if keep is None else keep
    protected = _protected(scope["workspace"] if scope else None)
    before = retention.cutoff(seconds)
    chk = check(store, rows, disp, keep, before, protected)
    if chk["errors"] or (not chk["digested"] and not force):
        return {"started": False, "wiped": False, "refused": "undigested material", "check": chk, "next": chk["next"]}
    out = {"started": False, "wiped": False, "check": chk, "forced": bool(force and not chk["digested"]), "recycled": []}
    if retire_sessions:
        from . import footprint

        out["footprint"] = footprint.inventory(_policy(), before, temp=False)
        out.update(sessions_recycled=[], sessions_retained=[])
        # Exact lookback and fresh cursor sources belong to the next lesson.
        selected = set(scope.get("sessions", {}).values()) if scope else set()
        if selected:
            from . import session_history

            out["footprint"]["session_candidates"] = [
                row for row in out["footprint"]["session_candidates"]
                if session_history.candidate_id(row["agent"], Path(row["path"]), Path(row["root"])) not in selected
            ]
    if dry_run:
        out["preview"] = True
        out["next"] = "repeat without --dry-run to recycle eligible items and start"
        return out
    if scope:
        errors = switch.prepare_routes(scope["agents"])
        if errors:
            return {**out, "refused": "selected capture hooks could not be verified", "agents": errors,
                    "next": "fix the selected capture routes and preview again"}
    try:
        return _finish_start(out, project, lesson, minutes, live, keep, protected, recycle_fn, turn_on, archive_fn, scope)
    finally:
        if scope and not out["started"]:
            switch.remove_routes(scope["agents"])


def _finish_start(out, project, lesson, minutes, live, keep, protected, recycle_fn, turn_on, archive_fn, scope):
    from . import switch

    archived = live.exists()
    snapshot = None
    if archived:
        try:
            if archive_fn:
                out["archive"] = archive_fn(project)
            else:
                snapshot = _archive_snapshot(_archive_files(live))
                s = st.Store(live)
                problems, _ = s.verify()
                if problems:
                    raise OSError(f"capture ledger verification found {len(problems)} problem(s)")
                state = s.state()
                history = {"events": sorted(state["events"]), "dispositions": dispositions(),
                           "hooked_claude": sorted({item["session"] for item in state["items"].values()
                                                    if item.get("session") and item.get("agent") == "claude" and not item.get("lesson_id")
                                                    and not str(item.get("origin", "")).startswith("scan")})}
                out["archive"] = archive(project, store_dir=live, history=history)
        except (OSError, ValueError, tarfile.TarError) as exc:
            out.update(refused=f"capture archive verification failed: {exc}", next="inspect the live capture store")
            return out
    _apply(out, keep, protected, recycle_fn)
    if out.get("refused"):
        return out
    if out.get("footprint"):
        _retire_sessions(out, live, recycle_fn)
        if out.get("refused"):
            return out
    if archived or live.exists():
        unchanged = archived and (snapshot is None or snapshot == _archive_snapshot(_archive_files(live)))
        receipt = (_recycle_one(live, recycle_fn) if unchanged else
                   {"ok": False, "error": "capture store changed after archive inspection"})
        if not receipt["ok"]:
            out.update(refused=f"could not recycle {live}", failure={"path": str(live), **receipt},
                       next="inspect with fieldkit size trash, then preview again")
            out["recovery_report"] = str(_report_path(out))
            return out
        out["recycled"].append(str(live))
    out["wiped"] = True
    kw = {} if minutes is None else {"minutes": minutes}
    if scope:
        kw["scope"] = scope
    try:
        out["switch"] = (turn_on or (lambda *a, **k: switch.turn_on(*a, schedule=switch.schedule_on, **k)))(
            lesson, project=project, **kw)
    except (OSError, ValueError) as exc:
        out.update(refused=f"could not start the capture switch: {exc}", next="inspect the archive and run fieldkit capture state")
        out["recovery_report"] = str(_report_path(out))
        return out
    out["started"] = True
    out["next"] = "work; `fieldkit capture off --distill` when the lesson is done"
    return out


def _print(a, d):
    if a.json:
        print(json.dumps(d, indent=1, ensure_ascii=False, default=str))
        return
    c = d.get("check") or {}
    f = d.get("footprint")
    if f:
        for row in (*f["temp"], *f["sessions"]):
            label = row.get("agent") or ", ".join(row["sources"])
            age = f", {row['older']} older, {row['recent']} recent" if f["cutoff_ns"] is not None else ""
            print(f"  {label} {row['root']} ({row['pattern']}): {row['status']}, "
                  f"{row['files']} file(s){age}, {row['boundaries']} boundary skips, {row['unreadable']} unreadable; {row['policy']}")
            if row.get("detail"):
                print(f"    {row['detail']}")
        for error in f["errors"]:
            print(f"  {error}")
        print(f"  sessions: {len(f['session_candidates'])} conditional candidate(s), "
              f"{len(d['sessions_recycled'])} archived and recycled, {len(d['sessions_retained'])} retained after inspection")
        for row in (d["sessions_retained"] or f["session_candidates"])[:SHOW]:
            print(f"    {row['path']} ({row['reason']})")
    for root, n in (c.get("counts") or {}).items():
        print(f"  count {root}: {n['items']} item(s), {n['files']} file(s)")
    if c:
        print(f"  eligible {len(c['wipeable'])}; retained {len(c['retained'])}; unresolved {len(c['scripts'])}")
        for label in ("wipeable", "retained", "scripts", "errors"):
            for row in c[label][:SHOW]:
                print(f"    {label:9} {row['path']}" + (f" ({row['reason']})" if row.get("reason") else ""))
    if d.get("refused"):
        print(f"capture {a.action} REFUSED: {d['refused']}")
        for agent, reason in d.get("agents", {}).items():
            print(f"  {agent}: {reason}")
        for k, v in (c.get("store") or {}).items():
            print(f"  store {k}: {len(v)} (first {SHOW}: {', '.join(map(str, v[:SHOW]))})")
    else:
        print(("capture STARTED" if d.get("started") else f"capture {a.action} {'preview' if d.get('preview') else 'complete'}")
              + f"; recycled {len(d.get('recycled', []))} item(s)"
              + (f"; archive {d['archive']['path']}" if d.get("archive") else ""))
    if d.get("recovery_report"):
        print(f"  recovery report {d['recovery_report']}")
    print(f"NEXT: {d.get('next')}")


def cmd(a):
    try:
        if getattr(a, "dispose", None):
            if a.dry_run or a.keep_last is not None or a.all or a.lookback or a.session:
                raise ValueError("--dispose is a separate action from preview and cleanup")
            with lessons.lock():
                record_disposition(a.dispose, a.because or "")
            print(f"recorded: {a.dispose} not worth keeping, because {a.because}\nNEXT: fieldkit capture start --project {a.project or 'NAME'} --lesson WHY")
            return 0
        seconds = retention.duration(a.keep_last) if a.keep_last is not None else None
        if a.action == "clean":
            if a.retire_sessions and not a.apply:
                raise ValueError("--retire-sessions requires --apply; the preview already reports conditional candidates")
            d = clean(seconds, a.apply, inventory=True, retire_sessions=a.retire_sessions)
        else:
            d = start(a.project, a.lesson, a.minutes, force=a.force_undigested, dry_run=a.dry_run, seconds=seconds,
                      workspace=a.workspace, agents=[name.strip().lower() for name in a.agents.split(",")] if a.agents else None,
                      sessions=a.session, lookback=retention.duration(a.lookback) if a.lookback is not None else None,
                      retire_sessions=a.all or seconds is not None)
    except ValueError as exc:
        print(f"capture {a.action}: {exc}", file=sys.stderr)
        return 2
    except (OSError, tarfile.TarError) as exc:
        d = {"refused": str(exc), "next": "resolve the reported filesystem error and preview again"}
    _print(a, d)
    return 3 if d.get("refused") or (
        not d.get("started") and not d.get("check", {}).get("digested")
    ) else 0
