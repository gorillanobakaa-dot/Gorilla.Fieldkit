"""The Firefox binary reproduced: the compiled tree built a SECOND time, in another folder with its own objdir, and
the packages compared byte for byte. The build harness's admitted gap until 2026-10-11 ("the source is proven; the
binary is not yet reproduced independently").

    fieldkit build-harness repro TASK             the plan: what is built where, and what stops it. Changes nothing.
    fieldkit build-harness repro TASK --run       the second build (hours, like the first, under the same thermal
                                                  governor), then the comparison
    fieldkit build-harness repro TASK --compare   only the comparison, of a second build already made

The second tree is a git worktree of HEAD in <workdir>-repro: the replay proof (build-harness replay) holds that HEAD
is exactly pristine upstream + the public patch set, so a build of HEAD in a fresh folder is a build anyone can make
from what is published. A worktree shares the object store (no second copy of the history) and holds no build
products. The second objdir is <objdir>-repro.

Held fixed, because each one alone makes every build differ:
  MOZ_BUILD_DATE   the BuildID: the owner's pin (config/versions.lock.json build.moz_build_date). No pin, no run.
  the mozconfig    the owner's, with every mention of the first objdir pointing at the second
Varied on purpose: the source folder and the objdir. What still differs is named by fieldkit/build/repro.py, inside
the zip and its omni.ja: a build folder in a binary or a PDB path, a PE link time or PDB signature (/Brepro), a date
in a file, a member order. The installer (a 7-Zip stub) is compared as bytes; the zip holds the same files, opened.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import task

MIN_FREE_GB = 60                 # a Firefox objdir with its package; the first build's own size is not measured


def _record(task_id):
    from .compile import _record_path
    p = _record_path(task_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def packages(objdir):
    """{'installer': Path, 'zip': Path} found in <objdir>/dist, the newest of each."""
    dist = Path(objdir) / "dist"
    out = {}
    for kind, pattern in (("installer", "*.installer.exe"), ("zip", "*.zip")):
        found = sorted(dist.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True) if dist.is_dir() else []
        if found:
            out[kind] = found[0]
    return out


def second_paths(rec):
    return Path(str(rec["workdir"]).rstrip("/\\") + "-repro"), Path(str(rec["objdir"]).rstrip("/\\") + "-repro")


def _state(task_id):
    return task.STATE / task_id / "repro.json"


def _ours(task_id):
    try:
        return json.loads(_state(task_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _free_gb(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 2 ** 30


def mozconfig_for(text, obj1, obj2):
    """The owner's mozconfig with the first objdir replaced by the second, in every spelling it is written in."""
    out, n = text, 0
    for a in {str(obj1), str(obj1).replace("\\", "/"), str(obj1).replace("/", "\\")}:
        b = str(obj2).replace("\\", "/") if "/" in a else str(obj2).replace("/", "\\")
        n += out.count(a)
        out = out.replace(a, b)
    if not n:
        raise ValueError(f"the mozconfig does not name the objdir {obj1}: cannot point a second build elsewhere")
    return out


def plan(task_id, owner_root=None, free_gb=None):
    """-> {ok, rows, work2, obj2, pin, first}. Reads only."""
    from . import buildrun, buildstamp
    from .compile import _git
    rows = []

    def row(check, ok, evidence):
        rows.append({"check": check, "ok": bool(ok), "evidence": evidence})
    rec = _record(task_id)
    if not rec:
        row("a gated build exists", False, "no build record: build-gate and build-run first")
        return {"ok": False, "rows": rows}
    t = task.load(task_id)
    owner = owner_root or buildrun._owner_root(t)
    first = packages(rec["objdir"])
    row("the first build's packages exist", bool(first),
        ", ".join(f"{k}: {p.name}" for k, p in first.items()) or f"none in {Path(rec['objdir']) / 'dist'}")
    wd = Path(rec["workdir"])
    same = _git(wd, "rev-parse", "HEAD^{tree}") == rec["tree"]
    dirty = [l for l in _git(wd, "status", "--porcelain", "--untracked-files=no").splitlines() if l.strip()]
    row("the tree is the one that was built", same and not dirty,
        "HEAD is the gated tree, nothing uncommitted" if same and not dirty else
        ("the committed source changed after the build" if not same else f"{len(dirty)} uncommitted edit(s)"))
    pin = buildstamp.pinned_build_date(owner) if owner else None
    row("the build date is pinned", bool(pin),
        f"MOZ_BUILD_DATE {pin} (config/versions.lock.json)" if pin else
        "no build.moz_build_date in config/versions.lock.json: every build would carry its own BuildID")
    work2, obj2 = second_paths(rec)
    ours = _ours(task_id)
    for label, p in (("source", work2), ("objdir", obj2)):
        free = not p.exists() or str(p) in (ours.get("work2"), ours.get("obj2"))
        row(f"the second {label} folder is free", free,
            f"{p}" + ("" if not p.exists() else " (made by an earlier repro: reused)" if free else
                      " EXISTS and was not made by repro: move it, nothing is deleted that repro did not make"))
    gb = _free_gb(obj2) if free_gb is None else free_gb
    row("room for a second build", gb >= MIN_FREE_GB, f"{gb:.0f} GB free, need {MIN_FREE_GB}")
    return {"ok": all(r["ok"] for r in rows), "rows": rows, "work2": str(work2), "obj2": str(obj2), "pin": pin,
            "first": {k: str(v) for k, v in first.items()}, "workdir": str(wd), "objdir": rec["objdir"], "owner": owner}


def compare(p):
    """The first build's packages against the second's, each pair through fieldkit/build/repro.py."""
    from ..build import repro
    second = packages(p["obj2"])
    roots = [p["workdir"], p["work2"], p["objdir"], p["obj2"]]
    results = []
    for kind, a in p["first"].items():
        b = second.get(kind)
        if not b:
            results.append({"package": kind, "identical": False, "why": f"the second build made no {kind}"})
            continue
        if Path(a).name != b.name:
            results.append({"package": kind, "identical": False, "why": f"named {Path(a).name} vs {b.name}"})
            continue
        c = repro.compare(a, b, roots=roots)
        results.append({"package": kind, "file": b.name, "identical": c["identical"],
                        "sha256": [repro.sha256(a), repro.sha256(b)],
                        "findings": [f for d in c["differ"] for f in d["findings"]][:30]})
    return {"ok": bool(results) and all(r["identical"] for r in results), "results": results}


def _mach(work2, stage):
    """The command that runs `mach STAGE` in the second tree the way the owner's builds run: MozillaBuild's bash on
    Windows, the tree's own mach elsewhere."""
    from .buildrun import MOZBUILD_BASH
    if os.name == "nt":
        msys = "/" + str(work2).replace(":", "").replace("\\", "/")
        return [str(MOZBUILD_BASH), "-l", "-c", f"cd {msys} && ./mach {stage}"]
    return [sys.executable, str(Path(work2) / "mach"), stage]


def run(task_id, say=print, execute=None, sensor=None, owner_root=None, free_gb=None):
    """The second build, then the comparison. `execute(cmd, cwd, env, log)` -> (rc, lines) replaces the build in tests;
    by default the build runs under the thermal governor, exactly like build-run."""
    from .compile import _git, mozconfig_path
    p = plan(task_id, owner_root, free_gb)
    for r in p["rows"]:
        say(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['check']}: {r['evidence']}")
    if not p["ok"]:
        return {"ok": False, "why": "the plan has a failing row", "rows": p["rows"]}
    t = task.load(task_id)
    wd, work2, obj2 = Path(p["workdir"]), Path(p["work2"]), Path(p["obj2"])
    _state(task_id).parent.mkdir(parents=True, exist_ok=True)
    _state(task_id).write_text(json.dumps({"work2": str(work2), "obj2": str(obj2)}), encoding="utf-8")
    if work2.exists():
        _git(wd, "worktree", "remove", "--force", str(work2))
    if obj2.exists():
        shutil.rmtree(obj2)            # repro's own earlier objdir (plan checked): a reused objdir is not a new build
    _git(wd, "worktree", "add", "--detach", str(work2), "HEAD")
    moz1 = mozconfig_path(p["owner"])
    moz2 = task.STATE / task_id / "mozconfig.repro"
    moz2.write_text(mozconfig_for(Path(moz1).read_text(encoding="utf-8"), p["objdir"], obj2), encoding="utf-8")
    env = {"MOZCONFIG": str(moz2), "MOZ_BUILD_DATE": p["pin"], "MOZILLABUILD": r"C:\mozilla-build"}
    log = task.STATE / task_id / f"repro-{time.strftime('%Y%m%d-%H%M%S')}.log"
    task.journal(t, "repro-start", work2=str(work2), obj2=str(obj2), pin=p["pin"], log=str(log))
    if execute is None:
        from . import buildrun
        from ..thermal import governor as gv
        name, fn, surface = (sensor or buildrun.thermal_sensor)(say)
        if fn is None:
            return {"ok": False, "why": "no CPU temperature source responds to load (fieldkit thermal prove)"}

        def execute(cmd, cwd, env, log):
            gov = gv.Governor(fn, target_c=75.0, interval=3.0, kill=lambda why: None, on_event=say,
                              csv_path=task.STATE / task_id / f"thermal-repro-{time.strftime('%Y%m%d-%H%M%S')}.csv",
                              max_cap=buildrun.SURFACE_CAP if surface else None, dead_check=not surface)
            rc, lines = buildrun._stream(cmd, cwd, log, say, governor=gov, watch_thermal=not surface, env=env)
            if gov.verdict:
                lines.append(f"FIELDKIT THERMAL GOVERNOR: {gov.verdict}")
            return rc, lines
    for stage in ("build", "package"):
        say(f"{time.strftime('%H:%M:%S')}  repro {stage}: {work2}")
        rc, lines = execute(_mach(work2, stage), work2, env, log)
        if rc != 0:
            task.journal(task.load(task_id), "repro-stop", stage=stage, rc=rc, last=[l for l in lines if l.strip()][-5:])
            return {"ok": False, "why": f"the second {stage} stopped (exit {rc}); log {log}",
                    "last": [l for l in lines if l.strip()][-8:]}
    return finish(task_id, p, say)


def finish(task_id, p, say=print):
    r = compare(p)
    t = task.load(task_id)
    task.journal(t, "repro-proof", ok=r["ok"], packages=[x["package"] for x in r["results"]],
                 differ=[f"{x['package']}: {(x.get('findings') or [{}])[0].get('where', x.get('why', ''))}"
                         for x in r["results"] if not x["identical"]][:10])
    for line in lines(r):
        say(line)
    return r


def compare_only(task_id, say=print, owner_root=None):
    rec = _record(task_id)
    if not rec:
        return {"ok": False, "why": "no build record"}
    work2, obj2 = second_paths(rec)
    p = {"first": {k: str(v) for k, v in packages(rec["objdir"]).items()}, "obj2": str(obj2), "work2": str(work2),
         "workdir": rec["workdir"], "objdir": rec["objdir"]}
    return finish(task_id, p, say)


def lines(r):
    out = []
    for x in r["results"]:
        if x["identical"]:
            out.append(f"  SAME    {x['package']}: {x['file']}  {x['sha256'][0][:16]}")
            continue
        out.append(f"  DIFFER  {x['package']}: {x.get('file') or x.get('why')}")
        for f in x.get("findings", [])[:10]:
            out.append(f"          {f['where']}: {f['what']}" + (f"; cause: {'; '.join(f['causes'])}" if f.get("causes") else ""))
    out.append("REPRODUCED: the second build's packages are byte for byte the first's" if r["ok"] else
               "NOT REPRODUCED: see the differences above; each names the file and the cause")
    return out
