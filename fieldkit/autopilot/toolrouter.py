# Version: 1.8.0 · updated 26-10-28-20-30
"""The tool router: a command that does by hand what a harness tool already does is refused, with the tool's own
command in the refusal, before it runs. For every model (Claude Code PreToolUse hook, Antigravity guard.py).

Owner, 2026-10-18 (after a model ran Get-ChildItem -Recurse over AppData for 10 minutes to find vctip.exe): "we have
the pfind and other tools you never bother to use? ... make sure all models are actually aware of these tools and
actually use them". A skill and a memory had said "folder sizes and file searches only via fieldkit size" for a week;
the model typed the hand-rolled scan anyway. An instruction is forgotten; a refusal at the moment of the command is
not. The table is data: one row per tool, each with a test (tests/test_autopilot_toolrouter.py).

    python -m fieldkit.autopilot.toolrouter --hook        (Claude Code PreToolUse: reads the tool call on stdin;
                                                           an allowed call also gets the room's part, room.pretool)
    python -m fieldkit.autopilot.toolrouter "<command>"   (prints the verdict; exit 2 when refused)
"""
import json
import re
import sys

FK = "python -m fieldkit"                       # examples for a model; no personal path is published

#: (name, pattern on the command, the tool to use instead, an example)
ROUTES = [
    ("recursive-file-search",
     # ls: only -R or --recursive recurses (R5, 2026-10-28: `ls -r` sorts in reverse, `ls -s` shows sizes and
     # `git ls-files -s` lists the index; all three were refused). In PowerShell ls is Get-ChildItem: PS_LS below.
     re.compile(r"(Get-ChildItem|\bgci\b|(?<![\w-])dir(?![\w-]))[^|;\n]*\s-(Recurse|r\b|s\b)|"
                r"(?<![\w-])ls(?![\w-])[^|;\n]*\s(-(?-i:[a-zA-Z]*R[a-zA-Z]*)\b|--recursive\b)|\bdir\s+[^|;\n]*/s\b|"
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
    # -- 2026-10-25: the hand steps of the Gorilla OpenCode v0.1.152 release from Windows, each now a tool
    ("release-sums-by-hand",
     # before hash-by-hand: `sha256sum ... > SHA256SUMS` is the hand-typed checksum file, not a one-off hash
     re.compile(r">>?\s*['\"]?[^|;\n\s]*SHA256SUMS", re.I),
     "fieldkit pipeline run gorilla-opencode (its checksums stage lists exactly the published files and writes the "
     "page's table, measured); for another folder: fieldkit release table SHA256SUMS --dir DIR",
     FK + " release table SHA256SUMS-v0.1.152.txt --dir dist"),
    ("packages-by-hand",
     re.compile(r"\bnfpm\s+(package|pkg)\b|\bdpkg-deb\s+(-b|--build)\b|\brpmbuild\s+-b[abps]?\b", re.I),
     "fieldkit pipeline run gorilla-opencode (packages stage: the project's scripts on Linux, nfpm on Windows, the "
     ".deb compared with the last release's, the tarball checked by its bytes)",
     FK + " pipeline run gorilla-opencode --var version=v0.1.153"),
    ("release-check-by-hand",
     re.compile(r"\bgh\s+release\s+download\b|\bgh\s+workflow\s+run\s+linux-install\b", re.I),
     "fieldkit release verify TAG --repo OWNER/NAME (into a new empty folder: every asset against SHA256SUMS and "
     "GitHub's digest, the page's table, then linux-install.yml run and its jobs read)",
     FK + " release verify v0.1.152 --repo gorillanobakaa-dot/Gorilla.Opencode"),
    ("lmstudio-log-by-hand",
     re.compile(r"\b(grep|rg|cat|tail|head|less|type|Get-Content|gc|Select-String|sls)\b[^|;\n]*lm-studio[\\/]+server-logs",
                re.I),
     "fieldkit lmstudio-log (per request: prompt tokens, prefill and generation tok/s, n_tokens; Vulkan device lost "
     "with the fix)",
     FK + " lmstudio-log --day 2026-10-25 --last 10"),
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
     FK + r" size recycle D:\Build.Work\old-tree --whole-folder --json"),
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
    # -- 2026-10-28 (O4): before sleep-to-wait, so a CI loop gets the CI tool. 103 hand-written wait loops and 210 gh
    #    status polls in 41 VAIO transcripts in one week; gh's own exit code merged #124 during a re-run
    ("ci-wait-by-hand",
     re.compile(r"\bgh\s+pr\s+checks\b[^|;\n]*--watch\b|\bgh\s+run\s+watch\b|"
                r"\b(until|while)\b[^\n]*\bgh\s+(pr\s+(checks|view)|run\s+(list|view))\b", re.I),
     "fieldkit ship ci-wait PR [--merge], in the background: one wake when CI ends, the train's verdict (every check "
     "and workflow run of the head), the failed log tail on red",
     FK + " ship ci-wait 166 --merge"),
    ("sleep-to-wait",
     re.compile(r"\bStart-Sleep\s+(-s(econds)?\s+)?([1-9]\d|\d{3,})\b|(^|[;&|]\s*|\bdo\s+|\bthen\s+)sleep\s+([1-9]\d|\d{3,})\b", re.I),
     "fieldkit wait --until CMD --matches RE --timeout S (wait on the condition, never on a clock)",
     FK + ' wait --until "git log -1 --format=%h" --matches c75f600 --timeout 600'),
]
#: PowerShell: ls is Get-ChildItem, so -r and -s are -Recurse there (the bash row above lets them through)
PS_LS = re.compile(r"(?<![\w-])ls(?![\w-])[^|;\n]*\s-(r|s)\b", re.I)
#: rows whose habit IS a search command: a search statement is judged for them (R5 fix 4 exempts it for the others)
SEARCH_ROWS = {"lmstudio-log-by-hand", "text-search-in-tree"}
#: rows judged on the raw command line: the delete exemption needs the quoted path
RAW_ROWS = {"permanent-delete"}
#: a script handed to a shell or an interpreter (`grep -c "x" f` is a count, not a script)
INNER = re.compile(r"\b(?:(?:ba|z|da)?sh|pwsh|powershell|python[\d.]*|py|cmd)(?:\.exe)?(?![\w.-])[\"']?[^|;&\n]*?"
                   r"\s(?:-c|-Command|/c)\s+"
                   r"([\"'])(.*?)\1", re.S | re.I)
SLEEP = re.compile(r"^\s*(Start-Sleep\s+(-s(econds)?\s+)?|sleep\s+)([1-9]\d|\d{3,})\b", re.I)
#: a refusal can be overridden only by saying why in the command itself, so the reason is on record
OVERRIDE = re.compile(r"#\s*toolrouter-ok:\s*\S", re.I)

# -- 2026-10-26 (owner: "fa permanent"): a session pushes a repository the owner-wide mandate covers ONLY through
#    fieldkit ship push (the gate, the log, fast-forward only), never by a hand-typed git push. The owner's own manual
#    path (GORILLA_OWNER_APPROVED in the owner's PowerShell) never passes through this router and stays the owner's.
GIT_PUSH = re.compile(r"(^|[;&|(]\s*|\bthen\s+|\bdo\s+)(?:[\w]+=\S*\s+)*(?:[\"'][^\"']*[\\/]|[^\s\"';&|]*[\\/])?"
                      r"git(\.exe)?[\"']?"
                      r"((?:\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+)|\s+-c\s+\S+|\s+--[\w-]+(=\S+)?)*)\s+push\b", re.I | re.M)
CD = re.compile(r"(?:^|[;&|]\s*)(?:cd|Set-Location|pushd)\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)", re.I | re.M)
SHIP_PUSH = {"route": "git-push-by-hand",
             "use": "fieldkit ship push (under the owner-wide mandate: the pre-push gate, the log, fast-forward only; "
                    "push --tag vX for a new tag, ship gh-release vX for a release). GORILLA_OWNER_APPROVED stays the "
                    "owner's own manual path, typed by the owner, never by a session",
             "example": FK + " ship push --repo <the clone>"}


def _unquote(s):
    s = s[1:-1] if s[:1] in "\"'" and s[-1:] == s[:1] else s
    # Git Bash on Windows: /c/Users/... is C:/Users/... (Python on Windows reads /c/ as a folder named c)
    m = re.match(r"^/([A-Za-z])(/|$)", s)
    import os
    return f"{m.group(1).upper()}:/{s[3:]}" if m and os.name == "nt" else s


def covered_names():
    """The repository names the mandates on this computer cover (the packaged owner-wide.yaml when none is here)."""
    try:
        from ..mandate import upkeep
        return set(upkeep.covered_repos())
    except Exception:  # noqa: BLE001 - no mandate code: nothing is known to be covered
        return set()


def push_repo(command, cwd=""):
    """-> the folder a hand-typed `git push` in the command pushes from, or None when there is no git push."""
    import os
    m = GIT_PUSH.search(command or "")
    if not m:
        return None
    dash_c = re.findall(r"-C\s+(\"[^\"]+\"|'[^']+'|\S+)", m.group(3) or "")
    cds = [x for x in CD.finditer(command) if x.start() < m.start()]
    where = cwd or os.getcwd()
    if cds:
        where = os.path.join(where, os.path.expanduser(_unquote(cds[-1].group(1))))
    for c in dash_c:
        where = os.path.join(where, os.path.expanduser(_unquote(c)))
    return where


def push_refusal(command, cwd="", names=None):
    """-> the SHIP_PUSH route when the command hand-types a git push from a clone a mandate covers, else None."""
    where = push_repo(command, cwd)
    if where is None:
        return None
    try:
        from ..mandate import proofs, upkeep
        if not upkeep.git_here()[0]:                      # never run macOS's /usr/bin/git stub (a dialog)
            return None
        name = proofs.where(where)[0]
    except Exception:  # noqa: BLE001 - not a clone (or no git): nothing to say
        return None
    return SHIP_PUSH if name and name in (covered_names() if names is None else names) else None


# -- 2026-10-27 (CRASH ~04:14: the laptop lost power below Windows while a session ran a full pre-commit suite AND a
#    mutate at once, both outside the guard, on top of goal workers compiling). Owner, verbatim: "guard obligatoriu prin
#    tool router". On Windows a heavy command runs ONLY under `fieldkit guard --` (one machine-wide slot, the preflight,
#    the fan check, the governor that brakes at thermal.build_target), or on the other computer. `fieldkit admit` alone
#    queues but does not brake, so it is not enough. A commit in a Fieldkit checkout runs the full suite in its hook.
_AT = (r"(?:^|[;&|(]\s*|\s--\s+|\b(?:then|do|time|nice|nohup|exec|env)\s+|\btimeout\s+\d+\S*\s+|\s-c\s+[\"']\s*)"
       r"(?:\w+=\S*\s+)*(?:[\"']?[^\s\"';&|]*[\\/])?")
_PY = r"(?:python[\d.]*|py|\"?\$\{?PY\}?\"?)(?:\.exe)?[\"']?(?:\s+-[^m\s]\S*)*\s+-m\s+"
HEAVY = [
    ("mutate", re.compile(_AT + r"(?:fieldkit(?:\.exe)?[\"']?|" + _PY + r"fieldkit)\s+mutate\b", re.I | re.M)),
    ("admit-suite", re.compile(_AT + r"(?:fieldkit(?:\.exe)?[\"']?|" + _PY + r"fieldkit)\s+admit\s+suite\b", re.I | re.M)),
    ("cargo", re.compile(_AT + r"cargo(?:\.exe)?[\"']?(?:\s+\+\S+)?\s+(?:build|test|check|clippy|run|install|bench|doc)\b",
                         re.I | re.M)),
    ("go", re.compile(_AT + r"go(?:\.exe)?[\"']?\s+(?:build|test|install|vet)\b", re.I | re.M)),
    ("js-build", re.compile(_AT + r"(?:(?:npm|pnpm|yarn)(?:\.cmd)?\s+(?:run\s+)?build|(?:npx\s+)?vite\s+build)\b", re.I | re.M)),
    ("native-build", re.compile(_AT + r"(?:make|mingw32-make|nmake|msbuild|ninja)(?:\.exe)?\b|\bcmake\s+--build\b|"
                                      r"\bmach(?:\.py)?\s+build\b", re.I | re.M)),
]
PYTEST = re.compile(_AT + r"(?:pytest(?:\.exe)?[\"']?|" + _PY + r"pytest)([^;&|\n]*)", re.I | re.M)
#: pytest flags that take a value (the value is not a test path)
_PYTEST_VAL = {"-k", "-m", "-p", "-o", "-c", "-W", "--basetemp", "--rootdir", "--deselect", "--ignore", "--tb",
               "--timeout", "--maxfail", "--durations", "-r", "--junitxml", "--confcutdir"}
GUARDED = re.compile(r"(?:\bfieldkit(?:\.exe)?[\"']?|-m\s+fieldkit)\s+guard\b|\bfieldkit\.thermal\.buildguard\b", re.I)
#: the work runs on the other computer (the VAIO: thermald, a desktop-class cooler), not on this laptop
REMOTE = re.compile(r"(?:^|[;&|]\s*)ssh(?:\.exe)?\s|\bfieldkit(?:\.exe)?\s+(?:task\s+start|peers\s+link\s+(?:run|send|ask))\b|"
                    r"\s--on\s+(?!this\b|local\b)\S", re.I | re.M)
GIT_COMMIT = re.compile(r"(^|[;&|(]\s*|\bthen\s+|\bdo\s+)(?:\w+=\S*\s+)*(?:[\"']?[^\s\"';&|]*[\\/])?git(\.exe)?[\"']?"
                        r"((?:\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+)|\s+-c\s+\S+|\s+--[\w-]+(=\S+)?)*)\s+commit\b", re.I | re.M)
SEARCH = re.compile(r"^\s*(?:[^\s\"';&|]*[\\/])?(?:grep|egrep|fgrep|rg|ag|findstr|select-string|git\s+(?:-C\s+\S+\s+)?grep)"
                    r"(?:\.exe)?\b(?![^\n]*(?:\s-c\s|\$\(|`|\s--exec\b|\s-x\b))", re.I)
HEAVY_GUARD = {"route": "heavy-work-outside-the-guard",
               "use": "fieldkit guard -- <the command> (one machine-wide slot, the preflight, the fan check and the "
                      "governor that brakes at thermal.build_target), or run it on the VAIO (fieldkit task start BRIEF "
                      "--on peer). Never two heavy jobs at once on this laptop (crash 2026-10-27 04:14)",
               "example": FK + " guard -- python -m pytest -q tests"}


def _pytest_heavy(args):
    """-> True when a pytest call runs a whole suite or many files (more than 3 test files, a folder, or none)."""
    toks, paths, skip = args.split(), [], False
    for t in toks:
        if skip:
            skip = False
            continue
        if t.startswith("-"):
            if t == "-n" or t.startswith("-n=") or t.startswith("--numprocesses"):
                return True                               # xdist: every core
            skip = t in _PYTEST_VAL
            continue
        if t.startswith((">", "2>", "<")):
            break
        paths.append(t.strip("\"'"))
    if not paths:
        return True
    files = {p.split("::")[0] for p in paths}
    return any(not f.lower().endswith(".py") for f in files) or len(files) > 3


def _is_fieldkit_checkout(where):
    import os
    d = os.path.abspath(where)
    while True:
        if os.path.isfile(os.path.join(d, "hooks", "pre-commit")) and os.path.isdir(os.path.join(d, "fieldkit")):
            return True
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up


def heavy_refusal(command, cwd="", platform=None):
    """-> HEAVY_GUARD when, on Windows, the command runs heavy work outside `fieldkit guard` and not on the other
    computer; else None. Heavy: a pytest suite (no path, a folder, more than 3 files, -n), fieldkit mutate, fieldkit
    admit suite, cargo/go builds and tests, npm/vite builds, make/msbuild/ninja/cmake --build/mach build, and a git
    commit in a Fieldkit checkout (its hook runs the full suite)."""
    import os
    platform = sys.platform if platform is None else platform
    if platform != "win32" or not command or GUARDED.search(command) or REMOTE.search(command):
        return None
    # quoted text is data (`grep -n "a\|go test" f` was refused 2026-10-27), except a shell's -c / -Command script,
    # which is judged as a command of its own
    inner = [m.group(2) for m in re.finditer(r"\s-(?:c|Command)\s+([\"'])(.*?)\1", command, re.S)]
    raw, command = command, "\n".join([re.sub(r"\"[^\"\n]*\"|'[^'\n]*'", '""', command)] + inner)
    # 2026-10-27 (#108 fix 5): the words of a search are its pattern, not a command (`grep -n -- pytest f`); a
    # search statement is dropped whole, the statements around it are still judged (`grep x f; pytest` is heavy)
    command = "".join("" if SEARCH.match(p) else p for p in re.split(r"(;|&&|\|\||\||\n)", command))
    if any(p.search(command) for _, p in HEAVY):
        return HEAVY_GUARD
    if any(_pytest_heavy(m.group(1)) for m in PYTEST.finditer(command)):
        return HEAVY_GUARD
    m = GIT_COMMIT.search(raw) if GIT_COMMIT.search(command) else None      # the paths come from the raw command
    if m:
        dash_c = re.findall(r"-C\s+(\"[^\"]+\"|'[^']+'|\S+)", m.group(3) or "")
        cds = [x for x in CD.finditer(raw) if x.start() < m.start()]
        where = cwd or os.getcwd()
        if cds:
            where = os.path.join(where, os.path.expanduser(_unquote(cds[-1].group(1))))
        for c in dash_c:
            where = os.path.join(where, os.path.expanduser(_unquote(c)))
        no_hook = re.search(r"\s(--no-verify|-n)\b", raw[m.end():].split("\n")[0])   # no hook = no suite
        if _is_fieldkit_checkout(where) and not no_hook:
            return HEAVY_GUARD
    return None


HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)


#: Python or PowerShell code fed through a shell heredoc that contains backslashes: the Bash tool's quoting turns
#: '\\\\' into '\\' and '\\n' into a newline inside the code (2026-10-18: about eight broken edits in one night, NUL
#: bytes, a form feed in a release page, regexes that matched nothing; a memory had warned for a week)
#: R5 (2026-10-28, 247 refusals): with a quoted delimiter (<<'EOF') bash passes the body byte for byte; unquoted, it
#: rewrites only a double backslash, backslash-dollar, backslash-backtick and backslash-newline. Only that is refused.
CODE_HEREDOC = re.compile(r"\b(python[0-9.]*|py)(\.exe)?\s+-\s*<<-?\s*(['\"]?)(\w+)\3[^\n]*\n(.*?)^\s*\4\s*$", re.S | re.M)
SHELL_ESCAPE = re.compile(r"\\[\\$`\n]")


def _statements(cmd):
    """-> [(start, end, text)] of the statements between ; && || | and newlines."""
    out, pos = [], 0
    for m in re.finditer(r";|&&|\|\||\||\n", cmd):
        out.append((pos, m.start(), cmd[pos:m.start()]))
        pos = m.end()
    return out + [(pos, len(cmd), cmd[pos:])]


def _hit(name, pat, cmd):
    """R5 fix 4 (2026-10-28): a row's match counts only when its first word stands outside a quoted string and outside
    a search statement (`git commit -m "no robocopy"`, `grep -n Get-FileHash f` were refused). A shell's -c / -Command
    script is judged as a command of its own. The delete row reads the raw line (its exemption needs the quoted path)."""
    if name in RAW_ROWS:
        return bool(pat.search(cmd))
    dead = [m.span() for m in re.finditer(r"\"[^\"\n]*\"|'[^'\n]*'", cmd)]
    searches = [(a, b) for a, b, t in _statements(cmd) if SEARCH.match(t)]
    if name not in SEARCH_ROWS:
        dead += searches
    for m in pat.finditer(cmd):
        first = m.start() + len(m.group(0)) - len(m.group(0).lstrip(" \t;&|"))
        if not any(a <= first < b for a, b in dead):
            return True
    for m in INNER.finditer(cmd):
        if _hit(name, pat, m.group(2)):
            return True
    return False


def _sampling(cmd):
    """R5 fix 2: every long sleep has a real statement before AND after it, outside a loop: a sampling interval
    (measure, wait, measure again), not a wait for something to finish."""
    if re.search(r"(^|[;&|\s])(while|until|for|do|foreach)\b", cmd, re.I):
        return False
    parts = [t.strip() for _, _, t in _statements(cmd)]
    for i, t in enumerate(parts):
        if SLEEP.match(t):
            real = [x for x in parts if x and not SLEEP.match(x)]
            if not ([x for x in parts[:i] if x in real] and [x for x in parts[i + 1:] if x in real]):
                return False
    return True


def route(command, cwd="", shell=""):
    """-> None (allowed) or {"route", "use", "example"} for the first route the command matches. A heredoc's body is
    data written to a file or a program, not a command (2026-10-18: appending the rule that NAMES these commands to
    GEMINI.md was refused), so it is not judged - except Python code the shell would mangle (an unquoted delimiter and
    a backslash the shell rewrites). `cwd`: the folder the command runs in (the hook call's cwd), for the git-push row.
    `shell`: the hook's tool name ("powershell": ls is Get-ChildItem)."""
    m = CODE_HEREDOC.search(command or "")
    if m and not m.group(3) and SHELL_ESCAPE.search(m.group(5)) and not OVERRIDE.search(command or ""):
        return {"route": "code-with-backslashes-in-a-heredoc",
                "use": "a quoted delimiter (<<'EOF': the body reaches Python byte for byte), or the Write tool for "
                       "the script, then run the file",
                "example": "python - <<'EOF'"}
    cmd = HEREDOC.sub("<<heredoc", command or "")
    if OVERRIDE.search(cmd):
        return None
    for name, pat, use, example in ROUTES:
        hit = _hit(name, pat, cmd) or (name == "recursive-file-search" and shell.lower() == "powershell"
                                       and _hit(name, PS_LS, cmd))
        if hit and not (name == "sleep-to-wait" and _sampling(cmd)):
            return {"route": name, "use": use, "example": example}
    return heavy_refusal(cmd, cwd) or push_refusal(cmd, cwd)


def message(r):
    return (f"TOOL ROUTER: this does by hand what a harness tool does ({r['route']}). Use {r['use']}, for example:\n"
            f"  {r['example']}\n"
            "If the tool truly cannot do it, add '# toolrouter-ok: <why>' to the command (the reason is logged).")


def _claims(call):
    """The agent net's ground claims ride on this hook (2026-10-26, fieldkit/zero/claims.py): a write on ground
    another station holds, or outside the station's ordered area, is refused. -> the deny JSON or None; never raises."""
    try:
        from ..zero import claims
        return claims.pretool(call)
    except Exception:  # noqa: BLE001 - fail open
        return None


def _checkout(call):
    """One writer per primary checkout (fieldkit/zero/checkout_guard.py): only Zero changes the main checkout. Fails open."""
    try:
        from ..zero import checkout_guard
        return checkout_guard.pretool(call)
    except Exception:  # noqa: BLE001
        return None


def _rotate_stop(call):
    """Past the ROTATE line a session may only hand over (fieldkit/tokens/rotate_stop.py). Fails open."""
    try:
        from ..tokens import rotate_stop
        return rotate_stop.stop_refusal(call)
    except Exception:  # noqa: BLE001
        return None


def _breaker(call):
    """Circuit breakers (fieldkit/autopilot/breaker.py): the same call LIMIT times in a row is refused. Fails open."""
    try:
        from . import breaker
        return breaker.repeat_refusal(call)
    except Exception:  # noqa: BLE001
        return None


def _retire(call):
    """Zero retires only into a new Zero (fieldkit/zero/retire.py): archive_session on Zero's session is refused until
    the seat was handed over to an UP station and HANDOFF.md was written. Fails open."""
    try:
        from ..zero import retire
        return retire.pretool(call)
    except Exception:  # noqa: BLE001
        return None


def _deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def verdict(call):
    """-> the deny JSON for a tool call, or None: ground claims, the primary checkout, the rotation stop, the circuit breaker, then (Bash and
    PowerShell) the route table."""
    deny = _claims(call) or _checkout(call)
    if deny:
        return deny
    stop = _retire(call) or _rotate_stop(call) or _breaker(call)
    if stop:
        return _deny(stop)
    tool = (call.get("tool_name") or "").lower()
    if tool not in ("bash", "powershell"):
        return None
    r = route((call.get("tool_input") or {}).get("command", ""), call.get("cwd") or "", tool)
    return _deny(message(r)) if r else None


def _room(call):
    """The shared room, the hog's registry, the net lease and the rotation line, for an allowed call, in this process
    (C6, 2026-10-28: they were a second Python start per tool call, PostToolUse). Fails open."""
    try:
        from . import room
        return room.pretool(call)
    except Exception:  # noqa: BLE001
        return ""


def hook(stdin=None):
    """Claude Code PreToolUse, the one hook process per tool call: Bash and PowerShell commands, and (ground claims
    only) Edit / Write / MultiEdit / NotebookEdit; an allowed call then gets the room's part (room.pretool) as added
    context. Fails open on a malformed call (the hard lines are the safety layer; this one teaches)."""
    try:
        call = json.loads((stdin or sys.stdin).read() or "{}")
    except ValueError:
        return 0
    deny = verdict(call)
    if deny:
        print(json.dumps(deny))
        return 0
    ctx = _room(call)
    if ctx:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx}}))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--hook"]:
        from ..core import nowindow
        nowindow.prepare()                 # pythonw: a real stdout for the deny, no console for the children
        return hook()
    r = route(" ".join(argv))
    print(message(r) if r else "allowed")
    return 2 if r else 0


if __name__ == "__main__":
    sys.exit(main())
