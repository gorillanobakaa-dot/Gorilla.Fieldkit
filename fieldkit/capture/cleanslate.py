"""P40 (owner 2026-11-16): every project starts on a clean slate.

    fieldkit capture start --project NAME --lesson WHY [--for MIN] [--dry-run]

Before capture switches on, a digest check runs:
  store     every captured item is closed (`capture ledger --closed`: nothing raw or processing, nothing lost)
  scripts   every script under the sweep roots and this computer's scratch and download dirs is adopted (carded), or has
            a recorded "not worth keeping, because" (state/capture/dispositions.jsonl)
YES  the old store goes into a hash-chained archive bundle (the raw record is kept), the live copy and the digested
     scattered scripts are recycled (`fieldkit size recycle`, never a permanent delete), capture starts empty, tagged
     with this project only. Adopted (carded) tools are the harness and stay.
NO   nothing is touched; the undigested items are printed with the command that digests them. A forced wipe of
     undigested material needs `--force-undigested` typed at a real terminal (stdin and stdout are a tty and no agent
     marker is set); a model's call is refused. Keep list: keep.yaml.
Exit: 0 started, 3 refused (undigested), 2 bad usage.
"""
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import store as st

HERE = Path(__file__).resolve().parent
DISPOSITIONS = "dispositions.jsonl"
SHOW = 10
AGENT_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SESSION", "GEMINI_CLI", "FIELDKIT_AGENT")


def keep_list():
    return [str(x).lower() for x in (yaml.safe_load((HERE / "keep.yaml").read_text(encoding="utf-8")) or {}).get("keep", [])]


def extra_roots():
    d = yaml.safe_load((HERE / "keep.yaml").read_text(encoding="utf-8")) or {}
    return [Path(os.path.expanduser(x)) for x in d.get("extra_roots", [])]


def is_kept(path, keep=None):
    p = "/" + str(path).replace("\\", "/").lower().strip("/") + "/"
    return any(k in p for k in (keep if keep is not None else keep_list()))


def real_terminal():
    """The owner-only check (like `decide`): a person at a terminal, not an agent's pipe or tool call."""
    return sys.stdin.isatty() and sys.stdout.isatty() and not any(os.environ.get(k) for k in AGENT_ENV)


def dispositions():
    p = st.capture_dir() / DISPOSITIONS
    out = {}
    if p.is_file():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                if d.get("path") and d.get("because"):
                    out[os.path.normcase(str(Path(d["path"])))] = d
            except ValueError:
                pass
    return out


def record_disposition(path, because):
    if not (because or "").strip():
        raise ValueError("a disposition needs a reason: not worth keeping, because ...")
    p = st.capture_dir() / DISPOSITIONS
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"path": str(path), "because": because.strip(), "time": _now()}, ensure_ascii=False) + "\n")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check(store=None, rows=None, disp=None, keep=None):
    """-> {digested, store: {outstanding, lost, unaccounted, stuck}, scripts: [rows to digest], wipeable: [rows], next}.
    rows: sweep rows (injected by tests). A script row is digested when carded (adopted: it stays) or has a disposition;
    only the ones with a disposition are wipeable."""
    s = store or st.Store()
    s.recover()
    led = s.ledger()
    open_store = {k: led[k] for k in ("outstanding", "lost", "unaccounted", "stuck") if led[k]}
    if rows is None:
        from ..desk import sweep
        rows = sweep.scan(list(sweep.roots()) + [r for r in extra_roots() if r.is_dir()])
    disp = dispositions() if disp is None else disp
    undigested, wipeable = [], []
    for r in rows:
        if is_kept(r["path"], keep):
            continue
        if r["status"] in ("ok", "stale", "untested"):
            continue                                      # adopted: carded, part of the harness
        if os.path.normcase(str(Path(r["path"]))) in disp:
            wipeable.append(r)
        else:
            undigested.append(r)
    ok = not open_store and not undigested
    if open_store:
        nxt = "fieldkit capture classify --apply   (then fieldkit capture distill recipes --apply); then fieldkit capture ledger --closed"
    elif undigested:
        nxt = ("fieldkit sweep   (adopt each script: fieldkit adopt ..., or record why it goes: "
               "fieldkit capture start --dispose PATH --because WHY)")
    else:
        nxt = "nothing to digest"
    return {"digested": ok, "store": open_store, "scripts": undigested, "wipeable": wipeable, "next": nxt}


def archive(project, store_dir=None, out_dir=None):
    """Tar the capture store into a bundle with a hash-chained manifest (each file's hash includes the one before).
    -> {path, sha256, files, head}. The raw record stays; only the live copy is recycled afterwards."""
    src = Path(store_dir or st.capture_dir())
    out_dir = Path(out_dir or src.parent / "capture-archive")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    bundle = out_dir / f"capture-{project}-{stamp}.tar.gz"
    prev, chain = "0" * 64, []
    files = sorted(p for p in src.rglob("*") if p.is_file())
    for p in files:
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        prev = hashlib.sha256((prev + str(p.relative_to(src)).replace("\\", "/") + h).encode()).hexdigest()
        chain.append({"file": str(p.relative_to(src)).replace("\\", "/"), "sha256": h, "chain": prev})
    manifest = out_dir / f"capture-{project}-{stamp}.manifest.json"
    manifest.write_text(json.dumps({"project": project, "time": _now(), "head": prev, "files": chain}, indent=1), encoding="utf-8")
    with tarfile.open(bundle, "w:gz") as t:
        t.add(src, arcname="capture")
    return {"path": str(bundle), "manifest": str(manifest), "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "files": len(files), "head": prev}


def recycle(path, run=subprocess.run):
    r = run([sys.executable, "-m", "fieldkit", "size", "recycle", str(path), "--whole-folder", "--json"],
            capture_output=True, text=True)
    return r.returncode == 0


def start(project, lesson, minutes=None, force=False, dry_run=False, store=None, rows=None, disp=None, keep=None,
          recycle_fn=None, turn_on=None, archive_fn=None, terminal=None):
    """The one start gate. -> dict with 'started' and 'wiped'. Never wipes undigested material unless force came from a
    real terminal; a forced call from an agent is refused."""
    if not (project or "").strip():
        raise ValueError("--project is required")
    if force and not (real_terminal() if terminal is None else terminal):
        return {"started": False, "refused": "--force-undigested is the owner's: type it at a real terminal", "next": "digest it: fieldkit capture start --project " + project + " (shows what)"}
    chk = check(store, rows, disp, keep)
    if not chk["digested"] and not force:
        return {"started": False, "wiped": False, "refused": "undigested material", "check": chk, "next": chk["next"]}
    out = {"started": False, "wiped": False, "check": chk, "forced": bool(force and not chk["digested"])}
    if dry_run:
        out["next"] = "rerun without --dry-run to wipe and start"
        return out
    out["archive"] = (archive_fn or archive)(project)
    rc = recycle_fn or recycle
    live = st.capture_dir()
    out["recycled"] = []
    if live.exists():
        # the switch lives in the store folder: keep nothing but start fresh (turn_on rewrites it)
        if rc(live):
            out["recycled"].append(str(live))
        else:
            return {**out, "refused": f"could not recycle {live}; nothing started", "next": "fieldkit size recycle " + str(live)}
    for r in chk["wipeable"]:
        if not is_kept(r["path"], keep) and Path(r["path"]).exists() and rc(r["path"]):
            out["recycled"].append(r["path"])
    out["wiped"] = True
    from . import switch
    kw = {} if minutes is None else {"minutes": minutes}
    out["switch"] = (turn_on or (lambda *a, **k: switch.turn_on(*a, schedule=switch.schedule_on, **k)))(lesson, project=project, **kw)
    out["started"] = True
    out["next"] = "work; `fieldkit capture off --distill` when the lesson is done"
    return out


def _print(a, d):
    if a.json:
        print(json.dumps(d, indent=1, ensure_ascii=False, default=str))
        return
    c = d.get("check") or {}
    if d.get("refused"):
        print(f"capture start REFUSED: {d['refused']}")
        for k, v in (c.get("store") or {}).items():
            print(f"  store {k}: {len(v)} (first {SHOW}: {', '.join(map(str, v[:SHOW]))})")
        sc = c.get("scripts") or []
        if sc:
            kinds = {}
            for r in sc:
                kinds[r["status"]] = kinds.get(r["status"], 0) + 1
            print("  scripts undigested: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
            for r in sc[:SHOW]:
                print(f"    {r['status']:9} {r['path']}")
    else:
        print(("capture STARTED on a clean slate" if d.get("started") else "capture start: dry run, all digested")
              + f" (project {a.project}); recycled {len(d.get('recycled', []))} item(s)"
              + (f"; archive {d['archive']['path']}" if d.get("archive") else ""))
    print(f"NEXT: {d.get('next')}")


def cmd(a):
    try:
        if getattr(a, "dispose", None):
            record_disposition(a.dispose, a.because or "")
            print(f"recorded: {a.dispose} not worth keeping, because {a.because}\nNEXT: fieldkit capture start --project {a.project or 'NAME'} --lesson WHY")
            return 0
        d = start(a.project, a.lesson, a.minutes, force=a.force_undigested, dry_run=a.dry_run)
    except ValueError as exc:
        print(f"capture start: {exc}", file=sys.stderr)
        return 2
    _print(a, d)
    return 0 if d.get("started") or (a.dry_run and not d.get("refused")) else 3
