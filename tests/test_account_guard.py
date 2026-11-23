"""I-34: the quota guard is per ACCOUNT, not per device (L15, VAIO and MacBook Air share one Claude account)."""
import json
import time
from types import SimpleNamespace

import pytest

from fieldkit.lifecycle import models

NOW = 2_000_000_000.0


def _cfg(probe=True):
    acc = {"agents": ["claude", "claude-desktop"], "devices": ["L15-WIN", "VAIO-LNX", "AIR-MAC"]}
    if probe:
        acc["probe"] = ["claude", "-p", "OK", "--max-turns", "1"]
    return {"availability": {"cooldown_minutes": 120, "patterns": ["usage limit", "max turns", "rate_limit"]},
            "agent_availability": {"cooldown_minutes": 120, "unavailable": {}, "accounts": {"anthropic": acc}}}


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path))


def _run(rc, out="", err=""):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)
    run.calls = calls
    return run


def test_a_hold_on_one_member_blocks_the_whole_account():
    d = _cfg()
    models.mark_agent_unavailable("claude", "usage limit", until=NOW + 3600, now=NOW, data=d)
    assert models.is_agent_available("claude-desktop", now=NOW + 1, data=d) is False
    assert models.account_of("claude-desktop", d)[0] == "anthropic"


def test_a_later_proof_of_life_voids_a_quota_hold_from_any_device():
    d = _cfg()
    models.mark_agent_unavailable("claude", "usage limit", until=NOW + 3600, now=NOW, data=d)
    models.note_agent_success("claude-desktop", now=NOW + 10, data=d)
    assert models.is_agent_available("claude", now=NOW + 20, data=d) is True


def test_a_hold_written_after_the_proof_of_life_still_counts():
    d = _cfg()
    models.note_agent_success("claude", now=NOW, data=d)
    models.mark_agent_unavailable("claude", "usage limit", until=NOW + 3600, now=NOW + 10, data=d)
    assert models.is_agent_available("claude", now=NOW + 20, data=d) is False


def test_an_owner_pin_survives_a_proof_of_life_and_is_never_probed():
    d = _cfg()
    models.mark_agent_unavailable("claude", "owner said so", until="always", now=NOW, data=d)
    models.note_agent_success("claude", now=NOW + 10, data=d)
    run = _run(0, "OK")
    ok, why = models.agent_availability("claude", now=NOW + 20, data=d, probe=True, run=run)
    assert ok is False and "owner" in why and run.calls == []


def test_the_probe_clears_a_stale_hold_when_the_account_answers():
    d = _cfg()
    models.mark_agent_unavailable("claude", "usage limit", until=NOW + 3600, now=NOW, data=d)
    run = _run(0, "OK")
    ok, why = models.agent_availability("claude", now=NOW + 20, data=d, probe=True, run=run)
    assert ok is True and "probe answered" in why and len(run.calls) == 1
    assert models.is_agent_available("claude", now=NOW + 30, data=d) is True


def test_max_turns_from_the_probe_means_the_api_answered():
    d = _cfg()
    models.mark_agent_unavailable("claude", "usage limit", until=NOW + 3600, now=NOW, data=d)
    ok, _ = models.agent_availability("claude", now=NOW + 20, data=d, probe=True,
                                      run=_run(1, "Error: Reached max turns (1)"))
    assert ok is True


def test_the_probe_keeps_the_hold_when_the_limit_is_still_out():
    d = _cfg()
    models.mark_agent_unavailable("claude", "usage limit", until=NOW + 3600, now=NOW, data=d)
    ok, why = models.agent_availability("claude", now=NOW + 20, data=d, probe=True,
                                        run=_run(1, "", "You have hit your usage limit"))
    assert ok is False and "blocked" in why


def test_a_failed_probe_fails_closed():
    d = _cfg()
    models.mark_agent_unavailable("claude", "usage limit", until=NOW + 3600, now=NOW, data=d)

    def boom(argv, **kw):
        raise OSError("no such program")
    ok, _ = models.agent_availability("claude", now=NOW + 20, data=d, probe=True, run=boom)
    assert ok is False


def test_without_probe_flag_nothing_is_run():
    d = _cfg()
    models.mark_agent_unavailable("claude", "usage limit", until=NOW + 3600, now=NOW, data=d)
    run = _run(0, "OK")
    ok, _ = models.agent_availability("claude", now=NOW + 20, data=d, run=run)
    assert ok is False and run.calls == []


def test_an_agent_in_no_account_is_its_own_account():
    d = _cfg()
    assert models.account_of("codex", d) == ("codex", ["codex"])
    models.mark_agent_unavailable("codex", "usage limit", until=NOW + 3600, now=NOW, data=d)
    assert models.is_agent_available("codex", now=NOW + 1, data=d) is False
    assert models.is_agent_available("claude", now=NOW + 1, data=d) is True


def test_unblock_clears_every_member():
    d = _cfg(probe=False)
    models.mark_agent_unavailable("claude", "usage limit", until=NOW + 3600, now=NOW, data=d)
    models.clear_agent_unavailable("claude-desktop", data=d)
    assert models.is_agent_available("claude", now=NOW + 1, data=d) is True


def test_an_old_record_with_only_a_text_stamp_is_dated():
    d = _cfg()
    at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW))
    models.agent_availability_path(d).write_text(json.dumps(
        {"claude": {"reason": "rate_limit", "until": NOW + 3600, "source": "quota-guard", "at": at}}), encoding="utf-8")
    assert models.is_agent_available("claude", now=NOW + 1, data=d) is False
    models.note_agent_success("claude", now=NOW + 10, data=d)
    assert models.is_agent_available("claude", now=NOW + 20, data=d) is True


def test_the_shipped_yaml_puts_claude_in_one_account_for_all_three_laptops():
    name, members = models.account_of("claude")
    acc = models.load()["agent_availability"]["accounts"][name]
    assert "claude" in members and set(acc["devices"]) >= {"L15-WIN", "VAIO-LNX", "AIR-MAC"} and acc["probe"]
