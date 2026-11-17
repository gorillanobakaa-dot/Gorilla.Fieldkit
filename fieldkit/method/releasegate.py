"""One release per batch PER OPERATING SYSTEM (owner order 2026-11-15, every project; per system since the owner's
change 2026-11-17: six systems may each publish the same day): refuse a new release of a repository that published one
FOR THE SAME SYSTEM less than `min_hours` ago (releasegate.yaml, default 24). The system is the tag's suffix
(`system_of`); a tag without one is its own kind.  Escape: a logged `--hotfix WHY` (security fix or a broken
release); it is written to the room and the refusals ledger, and counts against the same window: one hotfix per window.

    guard(slug, tag, hotfix="")  -> {"ok", "state", "error", "next"}
Every place that tags or publishes calls it: ship gh-release / push --tag / release, the emerge release publisher,
the release cycle and the inbox send."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import yaml

DEFAULT_HOURS = 24.0
_WIN_TOKEN = re.compile(r"(?:^|[^a-z0-9])(win64|win32|windows|win-x64|win-x86|amd64-windows|x86_64-pc-windows)(?:[^a-z0-9]|$)")
_WIN_ARCHIVE_EXT = (".zip", ".7z", ".tar.gz", ".tgz", ".tar.xz", ".tar.zst")
_INSTALLER_EXE = re.compile(r"(?:installer|setup|install)(?:[^a-z0-9]|$)|\.installer\.exe$")


def is_windows_asset(name):
    """True when a release filename targets Windows (.exe/.msi/.msix/.appx or a Windows-tagged archive)."""
    n = Path(name).name.lower()
    if n.endswith((".exe", ".msi", ".msix", ".appx")):
        return True
    return bool(_WIN_TOKEN.search(n)) and n.endswith(_WIN_ARCHIVE_EXT)


def is_windows_release(tag, asset_names):
    """True when the release tag targets Windows or any asset targets Windows."""
    return system_of(tag) == "windows" or any(is_windows_asset(a) for a in (asset_names or ()))


def windows_installer_blocker(tag, asset_names):
    """-> '' if OK, else why a Windows release is refused (missing a click-to-install .exe/.msi)."""
    if not is_windows_release(tag, asset_names):
        return ""
    names = [Path(a).name.lower() for a in (asset_names or ())]
    if any(n.endswith((".msi", ".msix", ".appx")) for n in names):
        return ""
    has_win_archive = any(bool(_WIN_TOKEN.search(n)) and n.endswith(_WIN_ARCHIVE_EXT) for n in names)
    exes = [n for n in names if n.endswith(".exe")]
    if exes and (not has_win_archive or any(_INSTALLER_EXE.search(n) for n in exes)):
        return ""
    return (
        "Windows release has no installer (.exe/.msi): every Windows binary must ship a click-to-install "
        "installer (e.g. *.installer.exe), not only a .zip folder"
    )


def _cfg():
    try:
        return yaml.safe_load((Path(__file__).with_name("releasegate.yaml")).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - an unreadable config keeps the defaults, never opens the gate
        return {}


def min_hours():
    try:
        return float(_cfg().get("min_hours", DEFAULT_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_HOURS


def system_of(tag, systems=None):
    """The operating system a release tag is for: the first word after the version (v160.0.0-linux -> linux,
    v158.0-win64-build2 -> windows, macbook-air -> mac), mapped by releasegate.yaml systems; '' for a plain v1.2.3."""
    systems = _cfg().get("systems") or {} if systems is None else systems
    rest = re.sub(r"^v?\d+(?:\.\d+)*", "", tag or "")
    m = re.search(r"[A-Za-z][A-Za-z0-9]*", rest)
    if not m:
        return ""
    word = m.group(0).lower()
    return str(systems.get(word, word))


def _gh_recent(slug, limit=30):
    """-> [(tag, epoch seconds)] of the newest published releases, newest first; raises when gh fails."""
    r = subprocess.run(["gh", "release", "list", "-R", slug, "--limit", str(limit), "--json", "publishedAt,tagName"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-200:])
    out = []
    for row in json.loads(r.stdout or "[]"):
        if not row.get("publishedAt"):
            continue                                           # a draft is not published
        t = time.mktime(time.strptime(row["publishedAt"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        out.append((row["tagName"], t))
    return out


def _gh_last(slug):
    """-> the recent releases (see _gh_recent), or None when there are none. Kept as the one seam tests replace."""
    return _gh_recent(slug) or None


def _log_path():
    return Path(os.environ.get("FIELDKIT_STATE_DIR") or "state") / "releasegate" / "hotfixes.jsonl"


def _hotfixes():
    try:
        return [json.loads(l) for l in _log_path().read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


def _record(slug, tag, why, now):
    p = _log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"t": now, "repo": slug, "tag": tag, "why": why}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    text = f"HOTFIX release of {slug} {tag} inside the one-release-per-batch window: {why}"
    try:                                                              # the room and the ledger; never silent, never raises
        from ..zero import net
        net.append("note", os.environ.get("CLAUDE_SESSION_ID", "releasegate"), text=text)
    except Exception:  # noqa: BLE001
        pass
    try:
        from .. import refusals
        refusals.record("releasegate-hotfix", text)
    except Exception:  # noqa: BLE001
        pass


def guard(slug, tag="", hotfix="", last=None, now=None, hours=None):
    """-> {"ok": True, ...} to go on, or {"ok": False, "state": "batch-window", ...}. `last` is (tag, epoch), a list of
    them, or None for tests; otherwise gh is asked. Only a release for the SAME system counts. A gh failure fails
    closed."""
    now = time.time() if now is None else now
    hours = min_hours() if hours is None else hours
    hotfix = hotfix or os.environ.get("FIELDKIT_HOTFIX", "")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", slug or ""):      # a local or non-GitHub remote has no GitHub releases to count
        return {"ok": True, "state": "gate-not-github", "tag": tag}
    if last is None:
        try:
            last = _gh_last(slug)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "state": "gate-unreadable", "tag": tag, "error": f"could not read {slug}'s releases: {e}",
                    "next": "fail closed: fix gh access, then retry"}
    rows = [last] if isinstance(last, tuple) else list(last or [])
    if any(r[0] == tag for r in rows):                       # the release itself: more files for it
        return {"ok": True, "state": "gate-open", "tag": tag}
    system = system_of(tag)
    same = [r for r in rows if system_of(r[0]) == system and (now - r[1]) / 3600.0 < hours]
    if not same:
        return {"ok": True, "state": "gate-open", "tag": tag, "system": system}
    last = max(same, key=lambda r: r[1])
    age = (now - last[1]) / 3600.0
    wait = f"{hours - age:.1f}"
    if hotfix:
        window = now - hours * 3600
        prior = [h for h in _hotfixes() if h.get("repo") == slug and h.get("t", 0) >= window and h.get("tag") != tag
                 and system_of(h.get("tag", "")) == system]
        if prior:
            return {"ok": False, "state": "hotfix-used", "tag": tag,
                    "error": f"{slug} already had a hotfix ({prior[-1].get('tag')}) in the last {hours:g} h",
                    "next": "one hotfix per window: batch this fix into the next release"}
        if not any(h.get("repo") == slug and h.get("tag") == tag and h.get("t", 0) >= window for h in _hotfixes()):
            _record(slug, tag, hotfix, now)
        return {"ok": True, "state": "hotfix", "tag": tag, "hotfix": hotfix}
    return {"ok": False, "state": "batch-window", "tag": tag,
            "error": f"{slug} published {last[0]} {age:.1f} h ago (window {hours:g} h for "
                     f"{system or 'releases without a system in the tag'})",
            "next": f"batch the fixes, then release once from main after the last one ({wait} h left); "
                    "a security fix or a broken release: --hotfix \"WHY\" (logged)"}
