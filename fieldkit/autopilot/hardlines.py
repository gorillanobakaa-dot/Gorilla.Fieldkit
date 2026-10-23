# Version: 1.1.0 · updated 26-10-23-04-41
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


def decide(tool, args, stop_file=None):
    """-> ("deny", why) | ("allow", why). `tool` as the agent names it, `args` its input (dict)."""
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
        for pat, why in PUBLISH:
            if re.search(pat, cmd, re.I):
                return "deny", f"HARD LINE 1: {why} is the owner's job (fieldkit publish, after the owner's yes)"
        for pat, why in OWNER_ONLY:
            if re.search(pat, cmd, re.I):
                return "deny", f"HARD LINE 4: {why} is the owner's alone; put it in the owner inbox (fieldkit inbox add)"
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
