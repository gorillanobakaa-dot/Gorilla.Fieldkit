"""Recipes: how something was done before, kept so the next model does not have to work it out again.

    fieldkit recipes [list] [--json]          every recipe, public and the owner's own
    fieldkit recipes show ID                  one recipe, in full
    fieldkit recipes check                    every recipe meets the contract (exit 3 if not)
    fieldkit recipes capture ID SOURCE        copy a one-off script into local/recipes/ID/ and mark the
                                              old copy adopted (the card itself is written by hand)

WHY
    A one-off script (a probe, a patch applied once, a migration) is not a tool: it will not be run again as it is.
    But what it took to get it right is experience, and that was being lost every time Scripts was cleared (owner,
    2026-10-19: "the idea is to CAPTURE the experience"). A small model with no harness of its own (Gemma in LM
    Studio) gets that experience through discover and describe, the same doors as the tools.

A RECIPE IS READ AND ADAPTED, NEVER RUN. discover labels it "recipe", describe prints it, run refuses it.

Card fields (desk/recipes.yaml is published; local/recipes.yaml is the owner's own and never published)
    id          short, unique across tools and recipes
    title       one line: what it achieves, in the words someone would search with
    problem     the situation it answers
    technique   [steps], in order
    key_facts   [facts that decided it: versions, limits, traps measured]
    outcome     what happened when it was done (measured, with the date)
    pointer     the original script, inside Fieldkit (local/recipes/ID/...), or null
    platforms   [windows | linux | macos]
    tags        [extra search words]
    learned     YYYY-MM-DD
"""
import shutil
import time
from pathlib import Path

from ..core import settings

RECIPES = Path(__file__).resolve().parent / "recipes.yaml"
LOCAL_RECIPES = settings.ROOT / "local" / "recipes.yaml"
LOCAL_DIR = settings.ROOT / "local" / "recipes"
REQUIRED = ("id", "title", "problem", "technique", "key_facts", "outcome", "learned")
PLATFORMS = ("windows", "linux", "macos")
HOW_TO_USE = ("This is a RECIPE: how this was done before, to read and adapt. It is not a tool and is never run. "
              "Follow the technique, check the key facts against this machine, and if a pointer is given, read that "
              "script and adapt it; do not run it as it is.")


def _read(path):
    if not path.is_file():
        return []
    return list(settings.read_file(path).get("recipes") or [])


def load(public_only=False):
    """-> recipes, each with kind='recipe' and public=True|False."""
    out = [dict(r, kind="recipe", public=True) for r in _read(RECIPES)]
    if not public_only:
        out += [dict(r, kind="recipe", public=False) for r in _read(LOCAL_RECIPES)]
    return [r for r in out if not r.get("retired")]


def pointer_path(recipe):
    p = recipe.get("pointer")
    if not p:
        return None
    path = Path(settings.expand(p)) if "${" in str(p) else Path(p)
    return path if path.is_absolute() else settings.ROOT / path


def check(recipes=None, tool_ids=None):
    """-> [problem lines]. Empty means every recipe meets the contract."""
    recipes = load() if recipes is None else recipes
    if tool_ids is None:
        from . import registry
        tool_ids = {t["id"] for t in registry.load(strict=False)}
    problems, seen = [], set()
    for r in recipes:
        rid = r.get("id") or "?"
        for f in REQUIRED:
            if not r.get(f):
                problems.append(f"{rid}: '{f}' is missing")
        for f in ("technique", "key_facts"):
            if r.get(f) is not None and not isinstance(r.get(f), list):
                problems.append(f"{rid}: '{f}' must be a list")
        if rid in seen:
            problems.append(f"{rid}: two recipes share this id")
        if rid in tool_ids:
            problems.append(f"{rid}: a tool already has this id")
        seen.add(rid)
        bad = [p for p in r.get("platforms") or [] if p not in PLATFORMS]
        if bad:
            problems.append(f"{rid}: unknown platform(s) {bad}; use {list(PLATFORMS)}")
        pp = pointer_path(r)
        if pp is not None:
            if not _inside(pp, settings.ROOT):
                problems.append(f"{rid}: pointer {r['pointer']} is outside Fieldkit (Scripts is emptied at each sweep)")
            elif not pp.exists():
                problems.append(f"{rid}: pointer {r['pointer']} does not exist")
            elif r.get("public") and pp.resolve().relative_to(settings.ROOT.resolve()).parts[0] == "local":
                problems.append(f"{rid}: a published recipe points into local/, which is never published")
    return problems


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def text(recipe):
    """A recipe as a person or a model reads it."""
    L = [f"RECIPE {recipe['id']}: {recipe.get('title', '')}", "", HOW_TO_USE, "",
         f"PROBLEM: {recipe.get('problem', '')}", "", "TECHNIQUE:"]
    L += [f"  {i}. {s}" for i, s in enumerate(recipe.get("technique") or [], 1)]
    L += ["", "KEY FACTS:"] + [f"  - {k}" for k in recipe.get("key_facts") or []]
    L += ["", f"OUTCOME: {recipe.get('outcome', '')}"]
    if recipe.get("pointer"):
        L += [f"POINTER (read and adapt, never run as it is): {recipe['pointer']}"]
    if recipe.get("platforms"):
        L += [f"PLATFORMS: {', '.join(recipe['platforms'])}"]
    L += [f"LEARNED: {recipe.get('learned', '')}"]
    return "\n".join(L)


def capture(rid, source, log=print):
    """Copy a one-off script (file or folder) into local/recipes/ID/ and mark the old copy adopted, so the sweep stops
    listing it and `fieldkit adopt --finish --apply` recycles it later. -> the new path. The card is written by hand:
    what made it work is not in the code."""
    from .adopt import _mark
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"{source} does not exist")
    dest_dir = LOCAL_DIR / rid
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    else:
        shutil.copy2(src, dest)
    _mark(src, dest, time.strftime("%Y-%m-%d"))
    log(f"copied {src} -> {dest}; the old copy is marked adopted")
    log(f"NEXT: write the card in local/recipes.yaml with pointer: local/recipes/{rid}/{src.name}")
    return dest
