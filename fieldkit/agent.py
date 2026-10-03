"""The agent interface: discover a tool, check the inputs, run it safely, verify it.

One door for any agent or model, over every card in the collection:

    discover(goal)          -> up to 5 tools that fit, trusted ones first
    describe(tool)          -> its card: inputs, safety, how it is verified
    run(tool, inputs, mode) -> the enforced flow below
    undo(run_id)            -> put files back as they were before that run

The flow run() enforces, whatever the tool:
  1. the card must be reviewed (trust >= carded); drafts are refused, with the reason
  2. inputs are checked against the card: unknown names, missing required ones,
     wrong types, values outside `choices`, and values starting with "-" (which a
     tool could read as an option) are refused before anything runs
  3. read-only tools run directly
  4. tools that change things: mode=preview runs the card's preview (nothing changes);
     mode=apply needs approve=True unless the card says "reversible", backs up every
     file and folder in the card's `scope` inputs (if one cannot be backed up, nothing
     runs), runs, then runs the card's verify checks - and if verification fails,
     restores the backup automatically
  5. every run is journalled (state/agent-runs/<id>.json) with the resolved scope, a
     hash of each scope entry before and after the run, and the list it changed

undo() refuses rather than guess: the run id must be one run() made, every backup copy
must sit inside that run's own backup folder, every path it restores must lie inside
the scope recorded for the run, and a file edited after the run is not overwritten
unless the owner approves (approve=True, command line only). One bad entry refuses
the whole undo and nothing is changed.

Every answer is short and ends with NEXT: or CHOOSE:, so a small model can follow it.

Card fields the flow uses (tools.yaml):
    scope:  [input names]          files or folders those inputs name are backed up before apply
    inputs: [{..., allow_dash: true}]   this input may start with "-" (refused otherwise)
    modes:
      preview: ["+", "--check"]    extra arguments appended to the normal command, or
               [full, command, "{input}"]   a whole command template
      verify:  [{command: [...]}, {output_contains: RE}, {output_lacks: RE}, {files_exist: [...]}]
      undo:    [full, command, "{input}"]
"""
import hashlib
import json
import os
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
RUN_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")      # what run() makes: %Y%m%d-%H%M%S- + 6 hex


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
        # A value starting with "-" reaches the tool as an option, not as data. Only a card that
        # says so (allow_dash: true, or the value is one of its own choices) lets one through.
        if t != "flag" and not s.get("allow_dash") and not (s.get("choices") and v in s["choices"]):
            if any(str(x).startswith("-") for x in (v if isinstance(v, list) else [v])):
                raise Refused(f"input '{n}' starts with '-', so the tool could read it as an option. "
                              f"NEXT: give a value that does not start with '-' (a file can be written "
                              f".{os.sep}name), or ask the owner to mark this input allow_dash on its card.")
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
                argv += [s["flag"]] + [str(x) for x in (v if isinstance(v, list) else [v])]
    return argv + list(extra)


def _fill(template, inputs):
    """A command template: `{name}` becomes that input; `python` becomes fieldkit's own Python."""
    return _py([str(inputs.get(t[1:-1], t)) if re.fullmatch(r"\{\w+\}", t) else t for t in template])


# -- verification -------------------------------------------------------------------------------
def verify(card, inputs, output, runner, timeout=None):
    """The card's verify checks. -> (ok, [messages]). A card with none verifies nothing."""
    checks = card.get("modes", {}).get("verify") or []
    msgs, ok = [], True
    for chk in checks:
        (kind, arg), = chk.items()
        if kind == "command":
            r = runner.run(_fill(arg, inputs), name=f"verify-{card['id']}", timeout=timeout)
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


def _inside(path, root):
    """True if `path` is `root` or lies under it, after resolving `..` and links (case-blind on Windows)."""
    try:
        p = os.path.normcase(str(Path(path).resolve()))
        r = os.path.normcase(str(Path(root).resolve()))
        return os.path.commonpath([p, r]) == r
    except (ValueError, OSError, TypeError):                       # other drive, bad path
        return False


def _file_hash(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _state_hash(p):
    """One hash for what is at `p` now: a file's bytes, or a folder's names and file bytes. None if absent."""
    p = Path(p)
    if p.is_symlink():
        return "link:" + os.readlink(p)
    if p.is_file():
        return "file:" + _file_hash(p)
    if p.is_dir():
        h = hashlib.sha256()
        for top, dirs, files in os.walk(p, followlinks=False):
            dirs.sort()
            rel = Path(top).relative_to(p).as_posix()
            h.update(f"D {rel}\n".encode("utf-8"))
            for name in sorted(files):
                fp = Path(top) / name
                body = ("link:" + os.readlink(fp)) if fp.is_symlink() else _file_hash(fp)
                h.update(f"F {rel}/{name} {body}\n".encode("utf-8"))
            for name in dirs:                                  # links to folders are not walked into
                if (Path(top) / name).is_symlink():
                    h.update(f"L {rel}/{name} {os.readlink(Path(top) / name)}\n".encode("utf-8"))
        return "dir:" + h.hexdigest()
    if p.exists():
        return "other"
    return None


def _remove(p):
    p = Path(p)
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()


def _backup(run_id, paths):
    """Copy every scope entry into BACKUPS/<run_id>. Fails closed: anything that cannot be backed
    up (and checked to match) refuses the whole apply, so nothing runs without a way back."""
    dest = BACKUPS / run_id
    saved = []
    p = None
    try:
        for i, p in enumerate(paths):
            rp = Path(p).resolve()
            slot = dest / str(i)
            before = _state_hash(rp)
            if before == "other":
                raise Refused(f"scope entry {p} is neither a plain file nor a folder, so it cannot be backed up; "
                              "nothing was run. NEXT: give a file or folder path, or ask the owner.")
            if before is None:
                saved.append({"path": str(rp), "kind": "absent", "copy": None, "existed": False, "before": None})
                continue
            if rp.is_dir():
                if rp.parent == rp or _inside(BACKUPS, rp) or _inside(RUNS, rp) or _inside(rp, BACKUPS):
                    raise Refused(f"scope folder {p} is a drive root or holds fieldkit's own backups, so it cannot "
                                  "be backed up; nothing was run. NEXT: give a narrower folder.")
                slot.mkdir(parents=True, exist_ok=True)
                copy = slot / rp.name
                shutil.copytree(rp, copy, symlinks=True)
                kind = "dir"
            else:
                slot.mkdir(parents=True, exist_ok=True)
                copy = slot / rp.name
                shutil.copy2(rp, copy)
                kind = "file"
            if _state_hash(copy) != before:
                raise Refused(f"the backup of {p} does not match the original (it changed while being copied?); "
                              "nothing was run. NEXT: try again when nothing else is writing to it.")
            saved.append({"path": str(rp), "kind": kind, "copy": str(copy), "existed": True, "before": before})
    except Refused:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise Refused(f"could not back up {p}: {e}; nothing was run. NEXT: fix access to it, or ask the owner.")
    return saved


def _check_restore(saved, run_id, scope):
    """Refuse (and change nothing) unless every entry is one this run could have made."""
    if not isinstance(saved, list) or not isinstance(scope, list) or not all(isinstance(x, str) for x in scope):
        raise Refused(f"run {run_id}'s journal has no usable scope record, so its backup cannot be trusted; "
                      "nothing was changed. NEXT: tell the owner and put the files back by hand.")
    home = BACKUPS / run_id
    bad = []
    for s in saved:
        if not isinstance(s, dict) or not isinstance(s.get("path"), str):
            bad.append(f"malformed entry {s!r:.80}")
            continue
        if not any(Path(x).is_absolute() and _inside(s["path"], x) for x in scope):
            bad.append(f"{s['path']} is outside the run's scope")
        if s.get("existed"):
            copy = s.get("copy")
            kind = s.get("kind") or ("dir" if copy and Path(copy).is_dir() else "file")
            if not isinstance(copy, str) or not _inside(copy, home) or Path(copy).resolve() == home.resolve():
                bad.append(f"backup copy {copy} is outside this run's backup folder")
            elif kind not in ("file", "dir") or not (Path(copy).is_dir() if kind == "dir" else Path(copy).is_file()):
                bad.append(f"backup copy {copy} is missing")
    if bad:
        raise Refused(f"run {run_id}'s backup record fails its checks ({'; '.join(bad[:5])}); nothing was changed. "
                      "NEXT: tell the owner; the journal may have been edited.")


def _restore(saved):
    done = []
    for s in saved:
        p = Path(s["path"])
        if s["existed"]:
            kind = s.get("kind") or ("dir" if Path(s["copy"]).is_dir() else "file")
            p.parent.mkdir(parents=True, exist_ok=True)
            if kind == "dir":
                stage = p.with_name(f"{p.name}.fieldkit-restore-{uuid.uuid4().hex[:6]}")
                shutil.copytree(s["copy"], stage, symlinks=True)       # copy first, then swap
                if p.exists() or p.is_symlink():
                    _remove(p)
                stage.rename(p)
            else:
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p)
                shutil.copy2(s["copy"], p)
        elif p.exists() or p.is_symlink():
            _remove(p)                                     # it did not exist before the run
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
        scope = _scope_paths(card, ins)
        rec["scope"] = [str(p.resolve()) for p in scope]
        saved = _backup(run_id, scope)
        rec["backup"] = saved
        r = runner.run(build_argv(card, ins), name=f"apply-{tool_id}", timeout=timeout)
        out = r.stdout + r.stderr
        vok, vmsgs = verify(card, ins, out, runner, timeout=timeout)
        if not modes.get("verify"):
            vok, vmsgs = None, ["no verify checks on this card: result NOT proven"]
        for s in saved:                                    # the post-run state undo will compare against
            s["after"] = _state_hash(s["path"])
        changed = [s["path"] for s in saved if s["after"] != s["before"]]
        rec.update(ok=r.ok, output=out[-4000:], verify=vmsgs, verified=vok, changed=changed)
        if not r.ok or vok is False:
            restored = _restore(saved) if saved else []
            rec["restored"] = restored
            return _journal(rec, "FAILED - " + ("files restored from backup" if restored else "nothing to restore"),
                            ["NEXT: report the failure and the verify lines above; do not retry blindly."])
        return _journal(rec, "DONE" + (" and verified" if vok else " (not verified)"),
                        [f"  changed: {x}" for x in changed] +
                        ([] if changed or not saved else ["  changed: none of the files in scope"]) +
                        [f"NEXT: if the owner wants it undone: undo run_id={run_id}"])

    r = runner.run(build_argv(card, ins), name=f"run-{tool_id}", timeout=timeout)
    out = r.stdout + r.stderr
    vok, vmsgs = verify(card, ins, out, runner, timeout=timeout)
    rec.update(ok=r.ok, output=out[-4000:], verify=vmsgs, verified=vok if modes.get("verify") else None)
    good = r.ok and rec["verified"] is not False
    return _journal(rec, "DONE" if good else f"FAILED (exit {r.returncode})" if not r.ok else "FAILED VERIFY",
                    ["NEXT: use the output above to answer."] if good else
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


def undo(run_id, cards=None, approve=False, timeout=600):
    """Put back what run `run_id` changed. Every check happens before anything is touched.
    approve=True (the owner, on the command line) lets it overwrite files edited after the run."""
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise Refused(f"{str(run_id)[:80]!r} is not a run id (they look like 20261002-142530-a1b2c3). "
                      "NEXT: copy the run id from the run's answer.")
    p = RUNS / f"{run_id}.json"
    if not _inside(p, RUNS):
        raise Refused(f"run id {run_id} points outside the run journal. NEXT: check the run id.")
    if not p.is_file():
        raise Refused(f"no run {run_id}. NEXT: check the run id.")
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        raise Refused(f"run {run_id}'s journal is not valid JSON; nothing was changed. NEXT: tell the owner.")
    if not isinstance(rec, dict) or rec.get("run_id") != run_id:
        raise Refused(f"run {run_id}'s journal names a different run; nothing was changed. NEXT: tell the owner.")
    if rec.get("undone"):
        raise Refused(f"run {run_id} was already undone at {rec['undone']}. NEXT: nothing more to undo.")
    card = _find_card(rec.get("tool"), cards)
    undo_cmd = (card.get("modes") or {}).get("undo")
    saved = [] if rec.get("restored") else (rec.get("backup") or [])     # a failed run was put back already

    # -- every check first; nothing is touched until all pass --
    if saved:
        _check_restore(saved, run_id, rec.get("scope"))
        edited = [s["path"] for s in saved if "after" not in s or _state_hash(s["path"]) != s["after"]]
        if edited and not approve:
            raise Refused(f"{len(edited)} file(s) changed after run {run_id}: {', '.join(edited[:5])}. Undoing would "
                          "overwrite those later edits; nothing was changed. NEXT: show the owner; only the owner "
                          f"can force it, on the command line: fieldkit agent undo {run_id} --approve")
    ins = check_inputs(card, rec.get("inputs") or {}) if undo_cmd else {}
    if not saved and not undo_cmd:
        raise Refused(f"run {run_id} made no backup (or was already put back) and its card has no undo, so there "
                      "is nothing to put back. NEXT: tell the owner.")

    answer, restored, ok = [], [], True
    if saved:
        restored = _restore(saved)
        answer.append(f"UNDONE: {len(restored)} file(s) put back")
        answer += [f"  {x}" for x in restored]
    if undo_cmd:
        runner = Runner(settings.ROOT / "state" / "agent", settings.ROOT / "state" / "agent" / "logs")
        r = runner.run(_fill(undo_cmd, ins), name=f"undo-{card['id']}", timeout=timeout)
        rec["undo_output"] = (r.stdout + r.stderr)[-4000:]
        answer.append(f"UNDO COMMAND {'ran' if r.ok else f'FAILED (exit {r.returncode})'}: {card['id']}")
        answer += [f"  {l[:200]}" for l in rec["undo_output"].strip().splitlines()[-10:]]
        if not r.ok:
            p.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
            return {"run_id": run_id, "restored": restored, "ok": False,
                    "answer": answer + ["NEXT: tell the owner the undo failed."]}
    rec["undone"] = time.strftime("%Y-%m-%d %H:%M:%S")
    p.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    return {"run_id": run_id, "restored": restored, "ok": ok, "answer": answer + ["NEXT: tell the owner it is undone."]}
