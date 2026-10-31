"""fieldkit rules lint: the rules kept lean, fast and free of bottlenecks by a tool (I-09; owner, 2026-10-31: "make
sure you eliminate any bottleneck and that the tool you're creating the lint one can identify bottlenecks").

rules/rules.yaml is the data: one entry per rule (id, subject, audience, enforced_by, owner, text), the generated
lifecycle and rotate blocks (linted from what their code returns) and each agent's tail. rules/CORE.md is generated
from it (`fieldkit rules build`).

Checks, each a finding with the rule id and the evidence:
  drift          rules/CORE.md is not what rules.yaml generates
  budget         a rule over RULE_BUDGET bytes; an agent's file (core + tail + its blocks) over the 8192 limit
  duplicate      two rules with one subject, or naming one `fieldkit X Y` command that neither declares in `uses`
  misplaced      Zero-only commands in a rule every agent reads (they belong in the fieldkit-net skill)
  contradiction  a rule that asks the owner while another forbids asking
  BOTTLENECK     a rule that (1) asks or waits on the owner outside the owner-decision list, (2) makes a step manual
                 that a tool could do, (3) serialises work without a measured reason, (4) stops a headless run from
                 finishing its own job, (5) counts or retries on signals outside the job's control
  unenforced     (warning) a rule no hook, test or tool enforces: delete it or enforce it
  enforcer       an `enforced_by` entry that names nothing real: its first word must be a repo path that exists or a
                 `fieldkit X [Y]` command (X a real command, Y a word its code knows); the rest, in brackets, is a note
                 (owner order 2026-10-31: Zero 16 removed three invented enforcers by hand)
  proof          a `proofs:` entry whose test or mutated file is missing, or whose swap text is not exactly once in
                 the file (`fieldkit rules prove` runs each one through `fieldkit mutate`)
  unproven       (warning) an enforced rule with neither a proof nor a written gap
  identical      (installed files) a block differs between the agents' global files or from what it should be

Exit 0 clean, 3 findings (warnings alone exit 0).
"""
import hashlib
import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = "rules/rules.yaml"
RULE_BUDGET = 1000                       # bytes per core rule (HARD LINES, the longest, is about 900)
FILE_LIMIT = 8 * 1024
CORE_HEAD = ("<!-- Version: 1.1.0 · generated from rules/rules.yaml by `fieldkit rules build` -->\n"
             "<!-- fieldkit-core-rules v1: the ONE source is Fieldkit rules/CORE.md; copied between these markers into "
             "every agent's global file; edit only the source -->\n"
             "# Core rules (every agent, every project, every machine)\n")
CORE_END = "<!-- /fieldkit-core-rules -->"
TAIL_BEGIN, TAIL_END = "<!-- fieldkit-agent-tail v1 -->", "<!-- /fieldkit-agent-tail -->"
AGENTS = ("claude-code", "codex", "gemini")

NEGATION = re.compile(r"\b(no|never|not|without|nothing|nobody|none|don't|do not|need not)\b", re.I)
#: the five kinds of bottleneck (owner, 2026-10-31), each a pattern read in a rule's text
ASK = re.compile(r"\bask(s|ed|ing)?\b[^.;:]{0,40}\bowner\b|\bowner'?s? (yes|approval|ok)\b|\bapprovals?\b|"
                 r"\bwait(s|ing)? (for|on|until)\b|\?", re.I)
MANUAL = re.compile(r"\bby hand\b|\bmanually\b|\bzero (pushes|opens|merges|ships)\b|"
                    r"\bthe command post (opens|pushes|merges)\b|\bpaste\b", re.I)
SERIAL = re.compile(r"\bone at a time\b|\bone by one\b|\bserially\b|\bsequentially\b|\bin series\b", re.I)
HEADLESS = re.compile(r"\bheadless (run|job)s? (may not|cannot|can't|must not|reports? the branch|stops?)\b|"
                      r"\b(may not|cannot|can't) (push|open the (pr|pull request))\b|\breports the branch\b", re.I)
SIGNAL = re.compile(r"\bfail(s|ed)? CI\b|\bred CI\b|\bCI failures?\b", re.I)
OWN = re.compile(r"\bown\b", re.I)
NO_ASK = re.compile(r"\bdo not ask\b|\bnever ask\b|\bnever end a turn on a question\b", re.I)
ZERO_ONLY = re.compile(r"fieldkit zero (handover|retire|rollcall|open|establish)\b|\bset_monitor\b", re.I)
COMMAND = re.compile(r"`(fieldkit(?: [a-z][\w-]*){1,2})")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def load(root=None):
    import yaml
    return yaml.safe_load((Path(root or ROOT) / DATA).read_text(encoding="utf-8"))


def generate_core(data):
    """-> the text of rules/CORE.md: the header, every core rule (a blank line between), the end marker."""
    return CORE_HEAD + "\n" + "\n\n".join(r["text"].rstrip("\n") for r in data["core"]) + "\n" + CORE_END + "\n"


def tail_block(data, agent):
    return f"{TAIL_BEGIN}\n{data['tails'][agent].rstrip()}\n{TAIL_END}"


def generated_text(entry):
    mod, fn = entry["generator"].split(":")
    return getattr(importlib.import_module(mod), fn)()


def _negated(text, start):
    """True when the clause before a match says no / never / not (\"no session cards, no ... to paste\")."""
    clause = re.split(r"[.;:]\s", text[max(0, start - 120):start])[-1]
    return bool(NEGATION.search(clause))


def _hits(pattern, text, negatable=True):
    return [m for m in pattern.finditer(text) if not (negatable and _negated(text, m.start()))]


def _f(kind, rule, evidence, level="error"):
    return {"kind": kind, "rule": rule, "evidence": evidence, "level": level}


def bottlenecks(rid, text, entry):
    """-> the BOTTLENECK findings of one rule's text."""
    out = []
    asks = _hits(ASK, text)
    if asks and not entry.get("owner_decision"):
        out.append(_f("BOTTLENECK", rid, f"(1) asks or waits on the owner: \"{_ctx(text, asks[0])}\" and the rule "
                                         "has no owner_decision"))
    man = _hits(MANUAL, text)
    if man:
        out.append(_f("BOTTLENECK", rid, f"(2) a manual step a tool could do: \"{_ctx(text, man[0])}\""))
    ser = _hits(SERIAL, text, negatable=False)
    if ser and not DATE.search(str(entry.get("serial_reason") or "")):
        out.append(_f("BOTTLENECK", rid, f"(3) serialises work without a measured reason: \"{_ctx(text, ser[0])}\" "
                                         "(serial_reason needs a dated incident)"))
    hl = _hits(HEADLESS, text, negatable=False)
    if hl:
        out.append(_f("BOTTLENECK", rid, f"(4) stops a headless run from finishing its own job: \"{_ctx(text, hl[0])}\""))
    sig = _hits(SIGNAL, text, negatable=False)
    if sig and not OWN.search(text):
        out.append(_f("BOTTLENECK", rid, f"(5) counts failures outside the job's control: \"{_ctx(text, sig[0])}\" "
                                         "(count only the branch's own)"))
    return out


def _ctx(text, m, width=40):
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    return " ".join(text[a:b].split())


def entries(data):
    """-> [(id, text, entry)] of every rule: the core, the generated blocks, the tails."""
    out = [(r["id"], r["text"], r) for r in data["core"]]
    for g in data.get("generated") or []:
        out.append((g["id"], generated_text(g), g))
    for agent, text in (data.get("tails") or {}).items():
        out.append((f"TAIL {agent}", text, {"audience": agent, "enforced_by": ["fieldkit rules install"],
                                             "tail": True}))
    return out


def lint(data=None, root=None):
    """-> [finding] of the rules data and the generated rules/CORE.md."""
    root = Path(root or ROOT)
    data = data if data is not None else load(root)
    found = []
    core_path = root / "rules" / "CORE.md"
    want = generate_core(data)
    have = core_path.read_text(encoding="utf-8") if core_path.is_file() else ""
    if have.replace("\r\n", "\n") != want:
        found.append(_f("drift", "CORE.md", "rules/CORE.md is not what rules/rules.yaml generates "
                                            "(fieldkit rules build writes it)"))
    all_rules = entries(data)
    commands = _commands()
    subjects, owners, uses = {}, {}, {}
    for rid, text, e in all_rules:
        if e.get("tail"):
            continue
        size = len(text.encode("utf-8"))
        if "generator" not in e and size > RULE_BUDGET:
            found.append(_f("budget", rid, f"{size} bytes, over the {RULE_BUDGET} per rule"))
        subj = (e.get("subject") or "").strip().lower()
        if subj:
            if subj in subjects:
                found.append(_f("duplicate", rid, f"same subject \"{subj}\" as {subjects[subj]}"))
            subjects.setdefault(subj, rid)
        declared = set(e.get("uses") or [])
        uses[rid] = declared
        for cmd in sorted({_cmd(m.group(1)) for m in COMMAND.finditer(text)}):
            if cmd in declared:
                continue
            if cmd in owners and owners[cmd] != rid:
                found.append(_f("duplicate", rid, f"names `{cmd}`, which {owners[cmd]} owns "
                                                  "(merge the two, or list it under uses:)"))
            owners.setdefault(cmd, rid)
        for d in declared:
            if d not in text:
                found.append(_f("stale", rid, f"uses: `{d}` but the text does not name it"))
    for rid, text, e in all_rules:
        aud = e.get("audience")
        if aud == "all" and ZERO_ONLY.search(text):
            found.append(_f("misplaced", rid, f"Zero-only text in a rule every agent reads: "
                                              f"\"{ZERO_ONLY.search(text).group(0)}\" (the fieldkit-net skill)"))
        found += bottlenecks(rid, text, e)
        if e.get("owner_decision") and not _hits(ASK, text):
            found.append(_f("stale", rid, "owner_decision set but the rule asks nothing of the owner"))
        if not e.get("enforced_by") and e.get("kind") != "fact" and not e.get("tail"):
            found.append(_f("unenforced", rid, "no hook, test or tool enforces it: delete it or enforce it",
                            level="warning"))
        if e.get("tail"):
            continue
        for name in e.get("enforced_by") or []:
            ok, what = resolve(str(name), root, commands)
            if not ok:
                found.append(_f("enforcer", rid, f"enforced_by {name!r}: {what}"))
        found += proof_findings(rid, e, root)
        if e.get("enforced_by") and not (e.get("proofs") or e.get("gaps")):
            found.append(_f("unproven", rid, "enforced_by names tools but no proof (a test that fails without "
                                             "them) and no written gap", level="warning"))
    forbids = [rid for rid, text, _ in all_rules if NO_ASK.search(text)]
    for rid, text, e in all_rules:
        if forbids and _hits(ASK, text) and not e.get("owner_decision") and rid not in forbids:
            found.append(_f("contradiction", rid, f"asks the owner while {forbids[0]} says do not ask"))
    for agent in AGENTS:
        size = len(assembled(data, agent).encode("utf-8"))
        if size > FILE_LIMIT:
            found.append(_f("budget", f"FILE {agent}", f"its global file would be {size} bytes, over {FILE_LIMIT}"))
    return found


_SOURCE = {}


def _source_words(root):
    """Every quoted word in fieldkit/*.py (once per lint): a sub-command a module dispatches on is one of them."""
    if root not in _SOURCE:
        words = set()
        for f in (Path(root) / "fieldkit").rglob("*.py"):
            words.update(re.findall(r"[\"']([a-z][\w-]*)[\"']", f.read_text(encoding="utf-8", errors="replace")))
        _SOURCE[root] = words
    return _SOURCE[root]


def _commands():
    import argparse
    from . import cli
    sp = [x for x in cli.build_parser()._actions if isinstance(x, argparse._SubParsersAction)][0]
    return set(sp.choices)


def resolve(name, root=None, commands=None):
    """-> (ok, what) for one `enforced_by` entry: "fieldkit/x.py (note)", "tests/test_x.py", "hooks/pre-commit",
    "fieldkit task watch (note)". Brackets and what follows them are a note."""
    root = Path(root or ROOT)
    ref = name.split("(")[0].strip()
    words = ref.split()
    if not words:
        return False, "empty"
    if words[0] == "fieldkit" and len(words) >= 2 and "/" not in ref:
        cmds = commands if commands is not None else _commands()
        if words[1] not in cmds:
            return False, f"`fieldkit {words[1]}` is not a fieldkit command"
        sub = [w for w in words[2:3] if re.fullmatch(r"[a-z][\w-]*", w)]
        if sub and sub[0] not in _source_words(str(root)):
            return False, f"`fieldkit {words[1]} {sub[0]}`: no code knows the word {sub[0]!r}"
        return True, " ".join(words[:3])
    path = ref.split("::")[0]
    if len(words) == 1 and (root / path).exists():
        return True, path
    return False, f"{ref!r} is neither a repo path that exists nor a `fieldkit` command"


def proof_findings(rid, e, root):
    out = []
    for i, pr in enumerate(e.get("proofs") or []):
        tag = f"proof {i + 1} ({(pr.get('demand') or '?')[:40]})"
        test = str(pr.get("test") or "").split("::")[0]
        if not test or not (root / test).is_file():
            out.append(_f("proof", rid, f"{tag}: test {test or '-'} does not exist"))
        m = pr.get("mutate") or []
        if len(m) != 3:
            out.append(_f("proof", rid, f"{tag}: mutate must be [file, old, new]"))
            continue
        f = root / m[0]
        if not f.is_file():
            out.append(_f("proof", rid, f"{tag}: mutated file {m[0]} does not exist"))
            continue
        n = f.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").count(m[1])
        if n != 1:
            out.append(_f("proof", rid, f"{tag}: the swap text occurs {n} times in {m[0]} (must be exactly once: "
                                        "the enforcer changed, re-prove it)"))
    for g in e.get("gaps") or []:
        if not (g.get("demand") and g.get("why")):
            out.append(_f("proof", rid, "a gap needs both demand and why"))
    return out


def _cmd(s):
    """`fieldkit ship push` -> "fieldkit ship push"; a placeholder word in capitals is not part of it."""
    return " ".join(w for w in s.split() if not w.isupper())


def assembled(data, agent):
    """-> the generated part of an agent's global file: core block, tail, its generated blocks."""
    core = generate_core(data)
    core = core[core.index("<!-- fieldkit-core-rules"):]
    parts = [core.rstrip("\n"), tail_block(data, agent)]
    for g in data.get("generated") or []:
        if agent in (g.get("files") or []):
            parts.append(generated_text(g).rstrip("\n"))
    return "\n\n".join(parts) + "\n"


# ------------------------------------------------------------------ installed files

BLOCKS = {"core": ("<!-- fieldkit-core-rules", CORE_END), "tail": (TAIL_BEGIN, TAIL_END),
          "lifecycle": ("<!-- fieldkit-lifecycle-rule v1 -->", "<!-- /fieldkit-lifecycle-rule -->"),
          "rotate": ("<!-- fieldkit-rotate-rule v1 -->", "<!-- /fieldkit-rotate-rule -->")}


def block(text, name):
    a, b = BLOCKS[name]
    i = text.find(a)
    j = text.find(b, i + 1) if i >= 0 else -1
    return text[i:j + len(b)] if i >= 0 and j > i else None


def expected_blocks(data, agent):
    out = {"core": block(generate_core(data), "core"), "tail": tail_block(data, agent)}
    for g in data.get("generated") or []:
        if agent in (g.get("files") or []):
            out[g["id"].lower()] = generated_text(g).rstrip("\n")
    return out


def check_installed(files, data=None, root=None, skip=()):
    """files: {agent: path}. -> [finding]: each block present and as generated, the shared blocks identical
    (SHA-256) across the files that carry them. skip: block names not to check (rules.check_file does the core)."""
    data = data if data is not None else load(root)
    found, hashes = [], {}
    for agent, path in files.items():
        p = Path(path)
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        for name, want in expected_blocks(data, agent).items():
            if name in skip:
                continue
            have = block(text, name)
            if have is None:
                found.append(_f("identical", agent, f"no {name} block in {p} (fieldkit rules install)"))
            elif have != want:
                found.append(_f("identical", agent, f"the {name} block in {p} is not the current one "
                                                    "(fieldkit rules install)"))
            if have is not None and name != "tail":
                hashes.setdefault(name, {})[agent] = hashlib.sha256(have.encode("utf-8")).hexdigest()
    for name, per in hashes.items():
        if len(set(per.values())) > 1:
            found.append(_f("identical", name, "differs between " + ", ".join(f"{a} {h[:10]}" for a, h in per.items())))
    return found
