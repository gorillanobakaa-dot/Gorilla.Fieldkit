# Version: 1.0.0 · updated 26-10-18-19-00
"""Commands for the owner to paste are checked before the owner sees them.

Owner, 2026-10-18: "you were always supposed to give me the whole path ... it was supposed to be a deterministic
rule/check ... so no model ignores it or forgets". The rule (memory: owner commands in PowerShell syntax) was text
only, and a model gave `& "$env:APPDATA\\...\\claude.exe"` without `cd $HOME;` first, then "type /login" without
saying where. Every ```powershell / ```ps1 / ```pwsh block in an answer must:
  1. start with `cd` to a full path ($HOME, $env:..., or a drive path);
  2. call every program by its full path, except the commands every machine of the owner has on PATH (PATH_OK);
  3. use PowerShell syntax only: no `&&`/`||` chains, no `export X=`, no `$VAR` that is not `$env:` or a
     PowerShell variable, no `~/`, no backslash line continuations, no heredocs.
A Claude Code Stop hook (stop_hook) sends a failing answer back to the model to fix.

    python -m fieldkit.autopilot.ownercmd FILE      check a text file; exit 3 with the problems
"""
import json
import re
import sys

PATH_OK = {"python", "py", "git", "gh", "fieldkit", "winget", "pip", "node", "npm", "code", "notepad", "explorer",
           "powershell", "pwsh", "cmd", "wsl", "ssh", "scp", "curl.exe", "tar", "certutil", "sccache"}
FENCE = re.compile(r"```(powershell|ps1|pwsh|ps)\s*\n(.*?)```", re.S | re.I)
CD = re.compile(r"^\s*(cd|set-location|push-location|sl)\s+(\"?)(\$home|\$env:[a-z_]+|[a-z]:[\\/]|\\\\)", re.I)


def _statements(block):
    out = []
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out += [s.strip() for s in line.split(";") if s.strip()]
    return out


def _program(stmt):
    """The program a statement starts, or None for a cmdlet, assignment, keyword or a call through & of a path."""
    s = stmt.strip()
    if s.startswith("&"):
        return ("&", s[1:].strip())
    first = re.split(r"\s+", s, maxsplit=1)[0].strip("\"'")
    if not first or first.startswith(("$", "(", "[", "@", "{", "-")) or re.match(r"^[a-z]+-[a-z]+$", first, re.I):
        return None                                       # variable, expression, Verb-Noun cmdlet
    if first.lower() in {"cd", "sl", "set-location", "push-location", "pop-location", "if", "foreach", "for",
                         "while", "try", "catch", "return", "exit", "echo", "write-host", "start-sleep", "dir",
                         "ls", "cls", "mkdir", "copy", "move", "del", "type", "ren"}:
        return None
    return ("name", first)


def _absolute(path):
    p = path.strip().strip("\"'")
    return bool(re.match(r"^(\$home|\$env:[a-z_]+|[a-z]:[\\/]|\\\\|\.[\\/])", p, re.I))


def check_block(block):
    problems = []
    stmts = _statements(block)
    if not stmts:
        return problems
    if not CD.match(stmts[0]):
        problems.append("does not start with `cd` to a full path (cd $HOME; ... or cd C:\\...)")
    for s in stmts:
        if re.match(r"(export|set)\s+\w+=|source\s|\.\s+\S+\.sh\b", s, re.I):
            problems.append(f"`{s[:30]}`: bash syntax (export / source); PowerShell sets $env:NAME = 'value'")
            continue
        prog = _program(s)
        if prog and prog[0] == "&":
            target = re.match(r"\s*(\"[^\"]+\"|'[^']+'|\S+)", prog[1])
            if target and not _absolute(target.group(1)):
                problems.append(f"`& {target.group(1)}`: not a full path")
        elif prog and prog[0] == "name":
            name = prog[1]
            base = re.split(r"[\\/]", name)[-1].lower()
            if not _absolute(name) and base not in PATH_OK and base.removesuffix(".exe") not in PATH_OK:
                problems.append(f"`{name}`: call it by its full path (or it must be one of {sorted(PATH_OK)[:6]}...)")
    if re.search(r"&&|\|\|", block):
        problems.append("`&&` / `||` chains are bash/cmd, not Windows PowerShell 5.1: use `;` or separate lines")
    if re.search(r"^\s*export\s+\w+=|<<\s*['\"]?\w+|\\\s*$|(^|\s)~/", block, re.M):
        problems.append("bash syntax (export, heredoc, \\ continuation or ~/)")
    for v in re.findall(r"(?<![\w`])\$([A-Z][A-Z0-9_]{2,})\b", block):
        if v.upper() not in ("HOME", "PWD", "NULL", "TRUE", "FALSE", "ERROR", "LASTEXITCODE", "PSSCRIPTROOT"):
            problems.append(f"`${v}` looks like a bash variable: use $env:{v}")
            break
    return problems


def check(text):
    """-> [(block number, problem)] for every PowerShell block in an answer."""
    out = []
    for i, m in enumerate(FENCE.finditer(text), 1):
        out += [(i, p) for p in check_block(m.group(2))]
    return out


def _last_assistant_text(transcript_path):
    last = ""
    with open(transcript_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            msg = e.get("message") or {}
            if e.get("type") == "assistant" or msg.get("role") == "assistant":
                content = msg.get("content")
                if isinstance(content, str):
                    last = content
                elif isinstance(content, list):
                    t = "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
                    if t:
                        last = t
    return last


def stop_hook(stdin=sys.stdin, stdout=sys.stdout):
    """Claude Code Stop hook: an answer whose owner commands break the rule is sent back to be fixed (once)."""
    try:
        payload = json.load(stdin)
        if payload.get("stop_hook_active"):
            return 0                                      # already sent back once: never loop
        problems = check(_last_assistant_text(payload["transcript_path"]))
    except Exception:  # noqa: BLE001 - a broken check never blocks an answer
        return 0
    if problems:
        why = "; ".join(f"PowerShell block {n}: {p}" for n, p in problems[:6])
        stdout.write(json.dumps({"decision": "block", "reason":
            "Owner commands must be pasteable as they are (owner rule, checked by fieldkit.autopilot.ownercmd): " + why +
            ". Rewrite those blocks: cd to a full path first, full paths to programs, PowerShell syntax only."}))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--stop-hook":
        sys.exit(stop_hook())
    found = check(open(sys.argv[1], encoding="utf-8").read())
    for n, p in found:
        print(f"block {n}: {p}")
    sys.exit(3 if found else 0)
