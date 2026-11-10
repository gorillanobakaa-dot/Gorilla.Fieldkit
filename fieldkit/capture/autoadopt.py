"""P18 last step (owner 2026-11-10): a candidate tool is tried by itself and, when it passes, adopted and shipped.

`try_candidate(dir)` runs three checks, in this order; the first that fails stops it and names the step:
  test     the candidate's own test passes on the untouched script;
  mutate   `fieldkit mutate` breaks the script (one swap per step, and the strict header): the test must fail every
           time, so a test that cannot fail is refused;
  privacy  `fieldkit privacy scan` finds nothing in the candidate's files.
`adopt_all(dirs)` puts every candidate that passed into a fresh worktree of origin/main (the card appended to
fieldkit/desk/tools.yaml, script + test under fieldkit/capture/adopted/ID/, the platform index rewritten), commits on a
branch and ships it with `fieldkit ship push --auto`: ONE pull request per run. A candidate that failed becomes a line
in state/capture/agent-ledger.jsonl with the failed step. Nothing here runs a captured command: the script is only
read and swapped as text; the test only reads the script. The owner is never asked and no check is weakened.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..core import privacy
from ..method import mutate as M

ADOPTED = "fieldkit/capture/adopted"
TESTS = "tests/capture/adopted"
STRICT = "set -euo pipefail"


def _step_lines(script_text):
    """The command lines of a candidate script: every line that is not a comment, the shebang or the strict header."""
    return [ln for ln in script_text.splitlines() if ln.strip() and not ln.startswith("#") and ln != STRICT]


def _plan(script):
    text = script.read_text(encoding="utf-8")
    plan = [{"file": str(script), "swap": [STRICT, "set -e"], "why": "the strict header is checked"}]
    seen = set()
    for ln in _step_lines(text):
        if ln in seen or ln == "true":
            continue
        seen.add(ln)
        # the swap names the whole line with its newlines so it cannot match inside a comment
        plan.append({"file": str(script), "swap": ["\n" + ln + "\n", "\ntrue\n"], "count": max(1, text.count("\n" + ln + "\n")),
                     "why": f"step kept: {ln[:60]}"})
    return plan


def try_candidate(cdir, run_test=None):
    """-> {ok, step, reason, id}. step is the failed step (test | mutate | privacy | shape) or "" when all three passed."""
    cdir = Path(cdir)
    cid = cdir.name
    script, test, card = cdir / f"{cid}.sh", cdir / f"test_{cid}.py", cdir / "card.yaml"
    missing = [p.name for p in (script, test, card) if not p.is_file()]
    if missing:
        return {"ok": False, "id": cid, "step": "shape", "reason": f"candidate is missing {', '.join(missing)}"}
    for f in (script, test):                      # LF only: a CRLF script cannot be swapped line by line (Windows, 2026-11-10)
        raw = f.read_bytes()
        if b"\r\n" in raw:
            f.write_bytes(raw.replace(b"\r\n", b"\n"))
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test)]
    plan = _plan(script)
    if len(plan) < 2:
        return {"ok": False, "id": cid, "step": "mutate", "reason": "the script has no step to break: nothing proves its test can fail"}
    r = M.mutate(plan, argv, cwd=str(cdir), timeout=300)
    if r["baseline"] != "passed":
        return {"ok": False, "id": cid, "step": "test", "reason": "its own test fails on the untouched script: " + r.get("detail", "")[-300:].strip()}
    if not r["ok"]:
        bad = [x for x in r["results"] if x["verdict"] != "caught"]
        what = "; ".join(f"{x['verdict']}: {x.get('why') or x.get('detail', '')}" for x in bad[:3])
        return {"ok": False, "id": cid, "step": "mutate", "reason": f"the test cannot fail ({r['survived']} swap(s) survived, "
                f"{r['refused']} refused): {what}"}
    found = privacy.scan_path(cdir)
    if found:
        kinds = sorted({h["kind"] for hits in found.values() for h in hits})
        return {"ok": False, "id": cid, "step": "privacy", "reason": f"privacy scan finding(s) {', '.join(kinds)} in "
                f"{', '.join(Path(f).name for f in found)}"}
    return {"ok": True, "id": cid, "step": "", "reason": "test passed, every mutation caught, privacy scan clean"}


# ---- adopt: a worktree of origin/main, a branch, one pull request ---------------------------------------------------
def _git(wt, *a, check=True):
    r = subprocess.run(["git", "-C", str(wt), *a], capture_output=True, text=True, encoding="utf-8", timeout=300)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def _card_text(cdir, cid):
    """The candidate's card with its paths moved to where install puts the files (script beside the others, test in tests/)."""
    import yaml
    cards = yaml.safe_load((Path(cdir) / "card.yaml").read_text(encoding="utf-8"))
    for c in cards:
        c["path"] = "${FIELDKIT}/" + ADOPTED
        c["entry"] = ["python", "-m", "fieldkit", "capture", "adopted", cid]     # one command: `fieldkit capture adopted ID`
        c["test"] = ["python", "-m", "pytest", "-q", f"${{FIELDKIT}}/{TESTS}/test_{cid}.py"]
        c["notes"] = ("Adopted by itself from a `fieldkit capture` build run: its test passed, `fieldkit mutate` broke the script "
                      "and the test caught every swap, the privacy scan was clean. " + str(c.get("notes", "")))
    return yaml.safe_dump(cards, sort_keys=False, allow_unicode=True, width=120)


def install(wt, cdir):
    """Put one passed candidate into the worktree `wt`: script, test, card. -> the files written (relative)."""
    wt, cdir = Path(wt), Path(cdir)
    cid = cdir.name
    tools = wt / "fieldkit" / "desk" / "tools.yaml"
    body = tools.read_text(encoding="utf-8")
    if re.search(rf"(?m)^- id: {re.escape(cid)}$", body):
        raise ValueError(f"a card with id {cid} already exists in tools.yaml")
    (wt / ADOPTED).mkdir(parents=True, exist_ok=True)
    (wt / TESTS).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cdir / f"{cid}.sh", wt / ADOPTED / f"{cid}.sh")
    old = f"Path(__file__).with_name({cid + '.sh'!r})"
    text = (cdir / f"test_{cid}.py").read_text(encoding="utf-8")
    if old not in text:
        raise ValueError("the candidate test does not locate its script the expected way")
    new = f"(Path(__file__).resolve().parents[3] / {ADOPTED + '/' + cid + '.sh'!r})"
    (wt / TESTS / f"test_{cid}.py").write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    tools.write_text(body.rstrip("\n") + "\n\n" + _card_text(cdir, cid), encoding="utf-8", newline="\n")
    return [f"{ADOPTED}/{cid}.sh", f"{TESTS}/test_{cid}.py", "fieldkit/desk/tools.yaml"]


def _default_ship(wt):
    r = subprocess.run(["fieldkit", "ship", "push", "--auto"], cwd=str(wt), capture_output=True, text=True, encoding="utf-8", timeout=3600)
    return r.returncode == 0, (r.stdout + r.stderr)[-400:].strip()


def adopt_all(passed, repo, run_id, ship=None, base="origin/main"):
    """passed: candidate dirs that passed try_candidate. -> {adopted:[ids], failed:{id: reason}, branch, shipped, note}."""
    out = {"adopted": [], "failed": {}, "branch": None, "shipped": None, "note": ""}
    if not passed:
        return out
    ship = ship or _default_ship
    scratch = Path(tempfile.mkdtemp(prefix="capture-adopt-"))
    wt = scratch / "wt"
    branch = "feat/capture-adopt-" + re.sub(r"[^a-z0-9]+", "-", str(run_id).lower()).strip("-")[:40]
    try:
        _git(repo, "worktree", "add", "-b", branch, str(wt), base)
        files = set()
        for cdir in passed:
            try:
                files.update(install(wt, cdir))
                out["adopted"].append(Path(cdir).name)
            except Exception as e:  # noqa: BLE001
                out["failed"][Path(cdir).name] = f"adopt: {e}"
        if not out["adopted"]:
            return out
        subprocess.run([sys.executable, "-m", "fieldkit.core.platform_gate", "--write"], cwd=str(wt), check=True, capture_output=True)
        files.add("fieldkit/desk/platform_index.json")
        _git(wt, "add", "--", *sorted(files))
        ident = [] if _git(wt, "config", "user.email", check=False).stdout.strip() else [
            "-c", "user.name=fieldkit capture", "-c", "user.email=capture@users.noreply.github.com"]
        _git(wt, *ident, "commit", "-m", f"capture: adopt {len(out['adopted'])} tool(s) distilled from {run_id}\n\n" + "\n".join(f"- {i}" for i in out["adopted"]))
        out["branch"] = branch
        out["shipped"], out["note"] = ship(wt)
    except Exception as e:  # noqa: BLE001  adopting must never fail a build
        out["note"] = f"adopt failed: {type(e).__name__}: {e}"
        out["shipped"] = False
        out["failed"].update({i: out["note"] for i in out["adopted"]})
        out["adopted"] = []
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        try:
            scratch.rmdir()                  # our own, now empty folder: git removed the worktree above
        except OSError:
            pass
    return out


def scripts(root=None):
    """{id: path} of the adopted tools' scripts in this checkout."""
    base = Path(root or Path(__file__).resolve().parents[2]) / ADOPTED
    return {p.stem: p for p in sorted(base.glob("*.sh"))}


def run(cid, root=None):
    """`fieldkit capture adopted ID`: run the adopted tool's script (never the unreviewed candidate); -> exit code."""
    p = scripts(root).get(cid)
    if p is None:
        print(f"no adopted tool {cid}; `fieldkit capture adopted` lists them", file=sys.stderr)
        return 2
    return subprocess.run(["bash", str(p)]).returncode
