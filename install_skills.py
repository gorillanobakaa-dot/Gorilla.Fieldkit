"""install_skills.py - copy Fieldkit's skills where coding agents look for them.

    python install_skills.py            install/update
    python install_skills.py --check    report what is installed, change nothing
    python install_skills.py --remove   remove the skills it installed

Targets (only those whose parent folder exists, plus ~/.claude/skills):
    ~/.claude/skills   Claude Code
    ~/.agents/skills   Codex, Gemini CLI, and the other agents that read the shared .agents alias
    ~/.gemini/skills   Gemini CLI

Every skill is an Agent Skills folder (agentskills.io, `fieldkit skills validate`), so every platform that reads the
standard gets the same set. Three sources:
    skills/                         Fieldkit's own (fieldkit-*, gorilla-*)
    local/skills/                   private skills (the owner's own machines and procedures); git ignores local/
    third_party/anthropic-skills/   adopted third-party skills, only those with `install: true` in its ADOPTION.yaml

Only folders named fieldkit-* or gorilla-*, or a third-party name this script installed before (listed in the
target's .fieldkit-skills.json receipt), are ever written or removed; a third-party name already taken by someone
else's folder is reported as "foreign" and left alone. A copy is updated only when its content differs, so running
this twice changes nothing.
"""
import argparse
import filecmp
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "skills"
LOCAL_SRC = ROOT / "local" / "skills"
THIRD = ROOT / "third_party" / "anthropic-skills"
HOME = Path.home()
TARGETS = [HOME / ".claude" / "skills", HOME / ".agents" / "skills", HOME / ".gemini" / "skills"]
RECEIPT = ".fieldkit-skills.json"
OWN = ("fieldkit-", "gorilla-")


def targets():
    out = [TARGETS[0]]
    out += [t for t in TARGETS[1:] if t.parent.is_dir()]
    return out


def same(a, b):
    """True when folder b holds exactly what a holds, at every depth."""
    if not b.is_dir():
        return False
    cmp = filecmp.dircmp(a, b, ignore=["__pycache__"])
    if cmp.left_only or cmp.right_only or cmp.common_funny or cmp.funny_files:
        return False
    # bytes, not filecmp: it decides by (size, mtime) and caches answers by them, so an edit of the same size within
    # one second of the last comparison read as "same" (found by tests/test_skills.py)
    if any((a / f).read_bytes() != (b / f).read_bytes() for f in cmp.common_files):
        return False
    return all(same(a / d, b / d) for d in cmp.common_dirs)


def _adoption():
    man = THIRD / "ADOPTION.yaml"
    if not man.is_file():
        return {}
    import yaml
    return (yaml.safe_load(man.read_text(encoding="utf-8")) or {}).get("skills") or {}


def third_party():
    """Third-party skill folders marked install: true in ADOPTION.yaml (none when the manifest is absent)."""
    return [THIRD / "skills" / n for n, v in _adoption().items()
            if (v or {}).get("install") and (THIRD / "skills" / n / "SKILL.md").is_file()]


def wanted(t, s):
    """False when the manifest's skip_targets keeps skill folder s out of target t (e.g. claude-api out of
    ~/.claude/skills: Claude Code ships its own skill of that name)."""
    skip = (_adoption().get(s.name) or {}).get("skip_targets") or [] if s.parent.parent == THIRD else []
    return t.parent.name not in skip


def sources():
    own = [p for src in (SRC, LOCAL_SRC) if src.is_dir() for p in src.iterdir()
           if p.is_dir() and p.name.startswith(OWN)]
    return sorted(own, key=lambda p: p.name), sorted(third_party(), key=lambda p: p.name)


def _receipt(t):
    try:
        return set(json.loads((t / RECEIPT).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _write_receipt(t, names):
    if names:
        (t / RECEIPT).write_text(json.dumps(sorted(names), indent=1) + "\n", encoding="utf-8")
    elif (t / RECEIPT).exists():
        (t / RECEIPT).unlink()


def status():
    """[(target, name, state)] with state current | outdated | missing | foreign | orphan, changing nothing.
    orphan: a fieldkit-*/gorilla-* folder, or a receipt name, installed here with no source any more."""
    own, third = sources()
    names = {s.name for s in own + third}
    rows = []
    for t in targets():
        mine = _receipt(t)
        for s in own + third:
            dest = t / s.name
            if not wanted(t, s):
                continue
            if same(s, dest):
                rows.append((t, s.name, "current"))
            elif s in third and dest.exists() and s.name not in mine:
                rows.append((t, s.name, "foreign"))
            else:
                rows.append((t, s.name, "outdated" if dest.exists() else "missing"))
        if t.is_dir():
            here = {s.name for s in own + third if wanted(t, s)}
            for p in sorted(t.iterdir()):
                if p.is_dir() and ((p.name not in names and p.name.startswith(OWN)) or p.name in mine - here):
                    rows.append((t, p.name, "orphan"))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args(argv)
    if a.check:
        rows = status()
        for t, name, state in rows:
            print(f"{state:<9} {t / name}")
        return 1 if any(s in ("outdated", "missing") for _, _, s in rows) else 0
    own, third = sources()
    for t in targets():
        mine = _receipt(t)
        if a.remove:
            for s in own:
                if (t / s.name).is_dir():
                    shutil.rmtree(t / s.name)
                    print(f"removed   {t / s.name}")
            for n in sorted(mine):
                if (t / n).is_dir():
                    shutil.rmtree(t / n)
                    print(f"removed   {t / n}")
            _write_receipt(t, set())
            continue
        third_here = [s for s in third if wanted(t, s)]
        for s in own + third_here:
            dest = t / s.name
            if same(s, dest):
                print(f"current   {dest}")
                if s in third:
                    mine.add(s.name)
                continue
            if s in third and dest.exists() and s.name not in mine:
                print(f"foreign   {dest} (someone else's folder has this name: left alone)")
                continue
            t.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(s, dest, ignore=shutil.ignore_patterns("__pycache__"))
            if s in third:
                mine.add(s.name)
            print(f"installed {dest}")
        # a third-party skill no longer marked install: true is taken back out
        for n in sorted(mine - {s.name for s in third_here}):
            if (t / n).is_dir():
                shutil.rmtree(t / n)
                print(f"removed   {t / n} (no longer adopted)")
            mine.discard(n)
        _write_receipt(t, mine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
