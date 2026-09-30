"""Tool cards: one contract every tool meets, so an agent never has to guess.

A card says what a tool is for, what inputs it takes (typed), what it touches,
how dangerous it is, how to preview / apply / undo / verify it, how it is tested,
and where it runs. Curated cards are the entries in tools.yaml (reviewed by a
person). Draft cards are generated from a gathered script's own code - argparse
definitions, docstring, what it touches - and are marked draft until reviewed.

Card fields
    id, title, path, entry            what and how to start it
    inputs: [{name, flag, type, required, help, choices}]
    effects: [reads-files | writes-files | deletes-files | registry | services |
              processes | network | admin | hardware | power | gui-automation | git]
    safety:  read-only | reversible | irreversible | unknown
    modes:   {preview, apply, undo, verify}    command templates, {input} filled in
    tests:   [commands]
    platforms, portable, probe, draft, reviewed

Trust ladder (computed, never declared):
    gathered  -> a script exists in the toolbox
    carded    -> a reviewed card with known inputs and a known safety class
    tested    -> carded + declared tests that passed on the current file
    verified  -> tested + portable + safely startable + (read-only, or preview AND
                 undo AND verify declared)
"""
import ast
import hashlib
import json
import re
from pathlib import Path

from ..core import settings

TEST_RESULTS = settings.ROOT / "state" / "test-results.json"
LEVELS = ("gathered", "carded", "tested", "verified")
SAFETY = ("read-only", "reversible", "irreversible", "unknown")
# harvest "touches" -> card effects that change something
CHANGING = {"deletes-files", "registry", "services", "processes", "power", "git", "admin"}


# -- inputs from argparse, read without running -----------------------------------
def _const(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def argparse_inputs(path):
    """Every add_argument(...) in a Python file -> input specs. Nothing is executed."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, SyntaxError):
        return []
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_argument"):
            continue
        flags = [a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if not flags:
            continue
        kw = {k.arg: k.value for k in n.keywords if k.arg}
        positional = not flags[0].startswith("-")
        name = (_const(kw["dest"]) if "dest" in kw else None) or flags[-1].lstrip("-").replace("-", "_")
        action = _const(kw.get("action")) if "action" in kw else None
        nargs = _const(kw.get("nargs")) if "nargs" in kw else None
        many = nargs in ("*", "+") or (isinstance(nargs, int) and nargs > 1)
        typ = "flag" if action in ("store_true", "store_false") else \
            ("list" if many else (getattr(kw.get("type"), "id", None) or "str"))
        spec = {"name": name, "flag": None if positional else flags[0], "type": typ,
                "required": positional and nargs not in ("?", "*") or bool(_const(kw.get("required"))),
                "help": (_const(kw.get("help")) or "")[:160]}
        if "choices" in kw and isinstance(_const(kw["choices"]), (list, tuple)):
            spec["choices"] = list(_const(kw["choices"]))
        out.append(spec)
    return out


def _effects(touches):
    eff = set(touches or [])
    return sorted(eff)


def guess_safety(effects, probe):
    """Inferred, never trusted as reviewed: no changing effect seen -> read-only (draft)."""
    if set(effects) & CHANGING:
        return "irreversible" if set(effects) & {"deletes-files", "registry", "services", "power", "admin"} else "unknown"
    return "read-only" if probe in ("safe", "no-cli") else "unknown"


# -- cards -----------------------------------------------------------------------------
def draft_card(entry):
    """A harvest index entry (see fieldkit.harvest) -> draft card."""
    path = entry["path"] if "path" in entry else entry["id"]
    inputs = argparse_inputs(path) if str(path).endswith(".py") else \
        [{"name": o, "flag": f"-{o}", "type": "str", "required": False, "help": ""} for o in entry.get("options", [])]
    effects = _effects(entry.get("touches"))
    return {"id": entry["id"], "title": entry.get("title") or "", "path": str(path),
            "entry": _entry_for(path, entry.get("language")), "inputs": inputs, "effects": effects,
            "safety": guess_safety(effects, entry.get("probe")), "safety_basis": "inferred from code",
            "modes": {}, "tests": [], "platforms": entry.get("platforms") or [],
            "portable": entry.get("portable", True), "probe": entry.get("probe"), "draft": True, "reviewed": False}


def _entry_for(path, language):
    p = str(path)
    if language == "python" or p.endswith(".py"):
        return ["python", p]
    if language == "powershell" or p.endswith(".ps1"):
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", p]
    if language == "shell" or p.endswith(".sh"):
        return ["bash", p]
    if p.endswith((".cmd", ".bat")):
        return ["cmd", "/c", p]
    return None


def curated_card(tool):
    """A tools.yaml entry -> card. Fields it lacks are filled from the code, marked as such."""
    card = {"id": tool["id"], "title": tool.get("title", ""), "path": tool.get("path"), "entry": tool.get("entry"),
            "inputs": tool.get("inputs"), "effects": tool.get("effects"), "safety": tool.get("safety"),
            "modes": tool.get("modes") or {}, "tests": [tool["test"]] if tool.get("test") else (tool.get("tests") or []),
            "platforms": tool.get("platforms") or [], "repo": tool.get("repo"), "retired": tool.get("retired", False),
            "scope": tool.get("scope") or [], "draft": False, "reviewed": True}
    if card["safety"] is None:                        # tools.yaml "changes" is the reviewed judgement
        card["safety"] = "read-only" if tool.get("changes") is False else "unknown"
    p = card["path"]
    if card["inputs"] is None and p and str(p).endswith(".py") and Path(p).is_file():
        card["inputs"] = argparse_inputs(p)
        card["inputs_basis"] = "read from argparse"
    if p and Path(p).is_file():
        from ..harvest import index_file
        e = index_file(Path(p), Path(p).parent)
        if card["effects"] is None:
            card["effects"] = _effects(e["touches"])
        card["portable"], card["probe"] = e["portable"], e.get("probe")
    if (card.get("entry") or [])[1:3] == ["-m", "fieldkit"]:
        card["probe"] = "safe"                        # started through fieldkit's own argparse CLI
    return card


# -- trust --------------------------------------------------------------------------------
def file_sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def test_results():
    try:
        return json.loads(TEST_RESULTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_test(card_id, path, ok):
    res = test_results()
    res[card_id] = {"ok": ok, "sha256": file_sha(path) if path else None}
    TEST_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    TEST_RESULTS.write_text(json.dumps(res, indent=1), encoding="utf-8")


def trust(card, results=None):
    """-> (level, [reasons it is not higher]). Computed from facts, never declared."""
    results = test_results() if results is None else results
    why = []
    if card.get("draft") or not card.get("reviewed"):
        why.append("card is a draft: a person has not reviewed its inputs and safety")
    if card.get("inputs") is None:
        why.append("inputs unknown")
    if card.get("safety") in (None, "unknown"):
        why.append("safety class unknown")
    if not card.get("entry"):
        why.append("no way to start it (no entry)")
    if why:
        return "gathered", why
    r = results.get(card["id"])
    if not card.get("tests"):
        return "carded", ["no test declared"]
    if not r or not r.get("ok"):
        return "carded", ["tests not passed yet" if not r else "tests failed"]
    if r.get("sha256") and card.get("path") and file_sha(card["path"]) != r["sha256"]:
        return "carded", ["file changed since its tests passed"]
    blockers = []
    if card.get("portable") is False:
        blockers.append("hard-coded home path")
    if card.get("probe") in ("runs-on-load", "library"):
        blockers.append(f"not safely startable ({card['probe']})")
    if card.get("safety") != "read-only":
        modes = card.get("modes", {})
        # undo is either a command on the card, or the agent's own backup of every file in `scope`
        has = {"preview": bool(modes.get("preview")), "verify": bool(modes.get("verify")),
               "undo": bool(modes.get("undo")) or (bool(card.get("scope")) and card.get("safety") == "reversible")}
        missing = [m for m in ("preview", "undo", "verify") if not has[m]]
        if missing:
            blockers.append("changes things but has no " + "/".join(missing))
    return ("tested", blockers) if blockers else ("verified", [])


CACHE = settings.ROOT / "state" / "cards-cache.json"


def _signature():
    """Changes whenever tools.yaml, a curated tool file, or any gathered source changes."""
    from . import registry
    from .. import gather
    parts = [str(registry.REGISTRY.stat().st_mtime_ns)]
    if registry.LOCAL_REGISTRY.is_file():
        parts.append(f"local:{registry.LOCAL_REGISTRY.stat().st_mtime_ns}")
    # the code that builds cards: a change to how cards are read must rebuild them
    here = Path(__file__).resolve().parent
    for f in (here / "cards.py", here / "discover.py", here / "registry.py", here.parent / "harvest.py",
              here.parent / "core" / "privacy.py"):
        parts.append(f"{f.name}:{f.stat().st_mtime_ns}")
    if gather.TOOLBOX.is_dir():
        parts += [f"{p.parent.name}:{p.stat().st_mtime_ns}" for p in sorted(gather.TOOLBOX.glob("*/PROVENANCE.json"))]
    for t in registry.load():
        p = t.get("path")
        if p and Path(p).is_file():
            parts.append(f"{t['id']}:{Path(p).stat().st_mtime_ns}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def all_cards(use_cache=True):
    """Curated cards (tools.yaml) + draft cards for every gathered script not already curated.
    Cached on disk; rebuilt when tools.yaml, a curated tool or a gathered source changes."""
    sig = _signature() if use_cache else None
    if use_cache:
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
            if cached.get("signature") == sig:
                return cached["cards"]
        except (OSError, ValueError):
            pass
    cards = _build_cards()
    if use_cache:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"signature": sig, "cards": cards}), encoding="utf-8")
    return cards


def _build_cards():
    from . import registry
    from .. import gather, harvest
    curated = [curated_card(t) for t in registry.load()]
    have = {str(Path(c["path"]).resolve()) for c in curated if c.get("path")}
    drafts = []
    if gather.TOOLBOX.is_dir():
        for e in harvest.build(gather.TOOLBOX):
            if e["is_test"]:
                continue
            path = gather.TOOLBOX / e["id"]
            if str(path.resolve()) in have:
                continue
            drafts.append(draft_card(dict(e, path=str(path))))
    return curated + drafts
