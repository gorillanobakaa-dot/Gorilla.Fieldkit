"""P18 last step (owner 2026-11-10): a candidate tool is tried by itself and, when it passes, adopted and shipped.

`try_candidate(dir)` first applies the quality gate (adopt_rules.yaml + the tool router's test-runner list), then runs three checks, in this order; the first that fails stops it and names the step:
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
import json
import re
import shutil
import subprocess
from fieldkit import proc as fkproc
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


def _rules():
    import yaml
    return yaml.safe_load((Path(__file__).with_name("adopt_rules.yaml")).read_text(encoding="utf-8"))


def _toolchain(cmd, table):
    words = cmd.split()
    while words and (words[0] == "cd" or "=" in words[0].split("/")[0] or words[0] in ("sudo", "env", "time")):
        words = words[1:] if words[0] != "cd" else []
    if not words:
        return None
    w = Path(words[0]).name.lower()
    w = re.sub(r"\.exe$", "", w)
    if w in ("python", "python3", "py") and words[1:3] == ["-m", "fieldkit"]:
        return None
    return next((tc for tc, names in table.items() if w in names), None)


def _example_acts(cdir):
    """{episode id: actions} of the episodes the candidate's replay.json names, read from the capture journal (never from the
    script's text). Each action is (family, template, failed, description, error key); failed comes from the exit code."""
    from . import recipes as R
    from . import store as st
    info = json.loads((Path(cdir) / "replay.json").read_text(encoding="utf-8"))
    s = st.Store()
    state = s.state()
    return {e: R.episode_actions(s, state, state["episodes"][e]) for e in info.get("examples", []) if e in state.get("episodes", {})}


def journal_replays(cdir):
    """How many journal episodes replay THIS candidate's own steps: every step's command family ran in the episode and ended
    with exit code 0, in the order the script has them. Counted from the journal records, never from the script's header.
    -> (count, intent); (0, None) when the candidate carries no journal evidence."""
    from . import distill as D
    cdir = Path(cdir)
    try:
        intent = json.loads((cdir / "replay.json").read_text(encoding="utf-8")).get("intent")
        acts = _example_acts(cdir)
    except (OSError, ValueError, KeyError):
        return 0, None
    fams = [D.family(ln) for ln in _step_lines((cdir / f"{cdir.name}.sh").read_text(encoding="utf-8")) if ln != "true"]
    n = 0
    for seq in acts.values():
        pos, ok = -1, bool(fams)
        for f in fams:
            hit = next((i for i, a in enumerate(seq) if i > pos and a[0] == f and not a[2]), None)
            if hit is None:
                ok = False
                break
            pos = hit
        n += ok
    return n, intent


def _git_flag_error(line):
    """-> a message when `git SUB -flag` uses a flag git's own usage text does not list (`git add -a`), else "". Reads only
    `git SUB -h`; nothing the candidate says is run. A git that cannot answer is never a reason to refuse."""
    words = line.split()
    if len(words) < 3 or words[0] != "git" or words[1].startswith("-") or not any(w.startswith("-") for w in words[2:]):
        return ""
    try:
        r = fkproc.run(["git", words[1], "-h"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    usage = r.stdout + r.stderr
    if "usage:" not in usage.lower():
        return ""
    known = set(re.findall(r"(?<![\w-])(--?[A-Za-z][\w-]*)", usage))
    for w in words[2:]:
        if w == "--":
            break
        if not w.startswith("-"):
            continue
        flag = w.split("=")[0]
        letters = [flag] if flag.startswith("--") else ["-" + c for c in flag[1:]]
        bad = [x for x in letters if x not in known]
        if bad:
            return f"`{line[:50]}`: git {words[1]} has no {bad[0]} (its own usage text)"
    return ""


def _intent_of_step(line, anchors):
    from . import distill as D
    fam = D.family(line)
    return {i for i, names in anchors.items() if any(fam == a or fam.startswith(a + " ") or line.startswith(a) for a in names)}


def quality(script_text, replays=0, intent=None):
    """-> a reason string when the script is not fit to adopt, else "". Rules: adopt_rules.yaml; test runners: toolrouter.
    replays: the journal's count (journal_replays); the "(N replayed)" text in the script's header is only a label and is not read."""
    from ..autopilot.toolrouter import TEST_RUNNERS
    from . import recipes as R
    rules = _rules()
    steps = _step_lines(script_text)
    why = []
    ph = sorted({m.group(0) for ln in steps for m in re.finditer(rules["placeholder"], ln)})
    if ph:
        why.append("unresolved placeholder(s) " + ", ".join(ph))
    chains = sorted({t for t in (_toolchain(ln, rules["toolchains"]) for ln in steps) if t})
    if len(chains) > 1:
        why.append("steps from more than one toolchain (" + ", ".join(chains) + "): they come from unrelated projects")
    if any(p.search(ln) for ln in steps for p in TEST_RUNNERS):
        why.append("a test-runner command (tests run in GitHub CI, never from a tool)")
    for rule in rules.get("refuse_steps", []):
        hit = [ln for ln in steps if re.search(rule["pattern"], ln)]
        if hit:
            why.append(f"{rule['name']}: `{hit[0][:50]}` ({rule['why']})")
    flags = [m for m in (_git_flag_error(ln) for ln in steps) if m]
    if flags:
        why.append("broken git step: " + flags[0])
    if intent and intent in R.ANCHORS:                      # coherence: one intent, one flow
        foreign = [ln for ln in steps if (a := _intent_of_step(ln, R.ANCHORS)) and intent not in a]
        if foreign:
            why.append(f"not one flow: the {intent} candidate carries steps of other intents (`{foreign[0][:40]}`"
                       f"{', ...' if len(foreign) > 1 else ''}): stitched from unrelated episodes")
    if replays < int(rules["min_replayed"]):
        why.append("never replayed successfully (no journal episode replays these steps with exit code 0)")
    return "; ".join(why)


def try_candidate(cdir, run_test=None, replays=None):
    """-> {ok, step, reason, id}. step is the failed step (quality | test | mutate | privacy | shape) or "" when all three passed."""
    cdir = Path(cdir)
    cid = cdir.name
    script, test, card = cdir / f"{cid}.sh", cdir / f"test_{cid}.py", cdir / "card.yaml"
    missing = [p.name for p in (script, test, card) if not p.is_file()]
    if missing:
        return {"ok": False, "id": cid, "step": "shape", "reason": f"candidate is missing {', '.join(missing)}"}
    n, intent = (replays or journal_replays)(cdir)
    bad = quality(script.read_text(encoding="utf-8"), n, intent)
    if bad:
        return {"ok": False, "id": cid, "step": "quality", "reason": bad}
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
    r = fkproc.run(["git", "-C", str(wt), *a], capture_output=True, text=True, encoding="utf-8", timeout=300)
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
    r = fkproc.run(["fieldkit", "ship", "push", "--auto"], cwd=str(wt), capture_output=True, text=True, encoding="utf-8", timeout=3600)
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
        fkproc.run([sys.executable, "-m", "fieldkit.core.platform_gate", "--write"], cwd=str(wt), check=True, capture_output=True)
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
        fkproc.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
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
    return fkproc.run(["bash", str(p)]).returncode
