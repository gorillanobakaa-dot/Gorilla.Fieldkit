# Version: 1.0.0 · updated 26-11-23-12-30
"""Agent permission audit: find the allow-everything settings of the coding agents and tighten them (owner, 2026-11-23).

    fieldkit agentperms audit [--home DIR] [--json]     list every finding; exit 3 when there is one
    fieldkit agentperms fix [--home DIR] [--apply]       show (or, with --apply, write) the tightened file; a copy
                                                         `FILE.fieldkit-bak` is kept next to it first

Looks at: Kiro (`~/.kiro/**/permissions.yaml`: a rule `capability: all, effect: allow`, or policy `auto-approve`),
IBM Bob (`~/.bob/settings/settings.json`), Antigravity (`~/.gemini/**/settings.json`: `trustedWorkspaces` holding the
home folder or a drive root), Gorilla OpenCode (`opencode.json` in the home, `~/.config/opencode*`, `%APPDATA%`).
The fix sets allow-everything to ask; trusted folders that are the whole home or a drive are dropped (add the project
folders you mean). Keys that look like credentials are never read or changed. Only the files above are touched;
nothing is deleted. The Kiro fix uses only `capability: all` + `effect: ask`.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

SECRET_WORDS = ("key", "token", "secret", "password")
FLAGS = ("yolo", "autoapprove", "auto_approve", "alwaysallow", "bypasspermissions")


def _read(p):
    return Path(p).read_text(encoding="utf-8-sig")


def kiro_files(home):
    base = Path(home, ".kiro")
    return sorted(base.rglob("permissions.yaml")) if base.is_dir() else []


def audit_kiro(p):
    try:
        d = yaml.safe_load(_read(p)) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [f"{p}: unreadable ({exc})"]
    out = []
    for r in d.get("rules") or []:
        if isinstance(r, dict) and r.get("capability") == "all" and r.get("effect") == "allow":
            out.append(f"{p}: rule allows every capability without asking")
    if "auto-approve" in (d.get("policies") or []):
        out.append(f"{p}: policy auto-approve")
    return out


def fix_kiro():
    return "rules:\n  - capability: all\n    effect: ask\npolicies: []\n"


def _is_broad(path, home):
    s = str(path).replace("\\", "/").rstrip("/").lower()
    h = str(home).replace("\\", "/").rstrip("/").lower()
    return s in (h, "") or (len(s) == 2 and s[1] == ":")


def json_files(home):
    home = Path(home)
    c = [home / ".bob" / "settings" / "settings.json", home / "opencode.json"]
    if (home / ".gemini").is_dir():
        c += list((home / ".gemini").rglob("settings.json"))
    if (home / ".config").is_dir():
        c += list((home / ".config").glob("*opencode*/opencode.json*"))
    appdata = os.environ.get("APPDATA")
    if appdata and Path(appdata).is_dir():
        c += list(Path(appdata).glob("*opencode*/opencode.json*"))
    return [p for p in c if p.is_file()]


def _secret(path):
    low = path.lower()
    return any(x in low for x in SECRET_WORDS)


def _walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node


def audit_json(p, home):
    try:
        d = json.loads(_read(p))
    except (OSError, ValueError) as exc:
        return [f"{p}: unreadable ({exc})"]
    out = []
    for path, v in _walk(d):
        if _secret(path):
            continue
        low = path.lower()
        leaf = low.split(".")[-1]
        if "permission" in low and v in ("allow", "always", True):
            out.append(f"{p}: {path} = {v!r} lets the agent act without asking")
        elif leaf in FLAGS and v is True:
            out.append(f"{p}: {path} = true")
        elif "trustedworkspaces" in low and isinstance(v, str) and _is_broad(v, home):
            out.append(f"{p}: trusted folder {v} is the whole home or a drive")
    return out


def fix_json(p, home):
    d = json.loads(_read(p))

    def go(node, path=""):
        if isinstance(node, dict):
            for k in list(node):
                sub = f"{path}.{k}" if path else k
                if _secret(sub):
                    continue
                v = node[k]
                if isinstance(v, (dict, list)):
                    go(v, sub)
                elif "permission" in sub.lower() and v in ("allow", "always"):
                    node[k] = "ask"
                elif "permission" in sub.lower() and v is True:
                    node[k] = False
                elif k.lower() in FLAGS and v is True:
                    node[k] = False
        elif isinstance(node, list):
            if "trustedworkspaces" in path.lower():
                node[:] = [x for x in node if not (isinstance(x, str) and _is_broad(x, home))]
            for x in node:
                if isinstance(x, (dict, list)):
                    go(x, path)

    go(d)
    return json.dumps(d, indent=2) + "\n"


def collect(home):
    findings, fixes = [], {}
    for p in kiro_files(home):
        f = audit_kiro(p)
        if f:
            findings += f
            fixes[p] = fix_kiro()
    for p in json_files(home):
        f = audit_json(p, home)
        if f:
            findings += f
            try:
                fixes[p] = fix_json(p, home)
            except (OSError, ValueError):
                pass
    return findings, fixes


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit agentperms", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("verb", choices=["audit", "fix"])
    ap.add_argument("--home", default=str(Path.home()))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    findings, fixes = collect(Path(a.home))
    if a.verb == "audit":
        print(json.dumps(findings) if a.json else "\n".join(findings) or "agentperms: no allow-everything setting found")
        return 3 if findings else 0
    for p, text in fixes.items():
        if a.apply:
            shutil.copy2(p, f"{p}.fieldkit-bak")
            Path(p).write_text(text, encoding="utf-8", newline="\n")
            print(f"agentperms: tightened {p} (copy: {p}.fieldkit-bak)")
        else:
            print(f"agentperms: would tighten {p}  (add --apply)")
    if not fixes:
        print("agentperms: nothing to fix")
    return 0


if __name__ == "__main__":
    sys.exit(main())
