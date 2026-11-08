"""P18 stages 2 to 4, no model: failure -> fix pairs, refusals -> corrections, intent groups of the episodes.

Everything is computed from the folded capture state (items, episodes). Nothing here rewrites or deletes an item: it
appends `state` entries (with evidence) and `episode` entries through the store API, and writes data files
(signatures, a refusal table) that a later stage and the router read.
"""
import collections
import re

from . import episodes as ep
from . import store as st

# ---- normalising ----------------------------------------------------------------------------------------------
_PATHS = [(re.compile(r"[a-z]:[\\/][^\s\"'<>|]*"), "<P>"), (re.compile(r"(?<![\w.<])/(?:[\w.@+-]+/)+[\w.@+-]*"), "<P>"),
          (re.compile(r"~[\\/][^\s\"']*"), "<P>"), (re.compile(r"\b[0-9a-f]{7,64}\b", re.I), "<H>"),
          (re.compile(r"\b\d+(?:[.,:]\d+)*\b"), "<N>"), (re.compile(r"\"[^\"\n]{1,80}\"|'[^'\n]{1,80}'"), "<S>")]
_PLACE = re.compile(r"(<[PHNS]>)")
_ERRWORD = re.compile(r"error|fail|denied|not found|cannot|can't|invalid|refus|no such|unknown|fatal|unable|missing|"
                      r"timed out|not recogni[sz]ed|unexpected|rejected|forbidden|conflict|exception|traceback", re.I)
_LEAD_CD = re.compile(r"^\s*(?:cd|pushd|Set-Location)\s+(?:\"[^\"]+\"|'[^']+'|[^\s;&|]+)\s*(?:&&|;|\|\|)\s*")
_TWO = {"git", "gh", "fieldkit", "npm", "cargo", "go", "pip", "docker", "winget", "scoop", "apt", "apt-get", "dpkg", "pnpm",
        "yarn", "make", "ssh", "systemctl", "lms", "ollama"}
_SKIP = ("[Request interrupted", "Tool loaded")


def norm(text):
    text = str(text).lower()
    for rx, rep in _PATHS:
        text = rx.sub(rep, text)
    return re.sub(r"\s+", " ", text).strip()


def command_of(d):
    c = d.get("command")
    if isinstance(c, dict):
        c = c.get("preview", "")
    c = str(c or "")
    while True:
        n = _LEAD_CD.sub("", c, count=1)
        if n == c:
            return c
        c = n


def family(command):
    toks = [t.strip("\"'") for t in command.split()]
    if not toks:
        return "(empty)"
    first = re.split(r"[\\/]", toks[0])[-1].lower()
    first = re.sub(r"\.(exe|cmd|bat)$", "", first)
    if re.fullmatch(r"python[\d.]*|py", first):
        rest = toks[1:3]
        if rest[:1] != ["-m"]:
            return "python script"
        sub = [t for t in toks[3:] if not t.startswith("-")][:1] if rest[1:2] == ["fieldkit"] else []
        return "python -m " + " ".join(rest[1:2] + [x.lower() for x in sub])
    if first in _TWO:
        sub = [t for t in toks[1:] if not t.startswith("-")][:1]
        return first + (" " + sub[0].lower() if sub else "")
    return first


def error_line(errors):
    """The one line that names the failure: the first line with an error word, else the first line."""
    if isinstance(errors, dict):
        errors = errors.get("preview", "")
    lines = [ln.strip() for ln in str(errors or "").splitlines() if ln.strip()]
    for ln in lines:
        if _ERRWORD.search(ln) and not ln.startswith(("at ", "File ")):
            return ln[:300]
    return lines[0][:300] if lines else ""


def error_key(errors):
    return norm(error_line(errors))[:140]


def key_regex(key):
    """A regex from a normalised key: literal words escaped, placeholders match one run of non-space characters."""
    parts = [".*?" if _PLACE.fullmatch(p) else re.escape(p) for p in _PLACE.split(key) if p]
    return re.sub(r"(\\ )+", r"\\s+", "".join(parts))


# ---- stage 2: failure -> fix ------------------------------------------------------------------------------------
def failure_pairs(store, state=None):
    """[(failed id, fixed id, episode id)]: each failed command with the first later successful command of its episode."""
    state = state or store.state()
    out = []
    for eid, e in state.get("episodes", {}).items():
        steps = ep.steps_of(store, e)
        for i, s in enumerate(steps):
            if s[1] != "command" or not s[3]:
                continue
            nxt = next((t for t in steps[i + 1:] if t[1] == "command" and not t[3]), None)
            if nxt:
                out.append((s[0], nxt[0], eid))
    return out


def group_pairs(state, pairs):
    """{(family, key): [pair, ...]} and the pairs that have no error text."""
    groups, blank = collections.defaultdict(list), []
    for p in pairs:
        d = state["items"][p[0]]
        if refusal_rule(d.get("errors")):
            continue                                  # a refusal is a rule, not a failure: stage 3
        key = error_key(d.get("errors"))
        if not key:
            blank.append(p)
            continue
        groups[(family(command_of(d)), key)].append(p)
    return groups, blank


def sig_id(fam, key):
    return "cap-" + re.sub(r"[^a-z0-9]+", "-", fam.lower()).strip("-")[:24] + "-" + st.sha(fam + "\0" + key)[:6]


def known_fix(lines, set_name="agent-shell"):
    """The fix of the first hand-kept triage signature (set agent-shell, trust-reviewed) whose pattern matches one of
    the failure lines, as one sentence; None when none matches. Used when the captured cases repeat no fix."""
    from ..build import triage
    for sig in triage.load_signatures(set_name):
        rx = re.compile(sig["pattern"], re.I | re.M)
        if any(rx.search(line or "") for line in lines):
            return f"Known fix (fieldkit triage --set {set_name}, {sig['id']}): {sig['fix']}"
    return None


def stage2(store, min_pairs=2):
    """Pairs -> signatures (a group of >= min_pairs whose regex matches every member's own error line), the rest ->
    procedures with the reason. Returns (summary, signature list, {item: (state, evidence)})."""
    state = store.state()
    pairs = failure_pairs(store, state)
    groups, blank = group_pairs(state, pairs)
    refused = [p for p in pairs if refusal_rule(state["items"][p[0]].get("errors"))]
    sigs, marks = [], {}
    in_sig = in_proc = mismatch = 0
    for (fam, key), ps in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        sid = sig_id(fam, key)
        rx = re.compile(key_regex(key), re.I | re.M)
        ok = [p for p in ps if rx.search(norm(error_line(state["items"][p[0]].get("errors"))))]
        mismatch += len(ps) - len(ok)
        informative = len(re.findall(r"[a-z]{3,}", _PLACE.sub(" ", key.replace("traceback (most recent call last)", "")))) >= 3     # "exit code <N>" says nothing
        if len(ok) >= min_pairs and informative:
            fixes = collections.Counter(family(command_of(state["items"][p[1]])) + ": " + norm(command_of(state["items"][p[1]]))[:90]
                                        for p in ok)
            fix, n = fixes.most_common(1)[0]
            sigs.append({"id": sid, "pattern": key_regex(key), "check": None, "source": "captured",
                         "cause": f"Seen {len(ok)} times in captured agent sessions: {fam} fails with '{key[:100]}'.",
                         "fix": (f"In the captured sessions the next command that succeeded ({n} of {len(ok)} times) was `{fix}`." if n >= 2 else
                             known_fix([error_line(state["items"][p[0]].get("errors")) for p in ok] + [key]) or
                             f"No single fix repeats in the {len(ok)} captured cases: `fieldkit capture find` the error text to read each one.")})
            for p in ok:
                marks[p[0]] = ("procedure", f"stage2 signature {sid}: fixed by item {p[1]} in episode {p[2]}")
            in_sig += len(ok)
        else:
            ok = []
        for p in ps:
            if p[0] not in marks:
                marks[p[0]] = ("procedure", f"stage2 single failure->fix pair (family {fam}), fixed by item {p[1]} in "
                                            f"episode {p[2]}: kept as a searchable procedure, too rare for a signature")
                in_proc += 1
    for p in refused:
        marks[p[0]] = ("procedure", f"stage2 pair is a router or hook refusal: counted in stage 3, next success item {p[1]}")
    for p in blank:
        marks[p[0]] = ("procedure", f"stage2 failure with no error text, next success was item {p[1]} in episode {p[2]}")
        in_proc += 1
    summary = {"pairs": len(pairs), "groups": len(groups), "signatures": len(sigs), "pairs_in_signatures": in_sig,
               "pairs_as_procedures": in_proc, "pairs_that_are_refusals": len(refused), "pattern_mismatches": mismatch,
               "pairs_accounted": len(marks), "duplicate_failed_items": len(pairs) - len({p[0] for p in pairs})}
    return summary, sigs, marks


def apply_marks(store, marks, only_raw=True):
    """Write the states (idempotent: an item that already has this state and evidence is skipped)."""
    state = store.state()
    new = [{"item": i, "state": s, "evidence": ev, "error": None} for i, (s, ev) in marks.items()
           if state["state"].get(i, {}).get("evidence") != ev and not (only_raw and state["state"].get(i, {}).get("state") == "tool")]
    store.append_many("state", new)
    return len(new)


# ---- stage 3: refusals -> corrections ----------------------------------------------------------------------------
_ROUTER = re.compile(r"TOOL ROUTER: .*?\(([a-z0-9-]+)\)")
_HARD = re.compile(r"HARD LINE \d+: ([^;.\n]{5,70})")
_OTHER = re.compile(r"(CHECKOUT GUARD|SESSION ROTATION STOP|SESSION ROTATION|MANDATE)")


def refusal_rule(errors):
    if isinstance(errors, dict):
        errors = errors.get("preview", "")
    t = str(errors or "")
    if "hook error" not in t.lower() and "TOOL ROUTER" not in t:
        return None
    m = _ROUTER.search(t)
    if m:
        return "router:" + m.group(1)
    m = _HARD.search(t)
    if m:
        return "hard-line:" + re.sub(r"[^a-z0-9]+", "-", m.group(1).lower()).strip("-")
    m = _OTHER.search(t)
    return ("guard:" + re.sub(r"[^a-z0-9]+", "-", m.group(1).lower()).strip("-")) if m else "hook:other"


def stage3(store):
    """rule -> what the agent did next (first later successful command of the episode, family + normalised command)."""
    state = store.state()
    table = collections.defaultdict(collections.Counter)
    marks, total = {}, 0
    for eid, e in state.get("episodes", {}).items():
        steps = ep.steps_of(store, e)
        for i, s in enumerate(steps):
            d = state["items"].get(s[0])
            rule = refusal_rule(d.get("errors")) if d else None
            if not rule:
                continue
            total += 1
            nxt = next((t for t in steps[i + 1:i + 6] if t[1] == "command" and not t[3]), None)
            if nxt:
                c = command_of(state["items"][nxt[0]])
                refused = norm(command_of(d))
                label = ("same command, prefixed: " + norm(c).replace(refused, "<same>")[:70]) if refused and refused in norm(c)                     else "the family " + family(c)
                table[rule][label] += 1
                marks[s[0]] = ("procedure", f"stage3 refusal {rule}: next success was item {nxt[0]} in episode {eid}")
            else:
                table[rule]["(no success within the next 5 steps)"] += 1
                marks[s[0]] = ("procedure", f"stage3 refusal {rule}: no success within 5 steps in episode {eid}")
    rows = [{"rule": r, "count": sum(c.values()), "correction": c.most_common(1)[0][0],
             "corrections": [{"command": k, "count": v} for k, v in c.most_common(5)]} for r, c in table.items()]
    rows.sort(key=lambda r: -r["count"])
    return {"refusals": total, "rules": len(rows), "table": rows}, marks


# ---- stage 4: intent groups --------------------------------------------------------------------------------------
INTENTS = [   # first match wins, in this order; words are matched on the lower-cased request
    ("release", ["release", "publish", "tag ", "gh-release", "ship ", "version bump", "changelog"]),
    ("build", ["build", "compile", "emerge", "package", "firefox", "kernel", "installer", "msi", "deb ", "nfpm"]),
    ("test", ["test", "pytest", "mutate", "coverage", "lint", "verify", "check"]),
    ("debug", ["fix", "bug", "error", "fail", "broken", "crash", "why ", "not working", "debug", "repair", "regress"]),
    ("sync", ["sync", "pull", "push", "merge", "pr ", "pull request", "branch", "rebase", "commit", "git "]),
    ("cleanup", ["clean", "delete", "remove", "recycle", "disk", "space", "archive", "prune", "uninstall"]),
    ("research", ["research", "find", "search", "look up", "compare", "explain", "what is", "how do", "investigate", "audit"]),
    ("documents", ["doc", "readme", "report", "cv", "docx", "pdf", "write up", "sitrep", "plan", "page", "brief"]),
]


def intent_of(text):
    low = " " + str(text or "").lower() + " "
    for kind, words in INTENTS:
        if any(w in low for w in words):
            return kind
    return None


def stage4(store):
    """Every episode gets exactly one intent: from its request, else from its steps' stated purposes, else `other`."""
    state = store.state()
    groups = collections.defaultdict(list)
    for eid, e in state.get("episodes", {}).items():
        kind = intent_of(e.get("request")) if e.get("real_request") else None
        how = "request"
        if kind is None:
            votes = collections.Counter(intent_of(state["items"][s[0]].get("description")) for s in ep.steps_of(store, e)
                                        if s[0] in state["items"])
            votes.pop(None, None)
            kind, how = (votes.most_common(1)[0][0], "purposes") if votes else ("other", "none")
        groups[kind].append((eid, how))
    out = {k: {"episodes": len(v), "by_request": sum(1 for _, h in v if h == "request"),
               "by_purposes": sum(1 for _, h in v if h == "purposes"),
               "with_3plus_steps": sum(1 for eid, _ in v if state["episodes"][eid]["n_steps"] >= 3)} for k, v in groups.items()}
    assigned = {eid: k for k, v in groups.items() for eid, _ in v}
    return {"episodes": len(state.get("episodes", {})), "assigned": len(assigned), "groups": out}, assigned


# ---- applying and writing ----------------------------------------------------------------------------------------
SIG_FILE = "fieldkit/build/signatures/captured.yaml"
TABLE_FILE = "fieldkit/capture/refusal_table.yaml"


def dump_yaml(path, data, header):
    import yaml
    from pathlib import Path
    Path(path).write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")


def apply_intents(store, assigned):
    """Append each episode again with its `intent` (the fold replaces the record; the digest is unchanged)."""
    state = store.state()
    new = [dict(state["episodes"][eid], intent=k) for eid, k in assigned.items() if state["episodes"][eid].get("intent") != k]
    store.append_many("episode", [store.spill(e) if not isinstance(e["steps"], dict) else e for e in new])
    return len(new)
