"""Capture judgement and the TLS ClientHello SNI reader."""
from fieldkit.buildh import capture


def test_judge_splits_vendor_trackers_and_others():
    v, tr, un = capture.judge({"firefox.settings.services.mozilla.com", "securepubads.g.doubleclick.net", "www.anthropic.com",
                               "cdn.anthropic.com", "ublockorigin.github.io", "example.net", "127.0.0.1"}, "www.anthropic.com")
    assert v == {"firefox.settings.services.mozilla.com"} and tr == {"securepubads.g.doubleclick.net"} and un == {"example.net"}


def test_sni_from_a_client_hello():
    name = b"push.services.mozilla.com"
    sni_ext = (0).to_bytes(2, "big") + (len(name) + 5).to_bytes(2, "big") + (len(name) + 3).to_bytes(2, "big") + b"\x00" + len(name).to_bytes(2, "big") + name
    body = b"\x03\x03" + b"\x00" * 32 + b"\x00" + b"\x00\x02\x13\x01" + b"\x01\x00" + len(sni_ext).to_bytes(2, "big") + sni_ext
    hs = b"\x01" + len(body).to_bytes(3, "big") + body
    rec = b"\x16\x03\x01" + len(hs).to_bytes(2, "big") + hs
    assert capture._sni(rec) == "push.services.mozilla.com"
    assert capture._sni(b"\x17\x03\x03\x00\x10" + b"x" * 60) is None


# ---- Linux: the same three witnesses (P9, owner 2026-11-12) -------------------------------------------------------------
import io
import json
import os
import socket
import subprocess
import sys
import tarfile

import psutil
import pytest

linux = pytest.mark.skipif(sys.platform == "win32", reason="the Linux path of the capture")


def _client_hello(name):
    b = name.encode()
    sni_ext = (0).to_bytes(2, "big") + (len(b) + 5).to_bytes(2, "big") + (len(b) + 3).to_bytes(2, "big") + b"\x00" + len(b).to_bytes(2, "big") + b
    body = b"\x03\x03" + b"\x00" * 32 + b"\x00" + b"\x00\x02\x13\x01" + b"\x01\x00" + len(sni_ext).to_bytes(2, "big") + sni_ext
    hs = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + len(hs).to_bytes(2, "big") + hs


@linux
def test_parse_pcap_reads_sni_and_dns_from_a_tcpdump_any_file(tmp_path):
    import dpkt
    pcap = tmp_path / "x.pcap"
    frames = []
    tcp = dpkt.tcp.TCP(sport=40000, dport=443, flags=dpkt.tcp.TH_PUSH, data=_client_hello("push.services.mozilla.com"))
    udp = dpkt.udp.UDP(sport=40001, dport=53, data=bytes(dpkt.dns.DNS(qd=[dpkt.dns.DNS.Q(name="telemetry.mozilla.org")])))
    other = dpkt.udp.UDP(sport=55555, dport=53, data=bytes(dpkt.dns.DNS(qd=[dpkt.dns.DNS.Q(name="not.ours.example")])))
    for l4, proto in ((tcp, dpkt.ip.IP_PROTO_TCP), (udp, dpkt.ip.IP_PROTO_UDP), (other, dpkt.ip.IP_PROTO_UDP)):
        ip = dpkt.ip.IP(src=socket.inet_aton("10.0.0.2"), dst=socket.inet_aton("93.184.216.34"), p=proto, data=l4)
        ip.len = len(bytes(ip))
        sll = dpkt.sll.SLL(type=4, hrd_type=1, hlen=6, hdr=b"\x00" * 8, data=ip)
        frames.append(bytes(sll))
    with open(pcap, "wb") as f:
        w = dpkt.pcap.Writer(f, linktype=dpkt.pcap.DLT_LINUX_SLL)
        for i, fr in enumerate(frames):
            w.writepkt(fr, ts=i)
    got = capture.parse_pcap(pcap, {40000, 40001})
    assert got["sni"] == {"push.services.mozilla.com"} and got["dns"] == {"telemetry.mozilla.org"}
    assert ("93.184.216.34", 443) in got["tcp"] and "not.ours.example" not in got["dns"]       # only the browser's ports


@linux
def test_sockets_of_a_process_tree_are_read_without_root():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    cli = socket.create_connection(srv.getsockname())
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    try:
        socks = capture._sockets(capture._tree_pids(os.getpid()))
        assert ("tcp", str(cli.getsockname()[1]), f"127.0.0.1:{srv.getsockname()[1]}") in socks
        assert ("udp", str(udp.getsockname()[1]), "127.0.0.1") in socks
    finally:
        srv.close(), cli.close(), udp.close()


@linux
def test_stop_tree_stops_only_the_process_it_started():
    mine = subprocess.Popen(["sleep", "60"])
    other = subprocess.Popen(["sleep", "60"])
    try:
        capture._stop_tree(mine.pid)
        mine.wait(timeout=10)                                      # psutil may have reaped it: the code is not the proof
        assert not psutil.pid_exists(mine.pid) and other.poll() is None
    finally:
        other.kill()
        other.wait()


@linux
def test_test_copy_unpacks_a_tarball_with_policies_and_finds_the_binary(tmp_path):
    tb = tmp_path / "gorilla.tar.xz"
    with tarfile.open(tb, "w:xz") as t:
        for name, data in (("gorilla/gorilla-unleashed", b"bin"), ("gorilla/omni.ja", b"x"), ("../evil", b"no")):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o755
            t.addfile(ti, io.BytesIO(data))
    copy = capture.test_copy(tb, tmp_path / "copy", {"DisableTelemetry": True})
    assert capture.browser_exe(copy) == copy / "gorilla-unleashed" and (copy / "omni.ja").is_file()
    assert json.loads((copy / "distribution" / "policies.json").read_text())["policies"] == {"DisableTelemetry": True}
    assert not (tmp_path / "evil").exists()


@linux
def test_test_copy_unpacks_a_deb_without_installing_it(tmp_path):
    if subprocess.run(["which", "dpkg-deb"], capture_output=True).returncode != 0:
        pytest.skip("no dpkg-deb")
    root = tmp_path / "root"
    (root / "usr/lib/gorilla-unleashed").mkdir(parents=True)
    (root / "usr/lib/gorilla-unleashed/gorilla-unleashed").write_bytes(b"bin")
    (root / "DEBIAN").mkdir()
    (root / "DEBIAN/control").write_text("Package: gorilla-unleashed\nVersion: 1\nArchitecture: all\nMaintainer: x <x@example.org>\nDescription: t\n")
    deb = tmp_path / "gorilla-unleashed_1_all.deb"
    subprocess.run(["dpkg-deb", "--build", str(root), str(deb)], check=True, capture_output=True)
    copy = capture.test_copy(deb, tmp_path / "copy")
    assert capture.browser_exe(copy).is_file() and not list(tmp_path.glob("gcap-deb-*"))      # the staging folder is gone
