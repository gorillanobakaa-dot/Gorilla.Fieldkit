"""PowerShell (.ps1) files with bash written into them (heredoc, `export VAR=`, `$(cmd args)`) or unbalanced
braces/parentheses. The brace count skips comments, here-strings and quoted strings (Kiro's counted `"{0}"`).
When powershell.exe or pwsh is on PATH its own parser is asked too (`--lexical` skips that); a file the real parser
rejects is a finding with the parser's line and message.

    fieldkit repair pwsh-syntax-check PATH... [--lexical] [--json]
Exit: 0 clean, 1 no .ps1 file found, 3 findings.
"""
import argparse
import re
import shutil
import subprocess
from pathlib import Path

from ._common import emit

BASH = [
    (re.compile(r"<<-?\s*['\"]?[A-Za-z_]+['\"]?\s*$", re.M), "bash heredoc (<<EOF) in a PowerShell script"),
    (re.compile(r"(?m)^\s*export\s+[A-Za-z_][A-Za-z0-9_]*="), "bash `export VAR=` in a PowerShell script"),
    (re.compile(r"\$\([a-z][a-z0-9_/-]*\s"), "bash $(cmd args) subshell, not a PowerShell expression"),
]
PARSE = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile($env:FK_PS1,[ref]$t,[ref]$e);"
         "foreach($x in $e){ '{0}|{1}' -f $x.Extent.StartLineNumber,$x.Message }")


def _blank(m):
    return re.sub(r"[^\n]", " ", m.group(0))


def blank_noncode(text, strings=True):
    """Blank here-strings, block and line comments (and, with strings=True, quoted strings), keeping newlines."""
    text = re.sub(r"@'\r?\n.*?\r?\n'@", _blank, text, flags=re.S)
    text = re.sub(r'@"\r?\n.*?\r?\n"@', _blank, text, flags=re.S)
    text = re.sub(r"<#.*?#>", _blank, text, flags=re.S)
    rx = r"""'(?:[^']|'')*'|"(?:[^"`]|`.|"")*\"|#[^\n]*""" if strings else r"#[^\n]*"
    return re.sub(rx, _blank, text)


def check_source(text):
    issues = []
    scan = blank_noncode(text, strings=False)
    for rx, desc in BASH:
        m = rx.search(scan)
        if m:
            issues.append({"line": scan[:m.start()].count("\n") + 1, "rule": desc})
    code = blank_noncode(text, strings=True)
    for o, c, name in (("{", "}", "braces"), ("(", ")", "parentheses")):
        if code.count(o) != code.count(c):
            issues.append({"line": 0, "rule": f"unbalanced {name}: open={code.count(o)} close={code.count(c)}"})
    return issues


def real_parse(path, run=subprocess.run, which=shutil.which):
    """-> list of {line, rule} from the PowerShell parser, [] when clean, None when no PowerShell is installed."""
    exe = which("pwsh") or which("powershell")
    if not exe:
        return None
    import os
    try:
        r = run([exe, "-NoProfile", "-NonInteractive", "-Command", PARSE], capture_output=True, text=True, timeout=60,
                env={**os.environ, "FK_PS1": str(path)})
    except (OSError, subprocess.SubprocessError):
        return None
    out = []
    for line in (r.stdout or "").splitlines():
        ln, _, msg = line.partition("|")
        if msg:
            out.append({"line": int(ln) if ln.isdigit() else 0, "rule": "parser: " + msg.strip()})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair pwsh-syntax-check", description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--lexical", action="store_true", help="do not ask powershell.exe/pwsh")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    files = []
    for raw in a.paths:
        p = Path(raw)
        if p.is_dir():
            files += sorted(p.glob("*.ps1"))
        elif p.is_file():
            files.append(p)
    if not files:
        emit(a.json, "FAIL", "no .ps1 files found", "pass a .ps1 file or a folder")
        return 1
    results = []
    for f in files:
        issues = check_source(f.read_text(encoding="utf-8", errors="replace"))
        if not a.lexical:
            issues += real_parse(f) or []
        results.append({"file": f.name, "issues": issues})
        if issues and not a.json:
            for i in issues:
                print(f"{f.name}:{i['line']}: {i['rule']}")
    bad = [r for r in results if r["issues"]]
    if bad:
        emit(a.json, "FAIL", f"{len(bad)}/{len(files)} PowerShell file(s) have syntax problems", "fix the listed lines", {"results": results})
        return 3
    emit(a.json, "PASS", f"{len(files)} PowerShell file(s) clean", "none", {"results": results})
    return 0
