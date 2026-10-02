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
