"""P18 stage 5: align the step sequences of one intent group into a recipe, name it once with a model, replay it.

Deterministic part (no model): from the group's episodes keep the action commands (git, gh, fieldkit, python -m ...),
count in how many episodes each command family appears (its support), order the families by where they first appear,
and for each step collect the usual command, the stated purposes and the known failures with the command that fixed
them. A model is called ONCE per group and only writes a plain sentence and a PASS check per step; its answer is
data, accepted only when every step is named and nothing personal is in it. The recipe is accepted only when it
replays (dry run, nothing is executed) on enough of its own episodes.
"""
import collections
import json
import math
import re
import statistics

from . import distill as D
from . import episodes as ep

ACTION_TOOLS = set(D._TWO) | {"cargo", "go"}
MIN_SUPPORT = 0.10            # a family must appear in this share of the group's episodes (and in at least 3)
MAX_STEPS = 12
REPLAY_MIN_STEPS = 3          # recipe steps the episode must show (groups are mixed: no episode shows them all)
REPLAY_ORDER = 0.8            # share of the matched step pairs that must come in the recipe's order
REPLAY_NEEDED = 3             # episodes that must replay for the recipe to be accepted
CHEAP_MODEL = "sonnet"
BUDGET_USD = "0.50"
_PERSONAL = re.compile(r"[a-z]:[\\/]|/home/|/users/|@[\w-]+\.|gorilla1|gorillanobaka", re.I)


READ_ONLY = {"git status", "git log", "git show", "git diff", "git rev-parse", "git branch", "git ls-files", "git ls-remote",
             "git cat-file", "git merge-base", "git config", "git remote", "git worktree", "git stash"}


def is_action(fam):
    """A command that does or checks something the recipe can name; reading git state is not a step."""
    return (fam.split(" ")[0] in ACTION_TOOLS or fam.startswith("python -m ")) and fam not in READ_ONLY


def template(command):
    """The start of the command (up to 4 words, before any pipe or &&) with paths, numbers, hashes and quoted text
    replaced by placeholders: the arguments belong to one episode, the start is what repeats."""
    words = re.split(r"\s*(?:&&|\|\||[|;])\s*", D.norm(D.command_of({"command": command}).splitlines()[0] if command.strip() else ""))[0].split()
    return " ".join(words[:4])[:80]


def episode_actions(store, state, e):
    """[(family, template, failed, description, error key)] of the action commands of one episode, in order."""
    out = []
    for sid, kind, _code, failed in ep.steps_of(store, e):
        d = state["items"].get(sid)
        if kind != "command" or not d:
            continue
        cmd = D.command_of(d)
        fam = D.family(cmd)
        if is_action(fam):
            out.append((fam, template(cmd), bool(failed), D.norm(d.get("description") or ""), D.error_key(d.get("errors")) if failed else ""))
    return out


# The request words put an episode in a group; these commands show it really did that work. An episode of the group
# with none of them (a question about a release, a note) is not a flow to learn from and is counted apart.
ANCHORS = {"release": ["gh release", "fieldkit ship", "fieldkit release-page", "fieldkit publish", "fieldkit release",
                       "fieldkit build-harness", "git tag"],
           "test": ["python -m pytest", "go test", "cargo test", "fieldkit testcost", "python -m fieldkit testcost"],
           "build": ["fieldkit emerge", "go build", "cargo build", "fieldkit guard", "python -m fieldkit guard", "make",
                     "msbuild", "npm run"]}


def group_episodes(store, state, intent, min_steps=3, with_skipped=False):
    seqs, skipped = {}, 0
    for eid, e in sorted(state.get("episodes", {}).items()):
        if e.get("intent") != intent or e["n_steps"] < min_steps:
            continue
        acts = episode_actions(store, state, e)
        if intent in ANCHORS and not any(a[0] in ANCHORS[intent] for a in acts):
            skipped += 1
            continue
        seqs[eid] = acts
    return (seqs, skipped) if with_skipped else seqs


# ---- align ----------------------------------------------------------------------------------------------------
def kemeny(order, firsts):
    """Move one family at a time to the place where more episodes agree with the order, until nothing improves.
    firsts: per episode {family: index of its first run}. Deterministic: the start order and the scan order are fixed."""
    def score(o):
        return sum(f[a] <= f[b] for f in firsts for i, a in enumerate(o) if a in f for b in o[i + 1:] if b in f)
    best, improved = score(order), True
    while improved:
        improved = False
        for i in range(len(order)):
            for j in range(len(order)):
                if i != j:
                    cand = order[:]
                    cand.insert(j, cand.pop(i))
                    sc = score(cand)
                    if sc > best:
                        best, order, improved = sc, cand, True
    return order


def align(seqs, known_sigs=None):
    """{episode id: actions} -> draft recipe steps. known_sigs: {signature id: fix text} from stage 2."""
    n = len(seqs)
    need = max(3, math.ceil(MIN_SUPPORT * n))
    support, pos = collections.Counter(), collections.defaultdict(list)
    for acts in seqs.values():
        first = {}
        for i, a in enumerate(acts):
            first.setdefault(a[0], i)
        for fam, i in first.items():
            support[fam] += 1
            pos[fam].append(i / max(1, len(acts) - 1))
    keep = [f for f, c in support.most_common() if c >= need][:MAX_STEPS]
    keep.sort(key=lambda f: (statistics.median(pos[f]), f))
    keep = kemeny(keep, [{a[0]: i for i, a in reversed(list(enumerate(acts)))} for acts in seqs.values()])
    steps = []
    for fam in keep:
        runs = [a for acts in seqs.values() for a in acts if a[0] == fam]
        ok = [a for a in runs if not a[2]]
        usual = collections.Counter(a[1] for a in (ok or runs)).most_common(1)[0][0]
        why = [w for w, _ in collections.Counter(a[3] for a in ok if a[3]).most_common(2)]
        fails = collections.Counter(a[4] for a in runs if a[2] and a[4])
        steps.append({"id": re.sub(r"[^a-z0-9]+", "-", fam).strip("-"), "family": fam, "command": usual,
                      "support": support[fam], "of": n, "succeeded": len(ok), "runs": len(runs), "purposes": why,
                      "failures": [{"error": k, "seen": c, "signature": D.sig_id(fam, k),
                                    "fix": (known_sigs or {}).get(D.sig_id(fam, k), "see `fieldkit capture find` with the error text")}
                                   for k, c in fails.most_common(3) if c >= 2]})
    return steps


# ---- replay (dry run: nothing is executed) ------------------------------------------------------------------
def replay(steps, acts):
    """Does this episode show the recipe? -> {ok, matched, order, unfinished}. Checks only what the capture recorded."""
    first, done = {}, collections.defaultdict(bool)
    for i, a in enumerate(acts):
        first.setdefault(a[0], i)
        done[a[0]] = done[a[0]] or not a[2]
    hit = [s for s in steps if s["family"] in first]
    pairs = [(hit[i], hit[j]) for i in range(len(hit)) for j in range(i + 1, len(hit))]
    inorder = sum(1 for x, y in pairs if first[x["family"]] <= first[y["family"]])
    order = inorder / len(pairs) if pairs else 1.0
    unfinished = [s["family"] for s in hit if not done[s["family"]]]
    return {"ok": len(hit) >= REPLAY_MIN_STEPS and order >= REPLAY_ORDER and not unfinished,
            "matched": len(hit), "order": round(order, 2), "unfinished": unfinished}


def replay_all(steps, seqs):
    res = {eid: replay(steps, acts) for eid, acts in seqs.items()}
    passed = sorted(e for e, r in res.items() if r["ok"])
    return {"passed": len(passed), "episodes": len(res), "accepted": len(passed) >= REPLAY_NEEDED, "examples": passed[:REPLAY_NEEDED]}


# ---- naming (the one model call) -------------------------------------------------------------------------------
def prompt_for(intent, steps):
    rows = [{"id": s["id"], "command": s["command"], "purposes": s["purposes"],
             "known_failures": [f["error"] for f in s["failures"]]} for s in steps]
    return ("You name a recipe made from real, repeated work (intent: %s). For EACH step write ONE plain sentence in US English "
            "telling a reader with ADHD what to do (max 20 words, no jargon), and ONE sentence saying what PASS looks like "
            "(max 20 words). Add a short title and a one-sentence goal. Answer with JSON only:\n"
            '{"title": "...", "goal": "...", "steps": {"<id>": {"sentence": "...", "check": "..."}}}\n'
            "Use every id exactly once. Do not invent steps. No file paths or names of people.\nSteps:\n%s"
            % (intent, json.dumps(rows, indent=1)))


def parse_names(text, steps):
    """-> (names, why): names is {title, goal, steps} when every step is named cleanly, else None and the reason."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or not isinstance(d.get("steps"), dict):
        return None, "the answer is not the JSON asked for"
    ids = [s["id"] for s in steps]
    if sorted(d["steps"]) != sorted(ids):
        return None, "the step ids do not match the draft"
    texts = [d.get("title"), d.get("goal")] + [v for s in d["steps"].values() if isinstance(s, dict) for v in (s.get("sentence"), s.get("check"))]
    if len(texts) != 2 + 2 * len(ids) or not all(isinstance(t, str) and 3 <= len(t) <= 200 for t in texts):
        return None, "a name is missing, empty or too long"
    if any(_PERSONAL.search(t) for t in texts):
        return None, "a name carries a path, e-mail or personal name"
    return d, ""


def run_sonnet(prompt):
    """The one model call: headless claude, model checked by the spend guard, a dollar cap. -> text."""
    import os
    import subprocess
    from ..lifecycle import models
    argv, env = models.guard_launch(["claude", "-p", "--model", CHEAP_MODEL, "--max-budget-usd", BUDGET_USD,
                                     "--output-format", "text", "--tools", ""], dict(os.environ), role="worker", origin="local")
    r = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=300, env=env, encoding="utf-8")
    return r.stdout


def default_names(steps):
    """No model: the stated purpose (or the command) and exit code 0."""
    return {"title": "", "goal": "", "steps": {s["id"]: {"sentence": (s["purposes"][0] if s["purposes"] else "Run " + s["family"]),
                                                          "check": "The command ends with exit code 0."} for s in steps}}


def build(store, intent, runner=None, known_sigs=None):
    """-> the recipe dict (accepted or not). runner: text -> text (the model); None = names without a model."""
    state = store.state()
    seqs, skipped = group_episodes(store, state, intent, with_skipped=True)
    steps = align(seqs, known_sigs)
    verdict = replay_all(steps, seqs)
    names, how, note = None, "deterministic", ""
    if runner and steps:
        names, note = parse_names(runner(prompt_for(intent, steps)), steps)
        how = "sonnet" if names else "deterministic"
    names = names or default_names(steps)
    for s in steps:
        nm = names["steps"][s["id"]]
        s["sentence"], s["check"] = nm["sentence"], nm["check"]
    return {"id": "recipe-" + intent, "intent": intent, "title": names.get("title") or f"Recipe: {intent}",
            "goal": names.get("goal") or "", "named_by": how, "naming_note": note,
            "episodes": {"in_group": len(seqs), "without_anchor_command": skipped, "replayed_ok": verdict["passed"], "needed": REPLAY_NEEDED,
                         "examples": verdict["examples"]},
            "accepted": bool(steps) and verdict["accepted"], "steps": steps,
            "card": {"how_to_read": "One step at a time: do the sentence, run the command, look at the check. Failures list the "
                                    "error seen and what fixed it.",
                     "replay": "dry run on the group's own captured episodes: nothing is executed"}}
