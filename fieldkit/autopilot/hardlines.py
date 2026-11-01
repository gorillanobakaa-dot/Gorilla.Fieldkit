# Version: 1.8.0 · updated 26-10-28-18-23
"""The hard lines of an unattended (autopilot) run: the few things no model may do on its own, whatever it says.

Owner, 2026-10-18, after 40 hours of pressing Yes: safety comes from the harness, not from approval prompts. In an
autopilot run everything is allowed except these, enforced by a hook in BOTH Claude Code and Antigravity, from this
one file:

  1. pushing or publishing          git push, gh release / pr / repo / api writes, package uploads
  2. deleting outside scratch       recursive deletes, rmtree, format - anywhere but the scratch roots
  3. changing a protection          Gorilla.firefox harness/, guards, hooks, rules files (GEMINI.md, CLAUDE.md,
                                    AGENTS.md), settings, leak-gate allowlists and dispositions, the decision register
                                    the project mandate store (~/.fieldkit/mandate: keys, trusted keys, mandates,
                                    seals, stops, the log), and FIELDKIT_MANDATE_HOME (a trust store of one's own)
  4. owner approvals                leak-gate approve/dispositions, record_decisions.py, GORILLA_OWNER_APPROVED,
                                    fieldkit mandate keygen / trust / approve / renew / install-hooks --yes
  5. credential files               read or write: .env, keys, gh hosts.yml, .git-credentials, tokens
  and: the owner's STOP file (fieldkit autopilot stop) denies everything that is not a read; installing or booting
  a kernel (linux-image, grub, initramfs, kexec, reboot) is denied whatever a mandate says (2026-10-25).

The owner-wide mandate (2026-10-25) adds two plain commands to hard line 1: `fieldkit ship push --tag vX` (a NEW tag
matching the mandate's tags:) and `fieldkit ship gh-release vX -- ...` (a release of a tag on origin, CI green).

A project mandate in force (fieldkit/mandate, M3, 2026-10-23) opens two of these and nothing else, each logged in
the mandate's log: hard line 1 for `fieldkit ship push` on a branch the mandate lets sessions push (fast-forward,
checked here) and `fieldkit ship merge` (which proves the merge's conditions itself and refuses without them); hard
line 4 for an owner approval listed in the mandate's owner_approvals; and its release door (release: {cycle: [..]})
opens hard line 1 for `fieldkit release-cycle PROJECT run --var version=vX.Y.Z` of a project it lists. Only as ONE plain statement: no `;`, `&&`,
pipe or substitution around it, so what was checked is what runs. `git push` stays denied: the tool is the door.
`fieldkit mac run NAME -- ...` on a Mac the mandate grants is judged as the Mac's business (hard lines 2 and 3 are
about this computer); publishing and credentials still apply. While a mandate is in force, installs go through
`fieldkit mandate install GB WHAT -- CMD` (the budget, measured and logged).

An approved plan item opens one part of hard line 3 (owner, 2026-10-28, "Yes, both": "headless autopilot may change
hooks/guards for an approved plan item, with tests that prove the check still fails"): a run whose brief (its first
message) names an open item of docs/HARNESS-PLAN.md (R5, O2, ...) may change fieldkit/autopilot/ and the
repository's hooks/, and a `git commit` of such a file needs a `fieldkit mutate` proof for that exact content. The
files that enforce on the run itself (this file, the Claude hook, the budget, the breakers, mutate) stay refused:
the hook reloads them on every call, so an edit would act before any test. Everything else in hard line 3 stays.

Lessons it encodes (2026-10-17/18, Gemini overnight): a blocked `git push` was followed three seconds later by
`gh release create`; a blocked `git checkout --` by a Python script doing the same; a blocked rmtree by hiding the
folder in .git/info/exclude; an approved allowlist entry widened afterwards; a decision flipped to enforced. Text
rules are the second layer: the first is that the credential is not reachable (fieldkit autopilot run).

    decide(tool, args)  -> ("deny", reason) | ("allow", reason)
"""
import json
import os
import re
import subprocess
from pathlib import Path

STATE = Path(os.environ.get("FIELDKIT_AUTOPILOT_STATE") or (Path.home() / ".fieldkit" / "autopilot"))
STOP_FILE = STATE / "STOP"

READ_TOOLS = {
    # Claude Code
    "read", "glob", "grep", "ls", "notebookread", "webfetch", "websearch", "todowrite", "toolsearch",
    # Antigravity
    "view_file", "view_file_outline", "list_directory", "grep_search", "find", "find_by_name", "finda",
    "code_search", "internal_search", "find_all_references", "command_status", "list_resources",
    "view_code_item", "conversation_history", "knowledge_artifacts", "ask_question", "finish", "checkpoint",
}

PUBLISH = [
    (r"\bgit\b[^\n]*\bpush\b", "pushing"),
    (r"\bgh\b[^\n]*\b(release|pr|repo|gist|secret|ssh-key|gpg-key|workflow|label|issue)\s+"
     r"(create|edit|delete|upload|merge|close|reopen|comment|run|enable|disable|rename|archive|fork|set|add|remove|transfer)\b",
     "publishing through gh"),
    (r"\bgh\b[^\n]*\bapi\b[^\n]*(-X|--method)\s*(POST|PUT|PATCH|DELETE)\b", "a gh api write"),
    (r"\bgh\b[^\n]*\bapi\b[^\n]*\s(-f|-F|--field|--raw-field|--input)\b", "a gh api write (fields imply POST)"),
    (r"\b(twine|npm|pnpm|yarn|cargo|poetry|flit)\b[^\n]*\bpublish\b|\btwine\b[^\n]*\bupload\b", "publishing a package"),
    (r"\bgit\b[^\n]*\bremote\b[^\n]*\b(add|set-url)\b", "changing where git pushes"),
    # 2026-10-23: ship push/merge/release/prune push through git inside the tool, which the line above never saw
    (r"\bfieldkit\b[^\n]*\bship\s+(push|merge|release|prune|gh-release)\b", "publishing through fieldkit ship"),
    # 2026-10-23: release-cycle run publishes a GitHub release inside the tool (plan, next and reset only read)
    (r"\bfieldkit\b[^\n]*\brelease-cycle\s+\S+\s+run\b", "publishing a release (fieldkit release-cycle run)"),
]

DELETE = re.compile(
    r"\brm\b[^\n]*\s-[a-z]*r|\brmdir\b|\brd\b[^\n]*/s|\bdel\b[^\n]*/s|remove-item[^\n]*-recurse|shutil\.rmtree|"
    r"\bformat\b\s+[a-z]:|\bgit\b[^\n]*\bclean\b|\bgit\b[^\n]*\breset\b[^\n]*--hard|"
    r"\bgit\b[^\n]*\bcheckout\b[^\n]*(\s--\s|\s\.\s*$)|\bgit\b[^\n]*\bstash\b[^\n]*\b(drop|clear)\b|"
    r"\bgit\b[^\n]*\b(rebase|filter-branch|filter-repo)\b|\bgit\b[^\n]*\bcommit\b[^\n]*--amend|"
    r"\bgit\b[^\n]*\bbranch\b[^\n]*\s-D\b|--no-verify\b|core\.hookspath",
    re.I)

PROTECTED = [
    (r"gorilla\.firefox/harness/", "the owner's build harness"),
    # I-10 (owner, 2026-10-31): the desktop app's session records; an edit (isArchived, spawnedFrom) would archive or
    # un-nest a session past the archive guard. Every agent behind these hard lines: Claude, Antigravity, OpenCode
    (r"claude-code-sessions/", "the desktop app's session records (archive only through archive_session, which "
                               "the archive guard checks)"),
    (r"(^|[/\s\"'])(gemini|agents|claude)\.md\b", "an AI rules file"),
    (r"\.gemini/config|\.claude/settings[^/\s\"']*\.json|\.claude/hooks", "a guard or AI settings"),
    # 2026-10-28: mutate writes the proof a guard commit is checked against, so it is guarded like the guard
    (r"\.git/hooks/|\.git/info/exclude|(^|/)hooks/(pre-|post-)[a-z-]+|fieldkit/autopilot/|fieldkit/method/mutate\.py",
     "a hook or this guard"),
    (r"leakgate/(allow|dispositions)[^/\s\"']*\.json|proposed-dispositions", "the leak-gate allowlist or dispositions"),
    (r"product-decisions\.yaml|gorilla_decisions\.json|claims/claims\.yaml|decisions/theme\.yaml", "the owner's decision register"),
    (r"(^|/)\.gitignore\b", "a .gitignore (a decision, not a default)"),
    (r"state/mutate-proofs", "a mutate proof (only fieldkit mutate writes one)"),
    # 2026-10-22, long horizon goals: a worker or the planner never edits what it is judged by, nor its own meter
    (r"state/goals/[^\s\"']*(goal\.json|goal\.md|state\.json|verdicts\.jsonl|spend\.jsonl|\.supervisor|preapprovals\.yaml|approval\.json)|"
     r"state/(goals|jobs)/[^\s\"']*(checkpoint\.json|budget\.json|meter\.json)",
     "a goal's criteria, verdicts, spend, pre-approvals or a job's budget meter"),
]
# 2026-10-23, the Windows review of the mandate (M1): a model that can write here can pin its own key, sign its own
# mandate, delete a stop or truncate the install budget's log. Models may read it; a command that names it and
# writes anything is refused as a whole (not per statement: `cd ~/.fieldkit && cp k.pub mandate/trusted/`).
# This is the SECOND layer: text cannot see a write by indirection (M1.2). What decides trust (owner keys, reviewer
# keys, stops) is in fieldkit/mandate/guarded.py, where this OS user cannot write at all.
MANDATE_STORE = re.compile(r"\.fieldkit/mandate\b|(^|[/\s\"'])mandate/(trusted|mandates)\b|owner_signing\.|"
                           r"(^|[/\s\"'])stopped\.txt\b|(^|[/\s\"'])local/mandates\b")
MANDATE_ENV = re.compile(r"fieldkit_mandate_home", re.I)
WRITE = re.compile(
    r"(set-content|add-content|out-file|new-item|copy-item|move-item|rename-item|remove-item|clear-content|"
    r"\bdel\b|\berase\b|\bcopy\b|\bmove\b|\bren\b|\bmv\b|\bcp\b|\brm\b|sed\s+-i|\btee\b|>>?|"
    r"open\([^)]*['\"][wa+]|write_text|write_bytes|writelines|\.write\(|json\.dump|"
    r"git\s+(add|commit|checkout|restore|rm|mv|apply|am)\b|\bpatch\b|truncate|chmod|attrib|icacls)",
    re.I)
OWNER_ONLY = [
    (r"leakgate-(approve|dispositions)|dispositions\.approve|approve_from", "approving a leak-gate exemption"),
    (r"record_decisions\.py", "re-recording the owner's pref decisions"),
    (r"gorilla_owner_approved", "the owner's own signature on a harness change"),
    (r"build-harness\s+accept\b", "accepting a hand port (the owner's call)"),
    # 2026-10-18 (found by the tool-harness review session): the other owner-only build-harness actions
    (r"build-harness\s+(approve|rewind|drop|withdraw|unblock|leakgate-rejudge)\b",
     "approving, rewinding, dropping, withdrawing, unblocking or re-judging (the owner's call)"),
    # 2026-10-23: `trust` needs only a typed yes, which a faked terminal can give
    (r"\bmandate\s+(keygen|trust|approve|renew)\b", "making, trusting or using the owner's mandate key"),
    # 2026-10-26: installing a push hook changes a protection: the owner's paste or a session the owner talks to
    (r"\bmandate\s+install-hooks\b[^\n]*--yes\b", "installing a repository's push hook"),
]
# 2026-10-28 (inbox #145, "Yes, ship only"): only fieldkit ship gets the credential back inside a run; a command
# that points gh or git at it by itself is refused (setting the variables, a credential helper, gh auth)
REARM = re.compile(r"(\$env:|\b)(GH_CONFIG_DIR|GH_TOKEN|GITHUB_TOKEN|GH_ENTERPRISE_TOKEN|GIT_ASKPASS|SSH_ASKPASS|"
                   r"GIT_CONFIG_(COUNT|KEY_\d+|VALUE_\d+|PARAMETERS)|FIELDKIT_REAL_GH_CONFIG_DIR)\s*=|credential\.helper|"
                   r"\bgh\s+auth\s+(token|login|setup-git|refresh)\b|\bunset\s+[^\n;&|]*\b(GH_CONFIG_DIR|GIT_CONFIG_)", re.I)
CREDENTIAL = re.compile(
    r"(^|[/\\\s\"'])(\.env(\.[a-z]+)?|id_rsa|id_ed25519|[^/\\\s\"']+\.(pem|pfx|p12|key)|hosts\.yml|"
    r"\.git-credentials|credentials\.json|\.netrc|token(s)?\.(json|txt))([\s\"']|$)", re.I)

EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit", "code_action", "file_change", "write_blob",
              "write_to_file", "replace_file_content", "multi_replace_file_content", "edit_notebook", "move",
              "delete_directory", "write_file", "create_file"}
COMMAND_KEYS = {"command", "commandline", "cmd"}


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


HOOK_WHAT = "a hook or this guard"
PLAN_GUARD = re.compile(r"fieldkit/autopilot/|(^|[/\s\"'])(?<!\.git/)hooks/(pre-|post-)[a-z-]+")
ENFORCING = re.compile(r"fieldkit/autopilot/(hardlines|claude_hook|budget|breaker)\.py|fieldkit/method/mutate\.py")
ITEM = re.compile(r"\b([RLOCI]\d+)\b")


def plan_item(transcript):
    """-> the open docs/HARNESS-PLAN.md item id the run's brief (its first user message) names, or None."""
    try:
        text = None
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for line in f:
                d = json.loads(line)
                if d.get("type") == "user":
                    c = (d.get("message") or {}).get("content")
                    text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
                    break
        if not text:
            return None
        from .. import plan
        st = plan.items((plan.ROOT / plan.PLAN).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return next((i for i in ITEM.findall(text) if i in st and not plan.is_done(st[i])), None)


def _plan_guard_only(text):
    """True when the only protection the text touches is a hook or guard file a plan item may change."""
    t = _norm(text)
    return bool(PLAN_GUARD.search(t)) and not ENFORCING.search(t) and _protected(PLAN_GUARD.sub(" ", t)) is None


def _opened(text, transcript):
    item = _plan_guard_only(text) and plan_item(transcript)
    if not item:
        return None
    return "allow", f"HARD LINE 3 opened for plan item {item}: a hook or guard file (its commit needs a mutate proof)"


def _git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _commit_gate(seg, cwd, transcript):
    """A `git commit` that carries a hook or guard file: denied unless the run has an open plan item, no enforcing
    file is in it, and every such file has a mutate proof for the content being committed. -> deny tuple or None."""
    m = re.search(r"\bgit\b((?:\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+))*)[^\n]*\bcommit\b", seg)
    if not m or not cwd:                               # the hook always passes where the command runs
        return None
    dash_c = re.findall(r"-C\s+(\"[^\"]+\"|'[^']+'|\S+)", m.group(1) or "")
    repo = Path(cwd)
    for c in dash_c:
        repo = repo / c.strip("\"'")
    every = re.search(r"\scommit\b[^\n]*(\s-[a-zA-Z]*a[a-zA-Z]*\b|\s--all\b)", seg)
    names = _git(repo, *(["diff", "HEAD", "--name-only"] if every else ["diff", "--cached", "--name-only"]))
    if names is None:
        return None
    guard = [f for f in names.decode("utf-8", "replace").splitlines() if _protected(f) == HOOK_WHAT]
    if not guard:
        return None
    item, bad = plan_item(transcript), [f for f in guard if not _plan_guard_only(f)]
    if not item or bad:
        return "deny", (f"HARD LINE 3: this commit changes {', '.join(bad or guard)} (a hook or this guard)"
                        + ("" if item else "; the run's brief names no open docs/HARNESS-PLAN.md item"))
    from ..method import mutate
    top = _git(repo, "rev-parse", "--show-toplevel")
    root = Path(top.decode().strip()) if top else repo
    for f in guard:
        data = (root / f).read_bytes() if every and (root / f).is_file() else _git(repo, "show", f":{f}")
        if data is None or not mutate.proven(root / f, data):
            return "deny", (f"HARD LINE 3: no mutate proof for {f} as committed (plan item {item}): run fieldkit "
                            "mutate --plan tests/<x>_mutations.yaml -- <its tests> until every mutation is caught")
    return None


def _norm(s):
    return s.replace("\\", "/").lower()


def _protected(text):
    t = _norm(text)
    return next((what for pat, what in PROTECTED if re.search(pat, t)), None)


def _statements(cmd):
    return [s for s in re.split(r";|&&|\|\||\r?\n", cmd) if s.strip()]


# 2026-11-01 (VAIO, fc6e5bb refused three times): a headless job there has no `fieldkit` on PATH and runs it by its
# full path or as `.../python3.12 -m fieldkit`; both are the same tool and go to the same mandate door
FIELDKIT = r"(?:\S*[/\\])?(?:fieldkit(?:\.exe|\.cmd)?|python[\d.]*(?:\.exe)?\s+-m\s+fieldkit)"
# B1 (2026-10-31): `push --auto [--title "T"] [--body-file F]` opens the PR with auto-merge on green, the same door
SHIP = re.compile(r"^\s*" + FIELDKIT + r"\s+ship\s+(push|merge)((?:\s+--(?:repo|base|tag|body-file)(?:\s+|=)[^\s;&|`$<>]+"
                  r"|\s+--auto|\s+--title(?:\s+|=)(?:\"[^\"`$]*\"|'[^']*'|[^\s;&|`$<>]+))*)\s*$", re.I)
# the owner-wide mandate (2026-10-25): a release of a tag already on origin, once CI is green (the mandate proves it)
GH_RELEASE = re.compile(r"^\s*" + FIELDKIT + r"\s+ship\s+gh-release\s+(v[\w.+-]+)((?:\s+--repo(?:\s+|=)[^\s;&|`$<>]+)?)"
                        r"(\s+--\s[^;&|`$<>]*)?\s*$", re.I)
# a kernel on this machine: never installed or booted by a model, mandate or not (owner, 2026-10-25)
KERNEL_BOOT = re.compile(r"\blinux-(image|headers|modules)\b|\bdpkg\b[^\n]*\s-i\b[^\n]*(kernel|linux)|"
                         r"\b(update-grub|grub-(install|mkconfig|reboot|set-default)|update-initramfs|mkinitcpio|"
                         r"dracut|kexec|bootctl|efibootmgr)\b|\bsystemctl\s+reboot\b|(^|[;&|]\s*|\bsudo\s+)reboot\b|"
                         r"\bshutdown\b[^\n]*\s-r\b", re.I)
RELEASE_RUN = re.compile(r"^\s*" + FIELDKIT + r"\s+release-cycle\s+([\w.-]+)\s+run\s+--var(?:\s+|=)version=(v\d+\.\d+\.\d+)"
                         r"(?:\s+--(?:only|from)(?:\s+|=)[\w-]+)?\s*$", re.I)
MAC_RUN = re.compile(r"^\s*" + FIELDKIT + r"\s+mac\s+run\s+([\w.-]+)\s+--\s", re.I)
INSTALL_WRAP = re.compile(r"^\s*" + FIELDKIT + r"\s+mandate\s+install\s", re.I)
INSTALLER = re.compile(r"\b(apt(-get)?|dnf|yum|pacman|zypper|brew|snap|flatpak|winget|choco|scoop)\s+(-\S+\s+)*install\b|"
                       r"\bpip3?\s+install\b|\bpipx\s+install\b|\bnpm\s+(i|install)\s+(-g|--global)\b|"
                       r"\bdpkg\s+-i\b|(^|[\s/])install\.(sh|ps1)\b", re.I)
NEVER_OPENED = re.compile(r"\bmandate\s+(keygen|trust|approve|renew|stop|install-hooks)\b|gorilla_owner_approved",
                          re.I)   # whatever a door says
PLAIN = re.compile(r"^[^;&|`\n]*$")                     # one statement: no ; && || | ` or newline
SUBST = re.compile(r"\$\(|<\(|>\(")


CD_THEN = re.compile(r"^\s*cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)\s*&&\s*(.+)$", re.S)
CD_PUSH = re.compile(r"^\s*" + FIELDKIT + r"\s+ship\s+push\b(?![^\n]*--tag)", re.I)
#: B1 live proof (VAIO, 2026-10-31): a model runs `fieldkit ship push --auto 2>&1 | tail -20`; the pipe made it two
#: statements and the mandate's door stayed shut. Only stderr joined to stdout and ONE trailing `| tail N` / `| head N`
#: (they print, write nothing) are read away; `| tee log`, `| sh` and anything else still keep the door shut.
VIEW_TAIL = re.compile(r"(?:\s+2>&1)?(?:\s*\|\s*(?:tail|head)(?:\s+-n)?(?:\s+-?\d+)?)?\s*$")
INBOX_NOTE = re.compile(r"^\s*(?:python[\d.]*(?:\.exe)?\s+-m\s+)?fieldkit(?:\.exe)?\s+inbox\s+add\s", re.I)


def _one_statement(cmd):
    return bool(PLAIN.match(cmd)) and not SUBST.search(cmd)


# -- 2026-10-27 (owner "Da" on inbox #108: 53 of 73 refusals 10-25..27 were false, BASELINE.md). Each rule below
#    narrows what is READ as an action; what is written, deleted or pushed is judged exactly as before.
#: a file-descriptor redirect writes nothing: 2>&1, >&2, 2>/dev/null, 2>nul, &>/dev/null (fix 1)
FD_REDIRECT = re.compile(r"\d?>&\d|&>>?\s*(/dev/null|nul)\b|\d?>>?\s*(/dev/null|nul)\b", re.I)
#: a cat/tee heredoc whose body cannot run: quoted delimiter, or no $( or backtick in it (fix 4). The first line
#: keeps its redirect (`cat > CLAUDE.md <<'EOF'` is still a write there); `cat <<EOF | sh` is never stripped.
SINK_HEREDOC = re.compile(r"^([ \t]*(?:cat|tee)\b[^\n|;&`]*<<-?[ \t]*(['\"]?)(\w+)\2[^\n|;&`]*)\n(.*?)\n[ \t]*\3[ \t]*$",
                          re.S | re.M)
#: reading the hooks-path setting (no value after it) is not a delete (fix 3)
HOOKS_READ = re.compile(r"^\s*git\s+config\s+(--(get|get-all|get-regexp|local|global|system|show-origin|show-scope|"
                        r"worktree|null|name-only)\s+)*core\.hookspath\s*$", re.I)
#: the arguments of an Edit/Write/Grep/... call that name a place, not its content (fix 2)
PATH_KEY = re.compile(r"path|file|dir|^(target|source|destination|glob|include|cwd)$", re.I)
CONTENT_KEY = re.compile(r"content|text|chunk|string|code|source_?code|edits|instruction|description", re.I)


def _writes(text):
    return WRITE.search(FD_REDIRECT.sub(" ", text))


def _sink_heredocs_out(cmd):
    def cut(m):
        quoted, body = m.group(2), m.group(4)
        return m.group(1) if quoted or not re.search(r"\$\(|`", body) else m.group(0)
    return SINK_HEREDOC.sub(cut, cmd)


def _comments_out(cmd):
    """Drop shell comments: a # at the start of a word, outside quotes, to the end of its line. Not under cmd.exe,
    where # is an ordinary character (`cmd /c echo #& rd /s x` runs the rd)."""
    if re.search(r"\bcmd(\.exe)?[\"']?\s+/[ck]\b", cmd, re.I):
        return cmd
    out, q, i = [], None, 0
    while i < len(cmd):
        c = cmd[i]
        if q:
            q = None if c == q else q
        elif c in "\"'":
            q = c
        elif c == "#" and (i == 0 or cmd[i - 1] in " \t\n;&|("):
            j = cmd.find("\n", i)
            i = len(cmd) if j < 0 else j
            continue
        out.append(c)
        i += 1
    return "".join(out)


SEARCH_PATTERN = re.compile(r"((?:^|[;&|\n])\s*(?:[^\s\"';&|]*[\\/])?(?:grep|egrep|fgrep|rg|ag|findstr|select-string|"
                            r"git\s+grep)(?:\.exe)?\b[^\"'\n;&|]*)(\"[^\"\n]*\"|'[^'\n]*')", re.I)


def _patterns_out(cmd):
    """The command without the pattern (first quoted argument) of each search statement; its files stay. Matched
    from the search word to its first quote, so a | inside the pattern does not split it."""
    return SEARCH_PATTERN.sub(lambda m: m.group(1) + '""', cmd)


def _delete_text(cmd):
    """The command as hard line 2 reads it: no comments, no plain reads of the hooks-path setting."""
    parts = re.split(r"(;|&&|\|\||\||\r?\n)", _comments_out(cmd))
    return "".join("" if HOOKS_READ.match(FD_REDIRECT.sub(" ", p)) else p for p in parts)


def _place_text(args, name=""):
    """-> the path-like arguments of a tool. None when a tool that may write names no place (then its whole input is
    judged, fail closed); "" for a read tool that names none (a search in the current folder)."""
    found = [s for k, v in args.items()
             if (PATH_KEY.search(k) and not CONTENT_KEY.search(k)) or (name == "glob" and k.lower() == "pattern")
             for s in _strings(v)]
    if found:
        return "\n".join(found)
    return "" if name in READ_TOOLS else None


class MandateDoor:
    """What the hard lines ask of the project mandate (fieldkit/mandate). Any error answers "not allowed"."""

    def decide(self, action, **kw):
        try:
            from ..mandate import mandate as M
            return M.decide(action, **kw)
        except Exception as e:  # noqa: BLE001 - a broken mandate never opens a door
            return "ask", f"the mandate could not be read ({e})", None

    def in_force(self):
        try:
            from ..mandate import mandate as M
            return bool(M.in_force())
        except Exception:  # noqa: BLE001
            return False

    def log(self, path, what, detail):
        try:
            from ..mandate import mandate as M
            M.log(path, what, detail, by="hard line")
        except Exception:  # noqa: BLE001
            pass


def _mandated(cmd, cwd, door):
    """-> ("allow", why) when a mandate in force opens hard line 1 or 4 for this exact command, ("shut", why) when the
    mandate was asked and said no (its reason goes into the refusal), else None."""
    if not _one_statement(cmd) or NEVER_OPENED.search(cmd):
        return None
    m = SHIP.match(cmd)
    if m:
        repo = re.search(r"--repo(?:\s+|=)(\S+)", m.group(2) or "")
        repo_path = repo.group(1) if repo else (cwd or os.getcwd())
        tag = re.search(r"--tag(?:\s+|=)(\S+)", m.group(2) or "")
        if m.group(1).lower() == "merge":
            # ship merge refuses by itself without a mandate that allows it; let it through only while one is in force
            return ("allow", "HARD LINE 1 opened: fieldkit ship merge proves the mandate's conditions itself") \
                if door.in_force() and not tag else None
        v, why, path = door.decide("tag", repo_path=repo_path, tag=tag.group(1)) if tag else \
            door.decide("push", repo_path=repo_path)
        if v == "allow":
            door.log(path, "push-allowed", f"{why}: {cmd.strip()[:200]}")
            return "allow", f"HARD LINE 1 opened by the mandate: {why}"
        return "shut", why                                # the refusal says the mandate's own reason (B1, 2026-11-01)
    m = GH_RELEASE.match(cmd)
    if m:                                                 # owner-wide: release of a tag on origin, CI green
        repo = re.search(r"--repo(?:\s+|=)(\S+)", m.group(2) or "")
        v, why, path = door.decide("release_tag", repo_path=repo.group(1) if repo else (cwd or os.getcwd()),
                                   tag=m.group(1))
        if v == "allow":
            door.log(path, "release-allowed", f"{why}: {cmd.strip()[:200]}")
            return "allow", f"HARD LINE 1 opened by the mandate: {why}"
        return "shut", why                                # the refusal says the mandate's own reason (B1, 2026-11-01)
    m = RELEASE_RUN.match(cmd)
    if m:                                                 # the mandate's release door; never with another release file
        v, why, path = door.decide("release", project=m.group(1), version=m.group(2))
        if v == "allow":
            door.log(path, "release-allowed", f"{why}: {cmd.strip()[:200]}")
            return "allow", f"HARD LINE 1 opened by the mandate: {why}"
        return "shut", why                                # the refusal says the mandate's own reason (B1, 2026-11-01)
    if any(re.search(pat, cmd, re.I) for pat, _ in OWNER_ONLY):
        v, why, path = door.decide("approval", what=cmd)
        if v == "allow":
            door.log(path, "approval-allowed", f"{why}: {cmd.strip()[:200]}")
            return "allow", f"HARD LINE 4 opened by the mandate: {why}"
    return None


def decide(tool, args, stop_file=None, cwd=None, door=None, transcript=None):
    """-> ("deny", why) | ("allow", why). `tool` as the agent names it, `args` its input (dict); `cwd` where a command
    runs (the hook payload's cwd); `door` the project mandate (MandateDoor, replaced in tests); `transcript` the run's
    transcript (its first message is the brief: an approved plan item opens hook and guard files)."""
    door = door or MandateDoor()
    name = (tool or "").lower()
    args = args if isinstance(args, dict) else {}
    texts = list(_strings(args))
    joined = "\n".join(texts)
    stop = Path(stop_file) if stop_file else STOP_FILE
    cmd = next((v for k, v in args.items() if k.lower() in COMMAND_KEYS and isinstance(v, str)), None)
    if cmd is not None:
        cmd = _sink_heredocs_out(cmd)                     # a note's text is not a command (#108 fix 4)
    # #108 fix 2: a command is judged on its command text (+ its path arguments); any other tool on the places it
    # names, not on what it writes or searches for; a tool that names no place is judged on its whole input
    place = _place_text(args, name)
    if cmd is not None:
        place = _patterns_out(cmd) + ("\n" + place if place else "")
    judged = joined if place is None else place
    if CREDENTIAL.search(judged):
        return "deny", "HARD LINE 5: a credential file is never read or written by a model"
    if name in READ_TOOLS:
        return "allow", "read"
    if stop.exists():
        return "deny", f"STOPPED by the owner ({stop}): only reads until the owner removes it (fieldkit autopilot resume)"
    if MANDATE_ENV.search(joined):
        return "deny", "HARD LINE 3: FIELDKIT_MANDATE_HOME would bring a trust store of its own; the mandate is the owner's"
    if cmd is not None and MANDATE_STORE.search(_norm(cmd)) and _writes(cmd):
        return "deny", "HARD LINE 3: this would change the owner's mandate store; models may only read it"
    if cmd is not None and REARM.search(cmd):
        return "deny", ("HARD LINE 5: re-arming a git or gh credential inside a run is fieldkit ship's alone "
                        "(owner, 2026-10-28, inbox #145: \"Yes, ship only\")")
    if cmd is not None and KERNEL_BOOT.search(cmd):
        return "deny", ("HARD LINE 4: installing or booting a kernel (or rebooting) on this machine is the owner's "
                        "alone, mandate or not; build, push and publish it, then put the install in the owner inbox")
    if cmd is not None:
        # 2026-10-27 (owner "Da" on inbox #97): `cd REPO && fieldkit ship push` is judged as the push run in REPO;
        # one plain `fieldkit inbox add "..."` is a note, whatever its quoted text names (it was refused for saying
        # "fieldkit ship" while asking the owner to publish)
        cmd = cmd[:VIEW_TAIL.search(cmd).start()] or cmd     # how the output is shown is not the action
        cd = CD_THEN.match(cmd)
        if cd and _one_statement(cd.group(2)) and CD_PUSH.match(cd.group(2)):    # a branch push only, judged in REPO
            cwd, cmd = cd.group(1).strip("\"'"), cd.group(2)
        if INBOX_NOTE.match(cmd) and _one_statement(cmd):
            return "allow", "a note to the owner's inbox"
        published = next((why for pat, why in PUBLISH if re.search(pat, cmd, re.I)), None)
        owner = next((why for pat, why in OWNER_ONLY if re.search(pat, cmd, re.I)), None)
        shut = None
        if published or owner:
            opened = _mandated(cmd, cwd, door)
            if opened and opened[0] == "allow" and not (published and owner):
                return opened
            shut = opened[1] if opened and opened[0] == "shut" else None
        if published:
            # 2026-11-01: a ship command the mandate was never asked about says so, and which shape it is asked about
            unasked = not opened and re.search(r"\bship\s+(push|merge|gh-release)\b", cmd, re.I)
            return "deny", (f"HARD LINE 1: {published} is the owner's job (fieldkit publish, after the owner's yes)"
                            + (f"; the mandate did not open it: {shut}" if shut else "")
                            + ("; the mandate was not asked: it is asked only about ONE plain statement `fieldkit ship "
                               "push|merge|gh-release ...` (or `cd DIR && fieldkit ship push ...`), with no other "
                               "; && | or $(...)" if unasked else ""))
        if owner:
            return "deny", f"HARD LINE 4: {owner} is the owner's alone; put it in the owner inbox (fieldkit inbox add)"
        mac = MAC_RUN.match(cmd) if _one_statement(cmd) else None   # a ; after -- would run HERE, not on the Mac
        if mac:
            v, why, path = door.decide("mac", name=mac.group(1))
            if v == "allow":
                door.log(path, "mac-run-allowed", f"{why}: {cmd.strip()[:200]}")
                return "allow", f"on the Mac {mac.group(1)}, granted by the mandate ({why})"
        if INSTALLER.search(cmd) and not INSTALL_WRAP.match(cmd) and door.in_force():
            return "deny", ("MANDATE: while a project mandate is in force, installs go through "
                            "fieldkit mandate install GB WHAT -- COMMAND (the budget, measured and logged)")
        # each deleting statement on its own paths (`rm -rf /tmp/x; cd C:/repo && git status` deletes only scratch)
        if any(DELETE.search(s) and not _scratch_only(s) for s in _statements(_delete_text(cmd))):
            return "deny", ("HARD LINE 2: deleting or discarding work outside the scratch folders; move it to a "
                            "labelled holding folder instead and say so")
        for seg in _statements(cmd):
            what = _protected(seg)
            if what and _writes(seg) and not (what == HOOK_WHAT and _opened(seg, transcript)):
                return "deny", f"HARD LINE 3: this would change {what}; report the need in the owner inbox"
            gate = _commit_gate(seg, cwd, transcript)
            if gate:
                return gate
        return "allow", "command inside the hard lines"
    if MANDATE_STORE.search(_norm(judged)):
        return "deny", "HARD LINE 3: the owner's mandate store may not be changed by a model; models may only read it"
    what = _protected(judged)
    if what and (name in EDIT_TOOLS or name not in READ_TOOLS):
        if what == HOOK_WHAT:
            opened = _opened(judged, transcript)
            if opened:
                return opened
        return "deny", f"HARD LINE 3: {what} may not be changed by a model; report the need in the owner inbox"
    if name == "delete_directory" and not _scratch_only(judged):
        return "deny", "HARD LINE 2: deleting a folder outside the scratch folders"
    return "allow", "inside the hard lines"


def scratch_roots():
    roots = [os.environ.get("TEMP"), os.environ.get("TMP"), "/tmp", str(STATE / "scratch")]
    return [_norm(r).rstrip("/") + "/" for r in roots if r]


def _scratch_only(text):
    """A delete whose every path argument lies under a scratch root (TEMP, /tmp, the autopilot scratch)."""
    # 2026-10-27 (#108 fix 3): $TEMP / ${TMP} / $TMPDIR are the scratch root too; a path that climbs out with .. never is
    paths = re.findall(r"[a-z]:[\\/][^\s\"';|&]+|/[^\s\"';|&]+|\$env:temp[^\s\"';|&]*|%temp%[^\s\"';|&]*|"
                       r"\$\{?(?:temp|tmp|tmpdir)\}?(?=[/\\\s\"']|$)[^\s\"';|&]*", text, re.I)
    if not paths:
        return False
    roots = scratch_roots()
    temp = _norm(os.environ.get("TEMP", "~temp"))
    ok = 0
    for p in paths:
        n = re.sub(r"^\$\{?(temp|tmp|tmpdir)\}?", lambda _: temp, _norm(p))
        n = n.replace("$env:temp", temp).replace("%temp%", temp)
        if re.search(r"(^|/)\.\.(/|$)", n):
            return False
        if any(n.startswith(r) for r in roots):
            ok += 1
        elif n.startswith(("/dev/", "/proc/")) or re.fullmatch(r"/[a-z]{1,2}", n):
            continue                        # a flag such as /s or /q read as a path
        else:
            return False
    return ok > 0
