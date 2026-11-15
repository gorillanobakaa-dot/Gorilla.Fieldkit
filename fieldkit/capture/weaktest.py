"""P18 stage 6: give every accepted workflow recipe to the plain-language reader rules and to a small local model.

Reader part (no model): each step's sentence and check is put in the sitrep shape and `fieldkit sitrep check` is run
on it; the share of steps that pass is the measure. Model part: for each step the model is shown the step's check and
an observed output (one that should PASS, one that should FAIL, both built from the recipe's own data) and must answer
PASS or FAIL. The answer is scored against the known verdict. The model is loaded for the test and unloaded after.
"""
import json
import re
import subprocess
import urllib.request
from pathlib import Path

import yaml

from .. import sitrep

WORKFLOWS = Path(__file__).resolve().parent.parent / "method" / "workflows"
URL = "http://localhost:1234"
DEFAULT_MODEL = "google/gemma-4-12b-qat"


def load_recipes(folder=None):
    """Accepted recipes (those with steps and `accepted: true`); procedures are not recipes."""
    out = []
    for p in sorted(Path(folder or WORKFLOWS).glob("*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if d.get("accepted") and d.get("steps"):
            out.append(d)
    return out


def step_report(recipe, step):
    """One step as a sitrep-shaped report, so the reader rules can be applied to exactly its words."""
    goal = str(recipe.get("goal") or recipe.get("title") or "").strip()
    if goal and goal[-1] not in ".!?":
        goal += "."
    item = f"{step['sentence'].strip()} Check: {step['check'].strip()}"
    return "\n\n".join(["# One line", goal, "# What I need from you", "Nothing.", "# Where we started", "- Nothing.",
                        "# What happened", "1. " + item, "# What was achieved", "- Nothing.",
                        "# Where we are now", "- Nothing.", "# What is left", "- Nothing."]) + "\n"


def reader_check(recipe):
    """-> {"steps", "passed", "failed": [{"step", "problems"}]}"""
    failed = []
    for s in recipe["steps"]:
        r = sitrep.check(step_report(recipe, s))
        if not r["ok"]:
            failed.append({"step": s["id"], "problems": r["problems"]})
    return {"steps": len(recipe["steps"]), "passed": len(recipe["steps"]) - len(failed), "failed": failed}


def cases(recipe):
    """[(step id, output, expected)] two per step: a clean run, and a run that failed (the recipe's own recorded
    error when it has one)."""
    out = []
    for s in recipe["steps"]:
        out.append((s["id"], "exit code 0\n(the command printed nothing wrong)", "PASS"))
        err = (s.get("failures") or [{}])[0].get("error") or "error: the command did not finish"
        out.append((s["id"], "exit code 1\n" + " ".join(str(err).split())[:200], "FAIL"))
    return out


def prompt(step, output):
    return ("A step of a checklist and what happened when it was run.\n"
            f"Step: {step['sentence']}\nCheck: {step['check']}\nWhat happened:\n{output}\n"
            "Does the check pass? Answer with one word: PASS or FAIL.")


def ask(model, text, url=URL, timeout=180):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": text}], "temperature": 0,
                       "max_tokens": 400}).encode()
    req = urllib.request.Request(url + "/v1/chat/completions", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def verdict(answer):
    """The last PASS or FAIL word in the answer (a model may think aloud first); None when there is none."""
    found = re.findall(r"\b(PASS|FAIL)\b", answer or "", re.I)
    return found[-1].upper() if found else None


def model_check(recipe, runner):
    by_id = {s["id"]: s for s in recipe["steps"]}
    wrong = []
    cs = cases(recipe)
    for sid, output, expected in cs:
        got = verdict(runner(prompt(by_id[sid], output)))
        if got != expected:
            wrong.append({"step": sid, "expected": expected, "got": got})
    return {"cases": len(cs), "correct": len(cs) - len(wrong), "wrong": wrong}


def lms(*args):
    return subprocess.run(["lms", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)


def run(recipes, model=None, runner=None):
    """-> {"recipes": [...], "model": id or None, "model_note": why not}. The model part runs only when asked
    (`model` or `runner`); a load that fails is recorded as not verified, never as a pass."""
    res = {"recipes": [], "model": model, "model_note": ""}
    loaded = False
    if runner is None and model:
        try:
            r = lms("load", model, "--yes")
        except subprocess.TimeoutExpired:
            r = subprocess.CompletedProcess([], 1, "", "load timed out")
        loaded = r.returncode == 0
        if loaded:
            runner = lambda t: ask(model, t)
        else:
            res["model_note"] = "not verified: lms load failed: " + (r.stderr or r.stdout).strip()[:200]
    elif runner is None:
        res["model_note"] = "not verified: no model asked for"
    try:
        for rec in recipes:
            row = {"id": rec["id"], "reader": reader_check(rec)}
            if runner:
                try:
                    row["model"] = model_check(rec, runner)
                except Exception as e:
                    row["model"] = {"error": f"not verified: {type(e).__name__}: {e}"[:200]}
            res["recipes"].append(row)
    finally:
        if loaded:
            lms("unload", model)
    return res


def lines(res):
    out = []
    for r in res["recipes"]:
        rd = r["reader"]
        out.append(f"{r['id']}: reader {rd['passed']}/{rd['steps']} steps pass the sitrep rules")
        for f in rd["failed"]:
            out.append(f"  FAIL {f['step']}: {f['problems'][0]}")
        m = r.get("model")
        if m and "error" in m:
            out.append(f"  model: {m['error']}")
        elif m:
            out.append(f"  model {res['model']}: {m['correct']}/{m['cases']} verdicts right")
            for w in m["wrong"]:
                out.append(f"  WRONG {w['step']}: expected {w['expected']}, got {w['got']}")
    if res["model_note"]:
        out.append(res["model_note"])
    return out
