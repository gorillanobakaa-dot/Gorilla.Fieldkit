# Version: 1.0.0 · updated 26-10-26
"""fieldkit skills - Agent Skills (agentskills.io) checked, aligned, vendored and installed the same way every time.

Owner, 2026-10-26: "since anthropic donated we are gonna align our standard with theirs for maximum compatibility
and interoperability." The standard went to the Agentic AI Foundation (Linux Foundation) on 2026-10-22; 70+ agent
platforms read it. A skill is prose a model may skip; this tool runs the same every time.

    fieldkit skills validate [PATH...] [--installed] [--staged] [--json]
        every spec rule (fieldkit/skills/spec.py) on every skill folder; default: skills/ and local/skills/ (strict:
        recommendations are errors) and third_party/anthropic-skills/skills/ (recommendations are warnings), plus the
        doors check of third_party/. --installed: also the installed copies (missing, outdated, foreign, orphan).
        --staged: the pre-commit form; does nothing unless a staged path is a skill, the manifest or install_skills.py.
    fieldkit skills doors [PATH] [--json]
        network doors in a skill tree (fieldkit/skills/doors.py); default third_party/anthropic-skills, where every
        door must be accepted in ADOPTION.yaml with a reason (exit 1 otherwise)
    fieldkit skills installed [--json]       the installed copies against the sources, changing nothing
    fieldkit skills align [PATH...] [--apply]
        give Fieldkit's own skills the spec's optional keys: license (the repo's AGPL-3.0-or-later) and compatibility
        (the fieldkit command, and the platforms of the tool cards that point at the skill)
    fieldkit skills vendor UPSTREAM_DIR [--apply]
        rebuild third_party/anthropic-skills/ from an extracted upstream tree at the pinned commit: the listed skills
        only, the manifest's strips applied, CHANGES.md written; then the doors check must pass
    fieldkit skills package | benchmark | eval ...
        the adopted skill-creator's scripts as commands (fieldkit/skills/adopted.py)

Exit codes: 0 clean, 1 findings (errors, unaccepted doors, stale installs, changes still to apply), 2 bad arguments.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from . import doors, spec

ROOT = Path(__file__).resolve().parents[2]
THIRD = ROOT / "third_party" / "anthropic-skills"
MANIFEST = THIRD / "ADOPTION.yaml"
LICENSE = "AGPL-3.0-or-later"
WATCHED = ("skills/", "third_party/anthropic-skills/", "install_skills.py", "fieldkit/skills/")


def _installer():
    sys.path.insert(0, str(ROOT))
    try:
        import install_skills
    finally:
        sys.path.remove(str(ROOT))
    return install_skills


def manifest():
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {}


def default_roots():
    return [(ROOT / "skills", True), (ROOT / "local" / "skills", True), (THIRD / "skills", False)]


def staged_paths(repo=ROOT):
    """The same question fieldkit/testcost.py asks before the suite: what does this commit change?"""
    r = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--name-only"], capture_output=True, text=True)
    return r.stdout.split() if r.returncode == 0 else []


def door_rows(root=THIRD):
    rules = (manifest().get("accepted") or []) if Path(root) == THIRD else []
    rows = []
    for f in doors.scan(root):                         # paths relative to root, as the manifest's rules name them
        rule = doors.accepted(f, rules)
        rows.append({**f, "accepted": rule.get("why") if rule else None})
    return rows


def cmd_validate(a):
    if a.staged and not any(p.startswith(WATCHED) for p in staged_paths()):
        return 0
    roots = [(Path(p), a.strict) for p in a.paths] if a.paths else default_roots()
    report, errors = [], 0
    for root, strict in roots:
        if not root.is_dir():
            continue
        for d in spec.skill_dirs(root):
            for rule, level, msg in spec.validate(d, strict=strict):
                report.append({"skill": str(d.relative_to(ROOT)) if d.is_relative_to(ROOT) else str(d),
                               "rule": rule, "level": level, "message": msg})
                errors += level == "error"
    unaccepted = []
    if not a.paths and THIRD.is_dir():
        unaccepted = [r for r in door_rows() if r["kind"] == "door" and not r["accepted"]]
    stale = []
    if a.installed:
        stale = [{"target": str(t), "name": n, "state": s} for t, n, s in _installer().status()
                 if s not in ("current",)]
    if a.json:
        print(json.dumps({"findings": report, "unaccepted_doors": unaccepted, "installed": stale}, indent=1))
    else:
        for r in report:
            print(f"{r['level'].upper():<5} {r['skill']}: [{r['rule']}] {r['message']}")
        for r in unaccepted:
            print(f"DOOR  third_party/anthropic-skills/{r['file']}:{r['line']} {r['what']} {r['match']}")
        for r in stale:
            print(f"{r['state'].upper():<8} {Path(r['target']) / r['name']}")
        n = sum(len(spec.skill_dirs(r)) for r, _ in roots if r.is_dir())
        print(f"SKILLS: {n} checked, {errors} error(s), {len(report) - errors} warning(s), "
              f"{len(unaccepted)} unaccepted door(s)" + (f", {len(stale)} install row(s) not current" if a.installed else ""))
    bad_install = [r for r in stale if r["state"] in ("missing", "outdated")]
    return 1 if errors or unaccepted or bad_install else 0


def cmd_doors(a):
    root = Path(a.path) if a.path else THIRD
    rows = door_rows(root)
    if a.json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            tag = "accepted" if r["accepted"] else ("prose" if r["kind"] == "prose" else "DOOR")
            print(f"{tag:<8} {r['file']}:{r['line']} {r['what']} {r['match']}"
                  + (f"  -- {r['accepted']}" if r["accepted"] else ""))
        bad = sum(r["kind"] == "door" and not r["accepted"] for r in rows)
        print(f"DOORS: {len(rows)} finding(s), {bad} unaccepted door(s) in code")
    return 1 if any(r["kind"] == "door" and not r["accepted"] for r in rows) else 0


def cmd_installed(a):
    rows = _installer().status()
    if a.json:
        print(json.dumps([{"target": str(t), "name": n, "state": s} for t, n, s in rows], indent=1))
    else:
        for t, n, s in rows:
            print(f"{s:<9} {t / n}")
    return 1 if any(s in ("missing", "outdated") for _, _, s in rows) else 0


def _card_platforms():
    """skill folder name -> sorted platforms, from every tool card whose skill: points at it."""
    out = {}
    for f in [ROOT / "fieldkit" / "desk" / "tools.yaml", ROOT / "local" / "tools.yaml"]:
        if not f.is_file():
            continue
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for card in (data.get("tools") or []) if isinstance(data, dict) else data:
            if isinstance(card, dict) and card.get("skill") and card.get("platforms"):
                out.setdefault(Path(card["skill"]).parent.name, set()).update(card["platforms"])
    order = list(PLATFORM)
    return {k: sorted(v, key=lambda p: (order.index(p) if p in order else len(order), p)) for k, v in out.items()}


PLATFORM = {"windows": "Windows", "linux": "Linux", "darwin": "macOS"}
OURS = "Needs the fieldkit command (Gorilla Fieldkit"


def compatibility(name, platforms):
    plats = platforms.get(name)
    where = (" on " + ", ".join(PLATFORM.get(p, p) for p in plats)) if plats else ""
    return (f"Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; "
            f"Python 3.11+ with PyYAML){where}. Works offline; no network access unless a step says so.")


def align_text(text, name, platforms):
    """text with license and compatibility added after the frontmatter's last key; None when nothing changes."""
    fm, body = spec.split_frontmatter(text)
    meta = yaml.safe_load(fm) or {}
    nl = "\r\n" if "\r\n" in text else "\n"
    want = "compatibility: " + json.dumps(compatibility(name, platforms), ensure_ascii=False)
    old = str(meta.get("compatibility", ""))
    if old.startswith(OURS) and old != compatibility(name, platforms):
        # a compatibility line this tool wrote, now stale (the cards' platforms changed): rewritten, never a human's
        fm = "".join(want + nl if ln.startswith("compatibility: ") else ln for ln in fm.splitlines(keepends=True))
        meta = yaml.safe_load(fm)
    add = []
    if "license" not in meta:
        add.append(f"license: {LICENSE}")
    if "compatibility" not in meta:
        add.append(want)
    if not add and fm == spec.split_frontmatter(text)[0]:
        return None
    fm2 = fm.rstrip("\r\n") + nl + "".join(x + nl for x in add)
    out = "---" + nl + fm2 + "---" + nl + body
    assert yaml.safe_load(fm2)["name"] == meta["name"]
    return out


def cmd_align(a):
    roots = [Path(p) for p in a.paths] if a.paths else [ROOT / "skills", ROOT / "local" / "skills"]
    plats, todo = _card_platforms(), 0
    for root in roots:
        if not root.is_dir():
            continue
        for d in spec.skill_dirs(root):
            md = spec.find_skill_md(d)
            if md is None:
                continue
            raw = md.read_bytes().decode("utf-8")
            try:
                new = align_text(raw, d.name, plats)
            except (ValueError, yaml.YAMLError) as e:
                print(f"SKIP    {d.name}: {str(e).splitlines()[0]} (fix the frontmatter first)")
                todo += 1
                continue
            if new is None:
                continue
            todo += not a.apply
            if a.apply:
                md.write_bytes(new.encode("utf-8"))
            print(f"{'aligned' if a.apply else 'would align'} {d.name}")
    return 1 if todo else 0


def apply_strip(dest, rule):
    """One manifest strip on the vendored copy: remove a file, delete matching lines, or replace a literal."""
    p = dest / rule["file"]
    if rule.get("remove"):
        if p.exists():
            p.unlink()
        return f"removed {rule['file']}"
    text = p.read_bytes().decode("utf-8")
    if rule.get("delete_lines"):
        rx = re.compile(rule["delete_lines"])
        lines = text.splitlines(keepends=True)
        kept = [ln for ln in lines if not rx.search(ln)]
        if len(kept) == len(lines):
            raise ValueError(f"strip matched nothing in {rule['file']}: {rule['delete_lines']}")
        text = "".join(kept)
    if rule.get("replace"):
        old, new = rule["replace"]
        if old not in text:
            raise ValueError(f"strip matched nothing in {rule['file']}: {old!r}")
        text = text.replace(old, new)
    p.write_bytes(text.encode("utf-8"))
    return f"changed {rule['file']}"


def cmd_vendor(a):
    m = manifest()
    up = Path(a.upstream)
    if not (up / "skills").is_dir():
        print(f"vendor: {up} is not an extracted anthropics/skills tree (no skills/ folder)")
        return 2
    names = sorted(m.get("skills") or {})
    missing = [n for n in names if not (up / "skills" / n / "SKILL.md").is_file()]
    if missing:
        print("vendor: not in the upstream tree: " + ", ".join(missing))
        return 1
    if not a.apply:
        for n in names:
            print(f"would vendor skills/{n}")
        print(f"would apply {len(m.get('strip') or [])} strip(s); run again with --apply")
        return 1
    target = THIRD / "skills"
    if target.exists():
        shutil.rmtree(target)
    for n in names:
        shutil.copytree(up / "skills" / n, target / n)
    for f in m.get("keep_files") or []:
        shutil.copy2(up / f, THIRD / f)
    log = [apply_strip(THIRD, r) + f" -- {r['why']}" for r in m.get("strip") or []]
    (THIRD / "CHANGES.md").write_bytes((                # bytes: LF on every OS (write_text makes CRLF on Windows)
        "# Changes from upstream (Apache-2.0 section 4(b))\n\n"
        f"Upstream {m.get('upstream')} at commit {m.get('commit')}. Files changed by `fieldkit skills vendor` "
        "from the strips in ADOPTION.yaml:\n\n" + "".join(f"- {x}\n" for x in log)).encode("utf-8"))
    for x in log:
        print(x)
    return cmd_doors(argparse.Namespace(path=None, json=False))


def build_parser():
    ap = argparse.ArgumentParser(prog="fieldkit skills", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("paths", nargs="*")
    v.add_argument("--strict", action="store_true", help="with PATHs: recommendations are errors")
    v.add_argument("--installed", action="store_true")
    v.add_argument("--staged", action="store_true")
    v.add_argument("--json", action="store_true")
    v.set_defaults(fn=cmd_validate)
    d = sub.add_parser("doors")
    d.add_argument("path", nargs="?")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_doors)
    i = sub.add_parser("installed")
    i.add_argument("--json", action="store_true")
    i.set_defaults(fn=cmd_installed)
    al = sub.add_parser("align")
    al.add_argument("paths", nargs="*")
    al.add_argument("--apply", action="store_true")
    al.set_defaults(fn=cmd_align)
    ve = sub.add_parser("vendor")
    ve.add_argument("upstream")
    ve.add_argument("--apply", action="store_true")
    ve.set_defaults(fn=cmd_vendor)
    return ap


PASS_THROUGH = {"package": "package", "benchmark": "benchmark", "eval": "run_eval"}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] and argv[0] in PASS_THROUGH:      # the adopted skill-creator scripts parse their own arguments
        from . import adopted
        return getattr(adopted, PASS_THROUGH[argv[0]])(argv[1:])
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
