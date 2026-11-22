"""F3.2 / F3.4 (owner audit 2026-11-22): the Stop hook and the room hook stay fast."""
import subprocess
import time

import pytest

from fieldkit.autopilot import room
from fieldkit.tokens import rotate


class _R:
    def __init__(self, out, rc=0):
        self.stdout, self.returncode = out, rc


@pytest.fixture()
def gh(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_ROTATE_DIR", str(tmp_path / "rotate"))
    calls = []

    def fake(cmd, **kw):
        calls.append((cmd, kw))
        return _R(fake.answer + "\n")
    fake.answer = "MERGED"
    monkeypatch.setattr(rotate.subprocess, "run", fake)
    return fake, calls


def test_gh_waits_five_seconds_not_thirty(gh):
    fake, calls = gh
    assert rotate.pr_state("https://github.com/o/r/pull/1") == "MERGED"
    assert calls[0][1]["timeout"] == 5


def test_a_known_pr_makes_no_second_network_call(gh):
    fake, calls = gh
    for _ in range(5):
        assert rotate.pr_state("https://github.com/o/r/pull/1") == "MERGED"
    assert len(calls) == 1                                  # a turn with no new PR: no network
    rotate.pr_state("https://github.com/o/r/pull/2")
    assert len(calls) == 2                                  # a new PR URL asks again


def test_an_open_pr_is_asked_again_after_the_ttl(gh, monkeypatch):
    fake, calls = gh
    fake.answer = "OPEN"
    u = "https://github.com/o/r/pull/3"
    assert rotate.pr_state(u) == "OPEN" and rotate.pr_state(u) == "OPEN" and len(calls) == 1
    t = time.time()
    monkeypatch.setattr(rotate.time, "time", lambda: t + rotate.PR_OPEN_TTL_S + 1)
    fake.answer = "MERGED"
    assert rotate.pr_state(u) == "MERGED" and len(calls) == 2


def test_a_failed_gh_is_not_cached(gh):
    fake, calls = gh
    fake.answer = ""
    assert rotate.pr_state("https://github.com/o/r/pull/4") is None
    assert rotate.pr_state("https://github.com/o/r/pull/4") is None and len(calls) == 2


def test_an_injected_runner_bypasses_the_cache(gh):
    seen = []
    assert rotate.pr_state("u", run=lambda *a, **k: seen.append(1) or _R("OPEN\n")) == "OPEN"
    assert rotate.pr_state("u", run=lambda *a, **k: seen.append(1) or _R("OPEN\n")) == "OPEN" and len(seen) == 2


def test_a_held_room_lock_skips_the_write_within_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")
    monkeypatch.setattr(room, "_log", lambda: tmp_path / "room" / "ROOM.log")
    assert room.LOCK_CAP_S <= 0.5
    with room._Lock():
        t = time.time()
        assert room.append("x\ty") is False                 # held: skipped, not waited for 3 s
        assert time.time() - t < 2
    assert room.append("x\ty") is True
