"""fieldkit firefox: one entry point for every Firefox build on every system (P38, owner order 2026-11-16).

    fieldkit firefox status [VERSION]          every system (Linux .deb, Windows, macOS 10.13): where each stands
    fieldkit firefox next [VERSION]            the one next step, with its command
    fieldkit firefox release VERSION           the whole chain, step by step, with each command and gate (prints; each
                                               step's own tool runs it)
    fieldkit firefox handover VERSION --to windows|mac [--send]   the package for the other system (handover.py)
    fieldkit firefox lessons [TASK]            every port lesson and its rule; exit 3 while one waits
    fieldkit firefox knowledge                 where each piece of Firefox knowledge lives
    fieldkit firefox names [--file F] [--tag T] [--title T]   the one product name and scheme (Gorilla-Unleashed-
                                               VERSION-OS-ARCH.EXT, vVERSION-OS, "Gorilla Unleashed VERSION (OS)");
                                               with no argument it scans Fieldkit's Firefox recipes; exit 3 on another name

It drives the existing tools (build-harness migrate, rebase-set/export-hand/replay, emerge recipes, macair, ship,
peers link); it does not rewrite them. VERSION defaults to the newest Linux migration task on this machine.
"""
import argparse
import json
import re
import sys

from ..core import concepts
from . import handover as ho


def _latest_version():
    try:
        from ..buildh import task
        nums = sorted(int(m.group(1)) for d in task.STATE.iterdir() if (m := re.fullmatch(r"firefox-(\d+)-linux", d.name)))
    except OSError:
        nums = []
    return f"{nums[-1]}.0" if nums else None


def _stage(tid):
    try:
        from ..migrate import plan
        p = plan.load_plan(tid)
    except Exception:  # noqa: BLE001
        p = None
    return (p or {}).get("stage")


def where(version):
    """-> {system: {...}} and the ordered step rows with done True/False/None (None = not measurable here)."""
    cb, tid = ho.cookbook(), ho.task_id(version)
    values = ho.step_values(version, tid)
    stage = _stage(tid)
    order = [s["id"] for s in cb["steps"] if s["id"].startswith("S")]
    root = ho.out_root()
    rows = []
    for s in cb["steps"]:
        if s["id"].startswith("S"):
            done = None if stage is None else order.index(s["id"]) < order.index(stage) if stage in order else None
        elif s["id"].startswith("H-"):
            done = (root / f"{version}-{s['system']}" / "SHA256SUMS").is_file()
        else:
            done = None
        rows.append(dict(s, run=s["run"].format_map(values), does=s["does"].format_map(values), done=done))
    systems = {k: dict(v, product=concepts.product_name(version)) for k, v in cb["systems"].items()}
    systems["linux"]["stage"] = stage or "no migration task on this machine"
    return {"version": version, "task": tid, "systems": systems, "steps": rows}


def _next(w):
    return next((r for r in w["steps"] if r["done"] is not True), None)


def name_check(files=(), tags=(), titles=(), scan=None):
    """-> problems: every given name in the scheme, and no old product name in Fieldkit's Firefox files."""
    from pathlib import Path
    probs = []
    for kind, vals in (("file", files), ("tag", tags), ("title", titles)):
        for v in vals or []:
            probs += concepts.name_problems(kind, v)
    root = Path(__file__).resolve().parents[2]
    for rel in (concepts.naming()["scan"] if scan is None else scan):
        f = root / rel
        if f.is_file():
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                probs += [f"{rel}:{i}: {x}" for x in concepts.name_problems("text", line)]
    return probs


def main(argv=None, prog="fieldkit firefox"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0],
                                 epilog="every subcommand takes --json (as JSON)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "next"):
        p = sub.add_parser(name)
        p.add_argument("version", nargs="?")
        p.add_argument("--json", action="store_true")
    p = sub.add_parser("release")
    p.add_argument("version")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("handover")
    p.add_argument("version")
    p.add_argument("--to", required=True, choices=ho.TARGETS)
    p.add_argument("--patchset", help="the patch-set repo (default: gorilla-firefox, or $GORILLA_FIREFOX)")
    p.add_argument("--task", help="the Linux migration task (default firefox-MAJOR-linux)")
    p.add_argument("--upstream", help="an upstream git tree to resolve the release tag in (optional)")
    p.add_argument("--out", help="where the package goes (default state/firefox/handover)")
    p.add_argument("--send", action="store_true", help="send it: windows over the cable, mac by scp to `macair`")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("lessons")
    p.add_argument("task", nargs="?")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("names")
    for k in ("file", "tag", "title"):
        p.add_argument(f"--{k}", action="append", default=[])
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("knowledge")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    out = lambda d: print(json.dumps(d, indent=1, default=str))  # noqa: E731

    if a.cmd == "knowledge":
        k = ho.cookbook()["knowledge"]
        if a.json:
            out(k)
        else:
            for r in k:
                print(f"  {r['place']}\n      {r['holds']}")
            print("NEXT: fieldkit firefox status")
        return 0

    if a.cmd == "names":
        probs = name_check(a.file, a.tag, a.title)
        if a.json:
            out({"problems": probs})
        else:
            for x in probs:
                print(f"  WRONG NAME: {x}")
            print(f"names: {len(probs)} problem(s)")
            print("NEXT: " + ("rename each to the scheme in fieldkit/firefox/cookbook.yaml naming" if probs
                              else "fieldkit release-page compose"))
        return 3 if probs else 0

    if a.cmd == "lessons":
        from ..buildh import lessonrules, task
        tid = a.task or (ho.task_id(v) if (v := _latest_version()) else None)
        if not tid:
            print("no Linux migration task on this machine\nNEXT: fieldkit firefox status")
            return 1
        waiting, ruled = lessonrules.split(task.lessons(tid))
        if a.json:
            out({"task": tid, "waiting": waiting, "ruled": [[r.get("lesson"), e["id"], e["kind"]] for r, e in ruled]})
        else:
            print(f"{tid}: {len(ruled)} lesson(s) ruled, {len(waiting)} waiting")
            for w in waiting:
                print(f"  WAITING {w.get('step')}: {str(w.get('lesson'))[:160]}")
            print("NEXT: " + ("add each waiting lesson to fieldkit/firefox/lesson_rules.yaml (tool, port-rule or no-rule)"
                              if waiting else "fieldkit firefox next"))
        return 3 if waiting else 0

    if a.cmd == "handover":
        pkg, man, problems = ho.build(a.version, a.to, a.patchset, a.task, a.upstream, a.out)
        problems += ho.verify(pkg)
        if a.json:
            out({"package": str(pkg), "manifest": man, "problems": problems})
        else:
            print(f"handover {man['product']} -> {a.to}: {pkg}")
            print(f"  patch set {str(man['patchset']['commit'])[:12]} (base {man['patchset']['base_branch']}), "
                  f"upstream {man['upstream']['tag']}, {len(man['patches'])} patch(es)")
            for p in problems:
                print(f"  CHECK FAILED: {p}")
        if problems:
            print("NEXT: fix the failed checks above, then run the handover again (nothing was sent)")
            return 3
        if a.send:
            rc = ho.send(pkg, a.to)
            if rc:
                return rc
        print("NEXT: " + ("the other side unpacks, checks SHA256SUMS and follows manifest.json" if a.send else
                          f"fieldkit firefox handover {a.version} --to {a.to} --send"))
        return 0

    version = getattr(a, "version", None) or _latest_version()
    if not version:
        print("no Firefox version given and no Linux migration task on this machine\n"
              "NEXT: fieldkit build-harness migrate seed firefox-MAJOR-linux")
        return 1
    w = where(version)
    if a.cmd == "status":
        if a.json:
            out(w)
            return 0
        print(f"{concepts.product_name(version)} ({w['task']})")
        for k, s in w["systems"].items():
            print(f"  {k:8} {s['what']}\n           device {s['device']}, recipe {s['recipe']}, skill {s['skill']}"
                  + (f", stage {s['stage']}" if k == "linux" else ""))
        for r in w["steps"]:
            mark = {True: "done", False: "open", None: "  ? "}[r["done"]]
            print(f"  [{mark}] {r['id']:6} {r['system']:7} {r['does'][:90]}")
        n = _next(w)
        print("NEXT: " + (n["run"] if n else "fieldkit firefox release " + version))
        return 0
    if a.cmd == "next":
        n = _next(w)
        if a.json:
            out(n)
        elif n:
            print(f"{n['id']} ({n['system']}): {n['does']}\nNEXT: {n['run']}")
        else:
            print("every step done\nNEXT: fieldkit release-page compose")
        return 0
    # release: the whole chain, in order
    if a.json:
        out(w["steps"])
        return 0
    print(f"{concepts.product_name(version)}: the chain from Mozilla's {concepts.upstream_tag(version)} to all three systems")
    for i, r in enumerate(w["steps"], 1):
        print(f"  {i:2}. [{r['system']}] {r['does']}\n        run:  {r['run']}\n        gate: {r['gate']}")
    n = _next(w)
    print("NEXT: " + (n["run"] if n else "fieldkit release-page compose"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
