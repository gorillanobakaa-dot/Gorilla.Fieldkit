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
