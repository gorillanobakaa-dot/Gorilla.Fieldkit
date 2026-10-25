"""Is an xz archive as small as xz can make it, without asking too much memory of the machine that unpacks it?

    fieldkit xzcheck FILE [FILE...] [--cap-mib 128] [--json]
    FILE: .xz / .tar.xz / .txz, or a .deb (its data.tar.xz and control.tar.xz are checked)

Two rules, read from the FINISHED file, never from the command that made it (a model "optimizing" with
`--lzma2=preset=9e,dict=512MiB` must be caught whatever it typed):

1. Unpacking needs at most --cap-mib of memory. Proven by unpacking the stream with that memory limit
   (Python's lzma decoder refuses past it), not estimated.
2. The dictionary is no bigger than the content: at most the smallest power of two that holds the
   uncompressed data. A bigger one compresses nothing more and only costs the unpacking machine memory.
3. ...and no smaller than it (or than the biggest power of two the cap allows): the most compression
   xz can give. Owner, 2026-10-25: "maximum compression possible, but not crazy".
   Measured 2026-10-25 on Gorilla OpenCode v0.1.152's .deb (59.7 MiB of content), xz -9e:
   dict 8 MiB 22,180,176 bytes, 32 MiB 18,332,984, 64 MiB 17,619,720; 96 and 128 MiB the same bytes as
   64 MiB, needing 97 and 129 MiB to unpack instead of 65.

A dictionary beyond 1.5 GiB (what xz itself accepts) is reported as such, not as a crash.
Exit 0 every stream passes, 3 a rule broken, 2 unreadable input.
"""
import argparse
import binascii
import io
import lzma
import struct
from pathlib import Path

from . import emit

MiB = 1 << 20
XZ_MAX_DICT = 1536 * MiB          # the largest dictionary xz will use (LZMA2 allows a property up to 4 GiB - 1)
XZ_MAGIC = b"\xfd7zXZ\x00"


class BadXz(ValueError):
    """Not an xz stream this tool can read."""


def lzma2_dict(prop: int) -> int:
    """LZMA2 dictionary-size property byte -> bytes (xz file format 5.3.1)."""
    if prop > 40:
        raise BadXz(f"LZMA2 dictionary property {prop} is invalid (max 40)")
    if prop == 40:
        return 0xFFFFFFFF
    return (2 | (prop & 1)) << (prop // 2 + 11)


def _varint(b: bytes, i: int):
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, i
        shift += 7
        if shift > 63:
            raise BadXz("varint too long")


def blocks(data: bytes) -> list:
    """[(dict_bytes, uncompressed_bytes)] for every block of one xz stream, from the index and block headers."""
    if data[:6] != XZ_MAGIC or data[-2:] != b"YZ":
        raise BadXz("no xz stream header/footer")
    backward = (struct.unpack("<I", data[-8:-4])[0] + 1) * 4
    index = data[-12 - backward:-12]
    if not index or index[0] != 0:
        raise BadXz("no xz index")
    count, i = _varint(index, 1)
    records = []
    for _ in range(count):
        unpadded, i = _varint(index, i)
        size, i = _varint(index, i)
        records.append((unpadded, size))
    out, pos = [], 12
    for unpadded, size in records:
        hsize = (data[pos] + 1) * 4
        head = data[pos:pos + hsize]
        if struct.unpack("<I", head[-4:])[0] != binascii.crc32(head[:-4]) & 0xFFFFFFFF:
            raise BadXz("block header checksum mismatch")
        flags, j = head[1], 2
        if flags & 0x40:
            _, j = _varint(head, j)
        if flags & 0x80:
            _, j = _varint(head, j)
        dict_size = None
        for _ in range((flags & 0x03) + 1):
            fid, j = _varint(head, j)
            psize, j = _varint(head, j)
            props = head[j:j + psize]
            j += psize
            if fid == 0x21:
                dict_size = lzma2_dict(props[0])
        if dict_size is None:
            raise BadXz("a block without an LZMA2 filter")
        out.append((dict_size, size))
        pos += unpadded + (-unpadded % 4)
    return out


def check_stream(data: bytes, cap: int, name: str) -> dict:
    """The two rules on one complete .xz stream."""
    bl = blocks(data)
    content = sum(s for _, s in bl)
    biggest = max(d for d, _ in bl)
    room = 1 << max(12, (max(content, 1) - 1).bit_length())      # smallest power of two >= content, >= 4 KiB
    problems = []
    if biggest > XZ_MAX_DICT:
        problems.append(f"dictionary {biggest // MiB} MiB is beyond what xz accepts (1536 MiB)")
    if biggest > room:
        problems.append(f"dictionary {biggest / MiB:g} MiB is bigger than the content needs "
                        f"({content / MiB:.1f} MiB -> at most {room / MiB:g} MiB): memory spent for nothing")
    # The target: the content's power of two, or the biggest power of two whose unpacking fits the cap
    # (xz needs about the dictionary plus 1 MiB).
    fits = 1 << ((cap - MiB).bit_length() - 1) if cap > MiB else 4096
    target = min(room, fits)
    if biggest < target and content > biggest:
        problems.append(f"dictionary {biggest / MiB:g} MiB is smaller than the content ({content / MiB:.1f} MiB): "
                        f"{target / MiB:g} MiB would compress more and still unpack within {cap // MiB} MiB")
    try:
        d = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=cap)
        d.decompress(data)
        unpack_ok = True
    except lzma.LZMAError as e:
        unpack_ok = False
        problems.append(f"unpacking needs more than the {cap // MiB} MiB cap ({e})")
    return {"name": name, "blocks": len(bl), "dict_mib": round(biggest / MiB, 3), "content_bytes": content,
            "compressed_bytes": len(data), "ratio": round(len(data) / content, 3) if content else None,
            "fits_cap": unpack_ok, "problems": problems, "ok": not problems}


def streams_in(path: Path) -> list:
    """[(name, bytes)]: the file itself, or the xz members of a .deb (an ar archive)."""
    raw = path.read_bytes()
    if raw[:8] == b"!<arch>\n":
        out, pos = [], 8
        while pos < len(raw):
            name, size = raw[pos:pos + 16].strip().decode().rstrip("/"), int(raw[pos + 48:pos + 58].strip())
            if name.endswith(".xz"):
                out.append((f"{path.name}:{name}", raw[pos + 60:pos + 60 + size]))
            pos += 60 + size + (size & 1)
        if not out:
            raise BadXz("a .deb with no xz member")
        return out
    return [(path.name, raw)]


CHECKED = (".deb", ".xz", ".txz")       # what the release pipeline and `release verify` pass through check_file


def check_file(path, cap_mib=128) -> dict:
    """One file, every xz stream in it -> {file, ok, streams, problems}. An unreadable file is a problem, never a
    pass: the gorilla-opencode pipeline's packages stage and `release verify` call this on every file they ship
    that ends in CHECKED."""
    path = Path(path)
    try:
        streams = [check_stream(data, cap_mib * MiB, name) for name, data in streams_in(path)]
    except (OSError, BadXz, IndexError, struct.error, ValueError) as e:
        return {"file": path.name, "ok": False, "streams": [], "problems": [f"{path.name}: not checked: {e}"]}
    problems = [f"{s['name']}: {p}" for s in streams for p in s["problems"]]
    return {"file": path.name, "ok": not problems, "streams": streams, "problems": problems}


def main(argv=None, prog="fieldkit xzcheck"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--cap-mib", type=int, default=128, help="most memory unpacking may need (128)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cap_mib <= 0:
        print("REFUSED: --cap-mib must be above 0")
        return 2
    results, bad = [], []
    for f in a.files:
        try:
            for name, data in streams_in(Path(f)):
                results.append(check_stream(data, a.cap_mib * MiB, name))
        except (OSError, BadXz, IndexError, struct.error, ValueError) as e:
            bad.append(f"{f}: {e}")
    ok = all(r["ok"] for r in results)
    out = {"ok": ok and not bad, "cap_mib": a.cap_mib, "streams": results, "unreadable": bad}
    if not ok:
        out["next"] = ("recompress with the smallest dictionary that holds the content (xz -9e already does up to "
                       "64 MiB): xz --lzma2=preset=9e,dict=<power of two >= content> and check again")

    def lines(d):
        for r in d["streams"]:
            yield (f"{'OK  ' if r['ok'] else 'FAIL'} {r['name']}: dict {r['dict_mib']:g} MiB, "
                   f"{r['content_bytes'] / MiB:.1f} MiB -> {r['compressed_bytes']:,} bytes ({r['ratio']}), "
                   f"{r['blocks']} block(s), unpacks within {d['cap_mib']} MiB: {'yes' if r['fits_cap'] else 'NO'}")
            for p in r["problems"]:
                yield f"     {p}"
        for b in d["unreadable"]:
            yield f"UNREADABLE {b}"
        yield "VERDICT " + ("PASS" if d["ok"] else "FAIL")

    emit(out, a.json, lines)
    if bad and not results:
        return 2
    return 0 if out["ok"] else 3
