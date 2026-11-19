"""Reuse a contiguous, verified prefix from a cleanly stopped namespace run."""
import hashlib
import json
import os
import platform
import re
import sys
from importlib import metadata
from pathlib import Path


FILE = "restart.json"
ARTIFACT_PATHS = ("mitm", "necko", "pcap", "strace", "dns")


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def context(package_digest, tree, upstream, policy_paths, tools, options, sha):
    from ..core.pipeline import tree_state

    try:
        source = tree_state(tree)
        if source.startswith("no-git ") or not package_digest:
            return None
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[a-f0-9-]{36}", boot):
            return None
        return digest({
            "format": 1, "job": options, "runner": [boot, platform.uname(), os.geteuid(), sys.version],
            "source": [source, upstream], "package": package_digest,
            "policy": [sha(path) if path and Path(path).is_file() else None for path in policy_paths],
            "fieldkit": [(path.name, sha(path)) for path in sorted(Path(__file__).parent.glob("*.py"))],
            "tools": {name: sha(path) if path else None for name, path in tools.items()},
            "packages": sorted((d.metadata["Name"], d.version) for d in metadata.distributions()
                               if d.metadata["Name"]),
        })
    except (OSError, ValueError, TypeError):
        return None


def files(work, artifact, sha):
    """Hash only regular sensor files inside this run. Missing optional files are recorded too."""
    root = (work / "runs").resolve()
    result = {}
    for key in ARTIFACT_PATHS:
        if not artifact.get(key):
            continue
        path = Path(artifact[key]).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"sensor file outside this run: {key}")
        result[key] = sha(path) if path.is_file() else None
    return result


def prefix(work, expected, context_id, checkpoint_dir, sha):
    """Return completed scenario records in order or refuse the old run."""
    try:
        marker = json.loads((work / FILE).read_text(encoding="utf-8"))
        if (not context_id or marker.get("context") != context_id or marker.get("state") != "ready"
                or (work / "test-results.json").exists()):
            return []
        records = marker.get("records", {})
        if not isinstance(records, dict) or set(records) != set(expected[:len(records)]):
            return []
        rows = []
        for name in expected[:len(records)]:
            path = work / checkpoint_dir / f"{name}.json"
            row = json.loads(path.read_text(encoding="utf-8"))
            if (row["scenario_run"] != name or digest(row) != records[name]["row"]
                    or files(work, row["artifact"], sha) != records[name]["files"]):
                return []
            if (not isinstance(row["events"], list) or
                    set(row["observations_since_previous_checkpoint"]) != {"page_results", "page_requests", "doh_queries"}
                    or not all(isinstance(items, list) for items in row["observations_since_previous_checkpoint"].values())):
                return []
            rows.append((row, records[name]))
        return rows
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []
