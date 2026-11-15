"""parity helpers / parity installed - the two checks of the goal "parity everywhere", as commands.

    fieldkit parity helpers [REPO] [--json]   Gorilla OpenCode pins the SAME helpers for windows-amd64, linux-amd64 and
                                              darwin-amd64 (internal/toolbox/SHA256SUMS, names without .exe), and
                                              parityBacklog in internal/toolbox/parity_test.go is empty
    fieldkit parity installed [--json]        gorilla-opencode's version on this computer and on every peer that answers
                                              over the cable are equal to each other and to the newest release tag

REPO defaults to the Gorilla.Opencode clone named in fieldkit.local.json sync.repos, else ${DOCUMENTS}/Gorilla.Opencode.
Exit 0 all equal, 1 could not be read (no repo, no gh), 3 a difference. Both ended up as local/goal-checks one-liners
after the owner (2026-11-01) saw the same scripts written and forgotten over and over.
"""
import json
import re
from pathlib import Path

from . import parity
from .core import settings

SYSTEMS = ("windows-amd64", "linux-amd64", "darwin-amd64")
_SUM = re.compile(r"(?m)^[0-9a-f]{64}\s+\*?([\w.-]+)/([\w.+-]+)\s*$")
_BACKLOG = re.compile(r"parityBacklog\s*=\s*map\[string\]bool\{([^}]*)\}")


def default_repo(local=None):
    local = settings.local_settings() if local is None else local
    for r in (local.get("sync", {}) or {}).get("repos", []) or []:
        if Path(str(r)).name.lower() == "gorilla.opencode":
            return Path(settings.expand(r, local))
    return Path(settings.expand("${DOCUMENTS}")) / "Gorilla.Opencode"


def helpers(repo=None):
    """-> {ok, repo, helpers, missing: {system: [name]}, backlog: [str], problems: [str]}."""
    repo = Path(repo) if repo else default_repo()
    tb = repo / "internal" / "toolbox"
    try:
        sums = (tb / "SHA256SUMS").read_text(encoding="utf-8")
        test = (tb / "parity_test.go").read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"cannot read {e.filename}: {e.strerror}", "repo": str(repo)}
    per = {}
    for m in _SUM.finditer(sums):
        per.setdefault(m.group(1), set()).add(m.group(2).removesuffix(".exe"))
    names = set().union(*(per.get(s, set()) for s in SYSTEMS))
    missing = {s: sorted(names - per.get(s, set())) for s in SYSTEMS if names - per.get(s, set())}
    blk = _BACKLOG.search(test)
    backlog = blk.group(1).split() if blk and "true" in blk.group(1) else []
    problems = [f"{s} lacks: {', '.join(v)}" for s, v in missing.items()]
    if backlog:
        problems.append("parityBacklog is not empty: " + " ".join(backlog))
    return {"ok": not problems, "repo": str(repo), "helpers": sorted(names), "missing": missing,
            "backlog": backlog, "problems": problems}


def newest_tag(repo, run=parity._run):
    """The newest release tag: gh when it is logged in, else the highest vX.Y.Z tag on the public repo (git ls-remote)."""
    rc, out, err = run(["gh", "release", "view", "--repo", repo, "--json", "tagName"], timeout=60)
    if rc == 0:
        try:
            return parity.norm_version(json.loads(out).get("tagName"))
        except ValueError:
            pass
    rc, out, err = run(["git", "ls-remote", "--tags", "--refs", f"https://github.com/{repo}"], timeout=60)
    tags = re.findall(r"refs/tags/v(\d+(?:\.\d+)+)\s*$", out if rc == 0 else "", re.M)
    return max(tags, key=lambda t: tuple(int(x) for x in t.split("."))) if tags else None


def installed(mine=None, peers=None, newest=None, run=parity._run):
    """-> {ok, newest, versions: {device: version or None}, problems}. mine/peers/newest are injectable for tests."""
    pin = parity.pin().get("gorilla-opencode") or {}
    if newest is None:
        newest = newest_tag(pin.get("repo"), run) if pin.get("repo") else None
    if mine is None:
        path = parity.opencode_path()
        mine = (parity.probe_tool(path, ["--version"], {}, run) or {}).get("version") if path else None
    if peers is None:
        peers = {}
        from .peers import link
        for d in link.others(link.household() or {}):
            m, _ = parity.peer_manifest(d["name"])
            peers[d["name"]] = (((m or {}).get("tools") or {}).get("gorilla-opencode") or {}).get("version")
    versions = {"this computer": mine, **peers}
    problems = []
    if not newest:
        return {"ok": False, "error": "cannot learn the newest release tag (gh release view failed)",
                "versions": versions, "newest": None, "problems": []}
    if not peers:
        problems.append("no peer answered over the cable: both computers cannot be compared")
    for dev, v in versions.items():
        if v != newest:
            problems.append(f"{dev} has gorilla-opencode {v or 'none'}, the newest release is {newest}")
    return {"ok": not problems, "newest": newest, "versions": versions, "problems": problems}


def run(what, repo=None, as_json=False):
    r = helpers(repo) if what == "helpers" else installed()
    if as_json:
        print(json.dumps(r, indent=1, sort_keys=True))
        return 1 if r.get("error") else 0 if r["ok"] else 3
    if r.get("error"):
        print(f"ERROR  {r['error']}\nNEXT: fix that, then rerun `fieldkit parity {what}`.")
        return 1
    if what == "helpers":
        print(f"OK: {len(r['helpers'])} helpers on all of {', '.join(SYSTEMS)}; backlog empty" if r["ok"]
              else "\n".join(r["problems"]))
    else:
        print(f"OK: gorilla-opencode {r['newest']} on {', '.join(r['versions'])}" if r["ok"]
              else "\n".join(r["problems"]))
    print("NEXT: nothing." if r["ok"] else
          "NEXT: align the lagging side (`fieldkit parity align --apply`) or fix the Gorilla.Opencode toolbox pins.")
    return 0 if r["ok"] else 3
