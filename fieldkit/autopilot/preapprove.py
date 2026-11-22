# Version: 1.0.0 · updated 26-11-22-18-15
"""One pre-approval per job (FIX-PLAN F0.7): the job lists every permission it may need, the owner approves the list
once, and it is written into each client's allow-list, so the job meets 0 permission prompts and 0 pastes.

A grant is {"job": ID, "bash": ["git status", ...], "tools": ["Edit", "Write", ...], "peer": ["fieldkit size find", ...]}.
  claude_settings(grant)   -> the "permissions" block for Claude Code / Desktop (allow only; the deny list and the
                              hard-line hook are never touched, so a grant can never open a hard line)
  opencode_config(grant)   -> the "permission" block for Gorilla OpenCode (the same list; everything else `deny`,
                              never `ask`: outside the grant a step takes its fallback, not a prompt)
  peer_steps(grant)        -> the cross-machine steps as `fieldkit peers link run` commands, never a paste
  write(grant, folder)     -> writes .claude/settings.local.json and opencode.json under `folder` (merged, not replaced)
"""
import json
from pathlib import Path

NEVER = ("git push", "gh release", "gh pr merge", "gh repo", "gh api", "git tag", "rm -rf", "reboot")


def _check(grant):
    if not isinstance(grant, dict) or not grant.get("job"):
        raise ValueError("a grant names its job")
    for c in list(grant.get("bash", [])) + list(grant.get("peer", [])):
        if any(c.strip().startswith(n) for n in NEVER):
            raise ValueError(f"{c!r} is a hard line; a grant cannot open it")
    return grant


def claude_settings(grant):
    g = _check(grant)
    allow = [f"Bash({c}:*)" for c in g.get("bash", [])] + list(g.get("tools", []))
    allow += [f"Bash({c}:*)" for c in peer_steps(g)]
    return {"permissions": {"allow": sorted(set(allow))}}


def opencode_config(grant):
    g = _check(grant)
    bash = {c + "*": "allow" for c in g.get("bash", [])}
    bash.update({c + "*": "allow" for c in peer_steps(g)})
    bash["*"] = "deny"
    tools = {t.lower(): "allow" for t in g.get("tools", [])}
    return {"permission": {"bash": bash, **tools}}


def peer_steps(grant):
    return [f"fieldkit peers link run -- {c}" for c in _check(grant).get("peer", [])]


def _merge(path, block):
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for key, val in block.items():
        cur = data.setdefault(key, {})
        for k, v in val.items():
            if isinstance(v, list):
                cur[k] = sorted(set(cur.get(k, [])) | set(v))
            else:
                cur[k] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def write(grant, folder):
    folder = Path(folder)
    return [_merge(folder / ".claude" / "settings.local.json", claude_settings(grant)),
            _merge(folder / "opencode.json", opencode_config(grant))]
