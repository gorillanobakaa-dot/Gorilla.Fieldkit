"""Release windows and the logged hotfix quota."""
import json

import pytest

from fieldkit.method import releasegate as rg, ship

NOW = 1_800_000_000.0
H = 3600.0


def _state(monkeypatch, tmp_path):
    monkeypatch.setenv("FIELDKIT_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("FIELDKIT_HOTFIX", raising=False)


def test_refuses_inside_the_window(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    r = rg.guard("o/r", "v2", last=("v1", NOW - 3 * H), now=NOW, hours=24)
    assert not r["ok"] and r["state"] == "batch-window"
    assert "batch the fixes" in r["next"] and "--hotfix" in r["next"]


def test_allows_outside_the_window_or_first_release(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    assert rg.guard("o/r", "v2", last=("v1", NOW - 25 * H), now=NOW, hours=24)["ok"]
    assert rg.guard("o/r", "v1", last=("v1", NOW - 1 * H), now=NOW, hours=24)["ok"]   # the release itself: more files
    assert rg.guard("o/r", "v2", now=NOW, hours=24)["ok"]                              # conftest: no release yet


def test_hotfix_limit_and_retry_are_deterministic(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    for version in range(2, 12):
        stamp = NOW + version
        r = rg.guard("o/r", f"v{version}-linux", "security fix", last=("v1-linux", NOW - H), now=stamp)
        assert r["ok"] and r["state"] == "hotfix"
    recent = [("v11-linux", NOW), ("v1-windows", NOW)]
    refused = rg.guard("o/r", "v12-linux", "another", last=recent, now=NOW + H)
    assert not refused["ok"] and refused["state"] == "hotfix-used"
    assert "10 of 10" in refused["error"]
    assert rg.guard("o/r", "v2-linux", "retry", last=recent, now=NOW + H)["ok"]
    rows = [json.loads(l) for l in (tmp_path / "releasegate" / "hotfixes.jsonl").read_text().splitlines()]
    assert len(rows) == 10 and rows[0] == {
        "why": "security fix", "tag": "v2-linux", "repo": "o/r", "t": NOW + 2}

    assert rg.guard("o/r", "v12-windows", "another OS", last=recent, now=NOW + H)["ok"]
    assert rg.guard("o/s", "v12-linux", "another repo", last=recent, now=NOW + H)["ok"]
    stamp = NOW + 24 * H + 2
    assert rg.guard("o/r", "v12-linux", "oldest expired", last=("v11-linux", stamp - H), now=stamp)["ok"]


def test_hotfix_fails_closed_when_the_ledger_is_unreadable(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    log = tmp_path / "releasegate" / "hotfixes.jsonl"
    log.parent.mkdir()
    log.write_text("partial record\n", encoding="utf-8")
    result = rg.guard("o/r", "v2", "retry", last=("v1", NOW - H), now=NOW)
    assert not result["ok"] and result["state"] == "hotfix-ledger-error"
    assert log.read_text(encoding="utf-8") == "partial record\n"


def test_candidate_reserves_only_blocked_platforms_and_only_when_all_have_room(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    monkeypatch.setattr(rg, "hotfix_limit", lambda: 1)
    windows = ("v1-win64", NOW - H)
    mac = ("v1-macos", NOW - H)
    platforms = ["debian", "windows", "darwin"]
    refused = rg.guard("o/r", "v2", last=[windows, mac], now=NOW, platforms=platforms)
    assert "mac, windows" in refused["error"]
    assert rg.guard("o/r", "v2-windows", "broken", last=[windows], now=NOW)["ok"]
    assert rg.guard("O/R", "v3", "broken", last=[windows, mac], now=NOW, platforms=platforms)["state"] == "hotfix-used"
    assert rg.guard("o/r", "v3", "broken", last=[mac], now=NOW, platforms=platforms)["ok"]
    assert rg.guard("o/r", "v3", "retry", last=[mac], now=NOW, platforms=platforms)["ok"]
    log = tmp_path / "releasegate" / "hotfixes.jsonl"
    assert [json.loads(line).get("systems") for line in log.read_text().splitlines()] == [None, ["mac"]]


def test_platform_candidates_treat_older_unscoped_releases_as_covering_the_matrix():
    assert not rg.guard("o/r", "v2", last=[("v1-rc1", NOW - H)], now=NOW, platforms=["debian"])["ok"]
    assert rg.guard("o/r", "v2", last=[("v1-windows", NOW - H)], now=NOW, platforms=["debian"])["ok"]


def test_public_quota_ignores_attempts_and_uses_shared_history(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path / "publisher-a")
    published = [("v1-windows", NOW - 25 * H), ("v2-windows", NOW - 3 * H)]
    monkeypatch.setattr(rg, "_gh_published", lambda slug: list(published))
    for version in range(3, 15):
        tag = f"v{version}"
        verdict = rg.guard("o/r", tag, "broken", now=NOW, platforms=["windows"], published_only=True)
        assert verdict["ok"]
        rg.record_hotfix_attempt("o/r", tag, "broken", verdict["systems"])
    assert len(rg._hotfixes()) == 12

    published.extend((f"v{version}-windows", NOW - H + version) for version in range(3, 11))
    _state(monkeypatch, tmp_path / "publisher-b")
    assert rg.guard("o/r", "v15", "last slot", now=NOW, platforms=["windows"], published_only=True)["ok"]
    published.append(("v15-windows", NOW - 15))
    denied = rg.guard("o/r", "v16", "broken", now=NOW, platforms=["windows"], published_only=True)
    assert denied["state"] == "hotfix-used" and "10 of 10" in denied["error"]
    assert rg.guard("o/r", "v15-windows", "retry", now=NOW, platforms=["windows"], published_only=True)["ok"]
    assert rg.guard("o/r", "v16", "broken", now=NOW, platforms=["debian"], published_only=True)["ok"]


def test_signed_publisher_lock_is_shared_between_checkouts(monkeypatch, tmp_path):
    monkeypatch.setattr(rg.Path, "home", lambda: tmp_path)
    _state(monkeypatch, tmp_path / "checkout-a")
    with rg.publication_slot(slug="owner/repo", single_publisher=True):
        _state(monkeypatch, tmp_path / "checkout-b")
        with rg.publication_slot(slug="owner/unrelated", single_publisher=True):
            pass
        with pytest.raises(ValueError, match="another ship command"):
            with rg.publication_slot(slug="Owner/Repo", single_publisher=True):
                pytest.fail("the second checkout acquired the lock")
    with rg.publication_slot(slug="owner/repo", single_publisher=True):
        pass


def test_gh_failure_fails_closed(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)

    def boom(slug):
        raise RuntimeError("no gh")
    monkeypatch.setattr(rg, "_gh_last", boom)
    assert rg.guard("o/r", "v2")["state"] == "gate-unreadable"


def test_min_hours_comes_from_data():
    assert rg.min_hours() == 24.0


def test_gh_release_stops_at_the_gate_and_never_creates(tmp_path, monkeypatch):
    from fieldkit.core import sync
    monkeypatch.setattr(sync, "_slug", lambda repo: "o/r")
    called = []
    r = ship.gh_release(str(tmp_path), "v2", (), mandate=lambda *a, **k: ("allow", "ok", None),
                        run=lambda *a, **k: called.append(a),
                        gate=lambda slug, tag, hotfix: {"ok": False, "state": "batch-window", "tag": tag, "error": "x", "next": "y"})
    assert not r["ok"] and r["state"] == "batch-window" and not called


def test_a_non_github_remote_has_no_releases_to_count():
    assert rg.guard("/tmp/some/remote/Repo", "v1")["state"] == "gate-not-github"


def test_each_operating_system_has_its_own_window(monkeypatch, tmp_path):
    """Owner 2026-11-17: Linux, Windows, macOS, FreeBSD, Android and ChromeOS may each publish the same day; only a
    second release for the SAME system inside the window is refused."""
    _state(monkeypatch, tmp_path)
    recent = [("v160.0.0-linux", NOW - 1 * H)]
    assert rg.guard("o/r", "v160.0.0-windows", last=recent, now=NOW, hours=24)["ok"]
    assert rg.guard("o/r", "v157.0.0-mac", last=recent, now=NOW, hours=24)["ok"]
    for other in ("freebsd", "android", "chromeos"):
        assert rg.guard("o/r", f"v160.0.0-{other}", last=recent, now=NOW, hours=24)["ok"], other
    six = recent + [("v160.0.0-windows", NOW - 0.5 * H), ("v157.0.0-mac", NOW - 0.2 * H)]
    r = rg.guard("o/r", "v160.0.1-linux", last=six, now=NOW, hours=24)
    assert not r["ok"] and r["state"] == "batch-window" and "v160.0.0-linux" in r["error"] and "linux" in r["error"]
    assert not rg.guard("o/r", "v158.0-win64-build3", last=six, now=NOW, hours=24)["ok"]   # win64 is windows
    assert not rg.guard("o/r", "v2.0.0", last=[("v1.9.0", NOW - H)], now=NOW, hours=24)["ok"]  # a plain project: as before


def test_system_of_reads_the_tag_suffix():
    assert rg.system_of("v160.0.0-linux") == "linux"
    assert rg.system_of("v158.0-win64-build2") == "windows"
    assert rg.system_of("macbook-air") == "mac"
    assert rg.system_of("v1.2.3") == "" and rg.system_of("v158.0-2") == ""


def test_every_place_that_publishes_a_release_goes_through_the_gate():
    """Drift guard (2026-11-15): a new `gh release create` or tag push that skips releasegate would bring back one
    release per fix. Every module that runs one must call releasegate.guard."""
    import re
    from pathlib import Path
    root = Path(rg.__file__).resolve().parents[1]
    publishers = []
    for f in root.rglob("*.py"):
        if f.name in ("mandate.py", "hardlines.py", "releasegate.py"):      # they describe or block, never publish
            continue
        t = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r'\[\s*"gh",\s*"release",\s*"create"|\("release", "create"|\["release", "create"|"release", "create", tag', t):
            publishers.append(f)
    assert publishers, "the scan found no publisher: fix the pattern"
    missing = [str(f.relative_to(root)) for f in publishers if "releasegate" not in f.read_text(encoding="utf-8", errors="replace")]
    assert not missing, f"publishes a release without the one-release-per-batch gate: {missing}"


def test_gh_recent_skips_draft_and_zero_timestamps_and_uses_utc(monkeypatch):
    """Windows mktime overflows on gh's 0001-01-01T00:00:00Z draft timestamp (2026-11-18)."""
    import calendar
    import time
    from types import SimpleNamespace

    payload = json.dumps([
        {"tagName": "leakgate-ci-160", "publishedAt": "0001-01-01T00:00:00Z"},
        {"tagName": "v0.10.0-draft", "publishedAt": "2026-11-18T01:00:00Z", "isDraft": True},
        {"tagName": "v0.9.4", "publishedAt": "2026-11-10T07:25:29Z", "isDraft": False},
    ])
    monkeypatch.setattr(rg.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=payload, stderr=""))
    got = rg._gh_recent("o/r")
    want_ts = calendar.timegm(time.strptime("2026-11-10T07:25:29", "%Y-%m-%dT%H:%M:%S"))
    assert got == [("v0.9.4", want_ts)]
