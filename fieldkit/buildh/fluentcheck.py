# Version: 1.0.0 · updated 26-10-14-23-10
"""Fluent (.ftl) files of a ported tree, checked against the pristine release: what parses, what is empty, what is gone.

    fieldkit build-harness fluent-check TASK

Born 2026-10-14 on the Linux 158 port, where two failures got past every step check:
  - five plural messages in places.ftl kept their id and lost their whole value (`.label =` and nothing after): the
    patch, exported on Windows, put a whitespace-only line inside the value and the Fluent port stopped there;
  - newtab-stocks-menu-learn-more vanished: patch 086 removed the SECOND of two copies on 157, 158 has one, so the
    mechanical apply deleted the only copy. Nothing fails to parse when a message is simply gone.

Three findings, each deterministic:
  syntax       the file does not parse (Firefox drops the message): firefox.syntax_problems
  empty        an attribute or message with no value at all (`  .label =` followed by nothing more indented)
  lost         a message id the pristine release has and the ported file lacks, unless the patch policy names it in
               `fluent_removed` (id, or a prefix ending in `-`, with the reason), e.g. the AI excision's genai-*
A file the port deleted is not judged (the deletion is checked by final-checks' unexplained_deletions).
"""
import json
import re
import subprocess
from pathlib import Path

MESSAGE = re.compile(r"^([a-z][a-z0-9_-]*)\s*=", re.M)
ATTR_EMPTY = re.compile(r"^(\s+)\.[\w-]+\s*=\s*$")
MSG_EMPTY = re.compile(r"^([a-z][a-z0-9_-]*)\s*=\s*$")


def _git(w, *args):
    r = subprocess.run(["git", "-C", str(w), *args], capture_output=True)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def empty_values(text):
    """-> [(line number, line)] for a message or attribute whose value is missing entirely."""
    lines = text.split("\n")
    out = []
    for i, l in enumerate(lines):
        m = ATTR_EMPTY.match(l)
        if m:
            # Fluent needs a continuation line to be indented, not MORE indented than the attribute (Windows exports
            # put `.accesskey =` and its `{ PLATFORM() -> ...}` at the same depth, which parses). The value is missing
            # when the next line is not indented at all, or is the next attribute.
            nxt = next((x for x in lines[i + 1:] if x.strip()), "")
            if not nxt.startswith(" ") or nxt.lstrip().startswith("."):
                out.append((i + 1, l.rstrip()))
            continue
        m = MSG_EMPTY.match(l)
        if m:
            nxt = next((x for x in lines[i + 1:] if x.strip()), "")
            if not nxt.startswith(" "):                  # a message with neither a value nor attributes
                out.append((i + 1, l.rstrip()))
    return out


def allowed(policy_root):
    """The `fluent_removed` entries of config/patch_policy.json: {id or prefix-: reason}."""
    try:
        return json.loads((Path(policy_root) / "config" / "patch_policy.json").read_text(encoding="utf-8")).get(
            "fluent_removed", {})
    except (OSError, ValueError):
        return {}


def check(workdir, policy_root=None):
    from .firefox import syntax_problems
    w = Path(workdir)
    rc, root = _git(w, "rev-list", "--max-parents=0", "HEAD")
    root = root.split()[0] if rc == 0 and root.strip() else None
    if not root:
        return {"ok": False, "problems": [f"{w} is not a work tree with a pristine root commit"], "files": 0}
    _, names = _git(w, "diff", "--name-only", root, "--", "*.ftl")
    files = [f for f in names.split() if (w / f).is_file()]
    allow = allowed(policy_root) if policy_root else {}
    problems = [f"syntax: {x}" for x in syntax_problems(w, files)]
    for f in files:
        text = (w / f).read_text(encoding="utf-8", errors="replace")
        problems += [f"empty: {f}:{n}: {line.strip()} has no value" for n, line in empty_values(text)]
        _, before = _git(w, "show", f"{root}:{f}")
        gone = sorted(set(MESSAGE.findall(before)) - set(MESSAGE.findall(text)))
        for mid in gone:
            if mid in allow or any(k.endswith("-") and mid.startswith(k) for k in allow):
                continue
            problems.append(f"lost: {f}: message {mid} is in the pristine release and not in the ported file")
    return {"ok": not problems, "problems": problems, "files": len(files)}


def lines(res):
    out = [f"  {p}" for p in res["problems"]]
    out.append(f"FLUENT {'OK' if res['ok'] else 'NOT OK'}: {res['files']} changed .ftl file(s), "
               f"{len(res['problems'])} problem(s)")
    return out
