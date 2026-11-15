"""The covering tests of a commit, split across the free devices instead of dropped (HARNESS-PLAN R15).

Owner, 2026-11-02: "a fixed 15-file cap does not scale to 20-50 machines". Above the laptop's share (the cap), the
pre-commit ran NOTHING (cli.py fans out to 59-74 test files). Now:

    this computer   keeps the `cap` heaviest test files and runs them
    other devices   every idle, capable device (orchestrate_load.idle/capable; the OS does not matter, a skipped
                    test is not a failure) gets a share of the rest, dealt largest first onto the lightest slot
                    (weights as in orchestrate_split: `def test_` lines + 1); a share goes as a signed C3
                    `admit suite` job
    no device free  the overflow is NOT dropped: it joins this computer's list (the guard still brakes it)

    python -m fieldkit.testsplit start --key KEY FILES...   dispatch; stdout = the files to run HERE, one per line
    python -m fieldkit.testsplit collect --key KEY          wait for the remote shares; exit 0 all signed passes,
                                                            10 a share has no signed pass (missing, unsigned or
                                                            FAILED on the other device): its files on stdout, run
                                                            them here, where they must pass (a peer's failure alone
                                                            never refuses a commit, 2026-11-03: the VAIO's own two
                                                            environment failures refused #350)

A remote result counts only when a pinned worker key signed it (orchestrate_work.verified_result) and says pass; an
unsigned, late or missing answer is never a pass (fail closed: the files run here).
"""
import json
import shutil
import sys
from pathlib import Path

from . import orchestrate_split as S

UNVOUCHED = 10
WAIT_S = 45 * 60


def _pending_file(key):
    from . import testcost
    return testcost.state_dir() / f"split-{key[:16]}.json"


def deal(files, cap, devices, weight=None):
    """-> (local files, {device: [files]}). `devices` = [(name, slots)]. The `cap` heaviest files stay here; the rest
    is dealt largest first onto the slot with the least weight so far. No device: everything stays here."""
    weight = weight or (lambda f: 1)
    order = sorted(files, key=lambda f: (-weight(f), f))
    if not devices or len(order) <= cap:
        return order, {}
    local, rest = order[:max(cap, 0)], order[max(cap, 0):]
    slots = [[0, name, i] for name, n in devices for i in range(max(1, int(n)))]
    out = {}
    for f in rest:
        s = min(slots, key=lambda s: (s[0], s[1], s[2]))
        s[0] += weight(f)
        out.setdefault(s[1], []).append(f)
    return local, out


def free_devices(rows, me):
    """-> [(name, free slots)]: the other devices that answered, are idle and can run pytest and git."""
    from . import orchestrate_load as L
    return [(r["name"], r["gate"]["free"]) for r in rows
            if not r.get("me") and r.get("ok") and L.idle(r) and L.capable(r, me, any_os=True)]


def start(repo, files, key, cap, rows=None, send=None, say=None, me=None, project="Gorilla.Fieldkit", wall=40):
    """-> {"local": [files], "sent": {device: {"job", "digest", "files"}}, "why": str}. Never raises: anything over
    the cable failing keeps the files here."""
    from . import orchestrate_load as L
    from . import orchestrate_work as W
    from .peers import link
    say = say or (lambda s: print(s, file=sys.stderr, flush=True))
    files = list(files)
    if len(files) <= cap:
        return {"local": files, "sent": {}, "why": ""}
    try:
        me = me or link.os_tag()
        rows = L.load_all() if rows is None else rows
        devs = free_devices(rows, me)
    except Exception as e:  # noqa: BLE001 - no link: everything runs here
        devs = []
        say(f"testsplit: no load report ({type(e).__name__}: {e}): all {len(files)} test files run here")
    if not devs:
        say(f"testsplit: {len(files)} test files cover the change (share here: {cap}) and no other device is "
            "free: they all run here, none is dropped")
        return {"local": files, "sent": {}, "why": "no free device"}
    root = Path(repo)
    w = {f: S.weight(root / f) if (root / f).is_file() else 1 for f in files}
    local, shares = deal(files, cap, devs, lambda f: w.get(f, 1))
    try:
        tree, _ = L.export_tree(repo)
    except Exception as e:  # noqa: BLE001
        say(f"testsplit: could not export the tree ({e}): all {len(files)} test files run here")
        return {"local": files, "sent": {}, "why": "export failed"}
    sent, back = {}, []
    try:
        for name, mine in sorted(shares.items()):
            try:
                jid, said, t = (send or W.send)("run", name, str(tree), project, argv=["admit", "suite", *mine],
                                                 wall=wall)
            except Exception as e:  # noqa: BLE001
                jid, said, t = None, f"{type(e).__name__}: {e}", None
            if said != "sent":
                say(f"testsplit: {name} did not take {len(mine)} file(s) ({said}): they run here")
                back += mine
                continue
            sent[name] = {"job": jid, "digest": t["digest"], "files": mine}
            say(f"testsplit: {len(mine)} test file(s) -> {name}, job {jid}")
    finally:
        shutil.rmtree(Path(tree).parent, ignore_errors=True)
    local = sorted(local + back, key=lambda f: (-w.get(f, 1), f))
    say(f"testsplit: {len(local)} file(s) run here, {sum(len(s['files']) for s in sent.values())} on "
        f"{', '.join(sorted(sent)) or 'no device'}")
    if key and sent:
        f = _pending_file(key)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"sent": sent, "project": project}), encoding="utf-8")
    return {"local": local, "sent": sent, "why": ""}


def collect(key, wait_s=WAIT_S, wait_fn=None, result_fn=None, say=None):
    """-> (exit code, [files to run here], [failed lines]). Reads and removes the pending record of KEY."""
    from . import orchestrate_work as W
    from .mandate import jobs
    say = say or (lambda s: print(s, file=sys.stderr, flush=True))
    f = _pending_file(key)
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, [], []
    f.unlink(missing_ok=True)
    again, bad = [], []
    for name, s in sorted(rec.get("sent", {}).items()):
        e = (wait_fn or jobs.wait)(s["job"], wait_s)
        r = (result_fn or W.verified_result)(s["job"], rec.get("project", "Gorilla.Fieldkit"), s["digest"]) if e \
            else None
        if r is None:
            say(f"testsplit: {name}: no result signed by a pinned worker key (job {s['job']}): "
                f"its {len(s['files'])} file(s) run here")
            again += s["files"]
        elif r.get("status") == "pass":
            say(f"testsplit: {name}: PASS (signed, job {s['job']}): {len(s['files'])} test file(s)")
        else:
            first = (r.get("evidence") or "").splitlines()[:1]
            say(f"testsplit: {name}: {r.get('status')} (job {s['job']}) {first[0][:120] if first else ''}: its "
                f"{len(s['files'])} file(s) run here to confirm (another device's own environment can differ)")
            again += s["files"]
    return (UNVOUCHED if again else 0), again, []


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m fieldkit.testsplit")
    ap.add_argument("action", choices=["start", "collect"])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--key", default="")
    ap.add_argument("--cap", type=int, default=15)
    ap.add_argument("--repo", default=".")
    a = ap.parse_args(argv)
    if a.action == "start":
        r = start(a.repo, a.files, a.key, a.cap)
        print("\n".join(r["local"]))
        return 0
    rc, again, _ = collect(a.key)
    if again:
        print("\n".join(again))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
