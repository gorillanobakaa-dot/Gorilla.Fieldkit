"""refcheck - every file, module or link that is named somewhere must exist.

Harvested from eight Gorilla.firefox scripts that each solved one case of it:
check_deleted_file_refs, validate_package_manifest, check_l10n_resources,
check_ftl_resources, check_lazy_getters, fix_orphan_cases, check_dropped_imports,
plus a document harness's reference-identifier check. One engine, rules as data.

A rule says where names come from and where they must exist:

    manifest   each non-empty, non-comment line of FILE is a path relative to BASE
    markdown   every relative link [text](path) in *.md files under ROOT
    python     every `import x.y` / `from x.y import` under ROOT that looks local
    regex      every capture group of PATTERN in files matching GLOB under ROOT

    fieldkit refcheck manifest MANIFEST.txt [--base DIR]
    fieldkit refcheck markdown DIR
    fieldkit refcheck python DIR
    fieldkit refcheck regex DIR --pattern 'src="([^"]+)"' --glob '*.html'

Output is short on purpose: "3 of 12 missing: a, b, c" and a NEXT line, so a
small model can act on it without reading anything else.
"""
import ast
import re
from pathlib import Path

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _result(rule, source, names, missing, base):
    return {"rule": rule, "source": str(source), "base": str(base), "checked": len(names),
            "missing": missing, "ok": not missing}


def check_manifest(manifest, base=None):
    manifest = Path(manifest)
    base = Path(base) if base else manifest.parent
    names = [l.strip() for l in manifest.read_text(encoding="utf-8", errors="replace").splitlines()
             if l.strip() and not l.strip().startswith("#")]
    missing = [n for n in names if not (base / n).exists()]
    return _result("manifest", manifest, names, missing, base)


def check_markdown(root):
    root = Path(root)
    names, missing = [], []
    for md in sorted(root.rglob("*.md")):
        for target in MD_LINK.findall(md.read_text(encoding="utf-8", errors="replace")):
            if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                continue                                  # http:, mailto:, in-page anchors
            path = target.split("#", 1)[0]
            if not path:
                continue
            ref = f"{md.relative_to(root).as_posix()} -> {target}"
            names.append(ref)
            if not (md.parent / path).exists():
                missing.append(ref)
    return _result("markdown", root, names, missing, root)


def _local_top_levels(root):
    tops = {p.stem for p in root.glob("*.py")} | {p.name for p in root.iterdir() if (p / "__init__.py").is_file()}
    return tops


def check_python(root):
    """Imports of the project's own packages that point at no module or package."""
    root = Path(root)
    tops = _local_top_levels(root)
    names, missing = [], []
    for py in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        mods = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mods.append(n.module)
        for m in mods:
            if m.split(".")[0] not in tops:
                continue                                  # stdlib / third party: not this check's business
            ref = f"{py.relative_to(root).as_posix()} -> {m}"
            names.append(ref)
            p = root.joinpath(*m.split("."))
            if not (p.with_suffix(".py").is_file() or (p / "__init__.py").is_file()):
                missing.append(ref)
    return _result("python", root, names, missing, root)


def check_regex(root, pattern, glob="*"):
    root = Path(root)
    rx = re.compile(pattern)
    names, missing = [], []
    for f in sorted(root.rglob(glob)):
        if not f.is_file():
            continue
        for m in rx.finditer(f.read_text(encoding="utf-8", errors="replace")):
            target = m.group(1)
            ref = f"{f.relative_to(root).as_posix()} -> {target}"
            names.append(ref)
            if not ((f.parent / target).exists() or (root / target).exists()):
                missing.append(ref)
    return _result("regex", root, names, missing, root)


def lines(r, limit=20):
    """The short answer a small model gets."""
    if r["ok"]:
        return [f"all {r['checked']} references exist ({r['rule']}).", "NEXT: nothing is missing."]
    head = f"{len(r['missing'])} of {r['checked']} missing ({r['rule']}):"
    body = [f"- {m}" for m in r["missing"][:limit]]
    more = [f"... and {len(r['missing']) - limit} more"] if len(r["missing"]) > limit else []
    return [head] + body + more + ["NEXT: these are exactly the missing ones; report them or restore them."]
