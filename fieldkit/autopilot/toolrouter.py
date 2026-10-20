# Version: 1.1.0 · updated 26-10-19
"""The tool router: a command that does by hand what a harness tool already does is refused, with the tool's own
command in the refusal, before it runs. For every model (Claude Code PreToolUse hook, Antigravity guard.py).

Owner, 2026-10-18 (after a model ran Get-ChildItem -Recurse over AppData for 10 minutes to find vctip.exe): "we have
the pfind and other tools you never bother to use? ... make sure all models are actually aware of these tools and
actually use them". A skill and a memory had said "folder sizes and file searches only via fieldkit size" for a week;
the model typed the hand-rolled scan anyway. An instruction is forgotten; a refusal at the moment of the command is
not. The table is data: one row per tool, each with a test (tests/test_autopilot_toolrouter.py).

    python -m fieldkit.autopilot.toolrouter --hook        (Claude Code PreToolUse: reads the tool call on stdin)
    python -m fieldkit.autopilot.toolrouter "<command>"   (prints the verdict; exit 2 when refused)
"""
import json
import re
import sys

FK = "python -m fieldkit"                       # examples for a model; no personal path is published

#: (name, pattern on the command, the tool to use instead, an example)
ROUTES = [
    ("recursive-file-search",
     re.compile(r"(Get-ChildItem|\bgci\b|\bdir\b|\bls\b)[^|;\n]*\s-(Recurse|r\b|s\b)|\bdir\s+[^|;\n]*/s\b|"
                r"(^|[;&|]\s*)find\s+(/|[A-Za-z]:|~|\$HOME|\.\.?/?\s)[^|;\n]*-(i?name|type|size|mtime)\b|"
                r"\bwhere(\.exe)?\s+/r\b", re.I),
     "fieldkit size find (gorilla-size: every core, read-only, no junction loops)",
     FK + " size find --name vctip.exe --type f --paths C:/"),
    ("folder-size",
     re.compile(r"\bdu\s+-|\bncdu\b|robocopy\s[^|;\n]*/L\b|Measure-Object\s+[^|;\n]*-Property\s+Length|"
                r"\bdir\s+/s\b", re.I),
     "fieldkit size (sizes of folders and drives; --files N for the largest files; --types for bytes by extension)",
     FK + r" size $HOME\Documents -f 20"),
    ("text-search-in-tree",
     re.compile(r"(Get-ChildItem|\bgci\b)[^|\n]*-Recurse[^|\n]*\|\s*(Select-String|sls)\b|"
                r"\bfindstr\s+[^|;\n]*/s\b", re.I),
     "the Grep tool (ripgrep), or fieldkit size find --grep TEXT for files matched by name, size or age",
     FK + " size find --ext py --grep vctip <folder>"),
    # -- 2026-10-19: every hand-rolled habit a Fieldkit tool replaces (worker session, job 2). Each row has a test that
    #    it fires and a test that its look-alike does not (tests/test_autopilot_toolrouter.py).
    ("hash-by-hand",
     re.compile(r"\bGet-FileHash\b|\bcertutil(\.exe)?\s+-hashfile\b|(^|[;&|]\s*)sha(1|256|512)sum\s+(?!-c\b|--check\b)",
                re.I),
     "fieldkit size hash (SHA-256 on the CPU's SHA instructions, files or whole folders, JSON per file)",
     FK + r" size hash C:\gfobj\dist\firefox-158.0.en-US.win64.installer.exe --json"),
    ("permanent-delete",
     # a folder removed for good, outside the session's own scratch or temp folders (2026-10-16: a "recycle" that
     # destroyed 157.0-clean; the owner's rule: never a permanent delete)
     # the exemption looks at the whole line, wherever the path stands relative to the flag
     re.compile(r"^(?![^\n]*(\\Temp\\|/tmp/|\$env:TEMP|\$TEMP\b|scratchpad|AppData\\Local\\Temp|pytest-tmp))"
                r"[^\n]*?(Remove-Item|\bri\b|(?<!git\s)\brm\b|\bdel\b|\brmdir\b|\brd\b)[^|;\n]*(-Recurse\b|\s-rf?\b|\s-fr\b|/s\b)",
                re.I | re.M),
     "fieldkit size recycle (to the Recycle Bin, verified in the bin's records, undo with fieldkit size undo)",
     FK + r" size recycle D:\Build.Work\old-tree --json"),
    ("tree-copy-by-hand",
     re.compile(r"\brobocopy\b(?![^|;\n]*/L\b)|\bxcopy\b[^|;\n]*/[sSeE]\b", re.I),
     "fieldkit size copy / move (every byte compared after, journalled, undo; a plan above 1000 files or 10 GiB)",
     FK + r" size copy C:\src\tree --to D:\backup --json"),
    ("office-by-hand",
     re.compile(r"\b(import\s+(docx|openpyxl|pptx|PyPDF2|pypdf|fitz)\b|from\s+(docx|openpyxl|pptx|pypdf|PyPDF2)\s+import)|"
                r"\bpdftotext\b", re.I),
     "fieldkit office read (Word, Excel, PowerPoint and PDF as text, offline; office check / scrub / render too)",
     FK + r" office read C:\path\report.docx"),
    ("network-diagnosis-by-hand",
     re.compile(r"\bipconfig\s+/all\b|\bnetsh\s+(wlan|interface|int)\s+show\b|\bTest-NetConnection\b|\btracert\b|"
                r"\bGet-NetRoute\b|\bnmcli\s+(dev|device|general)\b", re.I),
     "the nettriage card first (which link carries the internet, the hub, the radio), then fieldkit net / wifi",
     FK + " agent describe nettriage"),
    ("icon-cache-by-hand",
     re.compile(r"\bie4uinit(\.exe)?\s+-(show|ClearIconCache)\b|IconCache\.db|iconcache_\w*\.db", re.I),
     "fieldkit icons cache (rebuilds the icon cache with a backup; the desktop button does the same)",
     FK + " icons cache"),
    ("kill-by-name",
     # stop only the processes you started, by PID (2026-09: a name-based kill closed the owner's own programs)
     re.compile(r"\btaskkill(\.exe)?\b[^|;\n]*/IM\b|\bStop-Process\b[^|;\n]*-Name\b|\b(pkill|killall)\b", re.I),
     "Stop-Process -Id <PID> of a process this session started (find it with Get-CimInstance Win32_Process)",
     "Stop-Process -Id 12345   # the PID your own Start-Process returned"),
    ("download-by-hand",
     re.compile(r"\b(Invoke-WebRequest|iwr|Start-BitsTransfer|bitsadmin)\b|\bwget\s+https?://|"
                r"\bcurl(\.exe)?\s+[^|;\n]*(-[a-zA-Z]*[oO]\b|--output\b)", re.I),
     "a fetcher that names its host and checks the hash: fieldkit evidence fetch (pages), build-harness vault fetch "
     "(sources); every download is a door, say which and why",
     FK + " evidence fetch https://example.org/page"),
    ("registry-edit-by-hand",
     re.compile(r"\breg(\.exe)?\s+(add|delete|import)\b|\b(Set|New|Remove)-ItemProperty\b[^|;\n]*HK(LM|CU)", re.I),
     "a Windows 11 tweaks module (backup, preview, apply, undo): fieldkit win11 run MODULE preview|apply|undo",
     FK + " win11 run copilot preview"),
    ("sleep-to-wait",
     re.compile(r"\bStart-Sleep\s+(-s(econds)?\s+)?([1-9]\d|\d{3,})\b|(^|[;&|]\s*)sleep\s+([1-9]\d|\d{3,})\b", re.I),
     "fieldkit wait --until CMD --matches RE --timeout S (wait on the condition, never on a clock)",
     FK + ' wait --until "git log -1 --format=%h" --matches c75f600 --timeout 600'),
]
#: a refusal can be overridden only by saying why in the command itself, so the reason is on record
OVERRIDE = re.compile(r"#\s*toolrouter-ok:\s*\S", re.I)


HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)


#: Python or PowerShell code fed through a shell heredoc that contains backslashes: the Bash tool's quoting turns
#: '\\\\' into '\\' and '\\n' into a newline inside the code (2026-10-18: about eight broken edits in one night, NUL
#: bytes, a form feed in a release page, regexes that matched nothing; a memory had warned for a week)
CODE_HEREDOC = re.compile(r"\b(python[0-9.]*|py)(\.exe)?\s+-\s*<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)^\s*\3\s*$", re.S | re.M)


def route(command):
    """-> None (allowed) or {"route", "use", "example"} for the first route the command matches. A heredoc's body is
    data written to a file or a program, not a command (2026-10-18: appending the rule that NAMES these commands to
    GEMINI.md was refused), so it is not judged - except Python code with backslashes, which the shell mangles."""
    m = CODE_HEREDOC.search(command or "")
    if m and "\\" in m.group(4) and not OVERRIDE.search(command or ""):
        return {"route": "code-with-backslashes-in-a-heredoc",
                "use": "the Write tool for the script (it keeps every backslash), then run the file",
                "example": "Write the script to the scratchpad, then: python <scratchpad>/fix.py"}
    cmd = HEREDOC.sub("<<heredoc", command or "")
    if OVERRIDE.search(cmd):
        return None
    for name, pat, use, example in ROUTES:
        if pat.search(cmd):
            return {"route": name, "use": use, "example": example}
    return None


def message(r):
    return (f"TOOL ROUTER: this does by hand what a harness tool does ({r['route']}). Use {r['use']}, for example:\n"
            f"  {r['example']}\n"
            "If the tool truly cannot do it, add '# toolrouter-ok: <why>' to the command (the reason is logged).")


def hook(stdin=None):
    """Claude Code PreToolUse: Bash and PowerShell commands only; fails open on a malformed call (the hard lines
    are the safety layer; this one teaches)."""
    try:
        call = json.loads((stdin or sys.stdin).read() or "{}")
    except ValueError:
        return 0
    tool = (call.get("tool_name") or "").lower()
    if tool not in ("bash", "powershell"):
        return 0
    r = route((call.get("tool_input") or {}).get("command", ""))
    if r:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": message(r)}}))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--hook"]:
        return hook()
    r = route(" ".join(argv))
    print(message(r) if r else "allowed")
    return 2 if r else 0


if __name__ == "__main__":
    sys.exit(main())
