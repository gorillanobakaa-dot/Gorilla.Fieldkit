"""P27: the leak gate reads classic pcap (tcpdump, Linux) and pcapng (pktmon, Windows) through one reader."""
import socket
import struct

import pytest

dpkt = pytest.importorskip("dpkt")
from fieldkit.leakgate import ci, extras, pcapio, sensors  # noqa: E402


def _dns_frame(name="example.org"):
    q = dpkt.dns.DNS(qd=[dpkt.dns.DNS.Q(name=name, type=dpkt.dns.DNS_A)])
    u = dpkt.udp.UDP(sport=53000, dport=53, data=bytes(q))
    ip = dpkt.ip.IP(src=socket.inet_aton("10.0.0.2"), dst=socket.inet_aton("1.1.1.1"), p=17, data=u)
    return bytes(dpkt.ethernet.Ethernet(type=0x0800, data=ip))


def _classic(path, frames):
    with open(path, "wb") as f:
        w = dpkt.pcap.Writer(f)
        for i, fr in enumerate(frames):
            w.writepkt(fr, ts=100 + i)


def _ng(path, frames):
    with open(path, "wb") as f:
        w = dpkt.pcapng.Writer(f)
        for i, fr in enumerate(frames):
            w.writepkt(fr, ts=100 + i)


@pytest.mark.parametrize("writer,kind", [(_classic, "pcap"), (_ng, "pcapng")])
def test_both_formats_give_the_same_dns_event(tmp_path, writer, kind):
    p = tmp_path / "c.cap"
    writer(p, [_dns_frame()])
    assert pcapio.detect(p.read_bytes()[:4]) == kind
    assert len(list(pcapio.packets(p))) == 1
    ev = sensors.parse_pcap(p, set())
    assert any(e["kind"] == "dns-wire" and e["value"] == "example.org" for e in ev), ev


@pytest.mark.parametrize("magic", [0xa1b2c3d4, 0xd4c3b2a1, 0xa1b23c4d, 0x4d3cb2a1])
def test_every_classic_magic_is_pcap(magic):
    assert pcapio.detect(struct.pack("<I", magic)) == "pcap"


def test_garbage_is_refused_and_parse_pcap_does_not_crash(tmp_path):
    p = tmp_path / "x.cap"
    p.write_bytes(b"PK\x03\x04 zip, not a capture")
    with pytest.raises(ValueError):
        list(pcapio.packets(p))
    assert sensors.parse_pcap(p, set()) == []
    assert extras.tls_details(p, {1}) == []


def test_tls_details_reads_a_classic_pcap(tmp_path):
    p = tmp_path / "c.cap"
    _classic(p, [_dns_frame()])
    assert extras.tls_details(p, {53000}) == []        # no TLS in it: empty, and no "not a SHB" crash


def test_a_crash_keeps_the_partial_run(tmp_path):
    run = tmp_path / "runs" / "20261113-000000"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text('{"kind": "dns"}\n')
    (run / "big.pcap").write_bytes(b"x" * 10)
    art = ci.collect_partial(tmp_path, "RuntimeError: boom")
    assert (art / "events.jsonl").is_file() and not (art / "big.pcap").exists()
    assert "boom" in (art / "crash.txt").read_text()
