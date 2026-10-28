# Version: 1.0.0 · updated 26-10-28-20-22
"""Which devices are up and what each can run; a job split into pieces, each with a test that says it is done.

    fieldkit orchestrate inventory [--json]                  every device of the household list: up or not (its
                                                             own path), and what it can run (OS, cores, RAM, free
                                                             slots, toolchains, a local model yes or no)
    fieldkit orchestrate split [--tree REPO] [--pieces N] [--run] [--wait S] [--out FILE] [--json]
                                                             REPO's test suite as N pieces, each a group of test
                                                             files with its acceptance, placed on the devices that
                                                             are up and can run pytest; --run runs them and says
                                                             how many pieces were finished per hour

Plan item I6 (docs/HARNESS-PLAN.md, phase 6). The inventory is two existing probes joined by the machine tag: the
household list (fieldkit/peers/household.py, `fieldkit machine reach`: UP, DOWN, HERE, NOT SET UP...) and each
cable device's answer to the read-only link verb `load` (fieldkit/orchestrate_load.py: os, cores, RAM, the gate's
slots, `can`). An SSH-only device (the MacBook Air) answers no `load`: its row says so and it gets no piece.

THE SPLIT (fixed, no model): every tests/test_*.py of the tree, weighed by its count of `def test_` lines (plus 1),
dealt largest first into the lightest of N pieces. A piece's ACCEPTANCE is the pytest PASS line: the last pytest
summary of its run says at least one test passed and none failed or errored (orchestrate_work.test_counts, the rule
the worker itself signs). Exit code alone never accepts a piece.

PLACEMENT: a device gets pieces when it is UP or HERE, answered `load`, and has python, pytest and git. It runs as
many pieces at once as its gate offers (at least one). The plan deals pieces to slots largest first (every slot
assumed equally fast); --run uses a queue instead: each slot takes the next piece when it finishes one, so a slow
device takes fewer. Here a piece runs in the exported tree inside an admission slot; on another device it is a C3
run job, the same as `fieldkit task start --on DEV --run --tree DIR -- fieldkit admit suite FILES` (the tree goes
over the cable, the worker signs the result, the task ledger records it). Anything over the cable failing is said
for that piece; the piece is not retried elsewhere and is not counted as finished.

Doors: none new. The cable calls are the link's signed `load`, `put` and `job` verbs.
"""
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import orchestrate_load as L
from . import orchestrate_work as W

TEST_DEF = re.compile(r"^\s*(?:async\s+)?def test_", re.M)
DEFAULT_PIECES = 12


# ---- inventory ---------------------------------------------------------------------------------------------------

def inventory(reach_fn=None, load_fn=None):
    """-> [{tag, what, path, state, device, up, load, can, slots, runs, why}], one per household.yaml device."""
    from .peers import household
    rows = (reach_fn or (lambda: household.reach(sweep=False)))()
    try:
        loads = (load_fn or L.load_all)()
    except Exception as e:  # noqa: BLE001 - no link at all: every row says it has no load report
        loads = [{"name": "", "me": True, "ok": False, "error": f"{type(e).__name__}: {e}"}]
    by_name = {r.get("name"): r for r in loads if r.get("name")}
    mine = next((r for r in loads if r.get("me")), None)
    out = []
    for r in rows:
        up = r["state"] in ("UP", "HERE")
        ld = by_name.get(r.get("device")) or (mine if r["state"] == "HERE" else None)
        row = {k: r.get(k, "") for k in ("tag", "what", "path", "state", "device")}
        row.update(up=up, load=ld if ld and ld.get("ok") else None, can={}, slots=0, runs=[], why="")
        if not up:
            row["why"] = r.get("detail") or r["state"]
        elif not row["load"]:
            row["why"] = ("an SSH-only device answers no `load` (no Fieldkit listener there): what it can run is not "
                          "probed" if r["path"] == "ssh" else f"no load report: {(ld or {}).get('error') or 'none'}")
        else:
            c = ld.get("can") or {}
            row["can"] = c
            row["runs"] = [k for k, ok in (("pytest", c.get("pytest") and c.get("git")), ("go", c.get("go")),
                                           ("cargo", c.get("cargo")), ("agent", c.get("opencode")),
                                           ("model", c.get("model"))) if ok]
            row["slots"] = max(1, int((ld.get("gate") or {}).get("offered") or 1)) if "pytest" in row["runs"] else 0
            if not row["slots"]:
                row["why"] = "no pytest or no git there: it takes no test piece"
        out.append(row)
    return out


def inventory_lines(rows):
    L_ = [f"INVENTORY  {len(rows)} device(s) of the household list"]
    for r in rows:
        ld = r["load"]
        if ld:
            model = {True: "yes", False: "no"}.get((r["can"] or {}).get("model"), "?")
            facts = (f"{ld.get('os', '?')} {ld.get('cores', '?')} cores, RAM {ld.get('ram_free', 0) / 2 ** 30:.1f}/"
                     f"{ld.get('ram_total', 0) / 2 ** 30:.0f} GB free, {r['slots']} slot(s), python "
                     f"{(r['can'] or {}).get('python', '?')}, local model {model}; runs: "
                     f"{', '.join(r['runs']) or 'nothing'}")
        else:
            facts = r["why"]
        L_.append(f"  {r['state']:<13} {r['tag']:<12} {r['device'] or '-':<20} {facts}")
    n = sum(1 for r in rows if r["slots"])
    L_.append(f"VERDICT  {sum(r['up'] for r in rows)} of {len(rows)} up; {n} take test pieces "
              f"({sum(r['slots'] for r in rows)} slot(s))")
    L_.append("NEXT: " + ("fieldkit orchestrate split --tree REPO (the plan), then --run" if n else
                          "no device can take a test piece: the DETAIL of each row says why"))
    return L_


# ---- the split ---------------------------------------------------------------------------------------------------

def test_files(tree):
    """-> [relative posix path] of tests/test_*.py under the tree, sorted."""
    return sorted(p.relative_to(tree).as_posix() for p in (Path(tree) / "tests").glob("test_*.py") if p.is_file())


def weight(path):
    try:
        return len(TEST_DEF.findall(Path(path).read_text(encoding="utf-8", errors="replace"))) + 1
    except OSError:
        return 1


def split(tree, n=DEFAULT_PIECES, files=None):
    """-> [{id, files, weight, acceptance}]: each test file in exactly one piece, the pieces as even as the weights
    allow (largest first into the lightest). Raises ValueError when the tree has no test file."""
    files = test_files(tree) if files is None else list(files)
    if not files:
        raise ValueError(f"{tree}: no tests/test_*.py")
    n = max(1, min(int(n), len(files)))
    w = {f: weight(Path(tree) / f) for f in files}
    bins = [[0, i, []] for i in range(n)]
    for f in sorted(files, key=lambda f: (-w[f], f)):
        b = min(bins, key=lambda b: (b[0], b[1]))
        b[0] += w[f]
        b[2].append(f)
    bins.sort(key=lambda b: (-b[0], b[1]))
    return [{"id": f"p{i + 1:02d}", "files": sorted(b[2]), "weight": b[0],
             "acceptance": "the pytest PASS line: >= 1 passed, 0 failed, 0 errors"} for i, b in enumerate(bins)]


def accept(output):
    """-> (accepted, the PASS line or why not). The piece's acceptance test; the exit code is not looked at."""
    c = W.test_counts(output or "")
    line = next((ln.strip() for ln in reversed((output or "").splitlines()) if W.PYTEST_SUMMARY.search(ln)), "")
    if c is None:
        return False, "no pytest summary line in the output"
    if c["passed"] < 1:
        return False, f"no test passed ({line or 'no count'})"
    if c["failed"] or c["errors"]:
        return False, f"{c['failed']} failed, {c['errors']} error(s): {line}"
    return True, line


def plan(pieces, devices):
    """-> {piece id: device name}: largest piece first onto the slot with the least weight dealt so far (every slot
    taken as equally fast). Devices: inventory rows with slots."""
    slots = [[0, d["device"] or d["tag"], i] for d in devices for i in range(d["slots"])]
    if not slots:
        return {}
    out = {}
    for p in pieces:                                  # already largest first
        s = min(slots, key=lambda s: (s[0], s[1], s[2]))
        s[0] += p["weight"]
        out[p["id"]] = s[1]
    return out


# ---- the run -----------------------------------------------------------------------------------------------------

def run_here(piece, tree, timeout=3600):
    """-> (output, seconds): the piece's pytest in the exported tree, inside one admission slot of this computer."""
    from . import admission
    t0 = time.time()
    cmd = admission.suite_cmd(piece["files"])
    with admission.admitted("test", f"split {piece['id']} ({len(piece['files'])} files)"):
        try:
            p = subprocess.run(cmd, cwd=str(tree), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
            out = p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            out = f"timed out after {timeout} s\n{e.stdout or ''}"
    shutil.rmtree(cmd[cmd.index("--basetemp") + 1], ignore_errors=True)
    return out, time.time() - t0


def run_there(piece, device, tree, wait=3600, project="Gorilla.Fieldkit", start=None, wait_fn=None,
              result_fn=None):
    """-> (output, seconds): the piece as a C3 run job on DEVICE (fieldkit task start --on DEV --run), the signed
    result's evidence (exit, then the log tail with the pytest summary). Anything short of a signed result is said
    in the output, which then fails the acceptance."""
    from .lifecycle import tasks
    from .mandate import jobs
    t0 = time.time()
    try:
        rec = (start or tasks.start_run)(["fieldkit", "admit", "suite", *piece["files"]], device, str(tree),
                                         project=project, wall=max(1, int(wait // 60)))
        if rec.get("status") != "started":
            return f"{device} did not take it: {rec.get('answer')}", time.time() - t0
        e = (wait_fn or jobs.wait)(rec["job"], wait)
        r = (result_fn or W.verified_result)(rec["job"], project, rec.get("digest") or "") if e else None
        if r is None:
            return f"{device}: no result signed by a pinned worker key within {wait:g} s (job {rec['job']})", \
                time.time() - t0
        return f"{r.get('status')} on {r.get('host')} ({r.get('os')}), job {rec['job']}\n{r.get('evidence', '')}", \
            time.time() - t0
    except Exception as e:  # noqa: BLE001 - fail closed: the piece is not finished, and the reason is said
        return f"placing on {device} failed: {type(e).__name__}: {e}", time.time() - t0


def run(pieces, devices, tree, me, wait=3600, here=None, there=None, now=time.time, say=None):
    """A queue: one worker per slot of each device; each takes the next piece (largest first) when free.
    -> {pieces: [{id, device, accepted, line, seconds}], wall_s, finished, per_hour, per_device}."""
    say = say or (lambda s: print(s, flush=True))
    here = here or (lambda p: run_here(p, tree, wait))
    there = there or (lambda p, dev: run_there(p, dev, tree, wait))
    queue = list(pieces)
    lock = threading.Lock()
    done = []

    def worker(dev, local):
        while True:
            with lock:
                if not queue:
                    return
                p = queue.pop(0)
            say(f"PIECE  {p['id']} ({len(p['files'])} files) -> {dev}")
            out, secs = here(p) if local else there(p, dev)
            ok, line = accept(out)
            with lock:
                done.append({"id": p["id"], "device": dev, "accepted": ok, "line": line, "seconds": round(secs, 1),
                             "files": p["files"]})
            say(f"PIECE  {p['id']} on {dev}: {'ACCEPTED' if ok else 'NOT ACCEPTED'} in {secs:.0f} s: {line}")

    t0 = now()
    threads = [threading.Thread(target=worker, args=(d["device"] or d["tag"], d["state"] == "HERE"), daemon=True)
               for d in devices for _ in range(d["slots"])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = max(now() - t0, 1e-9)
    fin = [d for d in done if d["accepted"]]
    per = {}
    for d in done:
        x = per.setdefault(d["device"], {"pieces": 0, "accepted": 0, "seconds": 0.0})
        x["pieces"] += 1
        x["accepted"] += d["accepted"]
        x["seconds"] = round(x["seconds"] + d["seconds"], 1)
    return {"pieces": sorted(done, key=lambda d: d["id"]), "wall_s": round(wall, 1), "finished": len(fin),
            "of": len(pieces), "per_hour": round(len(fin) / (wall / 3600), 2), "per_device": per, "me": me}


def run_lines(r):
    L_ = [f"SPLIT RUN  {r['of']} piece(s) in {r['wall_s']:.0f} s"]
    for p in r["pieces"]:
        L_.append(f"  {'PASS' if p['accepted'] else 'FAIL'}  {p['id']}  {p['device']:<20} {p['seconds']:>6.0f} s  "
                  f"{p['line']}")
    for dev, x in sorted(r["per_device"].items()):
        L_.append(f"  DEVICE {dev:<20} {x['accepted']} of {x['pieces']} accepted, {x['seconds']:.0f} s of work")
    L_.append(f"VERDICT  {r['finished']} of {r['of']} pieces finished: {r['per_hour']:.2f} pieces per hour")
    L_.append("NEXT: " + ("nothing; every piece passed its acceptance" if r["finished"] == r["of"] else
                          "each FAIL row says why; run its files with fieldkit testcost or fieldkit admit suite"))
    return L_


# ---- the command -------------------------------------------------------------------------------------------------

def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit orchestrate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["inventory", "split"])
    ap.add_argument("--tree", default=".", help="split: the git checkout whose test suite is split (.)")
    ap.add_argument("--pieces", type=int, default=DEFAULT_PIECES, help=f"split: how many pieces ({DEFAULT_PIECES})")
    ap.add_argument("--run", action="store_true", help="split: run the pieces (here and over the cable)")
    ap.add_argument("--wait", type=float, default=3600.0, help="split --run: seconds a piece may take (3600)")
    ap.add_argument("--out", help="split --run: also write the result as JSON to this file")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = inventory()
    if a.action == "inventory":
        print(json.dumps({"devices": rows}, indent=1, ensure_ascii=False, default=str) if a.json
              else "\n".join(inventory_lines(rows)))
        return 0 if any(r["slots"] for r in rows) else 3
    devices = [r for r in rows if r["slots"]]
    if not devices:
        print("\n".join(inventory_lines(rows)))
        return 3
    repo = Path(a.tree).resolve()
    tree, key = L.export_tree(repo)
    try:
        pieces = split(tree, a.pieces)
        where = plan(pieces, devices)
        if not a.run:
            out = {"tree": key, "pieces": [dict(p, device=where.get(p["id"])) for p in pieces]}
            if a.json:
                print(json.dumps(out, indent=1, ensure_ascii=False))
            else:
                onto = ", ".join(f"{d['device'] or d['tag']} ({d['slots']} slot(s))" for d in devices)
                print(f"SPLIT  {len(pieces)} piece(s) of {sum(len(p['files']) for p in pieces)} test files, tree "
                      f"{key[:12]}, onto {onto}")
                for p in pieces:
                    print(f"  {p['id']}  weight {p['weight']:>4}  {len(p['files']):>3} files  -> {where[p['id']]}  "
                          f"accept: {p['acceptance']}")
                print("NEXT: fieldkit orchestrate split --run (a queue: each slot takes the next piece when free)")
            return 0
        me = next((d["device"] or d["tag"] for d in devices if d["state"] == "HERE"), "")
        r = dict(run(pieces, devices, tree, me, wait=a.wait), tree=key)
        if a.out:
            Path(a.out).write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(r, indent=1, ensure_ascii=False) if a.json else "\n".join(run_lines(r)))
        return 0 if r["finished"] == r["of"] else 3
    finally:
        shutil.rmtree(tree.parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
