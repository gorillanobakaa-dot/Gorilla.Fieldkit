"""The capture loop (P18, owner 2026-11-10): a build records, the end makes tools.

    begin(lesson, who)   capture on for the whole run (up to BUILD_MAX_MINUTES), unless someone else already has it on
    end(token)           capture off (only if we switched it on), then distil what the run recorded

`distil(since)` runs episodes, signatures, refusals, intents and classify on the store, then for every intent group that
has an episode newer than `since`:
  - a recipe that replays deterministically becomes a CANDIDATE TOOL under state/capture/candidates/ID/: a script, a
    tools.yaml card draft with `test:`, and a test; autoadopt then tries it (test, `fieldkit mutate`, privacy scan) and
    adopts it in one pull request per run, or ledgers the failed step;
  - a group that does not replay (or has no anchor command) becomes a LEDGER line with its reason in
    state/capture/agent-ledger.jsonl, for an agent to finish. The owner is never asked.
Nothing here executes a captured command: scripts are written, never run.
"""
import json
import os
import re
import shlex
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import store as st
from . import switch

BUILD_MAX_MINUTES = 7 * 24 * 60
LEDGER = "agent-ledger.jsonl"
# P26: the lesson of a job names its project, so the distil can sort by project (first match wins).
PROJECTS = (("firefox", r"firefox|gorilla-firefox|\bbh-|build-harness|migrate"),
            ("kernel", r"kernel"),
            ("opencode", r"opencode"))


def _root():
    from ..core import settings
    return Path(settings.ROOT)


def _home(home):
    """FIELDKIT_CAPTURE_HOME moves where the agents' settings files are looked for (proofs and tests use a sandbox)."""
    import os
    return home or os.environ.get("FIELDKIT_CAPTURE_HOME") or None


def project_of(text):
    """-> "firefox" | "kernel" | "opencode" | None: which project's harness section a lesson, a recipe or a pipeline
    name, or a working directory belongs to."""
    low = str(text or "").lower()
    return next((name for name, rx in PROJECTS if re.search(rx, low)), None)


@contextmanager
def job(project_text, name, begin_fn=None, end_fn=None):
    """A project job (a staged pipeline, a migration step) records for as long as it runs, like an emerge run. A capture
    someone else switched on is left alone (and nothing is distilled here: its owner distils). Never fails the job."""
    tok = None
    try:
        proj = project_of(project_text)
        if proj and not os.environ.get("FIELDKIT_EMERGE_NO_CAPTURE"):      # the switch tests and CI set (emerge's)
            tok = (begin_fn or begin)(f"{proj} {name}", f"{proj}:{name}:{os.getpid()}")
    except Exception:  # noqa: BLE001
        tok = None
    try:
        yield tok
    finally:
        if tok and tok.get("owner"):
            try:
                (end_fn or end)(tok)
            except Exception:  # noqa: BLE001
                pass


def begin(lesson, who, home=None, py=None, now=None):
    """-> token. owner False when a switch (hand-set or another build's) is already on: then we never switch it off."""
    cur = switch.read()
    if switch.is_on(cur, now) and not cur.get("legacy"):
        return {"owner": False, "who": who, "since": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "note": f"already on ({cur.get('owner') or 'by hand'})"}
    d = switch.turn_on(lesson, BUILD_MAX_MINUTES, home=_home(home), py=py, now=now, owner=who, cap=BUILD_MAX_MINUTES)
    return {"owner": True, "who": who, "since": d["since"], "note": f"on until {d['until']}"}


def end(token, home=None, run=None, system=None, now=None):
    """Switch off what we switched on, then distil. -> dict with 'off' and 'distil'."""
    out = {"off": "left alone: " + token.get("note", "") if not token.get("owner") else None, "distil": None}
    if token.get("owner"):
        cur = switch.read()
        if cur.get("owner") == token["who"]:
            kw = {"home": _home(home), "system": system, "now": now}
            if run:
                kw["run"] = run
            switch.turn_off(**kw)
            out["off"] = "capture OFF"
        else:
            out["off"] = "left alone: the switch is now someone else's"
    out["distil"] = distil(token.get("since"), token["who"])
    return out


# ---- distil -----------------------------------------------------------------------------------------------------------
def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "x"


def _ledger(entry):
    p = st.capture_dir() / LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return str(p)


def _script(rec):
    lines = ["#!/usr/bin/env bash", f"# {rec['title']}", f"# candidate tool distilled from {rec['episodes']['in_group']} captured episodes "
             f"({rec['episodes']['replayed_ok']} replayed); review before use: it has never been run.", "set -euo pipefail"]
    for s in rec["steps"]:
        cmd = s.get("command") or s.get("template") or ""
        lines.append(f"# {s.get('sentence', s['id'])}  [check: {s.get('check', '')}]")
        lines.append(cmd if cmd else "true")
    return "\n".join(lines) + "\n"


def _card(cid, rec, rel):
    import yaml
    return yaml.safe_dump([{
        "id": cid, "title": rec["title"], "harness": "method", "entry": ["bash", f"${{FIELDKIT}}/{rel}/{cid}.sh"],
        "path": f"${{FIELDKIT}}/{rel}", "effects": ["reads-files", "writes-files"], "safety": "reversible", "output": "own",
        "platforms": ["linux"], "platforms_why": "distilled from a Linux capture; Windows port not yet proven",
        "test": ["python", "-m", "pytest", "-q", f"${{FIELDKIT}}/{rel}/test_{cid}.py"],
        "notes": "candidate from `fieldkit capture` build loop; prove its test can fail with `fieldkit mutate`, then `fieldkit adopt`."}],
        sort_keys=False, allow_unicode=True, width=120)


def _test(cid, rec):
    anchors = [s.get("command") or s.get("template") or "" for s in rec["steps"]]
    return (f'"""Candidate test for {cid}: the script keeps every distilled step, in order, and has a check per step."""\n'
            "from pathlib import Path\n\n"
            f"STEPS = {json.dumps(anchors, ensure_ascii=False)}\n\n\n"
            f"def test_script_has_every_step_in_order():\n"
            f"    text = (Path(__file__).with_name({cid + '.sh'!r})).read_text(encoding='utf-8')\n"
            "    pos = -1\n    for s in STEPS:\n        i = text.find(s, pos + 1) if s else pos + 1\n"
            "        assert i > pos, f'step missing or out of order: {s}'\n        pos = i\n\n\n"
            "def test_script_is_strict():\n"
            f"    assert 'set -euo pipefail' in (Path(__file__).with_name({cid + '.sh'!r})).read_text(encoding='utf-8')\n")


def write_candidate(rec, out_dir=None):
    cid = _slug(rec["id"])
    d = Path(out_dir or st.capture_dir() / "candidates") / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.sh").write_text(_script(rec), encoding="utf-8", newline="\n")
    (d / f"test_{cid}.py").write_text(_test(cid, rec), encoding="utf-8", newline="\n")
    (d / "card.yaml").write_text(_card(cid, rec, f"local/kits/{cid}"), encoding="utf-8")
    return str(d)


def _auto_adopt(res, who, ship=None, repo=None):
    """Try every candidate (test, mutate, privacy); adopt the ones that pass in one pull request; ledger the rest."""
    from . import autoadopt as A
    passed = []
    for path in res["candidates"]:
        r = A.try_candidate(path)
        if r["ok"]:
            passed.append(path)
        else:
            res["ledger"].append(_ledger({"time": _now(), "run": who, "intent": None, "episodes": 0, "candidate": r["id"],
                                          "step": r["step"], "reason": f"candidate {r['id']} refused at step {r['step']}: {r['reason']}"}))
    done = A.adopt_all(passed, repo or _root(), who, ship=ship)
    for cid, why in done["failed"].items():
        res["ledger"].append(_ledger({"time": _now(), "run": who, "intent": None, "episodes": 0, "candidate": cid,
                                      "step": "adopt", "reason": f"candidate {cid} passed its checks but {why}"}))
    if done["adopted"] and done["shipped"] is False:
        res["ledger"].append(_ledger({"time": _now(), "run": who, "intent": None, "episodes": 0, "step": "ship",
                                      "reason": f"adopted {', '.join(done['adopted'])} on branch {done['branch']} but the push failed: {done['note']}"}))
    res["adopted"] = done["adopted"]
    res["adopt"] = done


def distil(since, who="manual", store=None, say=print, root=None, adopt=None, ship=None):
    """The whole chain on the store; -> {candidates:[path], adopted:[id], ledger:[reason], ...}. Never raises: a failure is a
    ledger line. Candidates are tried and adopted by themselves (autoadopt) unless adopt is False or FIELDKIT_CAPTURE_NO_ADOPT=1."""
    from . import classify as C
    from . import distill as D
    from . import episodes as E
    from . import recipes as R
    res = {"since": since, "candidates": [], "adopted": [], "ledger": [], "episodes": 0, "groups": {}}
    s = store or st.Store()
    try:
        s.recover()
        res["episodes"] = E.store_episodes(s)["episodes_found"]
        state = s.state()
        sm, assigned = D.stage4(s)
        D.apply_intents(s, assigned)
        state = s.state()
        fresh = {eid: e for eid, e in state.get("episodes", {}).items() if not since or str(e.get("end", "")) >= since}
        intents = sorted({e.get("intent") for e in fresh.values() if e.get("intent")})
        sigs = {}
        for intent in intents:
            if intent == "other":
                n = sum(1 for e in fresh.values() if e.get("intent") == "other")
                res["groups"][intent] = "no intent"
                res["ledger"].append(_ledger({"time": _now(), "run": who, "intent": intent, "episodes": n,
                                              "reason": f"{n} episode(s) of this run match no intent group: nothing to align"}))
                continue
            rec = R.build(s, intent, None, sigs)
            if rec["accepted"]:
                path = write_candidate(rec)
                res["candidates"].append(path)
                res["groups"][intent] = f"candidate {path}"
            else:
                if not rec["episodes"]["in_group"]:
                    n = sum(1 for e in fresh.values() if e.get("intent") == intent)
                    res["groups"][intent] = "ledger"
                    res["ledger"].append(_ledger({"time": _now(), "run": who, "intent": intent, "episodes": n,
                                                  "reason": f"{n} episode(s) of this run, none with 3+ steps and an anchor command of "
                                                            f"the {intent} flow: too short to align into a recipe"}))
                    continue
                proc, marks = R.procedure(s, rec)
                D.apply_marks(s, marks)
                res["groups"][intent] = "ledger"
                res["ledger"].append(_ledger({"time": _now(), "run": who, "intent": intent, "episodes": rec["episodes"]["in_group"],
                                              "reason": proc["reason"]}))
        if res["candidates"] and adopt is not False and not os.environ.get("FIELDKIT_CAPTURE_NO_ADOPT"):
            _auto_adopt(res, who, ship=ship, repo=root)
        marks, rules = C.classify(s)
        res["classified"] = C.apply(s, marks)
        if not fresh:
            res["ledger"].append(_ledger({"time": _now(), "run": who, "intent": None, "episodes": 0,
                                          "reason": "capture recorded no episode since this run began (no agent tool call was hooked)"}))
    except Exception as e:  # noqa: BLE001  a distil failure must never fail a build
        res["ledger"].append(_ledger({"time": _now(), "run": who, "intent": None, "episodes": 0,
                                      "reason": f"distil failed: {type(e).__name__}: {e}"}))
    say(f"capture distil: {res['episodes']} episode(s), {len(res['candidates'])} candidate tool(s), {len(res['adopted'])} adopted, "
        f"{len(res['ledger'])} ledger line(s) ({st.capture_dir() / LEDGER})")
    return res


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
