# Version: 1.0.0 · updated 26-10-26
"""Doors in a skill: every place its files can reach the network, load from a CDN or install software at run time.

The owner's threat model: nothing adopted calls home, loads remote code or pulls packages on its own schedule. A
skill is prose a model follows plus files it runs or renders, so the files are read as data and every door is listed.
fieldkit telemetry scan finds SDKs and endpoints; this finds the rest: remote <script>/<link>/@import, absolute URLs
in code (a fetch() of an absolute URL among them; a relative fetch stays on the page's own server), network APIs
(urllib, requests, httpx, http.client, socket, XMLHttpRequest, WebSocket, sendBeacon, webbrowser), model vendors'
SDKs and `claude -p` (a model call leaves the machine), and package
installs (pip, npm, pnpm, yarn, npx, uvx, curl, wget). Loopback, RFC 2606 example domains and XML namespace URIs are
not doors.

A finding is (file, kind, match). In code it is a "door"; in Markdown prose it is "prose" (an instruction a model may
follow, reported, never failing). A door stays only when the adoption manifest accepts it with a reason.
"""
import re
from pathlib import Path

CODE = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".ps1", ".html", ".htm", ".css", ".svg"}
PROSE = {".md", ".txt"}
LOCAL_HOSTS = re.compile(r"^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|::1|([a-z0-9-]+\.)*example\.(com|org|net)"
                         r"|[a-z0-9.-]+\.(example|test|localhost|invalid))(:\d+)?$", re.I)
NAMESPACE = re.compile(r"^(www\.w3\.org|schemas\.openxmlformats\.org|schemas\.microsoft\.com|purl\.org|ns\.adobe\.com)$", re.I)
URL = re.compile(r"\b(?:https?|wss?|ftp)://([^/\s\"'`<>)\]]+)[^\s\"'`<>)\]]*", re.I)
NET_API = re.compile(r"\b(urllib\.request|urllib3|requests\.(get|post|put|delete|head|Session|request)|httpx|aiohttp"
                     r"|http\.client|socket\.(create_connection|socket)|XMLHttpRequest|new WebSocket|navigator\.sendBeacon"
                     r"|webbrowser\.open)\b")
VENDOR_SDK = re.compile(r"(^\s*(?:import|from)\s+(anthropic|openai|google\.generativeai|google\.genai|mistralai|cohere)\b"
                        r"|@anthropic-ai/sdk|\"openai\"|\[\s*[\"']claude[\"']\s*,\s*[\"']-p[\"'])")
INSTALL = re.compile(r"\b(pip3? install|python3? -m pip install|npm (install|i|ci)|pnpm (install|add|dlx)|yarn (add|install)"
                     r"|npx |uvx |bunx |curl |wget |Invoke-WebRequest|iwr )", re.I)


def _host_is_local(host):
    host = host.split("@")[-1]
    if not host.startswith("["):
        host = host.split(":")[0]                    # the port, or a placeholder such as {port}
    return bool(LOCAL_HOSTS.match(host)) or bool(NAMESPACE.match(host))


def scan_text(rel, text):
    ext = Path(rel).suffix.lower()
    kind = "door" if ext in CODE else "prose" if ext in PROSE else None
    if kind is None:
        return []
    out = []
    for no, line in enumerate(text.splitlines(), 1):
        for m in URL.finditer(line):
            if not _host_is_local(m.group(1)):
                out.append({"file": rel, "line": no, "kind": kind, "what": "url", "match": m.group(0)})
        if kind == "door":
            for m in NET_API.finditer(line):
                out.append({"file": rel, "line": no, "kind": kind, "what": "net-api", "match": m.group(1)})
            for m in VENDOR_SDK.finditer(line):
                out.append({"file": rel, "line": no, "kind": kind, "what": "vendor-api", "match": m.group(0).strip()})
        for m in INSTALL.finditer(line):
            out.append({"file": rel, "line": no, "kind": kind, "what": "install", "match": m.group(1).strip()})
    return out


def scan(root):
    """Every finding under root (a skill folder or a folder of skills), files in a stable order."""
    root = Path(root)
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in CODE | PROSE and p.name != "LICENSE.txt":
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            out += scan_text(p.relative_to(root).as_posix(), text)
    return out


def accepted(finding, rules):
    """The rule that accepts this finding, or None. A rule names a file (exact, or a prefix ending in '/') and a
    substring of the match; 'what' narrows it further when given."""
    for r in rules:
        f = r.get("file", "")
        if not (finding["file"] == f or (f.endswith("/") and finding["file"].startswith(f))):
            continue
        if r.get("what") and r["what"] != finding["what"]:
            continue
        if r.get("match", "") in finding["match"]:
            return r
    return None
