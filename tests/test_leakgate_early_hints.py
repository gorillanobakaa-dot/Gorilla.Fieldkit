"""The early-hints scene (2026-10-07, audit B1): the local server answers /early-hints with a 103 reply carrying a
preconnect to an https host and a cross-site preload, then the page. No scene sent a 103 before, so the gap where a 103
preconnect bypassed speculative-parallel-limit 0 survived four release runs."""
import socket

from fieldkit.leakgate import scenarios as sc


def test_the_server_sends_a_103_with_both_links_before_the_page():
    srv = sc.Server()
    try:
        s = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
        s.sendall(b"GET /early-hints HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        data = b""
        while b"</body>" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
    finally:
        srv.close()
    text = data.decode("ascii", "replace")
    first, rest = text.split("\r\n\r\n", 1)
    assert " 103 Early Hints" in first.splitlines()[0]
    assert f"<{sc.EARLY_HINTS_PRECONNECT}>; rel=preconnect" in first
    assert f"<{sc.EARLY_HINTS_PRELOAD}>; rel=preload; as=script" in first
    assert " 200 " in rest.splitlines()[0] and "early-hints" in rest


def test_the_scene_is_a_fail_closed_action_scene_with_no_allowed_host():
    names = {n: hosts for n, _t, _s, _a, hosts, _w in sc.SCENARIOS}
    assert names["early-hints"] == []
    assert "early-hints" in sc.ACTION_SCENARIOS and "early-hints" in sc.REPORTING
    assert sc.SERVED["early-hints"] == ("/early-hints",)
    assert sc.EARLY_HINTS_PRECONNECT.startswith("https://")      # Firefox preconnects to https targets only
