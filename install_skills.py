"""install_skills.py - copy Fieldkit's skills where coding agents look for them.

    python install_skills.py            install/update
    python install_skills.py --check    report what is installed, change nothing
    python install_skills.py --remove   remove the fieldkit-* and gorilla-* skills it installed

Targets (only those whose parent folder exists, plus ~/.claude/skills):
    ~/.claude/skills   Claude Code
    ~/.agents/skills   Codex, Gemini CLI (the shared .agents alias)
    ~/.gemini/skills   Gemini CLI

Only folders named fieldkit-* or gorilla-* are ever written or removed. A copy is updated
only when its content differs, so running this twice changes nothing.
"""
import argparse
import filecmp
import shutil
from pathlib import Path

SRC = Path(__file__).resolve().parent / "skills"
HOME = Path.home()
TARGETS = [HOME / ".claude" / "skills", HOME / ".agents" / "skills", HOME / ".gemini" / "skills"]


def targets():
    out = [TARGETS[0]]
    out += [t for t in TARGETS[1:] if t.parent.is_dir()]
    return out


def same(a, b):
    if not b.is_dir():
        return False
    cmp = filecmp.dircmp(a, b)
    return not (cmp.left_only or cmp.right_only or cmp.diff_files)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args(argv)
    skills = sorted(p for p in SRC.iterdir() if p.is_dir() and p.name.startswith(("fieldkit-", "gorilla-")))
    for t in targets():
        for s in skills:
            dest = t / s.name
            if a.remove:
                if dest.is_dir():
                    shutil.rmtree(dest)
                    print(f"removed   {dest}")
                continue
            if same(s, dest):
                print(f"current   {dest}")
            elif a.check:
                print(f"{'outdated' if dest.exists() else 'missing'}  {dest}")
            else:
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(s, dest)
                print(f"installed {dest}")


if __name__ == "__main__":
    main()
