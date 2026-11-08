"""P18 stage 8: give every item that is still raw a final state with evidence, by rules (no model, nothing deleted).

An item is never dropped: it becomes a `procedure` (a searchable record) and the evidence says why, in the first rule
that applies:
  1. it is a step of a stored episode            -> "episode <id> (intent, step i of n)"
  2. it is one half of a failure -> fix pair     -> "failure-fix pair with <id>"
  3. a script/file/edit with a path seen again   -> "same path as <id>" (the earlier version is the canonical one)
  4. anything else                               -> "no-signal: <kind> from <origin>, outside any episode"
"""
from . import episodes as ep

OPEN = ("raw", "processing")


def _episode_index(store, state):
    """{item id: (episode id, intent, position, n)} for the first episode that holds the item."""
    idx = {}
    for eid, e in sorted(state.get("episodes", {}).items()):
        steps = ep.steps_of(store, e)
        for i, step in enumerate(steps, 1):
            idx.setdefault(step[0], (eid, e.get("intent") or "other", i, len(steps)))
    return idx


def classify(store, state=None):
    """[(item id, 'procedure', evidence)] for every open item, in item order, and a count per rule."""
    state = state or store.state()
    in_ep = _episode_index(store, state)
    first_path = {}
    out, rules = [], {"episode": 0, "failure-fix": 0, "same-path": 0, "no-signal": 0}
    for iid, d in state["items"].items():
        s = state["state"].get(iid) or {}
        path = d.get("path")
        if path and path not in first_path:
            first_path[path] = iid
        if s.get("state") not in OPEN:
            continue
        if iid in in_ep:
            eid, intent, i, n = in_ep[iid]
            rule, why = "episode", f"episode {eid} (intent {intent}, step {i} of {n})"
        elif iid in state["links"] or iid in state["fixes"]:
            rule, why = "failure-fix", f"failure-fix pair with {state['links'].get(iid) or state['fixes'][iid][0]}"
        elif path and first_path[path] != iid:
            rule, why = "same-path", f"same path as {first_path[path]}"
        else:
            rule = "no-signal"
            why = f"no-signal: {d.get('kind')} from {d.get('origin') or d.get('agent') or 'unknown'}, outside any episode"
        rules[rule] += 1
        out.append((iid, "procedure", why))
    return out, rules


def apply(store, marks):
    return store.append_many("state", [{"item": i, "state": st, "evidence": ev, "error": None} for i, st, ev in marks])
