"""P18 stage 7: serve the distilled workflow recipes one step at a time, and offer the matching one for a request.

    fieldkit capture workflow list                      the recipes and procedures, one line each
    fieldkit capture workflow show ID                   the recipe: each step as one sentence, one command, one check
    fieldkit capture workflow offer REQUEST...          the recipe for a request (same words as the stage 4 intents)

The prompt hook (autopilot/room.py) prints the `offer` line when a request matches a recipe.
"""
from pathlib import Path

import yaml

from . import distill as D
from .weaktest import WORKFLOWS


def load(folder=None):
    out = {}
    for p in sorted(Path(folder or WORKFLOWS).glob("*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if d.get("id"):
            out[d["id"]] = d
    return out


def offer(request, folder=None):
    """-> the accepted recipe whose intent the request has, or None (no match, or the group is only a procedure)."""
    kind = D.intent_of(request)
    for d in load(folder).values():
        if d.get("accepted") and d.get("steps") and d.get("intent") == kind:
            return d
    return None


def offer_line(request, folder=None):
    d = offer(request, folder)
    if not d:
        return ""
    return (f"WORKFLOW: this request looks like '{d['intent']}': recipe {d['id']} has {len(d['steps'])} steps, each one "
            f"sentence, one command, one check: fieldkit capture workflow show {d['id']}")


def show_lines(d):
    out = [f"{d['id']}: {d.get('title', '')}", f"Goal: {d.get('goal', '')}"]
    for i, s in enumerate(d.get("steps", []), 1):
        out += [f"{i}. {s['sentence']}", f"   Command: {s.get('command', '')}", f"   Check: {s['check']}"]
    return out


def list_lines(folder=None):
    out = []
    for d in load(folder).values():
        kind = f"recipe, {len(d['steps'])} steps" if d.get("accepted") and d.get("steps") else "procedure (did not replay)"
        out.append(f"{d['id']}  [{d.get('intent', '')}]  {kind}")
    return out
