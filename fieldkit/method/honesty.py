"""fieldkit honesty check: a report's claims backed by what the tools printed (rule HONESTY, owner order 2026-10-31:
"turn them all into deterministic tools with deterministic checks and safeguard that make sure those rules are
enforced properly").

    fieldkit honesty check REPORT [--session ID | --transcript PATH] [--log PATH ...] [--offline] [--json]

REPORT is a file (a sitrep, a hand-over, a Board row, a task report note) or `-` for stdin. The evidence is the
recorded tool output of the session that wrote it: in a Claude Code transcript only the tool_result blocks count (what
the model typed itself proves nothing); a headless job log or another agent's session file counts as a whole (marked
"raw"). Checks, each a finding with its line:

  UNBACKED      a line with a success word (PASS, passed, green, 0 findings, MERGED, DONE, all ok) and no quoted
                fragment (`...` or "...", 8+ characters) found word for word in the evidence
  ACTION        a checkable action the source denies: "merged #N" (gh pr view N: state MERGED), "pushed SHA"
                (git branch -r --contains SHA), "wrote PATH" / "recycled PATH" (the file exists / is gone)
  NOT-VERIFIED  no "NOT verified:" field, or it is empty, or a bare "-" without a reason in brackets
  UNCARDED      a script the report says it wrote under a sweep root (fieldkit sweep) that has no card or no test
  VERBATIM      a text called verbatim or original, quoted, with `(source: PATH)`, that is not in PATH (the sha256 of
                both is printed)

Exit 0 clean, 3 findings, 2 bad usage. Evidence it is needed: Board row "O3 false PASS: exit 0 in 0 s with no output
= FAIL" (a PASS claimed with nothing printed). No model, plain text matching.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

SUCCESS = re.compile(r"\b(PASS(ED)?|passed|green|MERGED|DONE(?! PROTOCOL)|all ok|0 findings?|0 finding\(s\))\b")
QUOTE = re.compile(r"`([^`]{8,})`|\"([^\"]{8,})\"")
NOT_VERIFIED = re.compile(r"\bNOT verified\b\s*[:|-]\s*(.*)", re.I)
NV_HEADING = re.compile(r"^#+\s*NOT verified\s*$", re.I)
MERGED = re.compile(r"\bmerged\s+(?:PR\s+)?#(\d+)|\b(?:PR\s+)?#(\d+)\s+(?:is\s+)?merged\b", re.I)
PUSHED = re.compile(r"\bpushed\s+(?:commit\s+)?`?([0-9a-f]{7,40})\b", re.I)
WROTE = re.compile(r"\b(wrote|written|created)\s+`([^`]+)`", re.I)
RECYCLED = re.compile(r"\brecycled\s+`([^`]+)`", re.I)
VERBATIM = re.compile(r"\b(verbatim|original)\b[^\n]*?\"([^\"]{8,})\"[^\n]*?\(source:\s*([^)]+)\)", re.I)
NEGATED = re.compile(r"\b(not|no|never|un|without|failed|NOT)\b[^.;]{0,12}$", re.I)


def _norm(s):
    return " ".join(str(s).split())


def evidence_from_transcript(path):
    """-> the text of every tool_result in a Claude Code transcript (JSONL)."""
    out = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        content = (e.get("message") or {}).get("content")
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                out += _texts(b.get("content"))
    return "\n".join(out)


def _texts(c):
    if isinstance(c, str):
        return [c]
    return [str(b.get("text") or "") for b in c if isinstance(b, dict) and b.get("type") == "text"] \
        if isinstance(c, list) else []


def session_evidence(session):
    """-> (text, kind) for a session id or transcript path, through fieldkit rotate's adapters."""
    from ..tokens import session_cost as sc
    p = Path(session)
    if p.is_file():
        return (evidence_from_transcript(p), "tool_result") if p.suffix == ".jsonl" and _is_claude(p) \
            else (p.read_text(encoding="utf-8", errors="replace"), "raw")
    a, path = sc.resolve(str(session), sc.adapters_for("all"))
    if a is None or path is None:
        return "", "none"
    return session_evidence(path)


def _is_claude(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        head = f.read(4000)
    return '"sessionId"' in head or '"tool_use' in head or '"type": "user"' in head or '"type":"user"' in head


def _gh_state(n):
    r = subprocess.run(["gh", "pr", "view", str(n), "--json", "state", "-q", ".state"], capture_output=True,
                       text=True, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else None


def _pushed(sha):
    r = subprocess.run(["git", "branch", "-r", "--contains", sha], capture_output=True, text=True, timeout=60)
    return r.returncode == 0 and bool(r.stdout.strip())


def _uncarded(path):
    try:
        from ..desk import sweep
        return sweep.uncarded(Path(path).expanduser().resolve())
    except Exception:  # noqa: BLE001 - no registry here: nothing to say
        return None


def check(report, evidence, offline=False, gh_state=_gh_state, pushed=_pushed, base=None, require_nv=True):
    """-> [{kind, line, text, detail}]. report: the report's text; evidence: the recorded tool output."""
    ev = _norm(evidence)
    base = Path(base or ".")
    found = []

    def f(kind, i, text, detail):
        found.append({"kind": kind, "line": i, "text": text.strip()[:160], "detail": detail})

    lines = report.splitlines()
    nv, fenced = None, False
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced                       # a quoted brief or a command block is not a claim
            continue
        if fenced:
            continue
        if NV_HEADING.match(line.strip()) and nv is None:     # "## NOT verified" then its text
            nxt = next((x for x in lines[i:] if x.strip()), "")
            nv = (i, "" if nxt.lstrip().startswith("#") else nxt.strip())
            continue
        m = NOT_VERIFIED.search(line)
        if m and nv is None:
            nv = (i, m.group(1).strip().strip("|").strip())
            continue                                  # the NOT verified field names what failed or was not run
        succ = [s for s in SUCCESS.finditer(line) if not NEGATED.search(line[:s.start()])]
        if succ and all(s.group(0).upper() == "MERGED" for s in succ) and not offline and MERGED.search(line):
            succ = []                                 # "merged #N" is checked at the source below: GitHub backs it
        if succ:
            quotes = [q.group(1) or q.group(2) for q in QUOTE.finditer(line)]
            if not any(_norm(q) and _norm(q) in ev for q in quotes):
                f("UNBACKED", i, line, f"\"{succ[0].group(0)}\" needs a quoted line (`...`) that the tools printed; "
                                       + ("none of its quotes is in the evidence" if quotes else "it quotes nothing"))
        if not offline:
            for m in MERGED.finditer(line):
                n = m.group(1) or m.group(2)
                st = gh_state(n)
                if st != "MERGED":
                    f("ACTION", i, line, f"#{n} is {st or 'not found'} on GitHub, not MERGED")
            for m in PUSHED.finditer(line):
                if not pushed(m.group(1)):
                    f("ACTION", i, line, f"no remote branch contains {m.group(1)}")
        for m in WROTE.finditer(line):
            if not (base / m.group(2)).expanduser().exists():
                f("ACTION", i, line, f"{m.group(2)} does not exist")
            else:
                st = _uncarded(base / m.group(2))
                if st:
                    f("UNCARDED", i, line, f"{m.group(2)} is a script under a sweep root with {'no card' if st == 'untracked' else 'no test'}: "
                                           "`fieldkit adopt` it, or add it to sweep.ignore with a reason")
        for m in RECYCLED.finditer(line):
            if (base / m.group(1)).expanduser().exists():
                f("ACTION", i, line, f"{m.group(1)} is still there")
        for m in VERBATIM.finditer(line):
            quote, src = m.group(2), (base / m.group(3).strip()).expanduser()
            have = src.read_text(encoding="utf-8", errors="replace") if src.is_file() else ""
            if _norm(quote) not in _norm(have):
                f("VERBATIM", i, line, f"not in {src} (sha256 of the quote {_sha(quote)[:12]}, of the source "
                                       f"{_sha(have)[:12] if have else 'missing'})")
    if not require_nv:
        pass                                          # a sitrep has its own sections (fieldkit sitrep check)
    elif nv is None:
        f("NOT-VERIFIED", 0, "", "no \"NOT verified:\" field: say what was not checked (or \"- (reason)\")")
    elif not nv[1] or re.fullmatch(r"[-\s]*", nv[1]):
        f("NOT-VERIFIED", nv[0], lines[nv[0] - 1], "empty: name what was not checked, or \"- (why nothing)\"")
    return found


def _sha(s):
    return hashlib.sha256(_norm(s).encode("utf-8")).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit honesty", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check"])
    ap.add_argument("report")
    ap.add_argument("--session", help="the session id whose recorded tool output backs the report")
    ap.add_argument("--transcript", help="a transcript or session file (instead of --session)")
    ap.add_argument("--log", action="append", default=[], help="a job log that counts as evidence (repeatable)")
    ap.add_argument("--offline", action="store_true", help="do not ask GitHub or git (ACTION merged/pushed skipped)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    report = sys.stdin.read() if a.report == "-" else Path(a.report).read_text(encoding="utf-8", errors="replace")
    parts, kinds = [], []
    if a.session or a.transcript:
        text, kind = session_evidence(a.transcript or a.session)
        parts.append(text)
        kinds.append(kind)
    for lg in a.log:
        parts.append(Path(lg).read_text(encoding="utf-8", errors="replace"))
        kinds.append("raw")
    if not parts:
        print("fieldkit honesty: give --session, --transcript or --log (the evidence)", file=sys.stderr)
        return 2
    found = check(report, "\n".join(parts), offline=a.offline)
    if a.json:
        print(json.dumps({"findings": found, "evidence": kinds}, indent=1, ensure_ascii=False))
    else:
        for x in found:
            print(f"{x['kind']:<13} line {x['line']}: {x['detail']}\n              {x['text']}")
        print(f"{len(found)} finding(s); evidence: {', '.join(kinds)}")
        print("NEXT: " + ("quote the line each tool printed, or say it was NOT verified" if found else "nothing"))
    return 3 if found else 0
