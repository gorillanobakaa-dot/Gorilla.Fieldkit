"""One cable listener per port (P28, 2026-11-16): 21 `peers link serve` piled up on L15-WIN."""
import errno
import socket
import sys

import pytest

from fieldkit.peers import link


def _sock(opts):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    for o in opts:
        s.setsockopt(*o)
    return s


def test_windows_binds_exclusively_and_posix_only_skips_time_wait():
    assert link.bind_options("win32")[0][1] == getattr(socket, "SO_EXCLUSIVEADDRUSE", -5)
    assert link.bind_options("linux") == [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]


def test_a_second_listener_cannot_bind_a_held_port():
    a = _sock(link.bind_options())
    a.bind(("127.0.0.1", 0))
    a.listen(1)
    b = _sock(link.bind_options())
    try:
        with pytest.raises(OSError) as e:
            b.bind(a.getsockname())
        assert link.port_taken(e.value)
    finally:
        a.close()
        b.close()


@pytest.mark.skipif(sys.platform != "win32", reason="the pile-up is a Windows socket rule")
def test_the_old_option_let_a_second_listener_in_on_windows():
    """The cause, kept as a test: SO_REUSEADDR on both sockets binds a port a live listener holds."""
    old = [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]
    a = _sock(old)
    a.bind(("127.0.0.1", 0))
    a.listen(1)
    b = _sock(old)
    try:
        b.bind(a.getsockname())                       # no error: two listeners on one port
    finally:
        a.close()
        b.close()


def test_port_taken_reads_both_systems():
    assert link.port_taken(OSError(errno.EADDRINUSE, "in use"))
    e = OSError(10048, "in use")
    assert link.port_taken(e)
    assert not link.port_taken(OSError(errno.EACCES if sys.platform != "win32" else 13, "no"))


def test_reload_on_posix_is_in_place(monkeypatch):
    seen = []
    link.reexec(platform="linux", execv=lambda f, a: seen.append((f, a)), popen=None, exit=None)
    assert seen and seen[0][0] == sys.executable


def test_reload_on_windows_starts_a_replacement_records_it_and_ends(monkeypatch, tmp_path):
    monkeypatch.setattr(link, "home", lambda: tmp_path)
    from fieldkit.core import procs
    monkeypatch.setattr(procs, "record", lambda *a, **k: None)
    started, ended = [], []

    class P:
        pid = 4242

    def popen(argv, **kw):
        started.append((argv, kw["creationflags"]))
        return P()
    link.reexec(platform="win32", execv=lambda *a: pytest.fail("no execv on Windows"), popen=popen,
                exit=lambda c: ended.append(c))
    assert started and started[0][0][0] == sys.executable
    assert started[0][1] & 0x8                         # detached: no console of its own
    assert (tmp_path / "serve.pid").read_text() == "4242"
    assert ended == [0]
