"""Common sense for the leak gate: what changed since the last PASSED gate, and could any of it reach the network?

    fieldkit build-harness leakgate-scope <task> [from=<tree>]

Born 2026-10-10. The owner: "this is basically the same version we have only released to the public and built. If
nothing has changed too much, the logic dictates that all the tests we have previously run for tens of hours ARE STILL
VALID. The leak gate needs some common sense." This reads, never runs, and never relaxes the gate by itself: it says
which files changed between the source tree the last passed gate tested and the tree now, and sorts them into
  - cannot reach the network: pictures, style sheets, text strings, documents (.png .jpg .webp .ico .svg .css .ftl
    .md .txt), and only when none of the lines the change ADDS holds a web address (http:// or https://, SVG and
    XML namespace declarations excepted);
  - can: everything else, and anything it cannot read (fail closed).
Verdict: nothing that can reach the network changed -> the last pass still stands for every scene, and a short
confirmation run is enough; otherwise -> the full gate, with the files that make it necessary. How the gate uses the
verdict is the maintainer's decision (leak-gate rules are approved by the maintainer only, D-157-10).
The last pass: the newest launcher result with exit 0; its build's tree from builds.jsonl, or KNOWN_TREES for builds
verified before that record existed (157.0 as released, 2026-10-04: the tree its release page names).
"""
import json
import re
import subprocess
from pathlib import Path

SAFE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".ico", ".svg", ".css", ".ftl", ".md", ".txt")
URL = re.compile(r"https?://", re.I)
NAMESPACE = re.compile(r"xmlns(:\w+)?=\"https?://|https?://www\.w3\.org/", re.I)
KNOWN_TREES = {"20261004224302": "109fd17fb5b9926e5e0c17115e9a69418b8173be"}     # 157.0 as released (release page)
BUILD_LINE = re.compile(r"leakgate: build (\d{14})")


def _git(w, *args, run=subprocess.run):
    return run(["git", "-C", str(w), *args], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def last_pass(launcher_dir, task_id, builds_jsonl):
    """-> {"run", "build_id", "tree"} of the newest passed gate run, or None."""
    from .summary import read
    for res in sorted(Path(launcher_dir).glob(f"{task_id}-*.result.json"), reverse=True):
        try:
            r = json.loads(res.read_text(encoding="utf-8-sig"))
        except ValueError:
            continue
        if r.get("exit_code") != 0 or r.get("mode") != "release":
            continue
        log = Path(r.get("log") or res.with_suffix("").with_suffix(".log"))
        m = BUILD_LINE.search(read(log)) if log.is_file() else None
        bid = m.group(1) if m else None
        tree = KNOWN_TREES.get(bid)
        if Path(builds_jsonl).is_file():
            for l in Path(builds_jsonl).read_text(encoding="utf-8").splitlines():
                d = json.loads(l) if l.strip() else {}
                if d.get("build_id") == bid:
                    tree = d.get("tree")
        return {"run": res.name.replace(".result.json", ""), "build_id": bid, "tree": tree}
    return None


def classify(w, a, b, run=subprocess.run):
    """Files changed between trees a and b -> (cannot, can): lists of (path, why)."""
    names = [l for l in _git(w, "diff", "--name-only", a, b, run=run).splitlines() if l.strip()]
    cannot, can = [], []
    for n in names:
        if not n.lower().endswith(SAFE_SUFFIXES):
            can.append((n, "code or configuration"))
            continue
        added = [l[1:] for l in _git(w, "diff", a, b, "--", n, run=run).splitlines()
                 if l.startswith("+") and not l.startswith("+++")]
        urls = [l for l in added if URL.search(NAMESPACE.sub("", l))]
        if urls:
            can.append((n, f"adds a web address: {urls[0].strip()[:80]}"))
        else:
            cannot.append((n, "picture, style or text, no web address added"))
    return cannot, can


def run(w, launcher_dir, task_id, builds_jsonl, from_tree=None, run_cmd=subprocess.run):
    """-> {"from", "to", "cannot", "can", "verdict"}."""
    lp = last_pass(launcher_dir, task_id, builds_jsonl)
    a = from_tree or (lp or {}).get("tree")
    b = _git(w, "rev-parse", "HEAD^{tree}", run=run_cmd).strip()
    if not a:
        return {"from": None, "to": b, "cannot": [], "can": [], "last_pass": lp,
                "verdict": "full gate: no passed gate whose source tree is known"}
    cannot, can = classify(w, a, b, run=run_cmd)
    verdict = ("the last pass still stands: nothing that can reach the network changed; a short confirmation run is enough"
               if not can else f"full gate: {len(can)} changed file(s) can reach the network")
    return {"from": a, "to": b, "cannot": cannot, "can": can, "last_pass": lp, "verdict": verdict}
