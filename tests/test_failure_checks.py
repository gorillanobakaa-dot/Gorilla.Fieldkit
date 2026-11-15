"""Stored install/release failure checks: each fires on its broken fixture (fake HOME), is quiet on a clean one."""
import os
import subprocess
import sys

import pytest

from fieldkit.build import failure_checks as fc, triage


@pytest.mark.parametrize("kind", ["install", "release"])
def test_self_test_passes(kind):
    ok, rows = fc.self_test(kind)
    assert ok and rows and all(r["ok"] for r in rows)


@pytest.mark.parametrize("name", sorted(fc.CHECKS))
def test_check_fires_only_on_damage(name, tmp_path):
    fn, fx = fc.CHECKS[name]
    assert fn(fx(tmp_path / "bad", True))
    assert not fn(fx(tmp_path / "good", False))


def test_every_signature_has_a_check_and_sample():
    for kind in ("install", "release"):
        for s in triage.load_signatures(kind):
            assert s["check"] in fc.CHECKS and s["_rx"].search(s["sample"])


def test_icon_shadow_names_the_user_copy(tmp_path):
    ctx = fc._fx_icon_shadow(tmp_path, True)
    (msg,) = fc.icon_shadow(ctx)
    assert ".local" in msg and "shadows" in msg


def test_same_bytes_do_not_shadow(tmp_path):
    assert fc.icon_shadow(fc._fx_icon_shadow(tmp_path, False)) == []


def test_icon_cache_stale_names_the_fix(tmp_path):
    (msg,) = fc.icon_cache(fc._fx_icon_cache(tmp_path, True))
    assert "gtk-update-icon-cache" in msg


def test_rolling_url_ignores_binaries_and_pinned(tmp_path):
    ctx = fc._fx_rolling(tmp_path, False)
    (ctx["root"] / "blob.bin").write_bytes(b"releases/download/latest/x")
    assert fc.rolling_url(ctx) == []


@pytest.mark.parametrize("branch,fires", [("main", True), ("HEAD", True), ("wip/x", True), ("feat/x", False),
                                          ("fix/x", False), ("release-1", False), ("night/a", False), ("", False)])
def test_branch_mandate(branch, fires):
    assert bool(fc.branch_mandate({"branch": branch})) is fires


def test_run_reports_fake_home_hits(tmp_path):
    ctx = fc._fx_desktop_exec(tmp_path, True)
    hits = fc.run("install", dict(ctx, use_env=False))
    assert [h["id"] for h in hits] == ["desktop-entry-dangling-exec"] and hits[0]["fix"]


def test_triage_names_headless_push_log():
    r = triage.triage_text("pre-push hook: push refused by the mandate; GORILLA_OWNER_APPROVED is not set", "release")
    assert [m["id"] for m in r["matches"]] == ["headless-push-refused"]


def _cli(*a):
    env = {k: v for k, v in os.environ.items() if k != "XDG_DATA_HOME"}
    return subprocess.run([sys.executable, "-m", "fieldkit", "triage", *a], capture_output=True, text=True, env=env)


def test_cli_self_test_and_checks(tmp_path):
    for kind in ("install", "release"):
        r = _cli("self-test", "--kind", kind)
        assert r.returncode == 0 and f"triage self-test --kind {kind}: OK" in r.stdout
    assert _cli("self-test").returncode == 2
    assert _cli("checks", "--kind", "install", "--home", str(tmp_path / "empty")).returncode == 0
    ctx = fc._fx_desktop_exec(tmp_path, True)
    r = _cli("checks", "--kind", "install", "--home", str(ctx["home"]))
    assert r.returncode == 3 and "does not exist" in r.stdout


def test_self_test_fails_on_bad_sample_and_noisy_check(monkeypatch):
    real = triage.load_signatures

    def bad_sample(name):
        return [dict(s, sample="nothing relevant here") for s in real(name)]
    monkeypatch.setattr(triage, "load_signatures", bad_sample)
    ok, rows = fc.self_test("install")
    assert not ok and all("sample" in r["why"] for r in rows)
    monkeypatch.setattr(triage, "load_signatures", real)
    fn, fx = fc.CHECKS["branch-mandate"]
    monkeypatch.setitem(fc.CHECKS, "branch-mandate", (lambda ctx: ["always"], fx))
    ok, rows = fc.self_test("release")
    assert not ok and any("clean fixture" in r["why"] for r in rows)
