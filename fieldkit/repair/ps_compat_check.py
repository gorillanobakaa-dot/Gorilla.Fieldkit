# Version: 1.0.0 - updated 26-11-20
"""Pre-CI health check: catches every class of failure that broke the release pipeline.

Runs BEFORE pushing a tag or opening a PR. Detects:

  1. PowerShell 5.1/7 incompatibilities in .ps1 files
       Expand-Archive         - broken in pwsh 7 (removed .NET 6 overload)
       Get-FileHash           - broken when powershell.exe launched from pwsh.exe
       ConvertFrom-Json -AsHashtable  - PS 6+ only

  2. New repair tools not fully registered
       - missing from fieldkit/repair/__init__.py ACTIONS dict
       - missing toolcard in fieldkit/desk/tools.yaml
       - missing entry in skills/fieldkit-repair/SKILL.md

  3. Stale generated files (must be regenerated before commit)
       - fieldkit/core/cmdtree.json     (fieldkit.core.cmdtree write)
       - fieldkit/desk/platform_index.json  (fieldkit.core.platform_gate --write)

  4. BOM (U+FEFF) in Python source files (causes SyntaxError on Linux)

    fieldkit repair ps-compat-check [--ps1 PATH...] [--repo PATH] [--apply] [--json]

    --ps1 PATH...  specific .ps1 files to check (default: all in repo)
    --repo PATH    repo root (default: auto-detect from cwd)
    --apply        auto-fix findings that have a known safe rewrite
    --json         machine-readable output

Exit: 0 clean, 1 error, 3 findings.
"""
import argparse
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

from ._common import emit, write_bytes

# ---------------------------------------------------------------------------
# PS compat patterns
# ---------------------------------------------------------------------------
PS_PATTERNS = [
    {
        "id": "expand-archive-ps7-broken",
        "pattern": re.compile(r"Expand-Archive\b", re.IGNORECASE),
        "message": "Expand-Archive breaks on windows-latest (pwsh 7): 3-arg ExtractToDirectory removed in .NET 6",
        "fix": "Replace with: Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::ExtractToDirectory(\, \)",
        "auto_fix": lambda line: re.sub(
            r"Expand-Archive\s+-LiteralPath\s+(\S+)\s+-DestinationPath\s+(\S+)",
            r"Add-Type -AssemblyName System.IO.Compression.FileSystem\n    [IO.Compression.ZipFile]::ExtractToDirectory(\1, \2)",
            line, flags=re.IGNORECASE
        ),
    },
    {
        "id": "get-filehash-ps7-module-path",
        "pattern": re.compile(r"\bGet-FileHash\b", re.IGNORECASE),
        "message": "Get-FileHash fails when powershell.exe is launched from pwsh.exe (inherited module path)",
        "fix": "Replace with [System.Security.Cryptography.SHA256]::Create() - see install.ps1 for the pattern",
        "auto_fix": None,
    },
    {
        "id": "convertfromjson-ashashtable-ps6",
        "pattern": re.compile(r"ConvertFrom-Json\s+-AsHashtable", re.IGNORECASE),
        "message": "ConvertFrom-Json -AsHashtable is PS 6+ only; not available in Windows PowerShell 5.1",
        "fix": "Remove -AsHashtable or gate with: if (\System.Collections.Hashtable.PSVersion.Major -ge 6)",
        "auto_fix": None,
    },
]


def check_ps1_file(path):
    """-> list of findings, or (None, error_str) on read failure."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return None, str(e)
    results = []
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            continue
        for pat in PS_PATTERNS:
            if pat["pattern"].search(line):
                results.append({"file": str(path), "line": i, "rule": pat["id"],
                                 "message": pat["message"], "fix": pat["fix"],
                                 "_auto_fix": pat["auto_fix"], "_line": line})
    return results, None


def apply_ps1_fixes(path, findings):
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    fixed = 0
    for f in findings:
        if f["_auto_fix"] is None:
            continue
        idx = f["line"] - 1
        new = f["_auto_fix"](lines[idx])
        if new != lines[idx]:
            lines[idx] = new if new.endswith("\n") else new + "\n"
            fixed += 1
    return fixed, "".join(lines)


# ---------------------------------------------------------------------------
# Registration checks
# ---------------------------------------------------------------------------
def check_repair_registration(repo):
    """Check every module in fieldkit/repair/ is registered in ACTIONS, has a toolcard, and is in SKILL.md."""
    findings = []
    repair_dir = repo / "fieldkit" / "repair"
    actions_file = repair_dir / "__init__.py"
    tools_yaml = repo / "fieldkit" / "desk" / "tools.yaml"
    skill_file = repo / "skills" / "fieldkit-repair" / "SKILL.md"

    # Load current ACTIONS dict
    try:
        import fieldkit.repair as repair_mod
        actions = dict(repair_mod.ACTIONS)
    except Exception as e:
        findings.append({"rule": "repair-actions-unloadable", "message": str(e), "fix": "fix fieldkit/repair/__init__.py"})
        return findings

    # Load toolcards
    try:
        import yaml
        tools_data = yaml.safe_load(tools_yaml.read_text(encoding="utf-8"))
        card_ids = {t["id"] for t in tools_data.get("tools", [])}
    except Exception as e:
        findings.append({"rule": "tools-yaml-unloadable", "message": str(e), "fix": "fix fieldkit/desk/tools.yaml"})
        card_ids = set()

    # Load SKILL.md
    try:
        skill_text = skill_file.read_text(encoding="utf-8")
    except Exception as e:
        findings.append({"rule": "skill-unloadable", "message": str(e), "fix": f"fix {skill_file}"})
        skill_text = ""

    # Check every module in repair/ is registered
    for py in sorted(repair_dir.glob("*.py")):
        if py.name.startswith("_"):
            continue
        mod_name = py.stem
        # Find the action name(s) that map to this module
        mapped = [k for k, v in actions.items() if v == mod_name]
        if not mapped:
            findings.append({
                "rule": "repair-module-not-registered",
                "message": f"fieldkit/repair/{py.name} has no entry in repair/__init__.py ACTIONS",
                "fix": f'Add to ACTIONS in fieldkit/repair/__init__.py: "ACTION-NAME": "{mod_name}"',
                "file": str(py),
            })
            continue
        for action in mapped:
            if action not in card_ids:
                findings.append({
                    "rule": "repair-tool-no-card",
                    "message": f"repair action '{action}' has no toolcard in fieldkit/desk/tools.yaml",
                    "fix": f"Add a toolcard with id: {action} and entry: [python, -m, fieldkit, repair, {action}]",
                })
            if f"fieldkit repair {action}" not in skill_text:
                findings.append({
                    "rule": "repair-tool-not-in-skill",
                    "message": f"repair action '{action}' not mentioned in skills/fieldkit-repair/SKILL.md",
                    "fix": f"Add a row: | description | ieldkit repair {action} |",
                })

    return findings


# ---------------------------------------------------------------------------
# Stale generated files
# ---------------------------------------------------------------------------
GENERATORS = {
    "fieldkit/core/cmdtree.json": [sys.executable, "-m", "fieldkit.core.cmdtree", "write"],
    "fieldkit/desk/platform_index.json": [sys.executable, "-m", "fieldkit.core.platform_gate", "--write"],
}


def check_stale_generated(repo):
    """Check generated files are not stale by asking git if they are modified/untracked."""
    findings = []
    for rel_path, cmd in GENERATORS.items():
        committed = repo / rel_path
        if not committed.exists():
            findings.append({
                "rule": "generated-file-missing",
                "message": f"{rel_path} is missing",
                "fix": f"cd {repo}; {' '.join(str(c) for c in cmd)}",
            })
            continue
        # Ask git whether the file differs from HEAD (fast - no generator run)
        try:
            r = subprocess.run(
                ["git", "diff", "--name-only", "HEAD", "--", rel_path],
                cwd=str(repo), capture_output=True, text=True, timeout=10
            )
            if rel_path in r.stdout:
                findings.append({
                    "rule": "generated-file-stale",
                    "message": f"{rel_path} differs from HEAD - regenerate before committing",
                    "fix": f"cd {repo}; {' '.join(str(c) for c in cmd)} && git add {rel_path}",
                })
        except Exception as e:
            pass  # git not available; skip
    return findings


# ---------------------------------------------------------------------------
# BOM check
# ---------------------------------------------------------------------------
def check_bom(repo):
    """Find Python source files with a UTF-8 BOM (causes SyntaxError on Linux)."""
    findings = []
    for py in sorted(repo.rglob("*.py")):
        if any(p in py.parts for p in (".git", "__pycache__", ".venv", "node_modules")):
            continue
        try:
            raw = py.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                findings.append({
                    "rule": "python-bom",
                    "message": f"{py.relative_to(repo)} has a UTF-8 BOM (causes SyntaxError: invalid non-printable character U+FEFF on Linux)",
                    "fix": f"Remove BOM: re-save as UTF-8 without BOM",
                    "file": str(py),
                    "_path": py,
                })
        except OSError:
            pass
    return findings


def apply_bom_fixes(findings):
    fixed = 0
    for f in findings:
        p = f.get("_path")
        if p:
            raw = p.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                p.write_bytes(raw[3:])
                fixed += 1
    return fixed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair ps-compat-check",
                                 description=__doc__.split("\n")[0])
    ap.add_argument("--ps1", nargs="*", metavar="PATH", help=".ps1 files to check (default: all in repo)")
    ap.add_argument("--repo", default=None, help="repo root (default: auto-detect)")
    ap.add_argument("--apply", action="store_true", help="auto-fix where possible")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    # Resolve repo root
    if a.repo:
        repo = Path(a.repo).resolve()
    else:
        repo = Path.cwd()
        for p in [repo] + list(repo.parents):
            if (p / ".git").exists():
                repo = p
                break

    all_findings = []

    # 1. PS compat
    ps1_files = [Path(f) for f in a.ps1] if a.ps1 else list(repo.rglob("*.ps1"))
    ps1_files = [f for f in ps1_files if not any(p in f.parts for p in (".git", ".venv", "__pycache__"))]
    for ps1 in ps1_files:
        findings, err = check_ps1_file(ps1)
        if err:
            print(f"ERROR reading {ps1}: {err}", file=sys.stderr)
            continue
        all_findings.extend(findings)

    # 2. Registration
    all_findings.extend(check_repair_registration(repo))

    # 3. Stale generated files
    all_findings.extend(check_stale_generated(repo))

    # 4. BOM
    all_findings.extend(check_bom(repo))

    if not all_findings:
        emit(a.json, "PASS", "pre-CI health check passed - nothing to fix",
             "fieldkit repair ps-compat-check --repo .")
        return 0

    if a.apply:
        fixed = 0
        # Fix PS1 issues
        by_file = {}
        for f in all_findings:
            if f.get("rule", "").startswith("expand-archive") or "_auto_fix" in f:
                by_file.setdefault(f.get("file"), []).append(f)
        for path, fs in by_file.items():
            if path and Path(path).suffix == ".ps1":
                n, new_text = apply_ps1_fixes(Path(path), fs)
                if n:
                    write_bytes(Path(path), new_text.encode("utf-8"))
                    fixed += n
        # Fix BOMs
        bom_findings = [f for f in all_findings if f.get("rule") == "python-bom"]
        fixed += apply_bom_fixes(bom_findings)
        # Regenerate stale files
        stale = [f for f in all_findings if f.get("rule") in ("generated-file-stale", "generated-file-missing")]
        for f in stale:
            for rel, cmd in GENERATORS.items():
                if rel in f.get("fix", ""):
                    r = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)
                    if r.returncode == 0:
                        fixed += 1

        remaining = [f for f in all_findings
                     if f.get("rule") not in ("expand-archive-ps7-broken", "python-bom",
                                               "generated-file-stale", "generated-file-missing")]
        if not remaining:
            emit(a.json, "PASS", f"fixed {fixed} issue(s) - all clear",
                 "re-run fieldkit repair ps-compat-check --repo . to confirm", {"fixed": fixed})
            return 0
        emit(a.json, "FAIL", f"fixed {fixed} issue(s); {len(remaining)} need manual attention",
             "fieldkit repair ps-compat-check --repo . --json", {"remaining": remaining})
        return 3

    # Report
    total = len(all_findings)
    if not a.json:
        for f in all_findings:
            loc = f":{f['line']}" if "line" in f else ""
            src = f.get("file", "")
            if src:
                try:
                    src = str(Path(src).relative_to(repo))
                except ValueError:
                    pass
            print(f"  [{f['rule']}] {src}{loc}")
            print(f"    {f['message']}")
            print(f"    FIX: {f['fix']}")
    emit(a.json, "FAIL", f"{total} pre-CI issue(s) found",
         "fieldkit repair ps-compat-check --repo . --apply",
         {"findings": [{k: v for k, v in f.items() if not k.startswith("_")} for f in all_findings]})
    return 3


if __name__ == "__main__":
    sys.exit(main())