"""Mach-O binary (thin or universal): the cpu, the minimum macOS it asks for and the dylibs it loads, read from the
header alone. The check for the Mac 10.13 release: the binary must not ask for a newer macOS than --max-minos.

Reads only the header and load commands, every size is bounds-checked, nothing is executed or loaded. Works on any
system (no `otool` needed). Only macOS minimum versions are compared (LC_VERSION_MIN_MACOSX, and LC_BUILD_VERSION
with platform 1); other platforms' versions are listed, not compared.

    fieldkit repair evidence-macho-extract BINARY [--max-minos 10.13.0] [--json]
Exit: 0 parsed and not newer than the maximum, 1 file unreadable, 3 not Mach-O / truncated / newer than the maximum.
"""
import argparse
import re
import struct
from pathlib import Path

from ._common import emit

FAT = {b"\xca\xfe\xba\xbe": (">", False), b"\xbe\xba\xfe\xca": ("<", False),
       b"\xca\xfe\xba\xbf": (">", True), b"\xbf\xba\xfe\xca": ("<", True)}
THIN = {b"\xcf\xfa\xed\xfe": ("<", True), b"\xfe\xed\xfa\xcf": (">", True),
        b"\xce\xfa\xed\xfe": ("<", False), b"\xfe\xed\xfa\xce": (">", False)}
DYLIB_CMDS = {0xC, 0x18, 0x1F, 0x20, 0x23}          # LOAD / LOAD_WEAK / REEXPORT / LAZY_LOAD / LOAD_UPWARD dylib
LC_RPATH, LC_VERSION_MIN_MACOSX, LC_BUILD_VERSION = 0x1C, 0x24, 0x32
VERSION = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")
MAX_SLICES = 64


def _tuple(value):
    m = VERSION.fullmatch(value)
    if not m:
        raise ValueError("version must be major.minor or major.minor.patch")
    return tuple(int(x or 0) for x in m.groups())


def _fmt(v):
    return f"{v >> 16}.{(v >> 8) & 255}.{v & 255}"


def parse_thin(data):
    if len(data) < 4 or data[:4] not in THIN:
        raise ValueError("a universal slice is not a supported Mach-O")
    endian, is64 = THIN[data[:4]]
    hdr = 32 if is64 else 28
    if len(data) < hdr:
        raise ValueError("Mach-O header is truncated")
    cpu, _sub, _ft, ncmds, sizeofcmds = struct.unpack(endian + "iiIII", data[4:24])
    if ncmds > sizeofcmds // 8:
        raise ValueError("load-command count exceeds its declared region")
    end = hdr + sizeofcmds
    if end > len(data):
        raise ValueError("load-command region is truncated")
    info = {"cpu": f"{cpu & 0xFFFFFFFF:x}", "min_os": [], "deps": [], "rpaths": []}
    off = hdr
    for _ in range(ncmds):
        if off + 8 > end:
            raise ValueError("load-command header is truncated")
        cmd, size = struct.unpack(endian + "II", data[off:off + 8])
        if size < 8 or off + size > end:
            raise ValueError("invalid load-command size")
        base = cmd & 0x7FFFFFFF
        if cmd == LC_BUILD_VERSION:
            if size < 24:
                raise ValueError("LC_BUILD_VERSION is truncated")
            plat, minos, _sdk = struct.unpack(endian + "III", data[off + 8:off + 20])
            info["min_os"].append({"platform": plat, "version": _fmt(minos), "macos": plat == 1})
        elif cmd == LC_VERSION_MIN_MACOSX:
            if size < 16:
                raise ValueError("LC_VERSION_MIN_MACOSX is truncated")
            minos, _sdk = struct.unpack(endian + "II", data[off + 8:off + 16])
            info["min_os"].append({"platform": 1, "version": _fmt(minos), "macos": True})
        elif base in DYLIB_CMDS or base == LC_RPATH:
            need = 24 if base in DYLIB_CMDS else 12
            if size < need:
                raise ValueError("a path load command is truncated")
            name_off = struct.unpack(endian + "I", data[off + 8:off + 12])[0]
            if name_off < need or name_off >= size:
                raise ValueError("invalid path offset in a load command")
            name = data[off + name_off:off + size].split(b"\0", 1)[0].decode("utf-8", errors="replace")
            info["rpaths" if base == LC_RPATH else "deps"].append(name)
        off += size
    return info


def parse_macho(data):
    """-> list of slice dicts (one for a thin file). Raises ValueError."""
    if len(data) < 4:
        raise ValueError("too short to be a Mach-O")
    magic = data[:4]
    if magic in THIN:
        return [parse_thin(data)]
    if magic not in FAT:
        raise ValueError(f"not Mach-O (magic {struct.unpack('<I', magic)[0]:x})")
    endian, is64 = FAT[magic]
    if len(data) < 8:
        raise ValueError("universal header is truncated")
    count = struct.unpack(endian + "I", data[4:8])[0]
    if not 1 <= count <= MAX_SLICES:
        raise ValueError(f"universal slice count must be 1..{MAX_SLICES}")
    esize = 32 if is64 else 20
    table_end = 8 + count * esize
    if table_end > len(data):
        raise ValueError("universal architecture table is truncated")
    out = []
    for i in range(count):
        e = data[8 + i * esize:8 + (i + 1) * esize]
        offset, size = struct.unpack(endian + ("QQ" if is64 else "II"), e[8:8 + (16 if is64 else 8)])
        if offset < table_end or size == 0 or offset + size > len(data):
            raise ValueError(f"universal slice {i} has invalid bounds")
        out.append(parse_thin(data[offset:offset + size]))
    return out


def newest_macos(slices):
    vs = [_tuple(m["version"]) for s in slices for m in s["min_os"] if m["macos"]]
    return max(vs) if vs else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair evidence-macho-extract", description=__doc__.split("\n")[0])
    ap.add_argument("binary")
    ap.add_argument("--max-minos", default="10.13.0", help="newest macOS the binary may ask for (default 10.13.0)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        maximum = _tuple(a.max_minos)
    except ValueError as e:
        emit(a.json, "FAIL", f"--max-minos: {e}", "use 10.13 or 10.13.0")
        return 2
    try:
        data = Path(a.binary).read_bytes()
    except OSError as e:
        emit(a.json, "FAIL", f"cannot read {a.binary}: {e}", "pass an existing file")
        return 1
    try:
        slices = parse_macho(data)
    except ValueError as e:
        emit(a.json, "FAIL", f"cannot parse Mach-O ({e})", "pass a complete Mach-O binary")
        return 3
    newest = newest_macos(slices)
    data_out = {"slices": slices, "newest_macos_min": ".".join(map(str, newest)) if newest else None, "max_minos": a.max_minos}
    if not a.json:
        for i, s in enumerate(slices):
            print(f"slice {i}: cpu={s['cpu']} min_os={[m['version'] + ('' if m['macos'] else '/plat' + str(m['platform'])) for m in s['min_os']]} "
                  f"deps={len(s['deps'])} rpaths={len(s['rpaths'])}")
    if newest and newest > maximum:
        emit(a.json, "FAIL", f"minimum macOS {'.'.join(map(str, newest))} is newer than {a.max_minos}",
             "build against an older SDK/deployment target, or raise --max-minos on purpose", data_out)
        return 3
    emit(a.json, "PASS", f"{len(slices)} slice(s); newest macOS minimum {data_out['newest_macos_min'] or 'not stated'} (limit {a.max_minos})",
         "none", data_out)
    return 0
