"""inventory.py - count the real tooling in local folders and GitHub repos.

Answers "what have I already built, and where is it?" before anything is
merged. For each place it counts code files, test files, config files
(json/yaml/toml) and SKILL.md files, and lists the paths, so a later step can
decide what to import. Read-only: it never changes a folder or a repo.

Usage:
    python inventory.py local  DIR [DIR ...]        [--json out.json]
    python inventory.py github OWNER [--repos a b]  [--json out.json]

GitHub mode uses the `gh` CLI (already logged in); no token is read or printed.
Works the same on Windows and Linux.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CODE = {".py", ".ps1", ".psm1", ".sh", ".js", ".mjs", ".ts", ".go", ".rs", ".c", ".cpp", ".cs"}
CONFIG = {".json", ".yaml", ".yml", ".toml"}
TEST_RE = re.compile(r"(^|[/\\])(tests?|__tests__)([/\\])|(^|[/\\])test_[^/\\]+$|_test\.(go|py)$|\.Tests\.ps1$", re.I)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "target", "dist", "build"}
SKIP_PREFIX = ("obj-",)           # Firefox object dirs
SKIP_NAMES = {"src"}              # only skipped when --skip-src (vendored upstream trees)


def classify(paths):
    out = {"files": len(paths), "code": [], "tests": [], "config": [], "skills": []}
    for p in paths:
        ext = os.path.splitext(p)[1].lower()
        if p.endswith("SKILL.md"):
            out["skills"].append(p)
        if TEST_RE.search(p):
            out["tests"].append(p)
        elif ext in CODE:
            out["code"].append(p)
        elif ext in CONFIG:
            out["config"].append(p)
    return out


def walk_local(root, skip_src):
    root = Path(root)
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(SKIP_PREFIX)
                       and not (skip_src and d in SKIP_NAMES and Path(dirpath) == root)]
        for f in filenames:
            paths.append(str(Path(dirpath, f).relative_to(root)).replace("\\", "/"))
    return paths


def gh_json(args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}: {r.stderr.strip()}")
    return json.loads(r.stdout)


def walk_github(owner, repo):
    meta = gh_json(["api", f"repos/{owner}/{repo}"])
    tree = gh_json(["api", f"repos/{owner}/{repo}/git/trees/{meta['default_branch']}?recursive=1"])
    paths = [t["path"] for t in tree.get("tree", []) if t["type"] == "blob"]
    return paths, {"private": meta["private"], "archived": meta["archived"],
                   "pushed": meta["pushed_at"][:10], "truncated": tree.get("truncated", False)}


def summary_line(name, c, extra=""):
    return (f"{name:<45} files={c['files']:<6} code={len(c['code']):<5} tests={len(c['tests']):<5} "
            f"config={len(c['config']):<4} skills={len(c['skills'])} {extra}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    lo = sub.add_parser("local")
    lo.add_argument("dirs", nargs="+")
    lo.add_argument("--skip-src", action="store_true", help="skip a top-level src/ (vendored upstream code)")
    gh = sub.add_parser("github")
    gh.add_argument("owner")
    gh.add_argument("--repos", nargs="*")
    for p in (lo, gh):
        p.add_argument("--json", help="write full path lists here")
    a = ap.parse_args(argv)

    report = {}
    if a.mode == "local":
        for d in a.dirs:
            if not Path(d).is_dir():
                print(f"{d}: not a folder", file=sys.stderr)
                continue
            c = classify(walk_local(d, a.skip_src))
            report[str(Path(d).resolve())] = c
            print(summary_line(Path(d).name, c))
    else:
        repos = a.repos or [r["name"] for r in gh_json(["repo", "list", a.owner, "--limit", "200", "--json", "name"])]
        for r in repos:
            try:
                paths, meta = walk_github(a.owner, r)
            except RuntimeError as e:
                print(f"{r}: {e}", file=sys.stderr)
                continue
            c = classify(paths)
            c["meta"] = meta
            report[f"{a.owner}/{r}"] = c
            flags = " ".join(k for k in ("private", "archived", "truncated") if meta[k])
            print(summary_line(r, c, f"pushed={meta['pushed']} {flags}"))
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"\nfull lists: {a.json}")


if __name__ == "__main__":
    main()
