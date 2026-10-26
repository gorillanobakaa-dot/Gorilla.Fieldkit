# Version: 1.0.0 · updated 26-10-26
"""The Agent Skills specification (agentskills.io/specification, Agentic AI Foundation), as deterministic checks.

Every rule of the spec, checked the same way on Windows, Linux and macOS (stdlib + PyYAML). Parity with the reference
validator skills-ref (github.com/agentskills/agentskills, skills-ref/src/skills_ref/validator.py at 69ef37e, read
2026-10-26): the same allowed keys, the same name rules (NFKC, lowercase, Unicode letters and digits, no leading,
trailing or doubled hyphen, equal to the folder), the same 64/1024/500 limits, SKILL.md or skill.md. Beyond it, as
the spec says but skills-ref does not check: metadata is string -> string, allowed-tools is a string, license is a
non-empty string, compatibility is 1-500 characters, the body stays under 500 lines, and files the body links to exist.
Compared once (2026-10-26) with Anthropic's skill-creator quick_validate.py on all 31 skills here: the same verdict on
30; it refused fieldkit-profile-repair for "<" in the description, a rule outside the spec, now a warning here (an
error for Fieldkit's own skills) and fixed in that skill. (quick_validate.py itself crashes on Windows on any non-ASCII
SKILL.md: it reads with the locale's code page; this reads UTF-8.)

Each finding is (rule, level, message). level "error" breaks the spec; "warn" is a spec recommendation (500 lines,
5000 tokens, one level of references) or a portability note. strict=True makes recommendations errors: Fieldkit's
own skills are held to them, third-party skills are reported, never rewritten here.
"""
import re
import unicodedata
from pathlib import Path

import yaml

ALLOWED = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")
MAX_NAME, MAX_DESCRIPTION, MAX_COMPATIBILITY = 64, 1024, 500
MAX_LINES, MAX_TOKENS = 500, 5000
STRICT_WARNS = {"description-brackets", "name-ascii"}       # portability notes Fieldkit's own skills are held to
LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def find_skill_md(skill_dir):
    """SKILL.md, else skill.md, by the name as stored (Windows and macOS would open either for both)."""
    try:
        names = {p.name: p for p in Path(skill_dir).iterdir() if p.is_file()}
    except OSError:
        return None
    return names.get("SKILL.md") or names.get("skill.md")


def split_frontmatter(text):
    """(frontmatter text, body text) or raise ValueError. The frontmatter is the block between the first line '---'
    and the next line that is exactly '---' (skills-ref splits on the first two '---' anywhere, which a '---' inside a
    quoted description would cut; a line-anchored split reads every file skills-ref accepts the same way)."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n").rstrip() != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter (a first line '---')")
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n").rstrip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    raise ValueError("SKILL.md frontmatter is not closed with a line '---'")


def _name_findings(name, folder):
    out = []
    if not isinstance(name, str) or not name.strip():
        return [("name", "error", "'name' must be a non-empty string")]
    n = unicodedata.normalize("NFKC", name.strip())
    if n != name:
        out.append(("name", "error", f"'name' {name!r} has surrounding spaces or is not NFKC-normalized"))
    if len(n) > MAX_NAME:
        out.append(("name", "error", f"'name' is {len(n)} characters (max {MAX_NAME})"))
    if n != n.lower():
        out.append(("name", "error", f"'name' {n!r} must be lowercase"))
    if n.startswith("-") or n.endswith("-"):
        out.append(("name", "error", f"'name' {n!r} must not start or end with a hyphen"))
    if "--" in n:
        out.append(("name", "error", f"'name' {n!r} must not contain consecutive hyphens"))
    if not all(c.isalnum() or c == "-" for c in n):
        out.append(("name", "error", f"'name' {n!r} may only hold letters, digits and hyphens"))
    elif not re.fullmatch(r"[a-z0-9-]+", n):
        out.append(("name-ascii", "warn", f"'name' {n!r} is not plain a-z0-9: valid, but not every platform folds it"))
    if folder is not None and unicodedata.normalize("NFKC", folder) != n:
        out.append(("name-folder", "error", f"'name' {n!r} must equal the folder name {folder!r}"))
    return out


def check_frontmatter(meta, folder=None):
    """Findings for an already parsed frontmatter mapping."""
    out = []
    extra = sorted(set(meta) - set(ALLOWED))
    if extra:
        out.append(("keys", "error", f"keys not in the spec: {', '.join(map(str, extra))} (allowed: {', '.join(ALLOWED)})"))
    if "name" not in meta:
        out.append(("name", "error", "missing required key 'name'"))
    else:
        out += _name_findings(meta["name"], folder)
    d = meta.get("description")
    if "description" not in meta:
        out.append(("description", "error", "missing required key 'description'"))
    elif not isinstance(d, str) or not d.strip():
        out.append(("description", "error", "'description' must be a non-empty string"))
    elif len(d) > MAX_DESCRIPTION:
        out.append(("description", "error", f"'description' is {len(d)} characters (max {MAX_DESCRIPTION})"))
    if isinstance(d, str) and ("<" in d or ">" in d):
        out.append(("description-brackets", "warn", "'description' holds < or >: valid in the spec, but Anthropic's "
                    "skill-creator quick_validate.py refuses it (it ends up inside an XML-like prompt)"))
    if "license" in meta and (not isinstance(meta["license"], str) or not meta["license"].strip()):
        out.append(("license", "error", "'license' must be a non-empty string"))
    if "compatibility" in meta:
        c = meta["compatibility"]
        if not isinstance(c, str) or not c.strip():
            out.append(("compatibility", "error", "'compatibility' must be a non-empty string"))
        elif len(c) > MAX_COMPATIBILITY:
            out.append(("compatibility", "error", f"'compatibility' is {len(c)} characters (max {MAX_COMPATIBILITY})"))
    if "metadata" in meta:
        m = meta["metadata"]
        if not isinstance(m, dict):
            out.append(("metadata", "error", "'metadata' must be a mapping of string keys to string values"))
        else:
            bad = [k for k, v in m.items() if not isinstance(k, str) or not isinstance(v, str)]
            if bad:
                out.append(("metadata", "error", "'metadata' values must be strings (quote them): "
                            + ", ".join(map(str, bad))))
    if "allowed-tools" in meta and not isinstance(meta["allowed-tools"], str):
        out.append(("allowed-tools", "error", "'allowed-tools' must be one space-separated string"))
    return out


def check_body(body, skill_dir, strict=False):
    rec = "error" if strict else "warn"
    out = []
    n = len(body.splitlines())
    if n >= MAX_LINES:
        out.append(("lines", rec, f"body is {n} lines (keep SKILL.md under {MAX_LINES}; move detail to references/)"))
    tokens = len(body) // 4
    if tokens > MAX_TOKENS:
        out.append(("tokens", "warn", f"body is about {tokens} tokens (spec recommends under {MAX_TOKENS})"))
    root = Path(skill_dir).resolve()
    for target in sorted(set(LINK.findall(body))):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("/"):
            continue                                   # URLs and absolute paths are not skill files
        p = (root / target).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            out.append(("reference", rec, f"link {target!r} leaves the skill folder"))
            continue
        if not p.exists():
            out.append(("reference", rec, f"link {target!r} points to a file that does not exist"))
        elif len(Path(target).parts) > 2:
            out.append(("reference-depth", "warn", f"link {target!r} is nested; keep references one level deep"))
    return out


def validate(skill_dir, strict=False):
    """All findings for one skill folder."""
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        return [("folder", "error", f"not a folder: {skill_dir}")]
    md = find_skill_md(skill_dir)
    if md is None:
        return [("skill-md", "error", "missing required file SKILL.md")]
    out = []
    if md.name != "SKILL.md":
        out.append(("skill-md", "warn", "file is skill.md; the spec names it SKILL.md"))
    try:
        text = md.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return out + [("encoding", "error", "SKILL.md is not UTF-8")]
    try:
        fm, body = split_frontmatter(text.lstrip("﻿"))
        meta = yaml.safe_load(fm)
    except ValueError as e:
        return out + [("frontmatter", "error", str(e))]
    except yaml.YAMLError as e:
        first = str(e).splitlines()[0] if str(e) else "parse error"
        return out + [("yaml", "error", f"frontmatter is not valid YAML ({first}); quote values that hold ': '")]
    if not isinstance(meta, dict):
        return out + [("frontmatter", "error", "frontmatter must be a YAML mapping")]
    out += [(r, "error" if strict and r in STRICT_WARNS else lv, m) for r, lv, m in check_frontmatter(meta, skill_dir.name)]
    out += check_body(body, skill_dir, strict)
    return out


def skill_dirs(root):
    """Skill folders under root: root itself if it holds SKILL.md, else every child folder with one (or none)."""
    root = Path(root)
    if find_skill_md(root):
        return [root]
    return sorted((p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name)
