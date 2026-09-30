"""The agent interface: discover a tool, check the inputs, run it safely, verify it.

One door for any agent or model, over every card in the collection:

    discover(goal)          -> up to 5 tools that fit, trusted ones first
    describe(tool)          -> its card: inputs, safety, how it is verified
    run(tool, inputs, mode) -> the enforced flow below
    undo(run_id)            -> put files back as they were before that run

The flow run() enforces, whatever the tool:
  1. the card must be reviewed (trust >= carded); drafts are refused, with the reason
  2. inputs are checked against the card: unknown names, missing required ones,
     wrong types and values outside `choices` are refused before anything runs
  3. read-only tools run directly
  4. tools that change things: mode=preview runs the card's preview (nothing changes);
     mode=apply needs approve=True unless the card says "reversible", backs up every
     file in the card's `scope` inputs, runs, then runs the card's verify checks -
     and if verification fails, restores the backup automatically
  5. every run is journalled (state/agent-runs/<id>.json) with a before/after diff

Every answer is short and ends with NEXT: or CHOOSE:, so a small model can follow it.

Card fields the flow uses (tools.yaml):
    scope:  [input names]          files those inputs name are backed up before apply
    modes:
      preview: ["+", "--check"]    extra arguments appended to the normal command, or
               [full, command, "{input}"]   a whole command template
      verify:  [{command: [...]}, {output_contains: RE}, {output_lacks: RE}, {files_exist: [...]}]
"""
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from .core import settings
from .core.proc import Runner
from .desk import cards as cardmod

# Changes to these need the owner's approval even when the tool can undo them:
# a model does not change system settings on its own.
SYSTEM_EFFECTS = {"registry", "services", "admin", "power", "hardware"}
RUNS = settings.ROOT / "state" / "agent-runs"
BACKUPS = settings.ROOT / "state" / "agent-backups"
ORDER = {lvl: i for i, lvl in enumerate(cardmod.LEVELS)}
WORD = re.compile(r"[a-z0-9]+")


class Refused(Exception):
    """A request the harness will not run. The message says why and what to do instead."""


def _cards():
    return cardmod.all_cards()


def _find_card(tool_id, cards=None):
    for c in cards or _cards():
        if c["id"] == tool_id:
            return c
    raise Refused(f"no tool '{tool_id}'. NEXT: call discover with what you want to do.")


# -- discover / describe ---------------------------------------------------------------
def discover(goal, limit=5, include_drafts=False, cards=None):
    q = [w for w in WORD.findall(goal.lower()) if len(w) > 2]
    results = cardmod.test_results()
    scored = []
    for c in cards or _cards():
        if c.get("retired"):
            continue
        level, why = cardmod.trust(c, results)
        if level == "gathered" and not include_drafts:
            continue
        hay = {"title": set(WORD.findall((c.get("title") or "").lower())),
               "id": set(WORD.findall(c["id"].lower())),
               "inputs": set(WORD.findall(" ".join(f"{i['name']} {i.get('help', '')}" for i in c.get("inputs") or [])
                                          .lower()))}
        score = sum(3 * (w in hay["title"]) + 2 * (w in hay["id"]) + (w in hay["inputs"]) for w in q)
        if score:
            scored.append((score + ORDER[level], c, level))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [{"tool": c["id"], "title": c.get("title", ""), "trust": lvl, "safety": c.get("safety"),
             "inputs": [i["name"] + ("" if i.get("required") else "?") for i in c.get("inputs") or []]}
            for _, c, lvl in scored[:limit]]


def describe(tool_id, cards=None):
    c = dict(_find_card(tool_id, cards))
    c["trust"], c["trust_blockers"] = cardmod.trust(c)
    return c


# -- inputs -> argv ---------------------------------------------------------------------------
def check_inputs(card, inputs):
    """Validate inputs against the card. -> normalised dict, or raises Refused."""
    inputs = dict(inputs or {})
    specs = {s["name"]: s for s in card.get("inputs") or []}
    unknown = sorted(set(inputs) - set(specs))
    if unknown:
        raise Refused(f"unknown input(s) {unknown}. This tool takes: {sorted(specs)}. NEXT: use only those names.")
    missing = [n for n, s in specs.items() if s.get("required") and inputs.get(n) in (None, "", [])]
    if missing:
        raise Refused(f"missing required input(s) {missing}. NEXT: provide them.")
    out = {}
    for n, v in inputs.items():
        s = specs[n]
        t = s.get("type", "str")
        try:
            if t == "int":
                v = int(v)
            elif t == "float":
                v = float(v)
            elif t == "flag":
                if not isinstance(v, bool):
                    raise ValueError("must be true or false")
            elif t == "list":
                v = [str(x) for x in (v if isinstance(v, list) else [v])]
            else:
                v = str(v)
        except (TypeError, ValueError) as e:
            raise Refused(f"input '{n}' must be {t}: {e}. NEXT: correct it.")
        if s.get("choices") and v not in s["choices"]:
            raise Refused(f"input '{n}' must be one of {s['choices']}, not {v!r}. NEXT: pick one of those.")
        out[n] = v
    return out


def _py(argv):
    """`python` in a card means the Python fieldkit runs on, not whatever PATH finds first."""
    import sys
    return [sys.executable if a in ("python", "python3") else a for a in argv]


def build_argv(card, inputs, extra=()):
    argv = _py(card["entry"])
    specs = card.get("inputs") or []
    for s in specs:                                             # positionals in declared order
        if s.get("flag") is None and s["name"] in inputs:
            v = inputs[s["name"]]
            argv += v if isinstance(v, list) else [v]
    for s in specs:
        if s.get("flag") and s["name"] in inputs:
            v = inputs[s["name"]]
            if s.get("type") == "flag":
                if v:
                    argv.append(s["flag"])
            else:
                argv += [s["flag"]] + (v if isinstance(v, list) else [str(v)])
    return argv + list(extra)


def _fill(template, inputs):
    """A command template: `{name}` becomes that input; `python` becomes fieldkit's own Python."""
    return _py([str(inputs.get(t[1:-1], t)) if re.fullmatch(r"\{\w+\}", t) else t for t in template])


# -- verification -------------------------------------------------------------------------------
def verify(card, inputs, output, runner):
    """The card's verify checks. -> (ok, [messages]). A card with none verifies nothing."""
    checks = card.get("modes", {}).get("verify") or []
    msgs, ok = [], True
    for chk in checks:
        (kind, arg), = chk.items()
        if kind == "command":
            r = runner.run(_fill(arg, inputs), name=f"verify-{card['id']}")
            good = r.ok
            msgs.append(f"{'ok  ' if good else 'FAIL'} {' '.join(_fill(arg, inputs))[:80]} (exit {r.returncode})")
        elif kind == "output_contains":
            good = re.search(arg, output or "", re.M) is not None
            msgs.append(f"{'ok  ' if good else 'FAIL'} output matches {arg!r}")
        elif kind == "output_lacks":
            good = re.search(arg, output or "", re.M) is None
            msgs.append(f"{'ok  ' if good else 'FAIL'} output free of {arg!r}")
        elif kind == "files_exist":
            paths = _fill(arg if isinstance(arg, list) else [arg], inputs)
            good = all(Path(p).exists() for p in paths)
            msgs.append(f"{'ok  ' if good else 'FAIL'} exists: {', '.join(paths)}")
        else:
            good = False
            msgs.append(f"FAIL unknown verify kind {kind!r}")
        ok &= bool(good)
    return ok, msgs


# -- backups for undo ------------------------------------------------------------------------------
def _scope_paths(card, inputs):
    paths = []
    for name in card.get("scope") or []:
        v = inputs.get(name)
        for p in (v if isinstance(v, list) else [v] if v else []):
            paths.append(Path(p))
    return paths


def _backup(run_id, paths):
    dest = BACKUPS / run_id
    saved = []
    for i, p in enumerate(paths):
        if p.is_file():
            (dest / str(i)).mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest / str(i) / p.name)
            saved.append({"path": str(p.resolve()), "copy": str(dest / str(i) / p.name), "existed": True})
        elif not p.exists():
            saved.append({"path": str(p.resolve()), "copy": None, "existed": False})
    return saved


def _restore(saved):
    done = []
    for s in saved:
        p = Path(s["path"])
        if s["existed"]:
            shutil.copy2(s["copy"], p)
        elif p.exists():
            p.unlink()                                     # it did not exist before the run
        done.append(str(p))
    return done


# -- run ----------------------------------------------------------------------------------------------
def run(tool_id, inputs=None, mode="apply", approve=False, cards=None, timeout=600):
    card = _find_card(tool_id, cards)
    level, why = cardmod.trust(card)
    if ORDER[level] < ORDER["carded"]:
        raise Refused(f"'{tool_id}' is not ready for an agent ({level}): {why[0]}. "
                      "NEXT: pick a tool from discover, or ask the owner to review this card.")
    ins = check_inputs(card, inputs)
    safety = card.get("safety")
    modes = card.get("modes") or {}
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    runner = Runner(settings.ROOT / "state" / "agent", settings.ROOT / "state" / "agent" / "logs")
    rec = {"run_id": run_id, "tool": tool_id, "trust": level, "safety": safety, "mode": mode, "inputs": ins}

    if safety != "read-only":
        if mode == "preview":
            if not modes.get("preview"):
                raise Refused(f"'{tool_id}' changes things and its card has no preview. CHOOSE: 1. run with "
                              "mode=apply and approve=true (the owner must agree)  2. use another tool.")
            argv = _fill(modes["preview"], ins) if isinstance(modes["preview"], list) and modes["preview"] and \
                modes["preview"][0] != "+" else build_argv(card, ins, modes["preview"][1:])
            r = runner.run(argv, name=f"preview-{tool_id}", timeout=timeout)
            rec.update(ok=r.ok, output=(r.stdout + r.stderr)[-4000:], changed=False)
            needs_owner = safety != "reversible" or bool(set(card.get("effects") or []) & SYSTEM_EFFECTS)
            return _journal(rec, "PREVIEW (nothing changed)",
                            ["NEXT: show this preview to the owner; only the owner can approve applying it."
                             if needs_owner else "NEXT: if this is what should happen, run again with mode=apply."])
        system = sorted(set(card.get("effects") or []) & SYSTEM_EFFECTS)
        if (safety != "reversible" or system) and not approve:
            reason = f"is {safety}" if safety != "reversible" else f"changes the system ({', '.join(system)})"
            raise Refused(f"'{tool_id}' {reason}: it needs approve=true, which only the owner may give. "
                          "NEXT: run mode=preview and show the owner what would change.")
        saved = _backup(run_id, _scope_paths(card, ins))
        rec["backup"] = saved
        r = runner.run(build_argv(card, ins), name=f"apply-{tool_id}", timeout=timeout)
        out = r.stdout + r.stderr
        vok, vmsgs = verify(card, ins, out, runner)
        if not modes.get("verify"):
            vok, vmsgs = None, ["no verify checks on this card: result NOT proven"]
        rec.update(ok=r.ok, output=out[-4000:], verify=vmsgs, verified=vok)
        if not r.ok or vok is False:
            restored = _restore(saved) if saved else []
            rec["restored"] = restored
            return _journal(rec, "FAILED - " + ("files restored from backup" if restored else "nothing to restore"),
                            ["NEXT: report the failure and the verify lines above; do not retry blindly."])
        return _journal(rec, "DONE" + (" and verified" if vok else " (not verified)"),
                        [f"NEXT: if the owner wants it undone: undo run_id={run_id}"])

    r = runner.run(build_argv(card, ins), name=f"run-{tool_id}", timeout=timeout)
    out = r.stdout + r.stderr
    vok, vmsgs = verify(card, ins, out, runner)
    rec.update(ok=r.ok, output=out[-4000:], verify=vmsgs, verified=vok if modes.get("verify") else None)
    return _journal(rec, "DONE" if r.ok else f"FAILED (exit {r.returncode})",
                    ["NEXT: use the output above to answer."] if r.ok else
                    ["NEXT: read the output; if the error is unclear, try triage on the log."])


def _journal(rec, status, next_lines):
    rec["status"] = status
    rec["when"] = time.strftime("%Y-%m-%d %H:%M:%S")
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{rec['run_id']}.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    tail = (rec.get("output") or "").strip().splitlines()[-15:]
    rec["answer"] = [f"{status}: {rec['tool']} (run {rec['run_id']})"] + [f"  {l[:200]}" for l in tail] + \
        [f"  {v}" for v in rec.get("verify", [])] + next_lines
    return rec


def undo(run_id, cards=None):
    p = RUNS / f"{run_id}.json"
    if not p.is_file():
        raise Refused(f"no run {run_id}. NEXT: check the run id.")
    rec = json.loads(p.read_text(encoding="utf-8"))
    if rec.get("undone"):
        raise Refused(f"run {run_id} was already undone at {rec['undone']}.")
    card = _find_card(rec["tool"], cards)
    answer, restored = [], []
    if rec.get("backup"):
        restored = _restore(rec["backup"])
        answer.append(f"UNDONE: {len(restored)} file(s) put back")
        answer += [f"  {x}" for x in restored]
    if (card.get("modes") or {}).get("undo"):
        runner = Runner(settings.ROOT / "state" / "agent", settings.ROOT / "state" / "agent" / "logs")
        r = runner.run(_fill(card["modes"]["undo"], rec.get("inputs") or {}), name=f"undo-{card['id']}")
        rec["undo_output"] = (r.stdout + r.stderr)[-4000:]
        answer.append(f"UNDO COMMAND {'ran' if r.ok else f'FAILED (exit {r.returncode})'}: {card['id']}")
        answer += [f"  {l[:200]}" for l in rec["undo_output"].strip().splitlines()[-10:]]
        if not r.ok:
            p.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
            return {"run_id": run_id, "restored": restored, "answer": answer + ["NEXT: tell the owner the undo failed."]}
    if not answer:
        raise Refused(f"run {run_id} made no backup and its card has no undo, so there is nothing to put back.")
    rec["undone"] = time.strftime("%Y-%m-%d %H:%M:%S")
    p.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    return {"run_id": run_id, "restored": restored, "answer": answer}
