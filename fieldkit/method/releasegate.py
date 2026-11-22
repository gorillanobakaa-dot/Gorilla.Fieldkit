"""One release per batch PER OPERATING SYSTEM (owner order 2026-11-15, every project; per system since the owner's
change 2026-11-17: six systems may each publish the same day): refuse a new release of a repository that published one
FOR THE SAME SYSTEM less than `min_hours` ago (releasegate.yaml, default 24). The system is the tag's suffix
(`system_of`); a tag without one is its own kind.  Escape: a logged `--hotfix WHY` (security fix or a broken
release); it is written to the room and the refusals ledger. The configured limit is per repository and system.

    guard(slug, tag, hotfix="")  -> {"ok", "state", "error", "next"}
Every place that tags or publishes calls it: ship gh-release / push --tag / release, the emerge release publisher,
the release cycle and the inbox send."""
import calendar
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
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


class ConfigUnreadable(ValueError):
    """releasegate.yaml cannot be read or is not a mapping: the gate fails closed."""


def _cfg():
    path = Path(__file__).with_name("releasegate.yaml")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigUnreadable(f"{path.name} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigUnreadable(f"{path.name} is not a mapping")
    return data


def min_hours():
    cfg = _cfg()
    try:
        return float(cfg.get("min_hours", DEFAULT_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_HOURS


def hotfix_limit():
    limit = _cfg().get("hotfix_limit")
    return limit if type(limit) is int and limit > 0 else 1


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
    r = subprocess.run(["gh", "release", "list", "-R", slug, "--limit", str(limit),
                        "--json", "publishedAt,tagName,isDraft"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-200:])
    out = []
    for row in json.loads(r.stdout or "[]"):
        published = row.get("publishedAt") or ""
        if row.get("isDraft") or not published or published.startswith("0001-"):
            continue                                           # a draft is not published (gh returns 0001-01-01T00:00:00Z)
        t = calendar.timegm(time.strptime(published[:19], "%Y-%m-%dT%H:%M:%S"))
        out.append((row["tagName"], t))
    return out


def _gh_last(slug):
    """-> the recent releases (see _gh_recent), or None when there are none. Kept as the one seam tests replace."""
    return _gh_recent(slug) or None


def _gh_published(slug):
    """All published releases, including history needed to classify the earliest hotfix in the window."""
    from .releasecandidate import GitHub

    return [(row["tag_name"], calendar.timegm(time.strptime(row["published_at"], "%Y-%m-%dT%H:%M:%SZ")))
            for row in GitHub(slug).releases() if not row["draft"] and row["published_at"]]


def _published_hotfix_quota(rows, systems, aliases, matrix, now, hours):
    """A public release inside the preceding same-platform window is a hotfix, whoever published it."""
    last = {}
    counts = dict.fromkeys(systems, 0)
    known = set(aliases.values()) | systems
    window = hours * 3600
    # GitHub tags are unique in normal operation. Count a tag at most once even if pages overlap.
    for tag, published in sorted(dict(rows).items(), key=lambda release: release[::-1]):
        platform = system_of(tag, aliases)
        targets = systems if matrix and platform not in known else systems.intersection({platform})
        for target in targets:
            if published > now - window and target in last and published - last[target] < window:
                counts[target] += 1
            last[target] = published
    return counts


def _log_path():
    return Path(os.environ.get("FIELDKIT_STATE_DIR") or "state") / "releasegate" / "hotfixes.jsonl"


def _hotfixes():
    try:
        return [json.loads(l) for l in _log_path().read_text(encoding="utf-8").splitlines() if l.strip()]
    except FileNotFoundError:
        return []


def _record(slug, tag, why, now, systems=None):
    p = _log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"t": now, "repo": slug, "tag": tag, "why": why}
    if systems is not None:
        row["systems"] = sorted(systems)
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


@contextmanager
def publication_slot(*, slug="", single_publisher=False):
    """Serialize each repository's draft and promotion on the designated host."""
    import hashlib

    from .. import admission

    root = Path.home() / ".fieldkit" / "release-publication" if single_publisher else _log_path().parent
    if slug:
        root = root / hashlib.sha256(slug.casefold().encode()).hexdigest()
    slot = admission.try_take(1, kind="releasegate", label=slug or "hotfix ledger", root=root)
    if slot is None:
        raise ValueError("another ship command is checking or publishing a release; retry after it finishes")
    try:
        yield
    finally:
        slot.release()


def record_hotfix_attempt(slug, tag, why, systems):
    """Log the reason before publication. GitHub history determines whether the attempt consumes quota."""
    _record(slug, tag, why, time.time(), systems)


def _reserve_hotfix(slug, tag, why, systems, now, hours, *, candidate_platforms=()):
    from .. import admission

    try:
        slot = admission.try_take(1, kind="releasegate", label="hotfix ledger", root=_log_path().parent)
        if slot is None:
            return {"ok": False, "state": "hotfix-busy", "tag": tag, "error": "another ship command holds the hotfix ledger",
                    "next": "retry after that ship command finishes"}
        try:
            window = now - hours * 3600
            history = [h for h in _hotfixes() if h["repo"].casefold() == slug.casefold() and h["t"] > window]
            limit = hotfix_limit()
            aliases = _cfg().get("systems") or {}
            known_systems = set(aliases.values()) | set(candidate_platforms)
            reserve = set()
            for system in sorted(systems):
                used = set()
                for entry in history:
                    recorded = entry.get("systems")
                    if recorded is None:
                        historical = system_of(entry["tag"], aliases)
                        recorded = systems if candidate_platforms and historical not in known_systems else {historical}
                    if system in recorded:
                        used.add(entry["tag"])
                if tag in used:
                    continue
                if len(used) >= limit:
                    return {"ok": False, "state": "hotfix-used", "tag": tag,
                            "error": f"{slug} used {len(used)} of {limit} hotfixes in the last {hours:g} h for "
                                     f"{system or 'tags without an operating-system suffix'}",
                            "next": "wait for a hotfix to age out or for the regular release window"}
                reserve.add(system)
            if reserve:
                _record(slug, tag, why, now, reserve if candidate_platforms else None)
        finally:
            slot.release()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "state": "hotfix-ledger-error", "tag": tag, "error": f"cannot use the hotfix ledger: {exc}",
                "next": "check the hotfix ledger before retrying"}
    return {"ok": True, "state": "hotfix", "tag": tag, "hotfix": why}


def guard(slug, tag="", hotfix="", last=None, now=None, hours=None, *, platforms=(), published_only=False):
    """-> {"ok": True, ...} to go on, or {"ok": False, "state": "batch-window", ...}. `last` is (tag, epoch), a list of
    them, or None for tests; otherwise gh is asked. Only a release for the SAME system counts. A gh failure fails
    closed."""
    now = time.time() if now is None else now
    try:
        hours = min_hours() if hours is None else hours
        aliases = _cfg().get("systems") or {}
    except ConfigUnreadable as exc:
        return {"ok": False, "state": "gate-config-unreadable", "tag": tag, "error": str(exc),
                "next": "fail closed: restore fieldkit/method/releasegate.yaml, then retry"}
    hotfix = hotfix or os.environ.get("FIELDKIT_HOTFIX", "")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", slug or ""):      # a local or non-GitHub remote has no GitHub releases to count
        return {"ok": True, "state": "gate-not-github", "tag": tag}
    if last is None:
        try:
            last = _gh_published(slug) if published_only else _gh_last(slug)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "state": "gate-unreadable", "tag": tag, "error": f"could not read {slug}'s releases: {e}",
                    "next": "fail closed: fix gh access, then retry"}
    rows = [last] if isinstance(last, tuple) else list(last or [])
    if any(r[0] == tag for r in rows):                       # the release itself: more files for it
        return {"ok": True, "state": "gate-open", "tag": tag}
    requested = {aliases.get(platform.casefold(), platform.casefold()) for platform in platforms}
    systems = requested or {system_of(tag, aliases)}
    known_systems = set(aliases.values()) | requested
    same = []
    blocked = set()
    for old_tag, published in rows:
        if (now - published) / 3600.0 >= hours:
            continue
        old_system = system_of(old_tag, aliases)
        overlap = systems if requested and old_system not in known_systems else systems.intersection({old_system})
        if overlap:
            same.append((old_tag, published))
            blocked.update(overlap)
    if not same:
        return {"ok": True, "state": "gate-open", "tag": tag, "system": system_of(tag, aliases)}
    last = max(same, key=lambda r: r[1])
    age = (now - last[1]) / 3600.0
    wait = f"{hours - age:.1f}"
    if hotfix:
        if published_only:
            counts = _published_hotfix_quota(rows, blocked, aliases, requested, now, hours)
            limit = hotfix_limit()
            for system, count in sorted(counts.items()):
                if count >= limit:
                    return {"ok": False, "state": "hotfix-used", "tag": tag,
                            "error": f"{slug} published {count} of {limit} hotfixes in the last {hours:g} h for "
                                     f"{system or 'tags without an operating-system suffix'}",
                            "next": "wait for a hotfix to age out or for the regular release window"}
            return {"ok": True, "state": "hotfix", "tag": tag, "hotfix": hotfix, "systems": blocked}
        return _reserve_hotfix(slug, tag, hotfix, blocked, now, hours, candidate_platforms=requested)
    affected = ", ".join(sorted(blocked)) or "releases without a system in the tag"
    return {"ok": False, "state": "batch-window", "tag": tag,
            "error": f"{slug} published {last[0]} {age:.1f} h ago (window {hours:g} h for {affected})",
            "next": f"batch the fixes, then release once from main after the last one ({wait} h left); "
                    "a security fix or a broken release: --hotfix \"WHY\" (logged)"}
