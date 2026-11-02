"""The "What is built in" table of a Gorilla OpenCode release page, generated, never typed (plan W6, owner 2026-11-01).

The release page named the carried helper programs once (or not at all). The truth is in the source tree:
    internal/toolbox/SHA256SUMS   which helper is pinned for which system (windows-amd64, linux-amd64, darwin-amd64)
    internal/toolbox/helpers.json name, version, what it is for in plain words, the macOS it needs
    third_party/PINS.md           where each version comes from; a version it does not name is refused
`table(src)` reads those and returns the markdown (plain words first, then the table), or "" for a tree that has no
toolbox yet. A pinned helper with no description, or a version PINS.md does not name, raises ValueError.
"""
import json
import re
from pathlib import Path

SYSTEMS = (("windows-amd64", "Windows"), ("linux-amd64", "Linux"), ("darwin-amd64", "macOS"))
INTRO = ("These programs come inside Gorilla OpenCode itself. There is nothing to install: the AI can use them the "
         "moment the program starts, and each one is checked against a fixed fingerprint (SHA-256) before it runs. "
         "This table is generated from the list the program is built from, not typed.")


def _pins(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split()
        if len(f) != 2 or "/" not in f[1]:
            raise ValueError(f"SHA256SUMS: cannot read line {line!r}")
        plat, file = f[1].lstrip("*").split("/", 1)
        out.setdefault(re.sub(r"\.exe$", "", file), set()).add(plat)
    return out


def table(src):
    src = Path(src)
    sums = src / "internal" / "toolbox" / "SHA256SUMS"
    cat = src / "internal" / "toolbox" / "helpers.json"
    if not sums.is_file():
        return ""
    if not cat.is_file():
        raise ValueError(f"{cat} is missing: the pinned helpers have no plain-words description")
    pins = _pins(sums.read_text(encoding="utf-8"))
    helpers = {h["name"]: h for h in json.loads(cat.read_text(encoding="utf-8"))}
    undescribed = sorted(set(pins) - set(helpers))
    if undescribed:
        raise ValueError("pinned but not described in helpers.json: " + ", ".join(undescribed))
    pinsmd = src / "third_party" / "PINS.md"
    known = pinsmd.read_text(encoding="utf-8") if pinsmd.is_file() else None
    rows = []
    for name, h in helpers.items():
        if name not in pins:
            continue
        for v in (h["version"], h.get("version_darwin")):
            if v and known is not None and v not in known:
                raise ValueError(f"{name} {v} is not named in third_party/PINS.md")
        ver = h["version"] + (f" (macOS: {h['version_darwin']})" if h.get("version_darwin") else "")
        cells = []
        for plat, _ in SYSTEMS:
            if plat not in pins[name]:
                cells.append("no")
            elif plat.startswith("darwin") and h.get("mac_min", "10.13") != "10.13":
                cells.append(f"yes, needs macOS {h['mac_min']}")
            else:
                cells.append("yes")
        rows.append(f"| `{name}` | {ver} | {h['purpose']} | " + " | ".join(cells) + " |")
    head = "| Helper | Version | What it is for | " + " | ".join(s for _, s in SYSTEMS) + " |"
    return ("### What is built in\n\n" + INTRO + f" {len(rows)} helpers. On macOS they need 10.13 (High Sierra) or newer, "
            "except where the table says otherwise.\n\n" + head + "\n|" + "---|" * (3 + len(SYSTEMS)) + "\n"
            + "\n".join(rows))
