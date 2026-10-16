import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fieldkit.core.host import host  # noqa: E402

windows_only = pytest.mark.skipif(not host()["is_windows"], reason="Windows-only behaviour")
linux_only = pytest.mark.skipif(not host()["is_linux"], reason="Linux-only behaviour")


# Tests never write into the real MCP flight recorder (subprocesses inherit this too).
import os as _os
import tempfile as _tempfile

# The pre-commit hook runs the tests with GIT_INDEX_FILE / GIT_DIR set. Any test that runs git in a temporary
# repository would then read and WRITE Fieldkit's own index (2026-10-01: foreign files ended up in it and the next
# commit was refused). Tests must never inherit git's repository-selecting variables.
for _k in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY",
           "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
           "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
           "GIT_COMMITTER_DATE"):
    _os.environ.pop(_k, None)
# Tests run with NO git identity, as on the GitHub runners (owner 2026-10-16: "can't this be a deterministic tool?").
# Three times code that commits or tags worked here, where the owner's ~/.gitconfig names him, and failed or went
# silent on CI: apidrift/companions commits (2026-10-15), the archive tag of ship prune (2026-10-16). With the global
# and system config hidden, such code fails in the pre-commit hook on this machine, before anything is pushed.
# Code that needs an identity must bring one (task.checkpoint's IDENT, shiprelease._ident) or say none is set.
_os.environ["GIT_CONFIG_GLOBAL"] = _os.path.join(_tempfile.mkdtemp(prefix="fieldkit-test-gitconfig-"), "empty")
open(_os.environ["GIT_CONFIG_GLOBAL"], "w").close()
_os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
for _k in ("EMAIL", "GIT_EDITOR"):
    _os.environ.pop(_k, None)
_os.environ["FIELDKIT_RECORDER"] = _os.path.join(_tempfile.mkdtemp(prefix="fieldkit-test-recorder-"), "mcp.jsonl")
_os.environ["FIELDKIT_OFFICE_BACKUPS"] = _tempfile.mkdtemp(prefix="fieldkit-test-office-backups-")


# Tests never look a name up on the real network (2026-10-06: during the build 27 release leak test the commit hook's
# test run resolved incoming.telemetry.mozilla.org, www.anthropic.com and ublockorigin.github.io, and the gate's DNS
# witness had to work out that it was not the browser). Loopback names and IP literals still resolve.
import ipaddress as _ip
import socket as _socket

_real_getaddrinfo = _socket.getaddrinfo


def _offline_getaddrinfo(host, *args, **kwargs):
    h = host.decode() if isinstance(host, bytes) else host
    if h in (None, "", "localhost") or str(h).endswith(".localhost"):
        return _real_getaddrinfo(host, *args, **kwargs)
    try:
        _ip.ip_address(str(h).strip("[]"))
        return _real_getaddrinfo(host, *args, **kwargs)
    except ValueError:
        raise _socket.gaierror(11001, f"tests are offline: {h} was not looked up")


def _offline_gethostbyaddr(addr):
    raise _socket.herror(1, f"tests are offline: no reverse lookup of {addr}")


_socket.getaddrinfo = _offline_getaddrinfo
_socket.gethostbyaddr = _offline_gethostbyaddr


# Tests never load the CPU or depend on the real fan control: the build guard (fan check + governor) is replaced by a
# no-op for every test, except tests marked `real_build_guard`, which test the guard itself on fake status files.
# There is deliberately NO runtime switch to skip the guard (2026-10-13: fail closed; an agent could flip a switch).
@pytest.fixture(autouse=True)
def _no_real_build_guard(request, monkeypatch):
    if request.node.get_closest_marker("real_build_guard"):
        yield
        return
    import contextlib
    from fieldkit.thermal import buildguard

    @contextlib.contextmanager
    def _noop(*a, **k):
        yield None
    monkeypatch.setattr(buildguard, "guard", _noop)
    monkeypatch.setattr(buildguard, "fan_check", lambda *a, **k: (True, "fan check: test (no real fan)"))
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "real_build_guard: run the real build guard (fan check) on fake inputs")


# ============================================================================
# THE SUITE SAYS WHAT IT IS DOING, OUT LOUD
# ============================================================================
# 2026-10-13. The suite already spoke, but it said "test, a test", which is
# ambiguous enough that the owner asked what it was for. His call: keep the
# voice, because an eight-minute run that announces itself keeps you honest,
# but make it say the actual thing. Funny AND informative, in that order of
# difficulty.
#
# So instead of stray words from individual tests, the SESSION speaks: once at
# the start with the real count, once at the end with the real result. That is
# the genuinely useful bit, because a long run finishing is exactly the moment
# you are not looking at the terminal.
#
# OFF BY DEFAULT. Somebody cloning this repo should not have their computer
# start talking at them, and a build server should never talk at all. Opt in
# with FIELDKIT_VOICE_TESTS=1.
import random as _random

_VOICE_TESTS = "FIELDKIT_VOICE_TESTS"


def _voice_tests_on():
    return _os.environ.get(_VOICE_TESTS, "").strip() not in ("", "0", "false", "no")


def _announce(text):
    """Say it, and never let the voice break the test run."""
    try:
        from fieldkit.buildh import herald
        herald.say(text)
    except Exception:
        pass


# KEEP EVERY LINE SHORT.
#
# 2026-10-13, the owner, listening to it: "on long sentences the voice gets a
# bit weird and hard to understand". He was right, and the measurement agrees:
# the worst line was 88 characters with a four-digit number read out longhand
# in the middle of it, so by the time the sentence ended the number was gone.
#
# A spoken line is not a written one. It has no punctuation you can see, no
# second reading, and no scrollback. So: one clause, the number early, and
# nothing after the full stop that matters. SPOKEN_LIMIT is enforced by a test.
#
# AND NO HOMOPHONES. A spoken line has no SPELLING either, which is a separate
# trap. An earlier test sentence said "the full Fieldkit suite" and the owner
# heard "the full Fieldkit sweet" — because that is correct English. "Suite" is
# pronounced identically to "sweet", so the synthesiser was right and the WORD
# was wrong. Nothing here says "suite", and "Reality won again" became "wins"
# because "won" and "one" are indistinguishable in a line full of numbers.
SPOKEN_LIMIT = 60

_STARTING = [
    "Running {n} tests. Put the kettle on.",
    "{n} tests. Let us find out what I broke.",
    "{n} tests, starting now.",
    "{n} tests. Gorilla science begins.",
]
_ALL_PASSED = [
    "All {p} passed. Gorilla science has occurred.",
    "{p} passed. Nothing failed.",
    "{p} passed, zero failures. Suspicious.",
    "Finished clean. {p} passed.",
]
_SOME_FAILED = [
    "{f} failed, out of {t}. Check the output.",
    "{f} failed, out of {t}. Reality wins again.",
    "Bad news. {f} failed, out of {t}.",
]


def pytest_report_header(config):
    if _voice_tests_on():
        return f"voice: on ({_VOICE_TESTS}=1); the suite will say when it finishes"
    return None


def pytest_collection_finish(session):
    if _voice_tests_on() and len(session.items) > 50:   # not for a single-file run
        _announce(_random.choice(_STARTING).format(n=len(session.items)))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not _voice_tests_on():
        return
    stats = terminalreporter.stats
    passed = len(stats.get("passed", []))
    failed = len(stats.get("failed", [])) + len(stats.get("error", []))
    total = passed + failed + len(stats.get("skipped", []))
    if total <= 50:
        return
    line = (_random.choice(_SOME_FAILED).format(f=failed, t=total) if failed
            else _random.choice(_ALL_PASSED).format(p=passed))
    _announce(line)
