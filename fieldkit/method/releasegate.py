"""One release per batch (owner order 2026-11-15, every project): refuse a new release of a repository that published
one less than `min_hours` ago (releasegate.yaml, default 24).  Escape: a logged `--hotfix WHY` (security fix or a broken
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


def min_hours():
    try:
        v = yaml.safe_load((Path(__file__).with_name("releasegate.yaml")).read_text(encoding="utf-8")) or {}
        return float(v.get("min_hours", DEFAULT_HOURS))
    except Exception:  # noqa: BLE001 - an unreadable config keeps the default, never opens the gate
        return DEFAULT_HOURS


def _gh_last(slug):
    """-> (tag, epoch seconds) of the newest published release, or None; raises when gh fails."""
    r = subprocess.run(["gh", "release", "list", "-R", slug, "--limit", "1", "--json", "publishedAt,tagName"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-200:])
    rows = json.loads(r.stdout or "[]")
    if not rows:
        return None
    t = time.mktime(time.strptime(rows[0]["publishedAt"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    return rows[0]["tagName"], t


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
    """-> {"ok": True, ...} to go on, or {"ok": False, "state": "batch-window", ...}. `last` is (tag, epoch) or None
    for tests; otherwise gh is asked. A gh failure fails closed."""
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
    if not last or last[0] == tag:
        return {"ok": True, "state": "gate-open", "tag": tag}
    age = (now - last[1]) / 3600.0
    if age >= hours:
        return {"ok": True, "state": "gate-open", "tag": tag}
    wait = f"{hours - age:.1f}"
    if hotfix:
        window = now - hours * 3600
        prior = [h for h in _hotfixes() if h.get("repo") == slug and h.get("t", 0) >= window and h.get("tag") != tag]
        if prior:
            return {"ok": False, "state": "hotfix-used", "tag": tag,
                    "error": f"{slug} already had a hotfix ({prior[-1].get('tag')}) in the last {hours:g} h",
                    "next": "one hotfix per window: batch this fix into the next release"}
        if not any(h.get("repo") == slug and h.get("tag") == tag and h.get("t", 0) >= window for h in _hotfixes()):
            _record(slug, tag, hotfix, now)
        return {"ok": True, "state": "hotfix", "tag": tag, "hotfix": hotfix}
    return {"ok": False, "state": "batch-window", "tag": tag,
            "error": f"{slug} published {last[0]} {age:.1f} h ago (window {hours:g} h)",
            "next": f"batch the fixes, then release once from main after the last one ({wait} h left); "
                    "a security fix or a broken release: --hotfix \"WHY\" (logged)"}
