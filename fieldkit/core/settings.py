"""Load JSON, YAML or TOML settings, with variables instead of personal paths.

Pipelines and profiles that are shared (or published) must not contain
C:\\Users\\<name> or /home/<name>. They write ${HOME}, ${DOCUMENTS},
${FIELDKIT}, ${ENV:NAME} or ${LOCAL:key} instead, and this module expands
them on the machine that runs them. ${LOCAL:key} reads fieldkit.local.json
(or .yaml), a per-machine file that is git-ignored and never published.
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the Fieldkit folder
LOCAL_FILES = ("fieldkit.local.json", "fieldkit.local.yaml")
_VAR = re.compile(r"\$\{([A-Z]+)(?::([^}]+))?\}")


class SettingsError(ValueError):
    pass


def read_file(path):
    """Parse .json / .yaml / .yml / .toml into Python data."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    ext = path.suffix.lower()
    if ext == ".json":
        return json.loads(text)
    if ext in (".yaml", ".yml"):
        import yaml
        return yaml.safe_load(text)
    if ext == ".toml":
        import tomllib
        return tomllib.loads(text)
    raise SettingsError(f"{path}: unsupported settings format {ext}")


def local_settings():
    for name in LOCAL_FILES:
        p = ROOT / name
        if p.is_file():
            return read_file(p) or {}
    return {}


def _documents():
    home = Path.home()
    for cand in (home / "Documents", home / "documents"):
        if cand.is_dir():
            return cand
    return home


def expand(value, local=None, strict=True):
    """Expand ${...} variables in strings, recursively through lists/dicts."""
    if isinstance(value, dict):
        return {k: expand(v, local, strict) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, local, strict) for v in value]
    if not isinstance(value, str):
        return value
    local = local_settings() if local is None else local
    depth = [0]

    def sub(m):
        depth[0] += 1
        kind, arg = m.group(1), m.group(2)
        if kind == "HOME":
            return str(Path.home())
        if kind == "DOCUMENTS":
            return str(_documents())
        if kind == "FIELDKIT":
            return str(ROOT)
        if kind == "ENV":
            if arg in os.environ:
                return os.environ[arg]
        elif kind == "LOCAL":
            cur = local
            for part in arg.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is not None:
                # a local setting may use variables itself ("${DOCUMENTS}/Vault"): expand it too
                return expand(str(cur), local, strict) if "${" in str(cur) and depth[0] < 5 else str(cur)
        if strict:
            raise SettingsError(f"cannot expand ${{{kind}{':' + arg if arg else ''}}}")
        return m.group(0)

    return _VAR.sub(sub, value)


def load(path, strict=True):
    return expand(read_file(path), strict=strict)
