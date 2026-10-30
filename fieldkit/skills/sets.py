"""fieldkit skills sets: a skill set per project, as data (fieldkit/skills/skillsets.yaml), written into Claude Code's
own skillOverrides setting, plus the user settings this machine never uses (O1).

    fieldkit skills sets              what would change, changing nothing (exit 1 when something would)
    fieldkit skills sets --apply      write it
    fieldkit skills sets --json

Writes only these keys: skillOverrides, permissions.deny (adds, never removes) and the keys under user_settings in
~/.claude/settings.json; skillOverrides in <project>/.claude/settings.local.json. Overrides the data does not name
are kept. The hooks section is never touched: a write that would change its text is refused. A project's
settings.local.json is kept out of git through the repository's .git/info/exclude (local, never published).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

DATA = Path(__file__).with_name("skillsets.yaml")


def load(path=DATA):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def own_skills():
    """Fieldkit's own skill folders for this OS (the installer's view: skills/ and local/skills/)."""
    from . import _installer
    return [s.name for s in _installer().sources()[0]]


def user_overrides(data, own):
    """{skill: outside state} for every known skill not in `everywhere`."""
    keep = set(data.get("everywhere") or [])
    known = list(dict.fromkeys(list(own) + list(data.get("others") or [])))
    return {s: data.get("outside", "user-invocable-only") for s in known if s not in keep}


def project_overrides(data, name):
    return {s: "on" for s in (data.get("projects") or {}).get(name) or []}


def find_projects(data, home=None):
    """{project name: folder} for every project of the data found under the roots on this machine."""
    home = str(home or Path.home())
    out = {}
    for name in data.get("projects") or {}:
        for root in data.get("roots") or []:
            d = Path(root.replace("~", home, 1) if root.startswith("~") else root) / name
            if d.is_dir():
                out[name] = d
                break
    return out


def _read(path):
    """-> (dict, raw text or '')."""
    try:
        raw = Path(path).read_bytes().decode("utf-8")   # bytes: CRLF stays CRLF (read_text would make it LF)
    except FileNotFoundError:
        return {}, ""
    return (json.loads(raw) if raw.strip() else {}), raw


def _hooks_text(raw):
    """The hooks section's text as written (from its key to the next top-level key), '' when absent."""
    m = re.search(r'\n(?P<i>[ \t]*)"hooks"\s*:', raw)
    if not m:
        return ""
    nxt = re.compile(r'\r?\n' + re.escape(m.group("i")) + r'"[^"]+"\s*:|\r?\n\}')
    end = nxt.search(raw, m.end())
    return raw[m.start(): end.start() if end else len(raw)].rstrip(",\r\n ")


def _dump(d, raw):
    """JSON in the file's own style: 2-space indent, its newline, its trailing newline or none."""
    nl = "\r\n" if "\r\n" in raw else "\n"
    text = json.dumps(d, indent=2, ensure_ascii=False).replace("\n", nl)
    return text + (nl if raw.endswith(("\n",)) or not raw else "")


def merged_user(cur, data, own):
    new = json.loads(json.dumps(cur))
    for k, v in (data.get("user_settings") or {}).items():
        new[k] = v
    ov = dict(new.get("skillOverrides") or {})
    ov.update(user_overrides(data, own))
    new["skillOverrides"] = ov
    deny = list((new.get("permissions") or {}).get("deny") or [])
    deny += [t for t in data.get("deny_tools") or [] if t not in deny]
    if deny:
        new.setdefault("permissions", {})["deny"] = deny
    return new


def merged_project(cur, data, name):
    new = json.loads(json.dumps(cur))
    ov = dict(new.get("skillOverrides") or {})
    ov.update(project_overrides(data, name))
    new["skillOverrides"] = ov
    return new


def _exclude(folder):
    """Keep .claude/settings.local.json out of git in the repository holding folder -> what was done."""
    rel = ".claude/settings.local.json"
    if subprocess.run(["git", "-C", str(folder), "rev-parse", "--git-dir"], capture_output=True).returncode:
        return "not a git repository"
    if subprocess.run(["git", "-C", str(folder), "check-ignore", "-q", rel], capture_output=True).returncode == 0:
        return "already ignored"
    r = subprocess.run(["git", "-C", str(folder), "rev-parse", "--git-path", "info/exclude"], capture_output=True,
                       text=True)
    ex = Path(r.stdout.strip())
    if not ex.is_absolute():
        ex = Path(folder) / ex
    ex.parent.mkdir(parents=True, exist_ok=True)
    old = ex.read_text(encoding="utf-8") if ex.is_file() else ""
    ex.write_text(old + ("" if not old or old.endswith("\n") else "\n") + "/" + rel + "\n", encoding="utf-8")
    return f"added to {ex}"


def plan(data, home=None, own=None):
    """[(path, current dict, new dict, raw text)] for every settings file the data covers on this machine."""
    home = Path(home or Path.home())
    own = own_skills() if own is None else own
    out = []
    user = home / ".claude" / "settings.json"
    cur, raw = _read(user)
    out.append((user, cur, merged_user(cur, data, own), raw))
    for name, folder in find_projects(data, home).items():
        f = folder / ".claude" / "settings.local.json"
        cur, raw = _read(f)
        out.append((f, cur, merged_project(cur, data, name), raw))
    return out


def apply(rows):
    """Write every changed file -> [(path, what)]; refuses a write that changes the hooks section's text."""
    done = []
    for path, cur, new, raw in rows:
        if new == cur:
            done.append((path, "current"))
            continue
        text = _dump(new, raw)
        if raw and _hooks_text(raw) != _hooks_text(text):
            done.append((path, "REFUSED: the write would change the hooks section"))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".fieldkit-tmp")
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, path)
        what = "written"
        if path.name == "settings.local.json":
            what += "; " + _exclude(path.parent.parent)
        done.append((path, what))
    return done


def changes(cur, new):
    """Short lines naming what differs."""
    out = []
    for k in sorted(set(cur) | set(new)):
        if cur.get(k) == new.get(k):
            continue
        if k == "skillOverrides":
            a, b = cur.get(k) or {}, new.get(k) or {}
            diff = sorted(s for s in set(a) | set(b) if a.get(s) != b.get(s))
            out.append(f"skillOverrides: {len(diff)} skill(s) -> " + ", ".join(f"{s}={b.get(s)}" for s in diff[:6])
                       + (" ..." if len(diff) > 6 else ""))
        elif k == "permissions":
            added = [t for t in (new.get(k) or {}).get("deny") or [] if t not in (cur.get(k) or {}).get("deny") or []]
            out.append("permissions.deny += " + ", ".join(added))
        else:
            out.append(f"{k}: {cur.get(k)!r} -> {new.get(k)!r}")
    return out


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="fieldkit skills sets", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="write the settings")
    p.add_argument("--json", action="store_true")
    p.add_argument("--data", default=str(DATA), help=argparse.SUPPRESS)
    p.add_argument("--home", help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    rows = plan(load(a.data), home=a.home)
    pending = [(path, changes(cur, new)) for path, cur, new, _ in rows if cur != new]
    done = apply(rows) if a.apply else []
    if a.json:
        print(json.dumps({"files": [{"path": str(pth), "changes": ch} for pth, ch in pending],
                          "applied": [{"path": str(pth), "what": w} for pth, w in done]}))
    else:
        for path, ch in pending:
            print(f"{'apply' if a.apply else 'would'}  {path}")
            for line in ch:
                print(f"    {line}")
        for path, w in done:
            print(f"{w:<9} {path}" if w == "current" else f"  -> {w}")
        if not pending:
            print("current: every settings file matches fieldkit/skills/skillsets.yaml")
        print("NEXT: fieldkit cost firstcall --parts (measure the first call)" if a.apply or not pending
              else "NEXT: fieldkit skills sets --apply")
    refused = any(w.startswith("REFUSED") for _, w in done)
    return 1 if refused or (pending and not a.apply) else 0


if __name__ == "__main__":
    raise SystemExit(main())
