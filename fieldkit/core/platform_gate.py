# Version: 1.0.0 · updated 26-10-20-12-30
"""The command line's platform gate: a `fieldkit` command whose card says it runs on another system is refused before
it starts, with the card's reason and what to use instead.

Owner, 2026-10-20: "the harness queries the os on which operating system is and never tries to run stuff that won't
work on that operating system ... we can't afford having users download the harness and their limited AI trying to
run stuff that won't work". That day `fieldkit thermal monitor` started PowerShell on Linux and died with
"No such file or directory: 'powershell'". Every card already said where it runs (`platforms`); only pipelines and
`next` read it. The same rule (core.host.platform_refusal) now stands at all three doors: this gate (a person or a
script typing a command), agent.run (a model running a card) and agent.discover (a model looking for one).

The gate reads platform_index.json, generated from the PUBLIC cards in desk/tools.yaml (`python -m
fieldkit.core.platform_gate --write`), because loading every card takes seconds and the gate runs before every command.
tests/test_platform_gate.py fails when the index and the cards disagree, so a card's platforms cannot change without
the gate knowing. The private cards in local/tools.yaml (git-ignored, this machine only) are never written into that
shipped file: they get their own index in state/platform_index.local.json, rebuilt when local/tools.yaml changes.

Help (-h, --help) always passes: reading what a tool does is not running it.
"""
import json
import sys
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "desk" / "platform_index.json"
FIELDKIT = (["python", "-m", "fieldkit"], ["python3", "-m", "fieldkit"])


def _words(tokens):
    """The leading command words: up to the first option or template ("--x", "{x}", "${X}")."""
    out = []
    for t in tokens:
        t = str(t)
        if t.startswith("-") or "{" in t or "$" in t:
            break
        out.append(t)
    return out


def build_index(tools):
    """{ "build-harness build-run": {"platforms": [...], "why": str, "ids": [...]}, ... } for every card that runs a
    fieldkit command and names its platforms. Cards sharing a command join their platforms (refuse only when NO card
    allows this system)."""
    idx = {}
    for t in tools:
        entry = list(t.get("entry") or [])
        if entry[:3] not in FIELDKIT or not t.get("platforms") or t.get("retired"):
            continue
        key = " ".join(_words(entry[3:]))
        if not key:
            continue
        row = idx.setdefault(key, {"platforms": [], "why": "", "ids": []})
        for p in t["platforms"]:
            if p not in row["platforms"]:
                row["platforms"].append(p)
        row["ids"].append(t["id"])
        if t.get("platforms_why") and not row["why"]:
            row["why"] = t["platforms_why"]
    for row in idx.values():
        row["platforms"].sort()
        row["ids"].sort()
    return dict(sorted(idx.items()))


def public_tools():
    """The cards that ship: desk/tools.yaml only, never the private local/tools.yaml."""
    from ..desk import registry
    from . import settings
    return settings.expand(list(settings.read_file(registry.REGISTRY)["tools"]), strict=False)


def _local_index():
    """This machine's private cards as an index, cached in state/ and rebuilt when local/tools.yaml changes."""
    from ..desk import registry
    from . import settings
    src = registry.LOCAL_REGISTRY
    if not src.is_file():
        return {}
    cache = settings.ROOT / "state" / "platform_index.local.json"
    stamp = src.stat().st_mtime_ns
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("mtime_ns") == stamp:
            return c["index"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        idx = build_index(settings.expand(settings.read_file(src).get("tools") or [], strict=False))
    except Exception:  # noqa: BLE001 - a broken private file must not stop every command; agent.run still checks
        return {}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"mtime_ns": stamp, "index": idx}), encoding="utf-8")
    except OSError:
        pass
    return idx


def load_index(path=None, local=True):
    try:
        idx = json.loads(Path(path or INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        idx = {}                        # no index: the gate passes everything; agent.run still checks the card
    if local and path is None:
        for key, row in _local_index().items():
            idx.setdefault(key, row)
    return idx


def refusal(args, index=None):
    """-> None when `fieldkit <args>` may run here, else the refusal sentence. The longest matching command wins."""
    if any(a in ("-h", "--help") for a in args):
        return None
    from .host import platform_refusal
    index = load_index() if index is None else index
    words = _words(args)
    for n in range(len(words), 0, -1):
        row = index.get(" ".join(words[:n]))
        if row:
            return platform_refusal("fieldkit " + " ".join(words[:n]), row["platforms"], row.get("why"))
    # No card for exactly this command: a group (`fieldkit win11`, `fieldkit hdmi`) is refused when EVERY card under
    # it is for another system; a mixed group passes and its own code decides.
    if words:
        head = " ".join(words) + " "
        under = [row for key, row in index.items() if key.startswith(head)]
        if under:
            allowed = sorted({p for row in under for p in row["platforms"]})
            why = next((row["why"] for row in under if row.get("why")), None)
            return platform_refusal("fieldkit " + " ".join(words), allowed, why)
    return None


def write(path=None):
    p = Path(path or INDEX)
    p.write_text(json.dumps(build_index(public_tools()), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


if __name__ == "__main__":
    if "--write" in sys.argv:
        print(f"wrote {write()}")
    else:
        print(__doc__)
