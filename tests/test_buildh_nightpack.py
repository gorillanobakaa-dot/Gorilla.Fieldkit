"""The night pack (owner 2026-10-19: "I preapprove it and then go to sleep"): armed once at the owner's terminal, run by
Windows' scheduler, each action once and only when its conditions hold, disarmed at the first surprise."""
import json
import time

import pytest

from fieldkit.buildh import nightpack as np, task


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state" / "build-harness")
    monkeypatch.setattr(np, "DIR", tmp_path / "state" / "nightpack")
    pub = tmp_path / "state" / "publish"
    pub.mkdir(parents=True)
    (pub / "v2.plan.json").write_text(json.dumps({"title": "T", "gh_repo": "o/r", "assets": {}}), encoding="utf-8")
    return tmp_path


def _arm(env, **kw):
    np.prepare("t", release="v2", rejudge="RUN1", say=lambda m: None, **kw)
    return np.arm("t", hours=12, say=lambda m: None, confirm=True, owner=lambda: True,
                  register=lambda tid, h: (True, "registered"))


def _run(**kw):
    out, removed = [], []
    base = dict(say=out.append, agent=lambda: "", speak=lambda t: None, remove=lambda: removed.append(1))
    base.update(kw)
    return np.run("t", **base), out, removed


def test_arm_needs_the_owner_and_a_prepared_pack(env):
    np.prepare("t", release="v2", say=lambda m: None)
    assert np.arm("t", say=lambda m: None, confirm=True, owner=lambda: False) == 3
    assert np.arm("other", say=lambda m: None, confirm=True, owner=lambda: True) == 3


def test_everything_runs_in_order_once_and_disarms(env):
    assert _arm(env) == 0
    sent = []
    rc, out, removed = _run(rejudge=lambda t, a: (True, "re-judged RUN1: PASS"), gate_ok=lambda t: True,
                            send=lambda tag, **k: sent.append(tag) or 0)
    assert rc == 0 and sent == ["v2"] and removed and not np._p("t", "armed").exists()
    report = np._p("t", "morning").with_suffix("").with_suffix(".morning.md")
    assert (np.DIR / "t.morning.md").read_text(encoding="utf-8").count("done:") == 2


def test_a_release_waits_until_the_gate_passes(env):
    _arm(env)
    rc, out, removed = _run(rejudge=lambda t, a: (True, "ok"), gate_ok=lambda t: False,
                            send=lambda tag, **k: pytest.fail("sent before the gate passed"))
    assert rc == 0 and not removed and np._p("t", "armed").exists()                  # still armed, waiting


def test_a_rejudge_that_adds_items_stops_and_disarms(env):
    _arm(env)
    rc, out, removed = _run(rejudge=lambda t, a: (False, "the re-judge ADDED items"), gate_ok=lambda t: True,
                            send=lambda tag, **k: pytest.fail("sent after a failed step"))
    assert rc == 3 and removed and "STOPPED at rejudge" in (np.DIR / "t.morning.md").read_text(encoding="utf-8")


def test_a_pack_changed_after_arming_runs_nothing(env):
    _arm(env)
    p = np._p("t", "pack")
    p.write_text(p.read_text(encoding="utf-8").replace("v2", "v3"), encoding="utf-8")
    rc, out, removed = _run(rejudge=lambda t, a: pytest.fail("ran a changed pack"))
    assert rc == 3 and removed


def test_an_agent_cannot_run_it_and_an_expired_arming_runs_nothing(env):
    _arm(env)
    rc, out, _r = _run(agent=lambda: "agent environment (CLAUDECODE)")
    assert rc == 3
    a = json.loads(np._p("t", "armed").read_text(encoding="utf-8"))
    a["expires"] = time.time() - 1
    np._p("t", "armed").write_text(json.dumps(a), encoding="utf-8")
    rc, out, removed = _run(rejudge=lambda t, a: pytest.fail("ran after expiry"))
    assert rc == 0 and removed and not np._p("t", "armed").exists()


def test_a_release_without_a_leak_gate_does_not_wait_for_one(env):
    np.prepare("t", release="v2", gate=False, say=lambda m: None)
    np.arm("t", say=lambda m: None, confirm=True, owner=lambda: True, register=lambda tid, h: (True, "registered"))
    sent = []
    rc, *_ = _run(gate_ok=lambda t: False, send=lambda tag, **k: sent.append(tag) or 0)
    assert rc == 0 and sent == ["v2"]


def test_the_condition_says_what_is_really_checked(env):
    # 2026-10-19 (worker): with gate=0 the pack still promised a leak-gate PASS
    p = np.prepare("t", release="v2", gate=False, say=lambda m: None)
    assert "no leak gate" in p["actions"][0]["condition"] and "leak gate release PASS" not in p["actions"][0]["condition"]
    assert "leak gate release PASS" in np.prepare("t", release="v2", say=lambda m: None)["actions"][0]["condition"]


# ---- Linux: the systemd user timer is the scheduled task's twin (P9, owner 2026-11-12) -------------------------------

class _R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_linux_arm_registers_a_user_timer_and_an_expiry_stop(env):
    calls = []
    ok, out = np._systemd_register("t", 12, runner=lambda c, **k: calls.append(c) or _R())
    assert ok and out == "registered"
    runs = [c for c in calls if c[0] == "systemd-run"]
    assert len(runs) == 2 and all("--user" in c for c in runs)          # user level: no root
    first, expire = runs
    assert "--unit=gorilla-night-pack" in first and "--on-unit-active=600" in first       # every 10 minutes
    assert "--property=RuntimeMaxSec=7200" in first
    assert first[-6:] == ["-m", "fieldkit", "build-harness", "night-pack", "t", "run"]
    assert "--on-active=43200" in expire and expire[-4:] == ["--user", "stop", "gorilla-night-pack.timer",
                                                             "gorilla-night-pack.service"]
    assert calls[0][:3] == ["systemctl", "--user", "stop"]             # a re-arm replaces the old timers


def test_linux_arm_fails_closed_when_the_expiry_timer_is_refused(env):
    calls = []

    def runner(c, **k):
        calls.append(c)
        return _R(1, "", "no bus") if "--unit=gorilla-night-pack-expire" in c else _R()
    ok, out = np._systemd_register("t", 1, runner=runner)
    assert not ok and "no bus" in out
    assert calls[-1][:3] == ["systemctl", "--user", "reset-failed"]      # the first timer was taken back


def test_linux_arm_reports_a_refused_timer(env):
    ok, out = np._systemd_register("t", 1, runner=lambda c, **k: _R(1, "", "Failed to connect to bus") if c[0] == "systemd-run" else _R())
    assert not ok and "bus" in out


def test_scheduler_picks_systemd_off_windows(monkeypatch):
    seen = []
    monkeypatch.setattr(np, "_systemd_register", lambda *a, **k: seen.append("reg") or (True, "registered"))
    monkeypatch.setattr(np, "_systemd_remove", lambda *a, **k: seen.append("rm"))
    monkeypatch.setattr(np.sys, "platform", "linux")
    assert np._scheduler_register("t", 1)[0] and np._scheduler_remove() is None
    assert seen == ["reg", "rm"]
