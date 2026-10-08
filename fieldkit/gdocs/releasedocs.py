"""A release's documents held against the material they were written from: is every number and web address in them
found in that material, does anything private or banned appear, and do the dual-track documents pass the full
layman / developer checks (checks.py)?

    fieldkit docs release --manifest <release-docs.yaml>
    fieldkit docs release [PLAIN.md ...] --source F [--source F ...] [--source-head F ...] [--head-lines 60]
                          [--layman-doc F] [--developer-doc F]

The docs workflow (`fieldkit docs check`) is bound to Fieldkit's own module groups: its corpus is a group's source
files and MEASUREMENTS.md. A release of another project (the 157 release notes, story and the two tracks, 2026-10-03)
has a different corpus: the migration plan, the decision register, the bench results, the patch READMEs, the counts
of the port (`build-harness stats`). On 2026-10-03 a throwaway script called the same check functions with that
corpus typed in; this is that script, with the paths as arguments or in a manifest beside the release:

    # release-docs.yaml - paths relative to this file (absolute paths work too)
    sources: [../../../MIGRATION-PLAN.md, ../../../decisions/PRODUCT-DECISIONS.yaml, counts-157.txt]
    source_heads: [../../../claims/AUDIT-157.md]      # only the first head_lines lines count (a long audit's head)
    head_lines: 60
    layman: [GORILLA-157-LAYMAN.md]
    developer: [GORILLA-157-DEVELOPER.md]
    plain: [RELEASE-NOTES-157.0.md, STORY-157.md]       # numbers, web addresses, privacy only

Fail closed: a source or document that cannot be read is a finding, never skipped. Exit 3 on any finding.
"""
from pathlib import Path

from . import checks

KEYS = ("sources", "source_heads", "layman", "developer", "plain")


def load_manifest(path):
    """-> {sources, source_heads, layman, developer, plain: [Path], head_lines: int}; relative paths from the file."""
    import yaml
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    unknown = set(data) - set(KEYS) - {"head_lines"}
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {sorted(unknown)}; known: {', '.join(KEYS)}, head_lines")
    out = {k: [(path.parent / p) if not Path(p).is_absolute() else Path(p) for p in (data.get(k) or [])] for k in KEYS}
    out["head_lines"] = int(data.get("head_lines", 60))
    return out


def _read(p, problems, head=None):
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readline() for _ in range(head)) if head else fh.read()
    except OSError as e:
        problems.append(f"cannot read {p}: {e.strerror or e}")
        return None


def check(sources=(), source_heads=(), head_lines=60, layman=(), developer=(), plain=(), parser=None, terms=None):
    """-> {"ok", "sources", "problems", "documents": {path: {"track", "findings", "metrics"}}}"""
    problems = []
    texts = [t for t in (_read(p, problems) for p in sources) if t is not None]
    texts += [t for t in (_read(p, problems, head_lines) for p in source_heads) if t is not None]
    if not texts:
        problems.append("no source material: every number and web address would be unsourced")
    corpus = "\n".join(texts)
    allowed = checks.allowed_numbers(texts)
    docs = {}
    for track, files in (("layman", layman), ("developer", developer), ("plain", plain)):
        for p in files:
            md = _read(p, problems)
            if md is None:
                continue
            if track == "layman":
                f, m = checks.check_layman(md, allowed, corpus, parser=parser, terms=terms)
            elif track == "developer":
                f, m = checks.check_developer(md, allowed, corpus, parser=parser, terms=terms)
            else:
                f = [f"numbers: {x}" for x in checks.number_findings(md, allowed)]
                f += checks.url_findings(md, corpus)
                f += checks.privacy_findings(md, terms, corpus)
                m = {"words": checks.words(md)}
            docs[str(p)] = {"track": track, "findings": f, "metrics": m}
    if not docs and not problems:
        problems.append("no document to check: give --layman, --developer or plain documents")
    ok = not problems and all(not d["findings"] for d in docs.values())
    return {"ok": ok, "sources": len(texts), "problems": problems, "documents": docs}


def lines(r):
    out = [f"release documents against {r['sources']} source file(s)"]
    out += [f"PROBLEM: {p}" for p in r["problems"]]
    for p, d in r["documents"].items():
        out.append(f"{'PASS' if not d['findings'] else 'FAIL'} [{d['track']}] {p}  ({d['metrics'].get('words_total') or d['metrics'].get('words')} words)")
        out += [f"     - {x}" for x in d["findings"]]
    out.append("RELEASE DOCS " + ("OK" if r["ok"] else "NOT OK") + (
        "" if r["ok"] else ": every number and web address must be in the sources (or next to 'not measured'); "
                           "fix the document or add the material it comes from"))
    return out
