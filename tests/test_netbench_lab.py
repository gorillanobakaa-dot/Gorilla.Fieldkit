"""netbench: the local servers and the relay, end to end, without a browser (127.0.0.1 only, a few seconds)."""
import asyncio
import json
import ssl

import h2.connection
import h2.events
import pytest

from fieldkit.netbench import certs, fixtures, links, relay, servers

FAST = links.Link("test", "test link", 80e6, 40e6, 30, 0.0)


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    c = certs.make(tmp_path_factory.mktemp("nbcerts"))
    rel = relay.Relay(FAST)
    lb = servers.Lab(fixtures.build(), c, rel).start()
    yield lb, c
    lb.stop()


async def _tunnel(port, host):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
    await w.drain()
    head = await r.readuntil(b"\r\n\r\n")
    return r, w, head.split(b"\r\n")[0]


async def _h1_get(port, ca, host, path, alpn=("http/1.1",)):
    r, w, status = await _tunnel(port, host)
    assert b" 200 " in status
    ctx = ssl.create_default_context(cafile=str(ca))
    ctx.set_alpn_protocols(list(alpn))
    await w.start_tls(ctx, server_hostname=host)
    proto = w.get_extra_info("ssl_object").selected_alpn_protocol()
    w.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nAccept-Encoding: gzip\r\n\r\n".encode())
    await w.drain()
    head = (await r.readuntil(b"\r\n\r\n")).decode("latin-1").lower()
    n = int(head.split("content-length:")[1].split("\r\n")[0])
    body = await r.readexactly(n)
    w.close()
    return proto, head, body


async def _h2_get(port, ca, host, path):
    r, w, status = await _tunnel(port, host)
    ctx = ssl.create_default_context(cafile=str(ca))
    ctx.set_alpn_protocols(["h2"])
    await w.start_tls(ctx, server_hostname=host)
    assert w.get_extra_info("ssl_object").selected_alpn_protocol() == "h2"
    conn = h2.connection.H2Connection()
    conn.initiate_connection()
    conn.send_headers(1, [(":method", "GET"), (":path", path), (":scheme", "https"), (":authority", host)], end_stream=True)
    w.write(conn.data_to_send())
    body, done, hdr = bytearray(), False, {}
    while not done:
        data = await r.read(65536)
        assert data, "connection closed early"
        for ev in conn.receive_data(data):
            if isinstance(ev, h2.events.ResponseReceived):
                hdr = {k.decode(): v.decode() for k, v in ev.headers}
            elif isinstance(ev, h2.events.DataReceived):
                body += ev.data
                conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
            elif isinstance(ev, h2.events.StreamEnded):
                done = True
        w.write(conn.data_to_send())
    w.close()
    return hdr, bytes(body)


def test_calibration_matches_the_link(lab):
    lb, _ = lab
    seen = []
    for _ in range(3):                    # a timing test: one quiet attempt out of three is enough on a busy laptop
        c = lb.call(relay.calibrate(lb.ports["relay"], FAST, seconds=1.0), 60)
        seen.append(c)
        assert c["connect_ms"] >= FAST.rtt_ms * 0.9       # the TCP handshake costs one round trip, never less
        assert c["rtt_ms"] >= FAST.rtt_ms * 0.9           # the delay is never shorter than the link's
        if 0.85 <= c["rtt_ratio"] <= 1.4 and c["down_ratio"] >= 0.8 and c["up_ratio"] >= 0.8:
            return
    pytest.fail(f"the relay missed the link three times: {seen}")


def test_http11_through_the_relay_with_gzip(lab):
    lb, c = lab
    before = lb.relay.totals().get("h1.netbench.test", {}).get("down", 0)
    proto, head, body = lb.call(_h1_get(lb.ports["relay"], c["ca"], "h1.netbench.test", "/wiki/Gorilla"), 30)
    assert proto == "http/1.1" and "content-encoding: gzip" in head
    assert body == fixtures.build()["/wiki/Gorilla"]["gzip"]
    assert lb.relay.totals()["h1.netbench.test"]["down"] - before > len(body)    # TLS adds bytes on the wire
    last = lb.origin.requests[-1]
    assert (last["role"], last["proto"], last["path"], last["resp_body"]) == ("h1", "http/1.1", "/wiki/Gorilla", len(body))


def test_h1_host_never_offers_h2(lab):
    lb, c = lab
    proto, _, _ = lb.call(_h1_get(lb.ports["relay"], c["ca"], "h1.netbench.test", "/ping.txt", alpn=("h2", "http/1.1")), 30)
    assert proto == "http/1.1"


def test_http2_blob_through_the_relay(lab):
    lb, c = lab
    hdr, body = lb.call(_h2_get(lb.ports["relay"], c["ca"], "www.netbench.test", "/blob/300000"), 30)
    assert hdr[":status"] == "200" and len(body) == 300000
    assert body == b"".join(fixtures.blob_chunks(300000))
    assert lb.origin.requests[-1]["proto"] == "h2"


def test_trickle_is_slow_and_long_lived(lab):
    lb, c = lab
    import time
    t0 = time.perf_counter()
    _, head, body = lb.call(_h1_get(lb.ports["relay"], c["ca"], "h1b.netbench.test", "/trickle/3"), 30)
    assert body == b"..." and time.perf_counter() - t0 >= 1.9          # one byte a second
    assert "access-control-allow-origin: *" in head


def test_outside_hosts_are_refused_and_counted(lab):
    lb, _ = lab
    _, w, status = lb.call(_tunnel(lb.ports["relay"], "example.org"), 30)
    w.close()
    assert b" 403 " in status
    assert lb.relay.refused_hosts().get("example.org") == 1


def test_report_host_runs_the_steps(lab):
    lb, c = lab
    st = lb.origin.new_run("t1", [("one", "https://www.netbench.test/a"), ("two", "https://www.netbench.test/b")])
    _, head, body = lb.call(_h1_get(lb.ports["relay"], c["ca"], "report.netbench.test", "/go?run=t1&step=one&wait=2"), 30)
    assert b"https://www.netbench.test/a" in body and b"2000" in body
    _, _, body = lb.call(_h1_get(lb.ports["relay"], c["ca"], "report.netbench.test", "/next?run=t1&step=one"), 30)
    assert json.loads(body) == {"step": "two", "url": "https://www.netbench.test/b"} and not st.done.is_set()
    _, _, body = lb.call(_h1_get(lb.ports["relay"], c["ca"], "report.netbench.test", "/next?run=t1&step=two"), 30)
    assert json.loads(body) == {} and st.done.is_set()
