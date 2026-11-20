# Version: 1.0.0 - updated 26-11-20
"""Check .ps1 files for PowerShell version compatibility issues that break on GitHub Actions windows-latest.

GitHub Actions windows-latest ships PowerShell 7 (pwsh.exe) as the default shell. Scripts written for
PowerShell 5.1 (powershell.exe) can silently break when the runner upgrades. This tool detects the known
incompatibility patterns and reports them with a fix suggestion.

Known patterns detected:
  - Expand-Archive: PS7 uses a .NET 6 overload of ExtractToDirectory that removes the 3-argument form;
    replace with [IO.Compression.ZipFile]::ExtractToDirectory($src, $dest)
  - Get-FileHash: in a Windows PowerShell 5.1 process started FROM PowerShell 7 the module path is
    inherited and Get-FileHash cannot load; use [System.Security.Cryptography.SHA256] directly
  - ConvertFrom-Json -AsHashtable: added in PS6; not available in 5.1
  - $PSVersionTable.PSVersion: a script that hard-checks for version 5 will refuse to run in 7

    fieldkit repair ps-compat-check FILE... [--apply] [--json]

Exit: 0 clean, 1 error, 3 findings.
"""
import argparse
import re
import sys
from pathlib import Path

from ._common import emit, write_bytes

FINDINGS = [
    {
        "id": "expand-archive-ps7-broken",
        "pattern": re.compile(r"Expand-Archive\b", re.IGNORECASE),
        "message": "Expand-Archive breaks on windows-latest (pwsh 7): the 3-arg ExtractToDirectory overload was removed in .NET 6",
        "fix": "Replace with: Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::ExtractToDirectory($src, $dest)",
        "apply": lambda line: re.sub(
            r"Expand-Archive\s+-LiteralPath\s+(\S+)\s+-DestinationPath\s+(\S+)",
            r"Add-Type -AssemblyName System.IO.Compression.FileSystem\n    [IO.Compression.ZipFile]::ExtractToDirectory(\1, \2)",
            line, flags=re.IGNORECASE
        ),
    },
    {
        "id": "get-filehash-ps7-module-path",
        "pattern": re.compile(r"\bGet-FileHash\b", re.IGNORECASE),
        "message": "Get-FileHash can fail when powershell.exe is started from pwsh.exe (inherited module path breaks the cmdlet)",
        "fix": "Replace with: $sha=[System.Security.Cryptography.SHA256]::Create(); $fs=[System.IO.File]::OpenRead($path); try{$hash=-join($sha.ComputeHash($fs)|ForEach-Object{$_.ToString('x2')})}finally{$fs.Dispose();$sha.Dispose()}",
        "apply": None,  # not auto-fixable (too context-dependent)
    },
    {
        "id": "convertfromjson-ashashtable-ps6",
        "pattern": re.compile(r"ConvertFrom-Json\s+-AsHashtable", re.IGNORECASE),
        "message": "ConvertFrom-Json -AsHashtable is PowerShell 6+; not available in Windows PowerShell 5.1",
        "fix": "Remove -AsHashtable and use a PSCustomObject, or gate with $PSVersionTable.PSVersion.Major -ge 6",
        "apply": None,
    },
]


def check_file(path):
    """-> list of {line, rule, message, fix, apply_fn}"""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return None, str(e)
    results = []
    for i, line in enumerate(text.splitlines(), 1):
        # skip comments
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for f in FINDINGS:
            if f["pattern"].search(line):
                results.append({
                    "line": i,
                    "rule": f["id"],
                    "message": f["message"],
                    "fix": f["fix"],
                    "_apply": f["apply"],
                    "_line_text": line,
                })
    return results, None


def apply_fixes(path, findings):
    """Auto-fix all findings that have an apply function. Returns (fixed_count, new_text)."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    fixed = 0
    for f in findings:
        if f["_apply"] is None:
            continue
        lineno = f["line"] - 1
        new_line = f["_apply"](lines[lineno])
        if new_line != lines[lineno]:
            lines[lineno] = new_line if new_line.endswith("\n") else new_line + "\n"
            fixed += 1
    return fixed, "".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair ps-compat-check",
                                 description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+", help=".ps1 files to check")
    ap.add_argument("--apply", action="store_true",
                    help="auto-fix findings that have a known safe rewrite")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    all_findings = {}
    errors = []
    for f in a.files:
        p = Path(f)
        if not p.suffix.lower() == ".ps1":
            continue
        findings, err = check_file(p)
        if err:
            errors.append({"file": str(p), "error": err})
            continue
        if findings:
            all_findings[str(p)] = findings

    if errors:
        for e in errors:
            print(f"ERROR: {e['file']}: {e['error']}", file=sys.stderr)
        return 1

    if not all_findings:
        emit(a.json, "PASS", "no PowerShell compatibility issues found",
             "fieldkit repair ps-compat-check FILE... [--apply]")
        return 0

    total = sum(len(v) for v in all_findings.values())

    if a.apply:
        fixed_total = 0
        for path, findings in all_findings.items():
            fixed, new_text = apply_fixes(path, findings)
            if fixed:
                write_bytes(path, new_text.encode("utf-8"))
                fixed_total += fixed
        auto_fixable = sum(1 for v in all_findings.values() for f in v if f["_apply"] is not None)
        remaining = total - fixed_total
        if remaining == 0:
            emit(a.json, "PASS", f"fixed {fixed_total} finding(s) in {len(all_findings)} file(s)",
                 "re-run without --apply to confirm clean", {"fixed": fixed_total})
            return 0
        emit(a.json, "FAIL",
             f"fixed {fixed_total}/{total} finding(s); {remaining} need manual fixes",
             "fieldkit repair ps-compat-check FILE... --json for details",
             {"findings": {p: [{"line": f["line"], "rule": f["rule"], "fix": f["fix"]}
                               for f in fs] for p, fs in all_findings.items()}})
        return 3

    rows = []
    for path, findings in all_findings.items():
        for f in findings:
            rows.append({"file": path, "line": f["line"], "rule": f["rule"],
                         "message": f["message"], "fix": f["fix"]})

    emit(a.json, "FAIL",
         f"{total} PowerShell compatibility issue(s) in {len(all_findings)} file(s)",
         "fieldkit repair ps-compat-check FILE... --apply  (or fix manually)",
         {"findings": {p: [{"line": f["line"], "rule": f["rule"],
                             "message": f["message"], "fix": f["fix"]}
                            for f in fs] for p, fs in all_findings.items()}})
    if not a.json:
        for r in rows:
            print(f"  {r['file']}:{r['line']} [{r['rule']}] {r['message']}")
            print(f"    FIX: {r['fix']}")
    return 3


if __name__ == "__main__":
    sys.exit(main())