import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Every child `python -m fieldkit` a test starts imports the Fieldkit UNDER TEST too, not whichever checkout pip
# installed (2026-10-19: in a second working tree the installed main checkout answered, and a test failed for a
# reason that was not the code's). Children inherit the environment.
import os as _os_root  # noqa: E402
_os_root.environ["PYTHONPATH"] = _os_root.pathsep.join(
    p for p in (str(Path(__file__).resolve().parents[1]), _os_root.environ.get("PYTHONPATH", "")) if p)

from fieldkit.core.host import host  # noqa: E402

windows_only = pytest.mark.skipif(not host()["is_windows"], reason="Windows-only behaviour")
linux_only = pytest.mark.skipif(not host()["is_linux"], reason="Linux-only behaviour")


def _can_symlink():
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="fieldkit-symlink-probe-"))
    try:
        (d / "l").symlink_to(d / "t")
        return True
    except OSError:
        return False


# Windows lets an account make symbolic links only as administrator or with Developer Mode on (WinError 1314).
# The GitHub runner is an administrator, so these tests pass there and failed only in this laptop's commit hook.
needs_symlinks = pytest.mark.skipif(not _can_symlink(), reason="this account cannot create symbolic links "
                                    "(Windows: administrator or Developer Mode, WinError 1314)")


# Tests never write into the real MCP flight recorder (subprocesses inherit this too).
import os as _os
import tempfile as _tempfile

# The pre-commit hook runs the tests with GIT_INDEX_FILE / GIT_DIR set. Any test that runs git in a temporary
# repository would then read and WRITE Fieldkit's own index (2026-10-01: foreign files ended up in it and the next
# commit was refused). Tests must never inherit git's repository-selecting variables.
for _k in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY",
           "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
           "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
           "GIT_COMMITTER_DATE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
    _os.environ.pop(_k, None)
# `git -c user.name=... commit` hands the hook its -c values in GIT_CONFIG_PARAMETERS (and git config --env style in
# GIT_CONFIG_KEY_n/VALUE_n): the tests then saw an identity and the no-identity rule failed only in the hook (2026-10-16).
for _k in [k for k in _os.environ if k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))]:
    _os.environ.pop(_k, None)
# Tests run with NO git identity, as on the GitHub runners (owner 2026-10-16: "can't this be a deterministic tool?").
# Three times code that commits or tags worked here, where the owner's ~/.gitconfig names him, and failed or went
# silent on CI: apidrift/companions commits (2026-10-15), the archive tag of ship prune (2026-10-16). With the global
# and system config hidden, such code fails in the pre-commit hook on this machine, before anything is pushed.
# Code that needs an identity must bring one (task.checkpoint's IDENT, shiprelease._ident) or say none is set.
_os.environ["GIT_CONFIG_GLOBAL"] = _os.path.join(_tempfile.mkdtemp(prefix="fieldkit-test-gitconfig-"), "empty")
open(_os.environ["GIT_CONFIG_GLOBAL"], "w").close()
_os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
# 2026-10-26: tests that run the real pre-commit hook on a temporary repo must not write their pass/refused rows
# into the real state/testcost log (fieldkit testcost report would count fake refusals as caught bugs).
_os.environ["FIELDKIT_TESTCOST_DIR"] = _tempfile.mkdtemp(prefix="fieldkit-test-testcost-")
for _k in ("EMAIL", "GIT_EDITOR"):
    _os.environ.pop(_k, None)
_os.environ["FIELDKIT_RECORDER"] = _os.path.join(_tempfile.mkdtemp(prefix="fieldkit-test-recorder-"), "mcp.jsonl")
_os.environ["FIELDKIT_OFFICE_BACKUPS"] = _tempfile.mkdtemp(prefix="fieldkit-test-office-backups-")
# C6 (2026-10-28): the tool router's hook also runs the room, the net lease and the hog registry, so a test that calls
# toolrouter.hook must not write a room line, check a station in or register a process in the real state.
_os.environ["FIELDKIT_ROOM_DIR"] = _tempfile.mkdtemp(prefix="fieldkit-test-room-")
_os.environ["FIELDKIT_HOG_DIR"] = _tempfile.mkdtemp(prefix="fieldkit-test-hog-")
_os.environ["FIELDKIT_COORDINATION_DIR"] = _tempfile.mkdtemp(prefix="fieldkit-test-coordination-")


# Tests never look a name up on the real network (2026-10-06: during the build 27 release leak test the commit hook's
# test run resolved incoming.telemetry.mozilla.org, www.anthropic.com and ublockorigin.github.io, and the gate's DNS  telemetry-ok: test fixture
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

# 2026-10-19: the guard above covers this process only; the canary registry test's CHILDREN resolved
# incoming.telemetry.mozilla.org on every run. Every child a test starts inherits this environment and is offline too  telemetry-ok: test fixture
# (a fieldkit child on import, any other Python child through fieldkit/core/offline_site/sitecustomize.py).
from fieldkit.core import offline as _offline

_os.environ.update(_offline.child_env())


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


# No single test makes a sound either (2026-10-16: on Windows a test reached System.Speech and told the owner "the
# laptop is offline" while it was online). Silent still resolves the voice and returns "spoken", so the tests check
# the same path. A test that pops it to fake the runner is fine: it restores it.
if not _voice_tests_on():
    _os.environ["FIELDKIT_VOICE_SILENT"] = "1"


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


# Tests never put a UAC prompt or an admin window on the owner's screen (2026-10-18: three leak-gate tests started
# the real elevated packet helper twice; six "Administrator: Windows PowerShell" windows were left open). A test that
# injects its own runner still exercises the helper.
@pytest.fixture(autouse=True)
def _own_admission_gate(tmp_path_factory, monkeypatch):
    # stage O4 (fieldkit/admission.py): a test never takes or reads a slot of this computer's real gate, and a suite
    # run inside the pre-commit's slot (FIELDKIT_ADMITTED inherited) still exercises the gate, not the nested pass
    monkeypatch.setenv("FIELDKIT_ADMISSION_HOME", str(tmp_path_factory.mktemp("admission")))
    monkeypatch.delenv("FIELDKIT_ADMITTED", raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_real_elevation(monkeypatch):
    import subprocess
    from fieldkit.leakgate import sensors
    real_start = sensors.ElevatedPktmon.start

    def start(self, *a, **k):
        if self.runner is subprocess.run:
            raise RuntimeError("tests never elevate: inject a runner")
        return real_start(self, *a, **k)
    monkeypatch.setattr(sensors.ElevatedPktmon, "start", start)
    yield


# Tests never stop a real process (2026-10-18: the leak gate quiets the machine at start; its tests run the real
# gate): the stop and the holder are no-ops unless a test hands quiet its own fake psutil.
@pytest.fixture(autouse=True)
def _no_real_quiet(monkeypatch):
    from fieldkit.autopilot import quiet
    real_stop = quiet.stop

    def stop(rows, psutil=None, say=print):
        return real_stop(rows, psutil, say) if psutil is not None else []
    monkeypatch.setattr(quiet, "stop", stop)
    monkeypatch.setattr(quiet, "noisy", lambda *a, **k: [] if not k.get("psutil") and not a else _real_noisy(*a, **k))
    monkeypatch.setattr(quiet, "start_holder", lambda pid, log: 0)
    yield


from fieldkit.autopilot.quiet import noisy as _real_noisy  # noqa: E402


# Liveness beacons a test's supervisor writes go to the test's own folder, never the checkout's state/liveness (where
# the real `fieldkit liveness check --act` would read them).
@pytest.fixture(autouse=True)
def _own_liveness_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIVENESS_DIR", str(tmp_path_factory.mktemp("liveness")))
    yield
    from fieldkit.core import liveness
    liveness._CURRENT = None


# Tests never inherit the autopilot marker (2026-10-26: a commit made from inside an autopilot session refused 8
# goal/peers-link tests that CI passes, because FIELDKIT_AUTOPILOT=1 leaked in). A test of the autopilot refusal sets
# it itself with monkeypatch.setenv.
@pytest.fixture(autouse=True)
def _no_inherited_autopilot(monkeypatch):
    monkeypatch.delenv("FIELDKIT_AUTOPILOT", raising=False)
    yield


# Tests see one fixed machine tag, never this computer's (fieldkit/peers/machine.py reads the real household file
# and the hardware otherwise). A test of the tag itself sets machine._CURRENT = None and its own FIELDKIT_LINK_HOME.
@pytest.fixture(autouse=True)
def _fixed_machine_tag(tmp_path_factory, monkeypatch):
    from fieldkit.peers import machine
    monkeypatch.setattr(machine, "_CURRENT", "[TEST-LNX]")
    said = tmp_path_factory.mktemp("said-machine")
    monkeypatch.setattr(machine, "_said_dir", lambda: said)
    yield


# Tests never write the owner's real inbox (2026-10-27: a refused job is an inbox row on the refusing device, and the
# job protocol's refusal tests would have filed theirs in ~/.fieldkit/autopilot/inbox). A test may set its own.
@pytest.fixture(autouse=True)
def _no_real_inbox(tmp_path, monkeypatch):
    from fieldkit.autopilot import inbox
    monkeypatch.setattr(inbox, "INBOX", tmp_path / "test-inbox")
    yield


# The plan line (fieldkit plan next) is saved to state/plan-next.json by zero, the handovers and the plan command:
# a test writes it to its own folder, never the checkout's state/ (2026-10-28).
@pytest.fixture(autouse=True)
def _plan_state_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_PLAN_STATE", str(tmp_path / "plan-next.json"))
    yield


# The room hook records what it showed a session (room-cursors/*.said.json, O2): a test records it in its own folder,
# never the checkout's state/ (2026-10-28: a test's said-file in the real state/ made the next run see nothing new).
@pytest.fixture(autouse=True)
def _room_in_tmp(tmp_path, monkeypatch):
    from fieldkit.autopilot import room
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room-state")
    # the relay line depends on whether THIS computer's relay listens: off in tests, except the test of it
    monkeypatch.setattr(room, "_relay", lambda call, event, ctx: ctx)
    yield


# Every job gets its own git worktree (lifecycle/worktrees.py, 2026-10-28). A test never adds or removes a worktree of
# the checkout it runs in: tasks start in place, and reap sees nothing. tests/test_lessons_to_tools.py imports the
# real functions before this runs and gives them a repository of its own.
@pytest.fixture(autouse=True)
def _no_real_worktrees(monkeypatch):
    from fieldkit.lifecycle import worktrees
    monkeypatch.setattr(worktrees, "make", lambda repo, name, **k: (None, "tests: in place"))
    monkeypatch.setattr(worktrees, "reap", lambda repo, **k: {"removed": [], "kept": []})
    yield
