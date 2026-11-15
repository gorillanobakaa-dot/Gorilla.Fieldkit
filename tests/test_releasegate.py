"""One release per batch: the gate refuses inside the window, allows outside it, logs --hotfix, one hotfix per window."""
import json

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


def test_hotfix_is_logged_and_only_one_per_window(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    r = rg.guard("o/r", "v2", "security fix", last=("v1", NOW - 3 * H), now=NOW, hours=24)
    assert r["ok"] and r["state"] == "hotfix"
    rows = [json.loads(l) for l in (tmp_path / "releasegate" / "hotfixes.jsonl").read_text().splitlines()]
    assert rows[0]["why"] == "security fix" and rows[0]["tag"] == "v2" and rows[0]["repo"] == "o/r"
    again = rg.guard("o/r", "v3", "another", last=("v2", NOW - 1 * H), now=NOW + H, hours=24)
    assert not again["ok"] and again["state"] == "hotfix-used"
    assert rg.guard("o/r", "v2", "security fix", last=("v1", NOW - 3 * H), now=NOW + H, hours=24)["ok"]  # same tag again
    assert len((tmp_path / "releasegate" / "hotfixes.jsonl").read_text().splitlines()) == 1


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
