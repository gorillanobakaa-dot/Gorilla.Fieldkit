"""Build-log triage: pull the real error out of a huge log and name it.

Generalised from Gorilla.firefox's triage_build_failure.py (2026-09-09), with
the idea of Red Hat/Fedora's Log Detective (read the failed build log, match
known failures) done deterministically: regex signatures kept as data in
build/signatures/<set>.yaml, no model involved.

The point is not a harness that handles anything. It is one that never hits
the same thing twice: a known failure is named with cause and fix; an
unknown one is reported as UNRECOGNISED with the error lines and a signature
stub ready to paste into the YAML.

    fieldkit triage LOG --set firefox-windows [--json]
    fieldkit triage LOG --set auto
"""
import re
from pathlib import Path

from ..core import settings

SIG_DIR = Path(__file__).resolve().parent / "signatures"

# Lines that carry an error, per tool. Order matters only for display.
EXTRACTORS = [
    # Gorilla harness house style: [-] failure, [!!!] fatal. The banner line
    # "[!!!] FATAL HALT [!!!]" says nothing; the reason is on the other lines.
    ("harness", re.compile(r"^\[(?:-|!!!)\]\s+(?!FATAL HALT)(\S.*)$")),   # detail lines are indented
    ("mach", re.compile(r"^\s*\d+:\d+\.\d+ E (.*)$")),
    ("make", re.compile(r"^(make(\[\d+\])?: \*\*\* .*)$")),
    ("compiler", re.compile(r"^(.*?(?:\berror\b|fatal error)(?: [A-Z]+\d+)?: .*)$")),
    ("dpkg", re.compile(r"^((?:dpkg|dpkg-buildpackage|dpkg-checkbuilddeps|debuild|sbuild)[\w-]*: (?:error|warning): .*)$")),
    ("linker", re.compile(r"^(.*(?:undefined reference to|LNK\d{4}|ld(?:\.lld)?: error).*)$")),
    ("python", re.compile(r"^((?:\w+\.)*\w*(?:Error|Exception): .*)$")),
    ("generic", re.compile(r"^(.*(?:\bERROR\b|\bFATAL\b|No space left|Killed|Segmentation fault).*)$")),
]


def available_sets():
    return sorted(p.stem for p in SIG_DIR.glob("*.yaml"))


def load_success(name):
    """Patterns that prove a run SUCCEEDED (top-level `success:` list in each set)."""
    names = available_sets() if name == "auto" else [name]
    return [re.compile(p, re.I | re.M) for n in names
            for p in (settings.read_file(SIG_DIR / f"{n}.yaml") or {}).get("success", [])]


def load_signatures(name):
    """A set by name, or 'auto' = every set (each hit says which set it came from)."""
    names = available_sets() if name == "auto" else [name]
    sigs = []
    for n in names:
        p = SIG_DIR / f"{n}.yaml"
        if not p.is_file():
            raise FileNotFoundError(f"no signature set {n!r}; have {available_sets()}")
        data = settings.read_file(p) or {}
        for s in data.get("signatures", []):
            for key in ("id", "pattern", "cause", "fix"):
                if key not in s:
                    raise ValueError(f"{p.name}: signature {s.get('id')!r} lacks {key!r}")
            s = dict(s, set=n, _rx=re.compile(s["pattern"], re.I | re.M))
            sigs.append(s)
    return sigs


# Lines that look like errors but carry no information.
NOISE = re.compile(r"^\s*\[!!!\] FATAL HALT \[!!!\]\s*$")


def extract_errors(text, limit=200):
    """Distinct error lines, in order of first appearance."""
    seen, out = set(), []
    for line in text.splitlines():
        if NOISE.match(line):
            continue
        for kind, rx in EXTRACTORS:
            m = rx.match(line)
            if m:
                msg = m.group(1).strip()
                key = re.sub(r"\s+", " ", msg)
                if key not in seen:
                    seen.add(key)
                    out.append({"kind": kind, "line": msg[:400]})
                break
        if len(out) >= limit:
            break
    return out


def triage_text(text, set_name="auto"):
    sigs = load_signatures(set_name)
    errors = extract_errors(text)
    # Match against error lines first; fall back to the whole log for failures
    # that print no recognisable error line (e.g. suppressed output).
    blob = "\n".join(e["line"] for e in errors)
    matches = []
    for s in sigs:
        where = "errors" if s["_rx"].search(blob) else ("log" if s.get("whole_log") and s["_rx"].search(text) else None)
        if where:
            matches.append({k: s[k] for k in ("id", "set", "cause", "fix") if k in s}
                           | {"check": s.get("check"), "matched_in": where})
    succeeded = any(rx.search(text) for rx in load_success(set_name))
    if matches:
        verdict = "known"
    elif succeeded:
        verdict = "success"            # warnings may exist, but the tool said it finished
    else:
        verdict = "unrecognised" if errors else "no-error-lines"
    result = {"errors": errors[:40], "error_count": len(errors), "matches": matches, "verdict": verdict}
    if not matches and errors:
        first = re.escape(errors[0]["line"][:60])
        result["stub"] = (f"- id: CHANGEME\n  pattern: '{first}'\n  cause: >-\n    What actually failed.\n"
                          f"  fix: >-\n    The fix that worked.\n  check: null\n")
    if result["verdict"] == "no-error-lines":
        result["hint"] = ("No error lines found. If the build failed while printing nothing, suspect output "
                          "suppression (agent environment variables) or a killed process (out of memory).")
    return result


def triage_file(path, set_name="auto"):
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"log": str(path), "set": set_name} | triage_text(text, set_name)
