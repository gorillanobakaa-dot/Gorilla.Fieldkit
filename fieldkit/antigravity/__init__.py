# Version: 1.0.0 · updated 26-11-15
"""fieldkit antigravity - checks of agent config files, run by the Rust binary agy-tools (rust/agy-tools).

    fieldkit antigravity check hooks|mcp|plugin|json-config PATH [--check-bins] [--check-paths] [--json]
        validate one hooks.json, mcp_config.json, plugin folder or skills.json/plugins.json
    fieldkit antigravity doors [FILE...] [--check-bins] [--home DIR] [--json]
        every MCP server the Claude, Codex, Gemini and Antigravity configs can start or reach (the THREAT MODEL as a
        check): remote URLs are listed, a legacy /sse endpoint, plain http or a missing binary is a finding
    fieldkit antigravity migrate-workflows [ROOT] [--apply|--verify|--undo] [--json]
        .agent/workflows -> .agents/skills; preview by default; undo moves files to .agent/workflows.undone (nothing
        is ever deleted)

    fieldkit antigravity sessions|send|stream|listen|bind-check|station ...   the session bridge (bridge.rs; docs/ANTIGRAVITY-BRIDGE.md)

This module is a thin wrapper. The binary is built from source by `fieldkit emerge run agy-tools` (pinned by sha256 in
the recipe report, never committed). Absent: one NEXT line and exit 1. No Python fallback, so a run is never silently
slow. Exit codes follow the binary: 0 fine, 3 findings, 2 bad usage; 1 binary missing.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK_KINDS = {"hooks": "hooks", "mcp": "mcp", "plugin": "plugin", "json-config": "json-config"}
NEXT_BUILD = "NEXT: fieldkit emerge run agy-tools"


def binary():
    """The built agy-tools: $FIELDKIT_AGY_TOOLS, else the emerge output in this checkout; None when absent."""
    env = os.environ.get("FIELDKIT_AGY_TOOLS")
    exe = "agy-tools.exe" if sys.platform == "win32" else "agy-tools"
    for p in ([Path(env)] if env else []) + [ROOT / "rust" / "agy-tools" / "target" / "release" / exe]:
        if p.is_file():
            return p
    return None


def run_tool(argv):
    """Run the binary with argv (after `run`); returns its exit code. Missing binary: NEXT line, 1."""
    exe = binary()
    if exe is None:
        print("agy-tools is not built here.")
        print(NEXT_BUILD)
        return 1  # missing binary
    # captured and re-printed through this process's own stdout: a child writing to an inherited handle is lost
    # when a parent captures output on Windows
    r = subprocess.run([str(exe), "run", *argv], capture_output=True, text=True, encoding="utf-8", errors="replace")
    sys.stdout.write(r.stdout)
    sys.stdout.flush()
    sys.stderr.write(r.stderr)
    return r.returncode


BRIDGE_VERBS = ("sessions", "send", "stream", "bind-check", "station", "listen")
RUST_BRIDGE = ("sessions", "send", "stream", "bind-check")


def _needs_python_bridge(cmd, rest):
    if cmd not in RUST_BRIDGE:
        return True
    return any(a in ("--device", "--station") or a.startswith(("--device=", "--station=")) for a in rest)


def run_bridge(cmd, rest):
    """The session bridge: the Rust binary (starts in ~5 ms, waits for agy, leaves no child) when it is built, else
    the same logic in bridge.py (one NOTE on stderr). `station`, `listen`, and `--device`/`--station` run in Python."""
    use_python = _needs_python_bridge(cmd, rest)
    exe = None if use_python else binary()
    if exe is None:
        if not use_python:
            print("NOTE: agy-tools is not built; using the slower Python bridge. NEXT: fieldkit emerge run agy-tools",
                  file=sys.stderr)
        from . import bridge
        return bridge.main([cmd, *rest])
    if cmd == "stream":  # inherits stdin/stdout: NDJSON in, fenced NDJSON out
        return subprocess.run([str(exe), "bridge", cmd, *rest]).returncode
    r = subprocess.run([str(exe), "bridge", cmd, *rest], capture_output=True, text=True, encoding="utf-8", errors="replace")
    sys.stdout.write(r.stdout)
    sys.stdout.flush()
    sys.stderr.write(r.stderr)
    return r.returncode


def main(args):
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = args[0], list(args[1:])
    if cmd in ("check", "doors", "migrate-workflows") and any(a in ("-h", "--help") for a in rest):
        print(__doc__)  # help never needs the binary
        return 0
    if cmd == "check":
        if not rest or rest[0] not in CHECK_KINDS:
            print("usage: fieldkit antigravity check hooks|mcp|plugin|json-config PATH [flags]", file=sys.stderr)
            return 2
        return run_tool([CHECK_KINDS[rest[0]], *rest[1:]])
    if cmd == "doors":
        return run_tool(["doors", *rest])
    if cmd == "migrate-workflows":
        mode = "preview"
        pos = []
        for a in rest:
            if a in ("--apply", "--verify", "--undo"):
                mode = a[2:]
            else:
                pos.append(a)
        if "--json" in pos:
            pos.remove("--json")
            pos.append("--json")
        root = pos[0] if pos and pos[0] != "--json" else "."
        tail = ["--json"] if "--json" in pos else []
        return run_tool(["workflow-migrate", root, "--mode", mode, *tail])
    if cmd in BRIDGE_VERBS:
        return run_bridge(cmd, rest)
    print(f"unknown antigravity command: {cmd}", file=sys.stderr)
    return 2
