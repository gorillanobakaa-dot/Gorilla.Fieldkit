"""Local runtime proof for the exact dependencies copied by Emergency Repair."""

import hashlib
import json
import os
import platform
import stat
import tempfile
from pathlib import Path

from ..buildh import layout, probe, throwaway
from ..core import settings
from ..visual import allow, probe_js, runtime


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def install_manifest(install_dir):
    def unreadable(error):
        raise error

    files = []
    for folder, dirs, names in os.walk(install_dir, onerror=unreadable):
        ignored = runtime.copy_ignored(folder, dirs + names)
        dirs[:] = sorted(set(dirs) - ignored)
        for name in sorted(set(dirs + names) - ignored):
            path = Path(folder) / name
            metadata = path.lstat()
            mode = metadata.st_mode
            reparse = getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)) or reparse:
                raise OSError("install proof does not reuse a tree with links or special files")
            files.append((path.relative_to(install_dir).as_posix(), stat.S_IMODE(mode),
                          digest(path) if stat.S_ISREG(mode) else None))
    return sorted(files)


def key(install_dir, pages, dprs):
    try:
        modules = (runtime, probe_js, allow, probe, layout, throwaway)
        inputs = {
            "schema": 1, "install": str(install_dir.resolve()), "pages": pages, "dprs": dprs,
            "files": install_manifest(install_dir), "runtime": [digest(Path(m.__file__)) for m in modules],
            "allow": digest(allow.DEFAULT), "host": platform.uname(), "python": platform.python_version(),
            "environment": {name: os.getenv(name) for name in (
                "LANG", "LC_ALL", "LC_CTYPE", "TZ", "DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE",
                "FONTCONFIG_FILE", "FONTCONFIG_PATH", "LD_LIBRARY_PATH", "MOZ_ENABLE_WAYLAND",
                "MOZ_DISABLE_CONTENT_SANDBOX", "MOZ_DISABLE_GPU_SANDBOX", "MOZ_DISABLE_RDD_SANDBOX")},
        }
        return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    except OSError:
        return None


def cache_path(proof_key):
    return settings.state_root() / "state" / "emerge" / "repair-proofs" / f"{proof_key}.json"


def complete(result, pages, dprs):
    items = result.get("items") or []
    if any(not isinstance(item, dict) or item.get("accepted") or any(
            not isinstance(item.get(k), str) for k in ("rule", "item", "verdict", "evidence")) for item in items):
        return False
    passed = {item["item"] for item in items if item["verdict"] == "PASS"}
    gate = {item["item"] for item in items if item["rule"] == "RT-RUN" and item["verdict"] == "PASS"}
    return all(f"dpr {dpr}" in gate and all(f"{page}@{dpr}x" in passed for page in pages) for dpr in dprs)


def read(proof_key, pages, dprs):
    try:
        proof = json.loads(cache_path(proof_key).read_text(encoding="utf-8"))
        if proof["key"] == proof_key and complete(proof, pages, dprs):
            return {"items": proof["items"]}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def write(proof_key, items):
    path = cache_path(proof_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"key": proof_key, "items": items}, stream)
        os.replace(temporary, path)
    except OSError:
        return False
    return True
