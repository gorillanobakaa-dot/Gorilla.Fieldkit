"""Read-only inspection of externally captured tool cards.

External cards are data, not authorization: this module never imports, probes,
tests, or runs the referenced tools. A candidate remains non-runnable until it
is reviewed and registered through Fieldkit's normal local-tool workflow.
"""
import fnmatch
import json
import os
import re
from pathlib import Path

from .cards import EFFECTS

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_INPUT_TYPES = {"str", "int", "float", "path", "flag", "list"}
_SAFETY = {"read-only", "reversible", "irreversible", "unknown"}
_PLATFORMS = {"windows", "linux", "darwin"}
_WORKFLOW_NAMES = ("workflow*.yaml", "workflow*.yml", "*.workflow.yaml", "*.workflow.yml")
MAX_CARD_BYTES = 1_048_576
MAX_CARDS = 500
MAX_FILES = 10_000
MAX_DIRECTORIES = 2_000


def _is_link(path):
    try:
        return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
    except OSError:
        return True


def _scan(root):
    cards, workflows = [], []
    seen_files = seen_dirs = 0
    limited = False
    for current, dirs, names in os.walk(root, followlinks=False):
        here = Path(current)
        dirs[:] = sorted(d for d in dirs if not _is_link(here / d))
        seen_dirs += 1
        if seen_dirs > MAX_DIRECTORIES:
            limited = True
            break
        for name in sorted(names):
            seen_files += 1
            if seen_files > MAX_FILES:
                limited = True
                break
            path = here / name
            if _is_link(path):
                continue
            lower = name.lower()
            if lower.endswith(".card.json"):
                if len(cards) >= MAX_CARDS:
                    limited = True
                    break
                cards.append(path)
            if any(fnmatch.fnmatchcase(lower, pattern) for pattern in _WORKFLOW_NAMES):
                workflows.append(path)
        if limited:
            break
    return sorted(cards), sorted(workflows), limited


def _inside(path, root):
    try:
        return path == root or root in path.parents
    except (OSError, ValueError):
        return False


def _member(value, base, root, label, issues):
    if not isinstance(value, str) or not value:
        issues.append(f"{label} is missing")
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        issues.append(f"{label} does not exist")
        return None
    if not _inside(resolved, root):
        issues.append(f"{label} resolves outside the catalog root")
        return None
    if candidate.is_symlink() or not resolved.is_file():
        issues.append(f"{label} is not a regular, non-symlink file")
        return None
    return resolved


def _card(path, root):
    issues = []
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_CARD_BYTES + 1)
        if len(raw) > MAX_CARD_BYTES:
            raise ValueError("card is larger than 1 MiB")
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return {"id": path.stem, "title": "", "status": "invalid",
                "issues": [f"card JSON cannot be read ({type(exc).__name__})"],
                "runnable": False}
    if not isinstance(data, dict):
        return {"id": path.stem, "title": "", "status": "invalid",
                "issues": ["card must be a JSON object"], "runnable": False}

    card_id = data.get("id")
    title = data.get("title")
    if not isinstance(card_id, str) or not _ID.fullmatch(card_id):
        issues.append("id must be lowercase letters, digits, and hyphens (max 64)")
    if not isinstance(title, str) or not title.strip():
        issues.append("title must be a non-empty string")
    if data.get("safety") not in _SAFETY:
        issues.append("safety must use Fieldkit's read-only/reversible/irreversible/unknown values")
    if not isinstance(data.get("draft"), bool) or not isinstance(data.get("reviewed"), bool):
        issues.append("draft and reviewed must be explicit booleans")

    script = _member(data.get("path"), path.parent, root, "path", issues)
    entry = data.get("entry")
    if not isinstance(entry, list) or len(entry) != 2 or entry[0] not in ("python", "python3"):
        issues.append("entry must be exactly [python, script.py] or [python3, script.py]")
    else:
        entry_script = _member(entry[1], path.parent, root, "entry script", issues)
        if script is not None and entry_script is not None and script != entry_script:
            issues.append("entry script and card path do not refer to the same file")
        if entry_script is not None and entry_script.suffix.lower() != ".py":
            issues.append("entry script must be Python")

    effects = data.get("effects")
    if not isinstance(effects, list) or any(not isinstance(item, str) for item in effects):
        issues.append("effects must be a list of strings")
    else:
        unsupported = sorted(set(effects) - set(EFFECTS))
        if unsupported:
            issues.append("effects outside Fieldkit schema: " + ", ".join(unsupported))

    inputs = data.get("inputs")
    if not isinstance(inputs, list):
        issues.append("inputs must be a list")
    else:
        for index, item in enumerate(inputs):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                issues.append(f"input {index} must be an object with a name")
                continue
            if not isinstance(item.get("type", "str"), str) or item.get("type", "str") not in _INPUT_TYPES:
                issues.append(f"input {item['name']} has an unsupported type")
            flag = item.get("flag")
            if flag is not None and (not isinstance(flag, str) or not flag.startswith("-")):
                issues.append(f"input {item['name']} has a non-option flag; positional inputs require null")

    tests = data.get("tests")
    if not isinstance(tests, list) or not tests:
        issues.append("tests must be a non-empty list")
    else:
        for index, test in enumerate(tests):
            if not isinstance(test, list) or not test or test[0] not in ("python", "python3"):
                issues.append(f"test {index} must be a Python argv list")
                continue
            for value in test[1:]:
                if isinstance(value, str) and value.lower().endswith(".py"):
                    _member(value, path.parent, root, f"test {index} path", issues)

    if not isinstance(data.get("platforms"), list):
        issues.append("platforms must be a list")
    elif any(not isinstance(platform, str) or platform not in _PLATFORMS
             for platform in data["platforms"]):
        issues.append("platforms must use windows, linux, or darwin")
    if not isinstance(data.get("modes"), dict):
        issues.append("modes must be an object")

    return {
        "id": card_id if isinstance(card_id, str) else path.stem,
        "title": title if isinstance(title, str) else "",
        "card": str(path),
        "script": str(script) if script else None,
        "status": "incompatible" if issues else "untrusted",
        "issues": issues,
        "runnable": False,
        "source_draft": data.get("draft"),
        "source_reviewed": data.get("reviewed"),
        "next": "review and register with Fieldkit before any execution",
    }


def inspect(root):
    """Return card metadata and compatibility findings without executing any source."""
    catalog = Path(root).expanduser().resolve(strict=True)
    if not catalog.is_dir():
        raise NotADirectoryError(f"catalog root is not a directory: {catalog}")

    cards = []
    seen = set()
    card_paths, workflow_paths, scan_limited = _scan(catalog)
    for path in card_paths:
        try:
            resolved_card = path.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            resolved_card = None
        if _is_link(path) or resolved_card is None or not _inside(resolved_card, catalog):
            cards.append({"id": path.stem, "title": "", "status": "invalid",
                          "issues": ["card file is not a regular catalog file"], "runnable": False})
            continue
        row = _card(path, catalog)
        if row["id"] in seen:
            row["issues"].append("duplicate card id")
            row["status"] = "incompatible"
        seen.add(row["id"])
        cards.append(row)

    workflows = [{"path": str(path), "status": "unrecognized", "runnable": False,
                  "issues": ["no Fieldkit workflow schema is registered for external files"]}
                 for path in workflow_paths]
    return {
        "root": str(catalog),
        "tools": cards,
        "workflows": workflows,
        "scan_limited": scan_limited,
        "counts": {
            "tools": len(cards),
            "compatible_untrusted": sum(row["status"] == "untrusted" for row in cards),
            "incompatible": sum(row["status"] in ("incompatible", "invalid") for row in cards),
            "workflows": len(workflows),
            "runnable": 0,
        },
        "next": ("narrow the catalog directory; the inventory stopped at a safety limit"
                 if scan_limited else
                 "review compatible cards, then register them through Fieldkit; external source is never run by this command"),
    }
