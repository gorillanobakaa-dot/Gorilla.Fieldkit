"""Facts about a repository before reading its code (docs/METHODS.md #8).

    fieldkit survey PATH [PATH ...] [--no-privacy] [--json]

Plans built on a guess cost a day: a fork assumed to be TypeScript was Go; a "general" tool was full of one
machine's paths. This reads only facts, the same way for every repository:

  - languages: files and lines per language (vendored, generated and build folders skipped)
  - build systems: go.mod (module, go version), pyproject/setup.py, package.json, Cargo.toml, CMake, .NET, PowerShell
  - tests: test files, and test functions counted per framework (Go, pytest, Rust, JS/TS, PowerShell Pester)
  - licence (SPDX guess from the LICENSE text), README size, CI workflows
  - files over 5 MB (they make clones slow and often should not be there)
  - the privacy scan: hard-coded home paths, emails, key-like strings, your private words (fieldkit privacy)
  - git: remote, branch, last commit date

Then read the code with these facts in hand, and check any claim made about the repository against them.
Exit 0; 2 when a path is missing.
"""
import argparse
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

from . import emit

LANG = {".go": "Go", ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
        ".mjs": "JavaScript", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".c": "C", ".h": "C", ".cpp": "C++",
        ".cc": "C++", ".hpp": "C++", ".cs": "C#", ".ps1": "PowerShell", ".psm1": "PowerShell", ".sh": "Shell",
        ".bash": "Shell", ".cmd": "Batch", ".bat": "Batch", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
        ".lua": "Lua", ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".yaml": "YAML", ".yml": "YAML",
        ".json": "JSON", ".md": "Markdown", ".toml": "TOML", ".xml": "XML"}
SKIP = {".git", "node_modules", "vendor", "dist", "build", "target", "__pycache__", ".venv", "venv", ".tox",
        ".mypy_cache", ".pytest_cache", "toolbox", "state", "local"}
TEST_FUNCS = [("Go", re.compile(r"^func (Test|Benchmark|Fuzz)\w*\(", re.M), ("_test.go",)),
              ("pytest", re.compile(r"^\s*(async\s+)?def test_\w*\(", re.M), (".py",)),
              ("Rust", re.compile(r"#\[(tokio::)?test\]"), (".rs",)),
              ("JS/TS", re.compile(r"^\s*(it|test)\(\s*['\"`]", re.M), (".js", ".ts", ".tsx", ".mjs")),
              ("Pester", re.compile(r"^\s*It\s+['\"]", re.M), (".ps1",))]
LICENCES = [("AGPL-3.0", r"GNU AFFERO GENERAL PUBLIC LICENSE"), ("LGPL", r"GNU LESSER GENERAL PUBLIC LICENSE"),
            ("GPL-3.0", r"GNU GENERAL PUBLIC LICENSE\s+Version 3"), ("GPL-2.0", r"GNU GENERAL PUBLIC LICENSE\s+Version 2"),
            ("Apache-2.0", r"Apache License,?\s+Version 2\.0"), ("MIT", r"Permission is hereby granted, free of charge"),
            ("BSD", r"Redistribution and use in source and binary forms"), ("MPL-2.0", r"Mozilla Public License"),
            ("Unlicense", r"This is free and unencumbered software")]
BIG = 5 * 1024 * 1024
DATA = {"Markdown", "JSON", "YAML", "TOML", "XML", "HTML", "CSS"}           # not what a project is written in


def _is_test_file(rel):
    n = rel.name
    return (n.endswith("_test.go") or (n.startswith("test_") and n.endswith(".py")) or n.endswith("_test.py")
            or re.search(r"\.(test|spec)\.(js|ts|tsx|mjs)$", n) is not None or n.endswith(".Tests.ps1")
            or "tests" in rel.parts[:-1])


def _git(path, *args):
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def survey(path, privacy=True):
    root = Path(path)
    langs, lines, tests = Counter(), Counter(), Counter()
    test_files, big, files = 0, [], 0
    entry = []
    for top, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("."))
        for name in names:
            p = Path(top) / name
            rel = p.relative_to(root)
            files += 1
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > BIG:
                big.append((str(rel), size))
            lang = LANG.get(p.suffix.lower())
            if not lang or size > 2 * 1024 * 1024:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            langs[lang] += 1
            lines[lang] += text.count("\n") + 1
            if _is_test_file(rel):
                test_files += 1
            for fw, rx, exts in TEST_FUNCS:
                if name.endswith(exts) and (fw != "pytest" or _is_test_file(rel)):
                    tests[fw] += len(rx.findall(text))
            if name in ("main.go", "__main__.py", "main.rs", "Program.cs") or (
                    p.suffix.lower() in (".cmd", ".bat") and len(rel.parts) == 1):
                entry.append(str(rel))
    build = {}
    gm = root / "go.mod"
    if gm.is_file():
        t = gm.read_text(encoding="utf-8", errors="replace")
        build["go"] = {"module": (re.search(r"^module\s+(\S+)", t, re.M) or [None, None])[1],
                       "go": (re.search(r"^go\s+(\S+)", t, re.M) or [None, None])[1]}
    for f, k in (("pyproject.toml", "python"), ("setup.py", "python"), ("package.json", "node"),
                 ("Cargo.toml", "rust"), ("CMakeLists.txt", "cmake"), ("Makefile", "make"),
                 (".goreleaser.yml", "goreleaser"), ("Dockerfile", "docker")):
        if (root / f).is_file():
            build.setdefault(k, {})["file"] = f
    if any(root.glob("*.csproj")) or any(root.glob("*.sln")):
        build["dotnet"] = {"file": next(iter(list(root.glob("*.sln")) + list(root.glob("*.csproj")))).name}
    lic_file = next((p for p in sorted(root.glob("*")) if re.match(r"(?i)(licen[sc]e|copying)(\.|$)", p.name)), None)
    licence = None
    if lic_file:
        txt = lic_file.read_text(encoding="utf-8", errors="replace")[:4000]
        licence = next((spdx for spdx, rx in LICENCES if re.search(rx, txt, re.I)), "unrecognised")
    readme = next((p for p in sorted(root.glob("*")) if p.name.lower().startswith("readme")), None)
    ci = sorted(p.name for p in (root / ".github" / "workflows").glob("*.y*ml")) \
        if (root / ".github" / "workflows").is_dir() else []
    rep = {"path": str(root), "files": files, "languages": dict(langs.most_common()),
           "lines": dict(lines.most_common()), "main_language": next((k for k, _ in lines.most_common() if k not in DATA), None),
           "build": build, "test_files": test_files, "test_functions": dict(tests), "licence": licence,
           "licence_file": lic_file.name if lic_file else None,
           "readme_bytes": readme.stat().st_size if readme else 0, "ci": ci, "entry_points": entry[:20],
           "big_files": sorted(big, key=lambda x: -x[1])[:10],
           "git": {"remote": _git(root, "remote", "get-url", "origin"), "branch": _git(root, "branch", "--show-current"),
                   "last_commit": _git(root, "log", "-1", "--format=%cs %h %s")}}
    if privacy:
        from ..core import privacy as priv
        try:
            found = priv.scan_path(root, git_only=(root / ".git").exists())
        except (ValueError, FileNotFoundError):
            found = priv.scan_path(root)
        kinds = Counter(f.get("kind", "?") for fs in found.values() for f in fs)
        rep["privacy"] = {"files": len(found), "findings": dict(kinds),
                          "examples": [f"{k}: {v[0].get('kind')} line {v[0].get('line')}" for k, v in
                                       list(found.items())[:8]]}
    return rep


def _lines(d):
    out = [f"{d['path']}: {d['main_language'] or 'no code'}; {d['files']} files"]
    out.append("  languages: " + ", ".join(f"{k} {d['languages'][k]} files / {d['lines'][k]:,} lines"
                                           for k in list(d["languages"])[:6]))
    if d["build"]:
        out.append("  build: " + ", ".join(f"{k} {v}" for k, v in d["build"].items()))
    out.append(f"  tests: {d['test_files']} test files; " + (", ".join(f"{k} {v}" for k, v in
                                                                     d["test_functions"].items() if v) or "no test functions found"))
    out.append(f"  licence: {d['licence'] or 'NONE'}; README {d['readme_bytes']:,} bytes; CI: {', '.join(d['ci']) or 'none'}")
    if d["big_files"]:
        out.append("  big files: " + ", ".join(f"{n} ({s // 1048576} MB)" for n, s in d["big_files"][:5]))
    if "privacy" in d:
        pr = d["privacy"]
        out.append(f"  privacy: {pr['files']} file(s) with findings {pr['findings'] or ''}")
        out += [f"    {e}" for e in pr["examples"]]
    g = d["git"]
    if g["remote"]:
        out.append(f"  git: {g['remote']} [{g['branch']}] last {g['last_commit']}")
    return out


def main(argv=None, prog="fieldkit survey"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--no-privacy", action="store_true", help="skip the privacy scan")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    missing = [p for p in a.paths if not Path(p).is_dir()]
    if missing:
        print(f"REFUSED: not a folder: {', '.join(missing)}")
        return 2
    reps = [survey(p, not a.no_privacy) for p in a.paths]
    if a.json:
        emit({"repositories": reps}, True, None)
    else:
        for r in reps:
            for line in _lines(r):
                print(line)
    return 0
