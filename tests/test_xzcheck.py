"""fieldkit xzcheck: an xz archive is as small as xz can make it, without asking too much memory to unpack.

Measured 2026-10-25 on Gorilla OpenCode v0.1.152's .deb (59.7 MiB of content, xz -9e): dict 8 MiB 22.2 MB,
32 MiB 18.3 MB, 64 MiB 17.6 MB; 96 and 128 MiB the same bytes as 64 MiB, with 97 and 129 MiB to unpack.
The tests rebuild those cases at a small scale (3 MB of content -> a 4 MiB dictionary is the right one).
"""
import binascii
import io
import lzma
import random
import struct
import tarfile

import pytest

from fieldkit.method import xzcheck as x

MiB = 1 << 20


def _content(n=3_000_000):
    rng = random.Random(7)
    words = [bytes(rng.randrange(97, 123) for _ in range(rng.randrange(3, 12))) for _ in range(4000)]
    out = bytearray()
    while len(out) < n:
        out += rng.choice(words) + b" "
    return bytes(out[:n])


CONTENT = _content()


def _xz(dict_mib, data=CONTENT):
    return lzma.compress(data, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64,
                         filters=[{"id": lzma.FILTER_LZMA2, "preset": 6, "dict_size": int(dict_mib * MiB)}])


def _with_dict_prop(stream, prop):
    """The same stream with its LZMA2 dictionary property byte replaced (block header CRC32 fixed)."""
    b = bytearray(stream)
    hsize = (b[12] + 1) * 4
    head = b[12:12 + hsize]
    i = head.index(b"\x21\x01") + 2           # filter id 0x21, 1 property byte
    head[i] = prop
    head[-4:] = struct.pack("<I", binascii.crc32(bytes(head[:-4])) & 0xFFFFFFFF)
    b[12:12 + hsize] = head
    return bytes(b)


def test_the_right_dictionary_passes_and_reports_what_it_read():
    r = x.check_stream(_xz(4), 128 * MiB, "right")
    assert r["ok"], r["problems"]
    assert r["dict_mib"] == 4 and r["content_bytes"] == len(CONTENT) and r["blocks"] == 1 and r["fits_cap"]


def test_a_dictionary_bigger_than_the_content_is_memory_for_nothing():
    r = x.check_stream(_xz(8), 128 * MiB, "too big")
    assert not r["ok"] and any("bigger than the content needs" in p for p in r["problems"])


def test_a_dictionary_smaller_than_the_content_leaves_compression_on_the_table():
    r = x.check_stream(_xz(1), 128 * MiB, "too small")
    assert not r["ok"] and any("would compress more" in p for p in r["problems"])


def test_the_memory_cap_is_proven_by_unpacking_not_estimated():
    r = x.check_stream(_xz(4), 4 * MiB, "over the cap")
    assert not r["fits_cap"] and any("cap" in p for p in r["problems"])
    assert not any("smaller than the content" in p for p in r["problems"]), "the cap lowers the target"


def test_a_dictionary_beyond_what_xz_accepts_is_refused_in_words_not_a_crash():
    r = x.check_stream(_with_dict_prop(_xz(4), 38), 128 * MiB, "2 GiB")      # property 38 = 2 GiB
    assert r["dict_mib"] == 2048 and not r["ok"]
    assert any("beyond what xz accepts" in p for p in r["problems"])
    with pytest.raises(x.BadXz):
        x.lzma2_dict(41)
    assert x.lzma2_dict(40) == 0xFFFFFFFF and x.lzma2_dict(20) == 4 * MiB and x.lzma2_dict(21) == 6 * MiB
    assert x.lzma2_dict(28) == 64 * MiB                    # xz -9e, as xz -lvv read it on the real .deb


def test_a_deb_is_checked_member_by_member(tmp_path):
    def member(name, data):
        head = f"{name:<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
        return head + data + (b"\n" if len(data) & 1 else b"")
    deb = tmp_path / "p.deb"
    deb.write_bytes(b"!<arch>\n" + member("debian-binary", b"2.0\n") + member("control.tar.xz", _xz(4, b"c" * 1000))
                    + member("data.tar.xz", _xz(4)))
    names = [n for n, _ in x.streams_in(deb)]
    assert names == ["p.deb:control.tar.xz", "p.deb:data.tar.xz"]


def test_exit_codes(tmp_path, capsys):
    good, bad, junk = tmp_path / "good.xz", tmp_path / "bad.xz", tmp_path / "junk.xz"
    good.write_bytes(_xz(4))
    bad.write_bytes(_xz(8))
    junk.write_bytes(b"not xz at all")
    assert x.main([str(good)]) == 0
    assert x.main([str(good), str(bad)]) == 3
    assert x.main([str(junk)]) == 2
    out = capsys.readouterr().out
    assert "VERDICT PASS" in out and "VERDICT FAIL" in out and "UNREADABLE" in out
