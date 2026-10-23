# Version: 1.3.0 · updated 26-10-23-15-10
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
                                    fieldkit mandate keygen / trust / approve
  5. credential files               read or write: .env, keys, gh hosts.yml, .git-credentials, tokens
  and: the owner's STOP file (fieldkit autopilot stop) denies everything that is not a read.

A project mandate in force (fieldkit/mandate, M3, 2026-10-23) opens two of these and nothing else, each logged in
the mandate's log: hard line 1 for `fieldkit ship push` on a branch the mandate lets sessions push (fast-forward,
checked here) and `fieldkit ship merge` (which proves the merge's conditions itself and refuses without them); hard
line 4 for an owner approval listed in the mandate's owner_approvals; and its release door (release: {cycle: [..]})
opens hard line 1 for `fieldkit release-cycle PROJECT run --var version=vX.Y.Z` of a project it lists. Only as ONE plain statement: no `;`, `&&`,
pipe or substitution around it, so what was checked is what runs. `git push` stays denied: the tool is the door.
`fieldkit mac run NAME -- ...` on a Mac the mandate grants is judged as the Mac's business (hard lines 2 and 3 are
about this computer); publishing and credentials still apply. While a mandate is in force, installs go through
`fieldkit mandate install GB WHAT -- CMD` (the budget, measured and logged).

Lessons it encodes (2026-10-17/18, Gemini overnight): a blocked `git push` was followed three seconds later by
`gh release create`; a blocked `git checkout --` by a Python script doing the same; a blocked rmtree by hiding the
folder in .git/info/exclude; an approved allowlist entry widened afterwards; a decision flipped to enforced. Text
rules are the second layer: the first is that the credential is not reachable (fieldkit autopilot run).

    decide(tool, args)  -> ("deny", reason) | ("allow", reason)
"""
import os
import re
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
    (r"\bfieldkit\b[^\n]*\bship\s+(push|merge|release|prune)\b", "publishing through fieldkit ship"),
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
    (r"(^|[/\s\"'])(gemini|agents|claude)\.md\b", "an AI rules file"),
    (r"\.gemini/config|\.claude/settings[^/\s\"']*\.json|\.claude/hooks", "a guard or AI settings"),
    (r"\.git/hooks/|\.git/info/exclude|(^|/)hooks/(pre-|post-)[a-z-]+|fieldkit/autopilot/", "a hook or this guard"),
    (r"leakgate/(allow|dispositions)[^/\s\"']*\.json|proposed-dispositions", "the leak-gate allowlist or dispositions"),
    (r"product-decisions\.yaml|gorilla_decisions\.json|claims/claims\.yaml|decisions/theme\.yaml", "the owner's decision register"),
    (r"(^|/)\.gitignore\b", "a .gitignore (a decision, not a default)"),
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
    (r"\bmandate\s+(keygen|trust|approve)\b", "making, trusting or using the owner's mandate key"),
]
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


def _norm(s):
    return s.replace("\\", "/").lower()


def _protected(text):
    t = _norm(text)
    return next((what for pat, what in PROTECTED if re.search(pat, t)), None)


def _statements(cmd):
    return [s for s in re.split(r";|&&|\|\||\r?\n", cmd) if s.strip()]


FIELDKIT = r"(?:\S*[/\\])?(?:fieldkit(?:\.exe|\.cmd)?|python3?(?:\.exe)?\s+-m\s+fieldkit)"
SHIP = re.compile(r"^\s*" + FIELDKIT + r"\s+ship\s+(push|merge)((?:\s+--(?:repo|base)(?:\s+|=)[^\s;&|`$<>]+)*)\s*$", re.I)
RELEASE_RUN = re.compile(r"^\s*" + FIELDKIT + r"\s+release-cycle\s+([\w.-]+)\s+run\s+--var(?:\s+|=)version=(v\d+\.\d+\.\d+)"
                         r"(?:\s+--(?:only|from)(?:\s+|=)[\w-]+)?\s*$", re.I)
MAC_RUN = re.compile(r"^\s*" + FIELDKIT + r"\s+mac\s+run\s+([\w.-]+)\s+--\s", re.I)
INSTALL_WRAP = re.compile(r"^\s*" + FIELDKIT + r"\s+mandate\s+install\s", re.I)
INSTALLER = re.compile(r"\b(apt(-get)?|dnf|yum|pacman|zypper|brew|snap|flatpak|winget|choco|scoop)\s+(-\S+\s+)*install\b|"
                       r"\bpip3?\s+install\b|\bpipx\s+install\b|\bnpm\s+(i|install)\s+(-g|--global)\b|"
                       r"\bdpkg\s+-i\b|(^|[\s/])install\.(sh|ps1)\b", re.I)
NEVER_OPENED = re.compile(r"\bmandate\s+(keygen|trust|approve|stop)\b|gorilla_owner_approved", re.I)   # whatever a door says
PLAIN = re.compile(r"^[^;&|`\n]*$")                     # one statement: no ; && || | ` or newline
SUBST = re.compile(r"\$\(|<\(|>\(")


def _one_statement(cmd):
    return bool(PLAIN.match(cmd)) and not SUBST.search(cmd)


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
    """-> ("allow", why) when a mandate in force opens hard line 1 or 4 for this exact command, else None."""
    if not _one_statement(cmd) or NEVER_OPENED.search(cmd):
        return None
    m = SHIP.match(cmd)
    if m:
        repo = re.search(r"--repo(?:\s+|=)(\S+)", m.group(2) or "")
        repo_path = repo.group(1) if repo else (cwd or os.getcwd())
        if m.group(1).lower() == "merge":
            # ship merge refuses by itself without a mandate that allows it; let it through only while one is in force
            return ("allow", "HARD LINE 1 opened: fieldkit ship merge proves the mandate's conditions itself") \
                if door.in_force() else None
        v, why, path = door.decide("push", repo_path=repo_path)
        if v == "allow":
            door.log(path, "push-allowed", f"{why}: {cmd.strip()[:200]}")
            return "allow", f"HARD LINE 1 opened by the mandate: {why}"
        return None
    m = RELEASE_RUN.match(cmd)
    if m:                                                 # the mandate's release door; never with another release file
        v, why, path = door.decide("release", project=m.group(1), version=m.group(2))
        if v == "allow":
            door.log(path, "release-allowed", f"{why}: {cmd.strip()[:200]}")
            return "allow", f"HARD LINE 1 opened by the mandate: {why}"
        return None
    if any(re.search(pat, cmd, re.I) for pat, _ in OWNER_ONLY):
        v, why, path = door.decide("approval", what=cmd)
        if v == "allow":
            door.log(path, "approval-allowed", f"{why}: {cmd.strip()[:200]}")
            return "allow", f"HARD LINE 4 opened by the mandate: {why}"
    return None


def decide(tool, args, stop_file=None, cwd=None, door=None):
    """-> ("deny", why) | ("allow", why). `tool` as the agent names it, `args` its input (dict); `cwd` where a command
    runs (the hook payload's cwd); `door` the project mandate (MandateDoor, replaced in tests)."""
    door = door or MandateDoor()
    name = (tool or "").lower()
    args = args if isinstance(args, dict) else {}
    texts = list(_strings(args))
    joined = "\n".join(texts)
    stop = Path(stop_file) if stop_file else STOP_FILE
    if CREDENTIAL.search(joined):
        return "deny", "HARD LINE 5: a credential file is never read or written by a model"
    if name in READ_TOOLS:
        return "allow", "read"
    if stop.exists():
        return "deny", f"STOPPED by the owner ({stop}): only reads until the owner removes it (fieldkit autopilot resume)"
    if MANDATE_ENV.search(joined):
        return "deny", "HARD LINE 3: FIELDKIT_MANDATE_HOME would bring a trust store of its own; the mandate is the owner's"
    cmd = next((v for k, v in args.items() if k.lower() in COMMAND_KEYS and isinstance(v, str)), None)
    if cmd is not None and MANDATE_STORE.search(_norm(cmd)) and WRITE.search(cmd):
        return "deny", "HARD LINE 3: this would change the owner's mandate store; models may only read it"
    if cmd is not None:
        published = next((why for pat, why in PUBLISH if re.search(pat, cmd, re.I)), None)
        owner = next((why for pat, why in OWNER_ONLY if re.search(pat, cmd, re.I)), None)
        if published or owner:
            opened = _mandated(cmd, cwd, door)
            if opened and not (published and owner):
                return opened
        if published:
            return "deny", f"HARD LINE 1: {published} is the owner's job (fieldkit publish, after the owner's yes)"
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
        if DELETE.search(cmd) and not _scratch_only(cmd):
            return "deny", ("HARD LINE 2: deleting or discarding work outside the scratch folders; move it to a "
                            "labelled holding folder instead and say so")
        for seg in _statements(cmd):
            what = _protected(seg)
            if what and WRITE.search(seg):
                return "deny", f"HARD LINE 3: this would change {what}; report the need in the owner inbox"
        return "allow", "command inside the hard lines"
    if MANDATE_STORE.search(_norm(joined)):
        return "deny", "HARD LINE 3: the owner's mandate store may not be changed by a model; models may only read it"
    what = _protected(joined)
    if what and (name in EDIT_TOOLS or name not in READ_TOOLS):
        return "deny", f"HARD LINE 3: {what} may not be changed by a model; report the need in the owner inbox"
    if name == "delete_directory" and not _scratch_only(joined):
        return "deny", "HARD LINE 2: deleting a folder outside the scratch folders"
    return "allow", "inside the hard lines"


def scratch_roots():
    roots = [os.environ.get("TEMP"), os.environ.get("TMP"), "/tmp", str(STATE / "scratch")]
    return [_norm(r).rstrip("/") + "/" for r in roots if r]


def _scratch_only(text):
    """A delete whose every path argument lies under a scratch root (TEMP, /tmp, the autopilot scratch)."""
    paths = re.findall(r"[a-z]:[\\/][^\s\"';|&]+|/[^\s\"';|&]+|\$env:temp[^\s\"';|&]*|%temp%[^\s\"';|&]*", text, re.I)
    if not paths:
        return False
    roots = scratch_roots()
    ok = 0
    for p in paths:
        n = _norm(p).replace("$env:temp", _norm(os.environ.get("TEMP", "~temp"))).replace(
            "%temp%", _norm(os.environ.get("TEMP", "~temp")))
        if any(n.startswith(r) for r in roots):
            ok += 1
        elif n.startswith(("/dev/", "/proc/")) or re.fullmatch(r"/[a-z]{1,2}", n):
            continue                        # a flag such as /s or /q read as a path
        else:
            return False
    return ok > 0
