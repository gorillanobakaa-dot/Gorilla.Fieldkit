"""Binary reproducibility: two independent builds of the same source must give the same bytes, or this says exactly
which bytes differ and why.

    fieldkit repro compare A B [--json]            two files or two folders, byte for byte (exit 0 same, 3 differ)

Born 2026-10-11 from the build harness's one admitted gap: "the source is proven; the binary is not yet reproduced
independently". A build that only matches itself on the same machine, in the same folder, proves little; so the
second build runs somewhere else (another folder, its own caches and temp folder, another time zone and locale),
and only then are the two compared.

A difference is never just "differs". Archives are opened and compared member by member (zip, tar in any of
gz/bz2/xz/zst, .deb as the ar archive it is), so the report names the file inside the package; and the bytes around
the first difference are read for the usual causes: a build folder baked in, a date or time, a user or machine name.
That is the line a person needs to fix the build, not a hash that does not match.
"""
import hashlib
import io
import os
import re
import shutil
import socket
import subprocess
import tarfile
import zipfile
from pathlib import Path

DATE_RX = re.compile(rb"(?:19|20)\d\d[-/.](?:0[1-9]|1[0-2])[-/.](?:[0-2]\d|3[01])|"
                     rb"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,? +\d{1,2}? *(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|"
                     rb"Oct|Nov|Dec)|\d\d:\d\d:\d\d")
MAX_DEPTH = 3
MAX_MEMBER = 512 << 20          # members bigger than this are hashed, never held in memory twice


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


# -- what a build can leak ---------------------------------------------------------------------------------------
def markers(roots=()):
    """Strings that, found in one build and not the other, name the cause: the build folders, home, user, host."""
    out = {}
    for i, r in enumerate(roots):
        if r:
            out[f"build folder {i + 1}"] = str(r).encode()
    home = str(Path.home())
    if len(home) > 3:
        out["home folder"] = home.encode()
    for k in ("USER", "USERNAME", "LOGNAME"):
        v = os.environ.get(k)
        if v and len(v) > 2:
            out["user name"] = v.encode()
            break
    try:
        h = socket.gethostname()
        if len(h) > 2:
            out["machine name"] = h.encode()
    except OSError:
        pass
    return out


def _printable(b):
    return re.sub(rb"[^\x20-\x7e]", b".", b).decode("ascii")


CHUNK = 1 << 16
COUNT_LIMIT = 16 << 20          # bytes of differing chunks counted exactly; past that the count is "at least"


def _pe_causes(a, b):
    """Windows binaries: the COFF header's link time, and the PDB signature (RSDS GUID and age) the linker wrote."""
    out = []
    if not (a[:2] == b[:2] == b"MZ") or len(a) < 0x40 or len(b) < 0x40:
        return out
    try:
        pa, pb = int.from_bytes(a[0x3c:0x40], "little"), int.from_bytes(b[0x3c:0x40], "little")
        if a[pa:pa + 4] == b[pb:pb + 4] == b"PE\0\0" and a[pa + 8:pa + 12] != b[pb + 8:pb + 12]:
            out.append("the PE header's link time differs (link with /Brepro: lld-link and MSVC both take it)")
    except (ValueError, IndexError):
        pass
    ra, rb = a.find(b"RSDS"), b.find(b"RSDS")
    if ra >= 0 and rb >= 0 and a[ra + 4:ra + 24] != b[rb + 4:rb + 24]:
        out.append("the PDB signature (RSDS GUID/age) differs: /Brepro makes it a hash of the content")
    return out


def explain_bytes(a, b, marks=None):
    """Why two byte strings differ: first offset, the text around it, and the causes recognised there."""
    n = min(len(a), len(b))
    first, diffs, counted, exact = None, 0, 0, True
    for off in range(0, max(len(a), len(b)), CHUNK):
        ca, cb = a[off:off + CHUNK], b[off:off + CHUNK]
        if ca == cb:
            continue
        if first is None:
            first = off + next((i for i in range(min(len(ca), len(cb))) if ca[i] != cb[i]), min(len(ca), len(cb)))
        if counted >= COUNT_LIMIT:
            exact = False
            continue
        diffs += sum(x != y for x, y in zip(ca, cb))
        counted += len(ca)
    if first is None:
        first = n
    diffs += abs(len(a) - len(b))
    lo, hi = max(0, first - 48), first + 48
    ca, cb = a[lo:hi], b[lo:hi]
    causes = _pe_causes(a, b)
    for name, m in (marks or {}).items():
        if (m in a) != (m in b) or a.count(m) != b.count(m):
            causes.append(f"{name} ({m.decode(errors='replace')}) is in one build and not the other")
    if DATE_RX.search(ca) or DATE_RX.search(cb):
        causes.append("a date or time is written into the file")
    if not causes:
        causes.append("look at the bytes around the first difference")
    return {"first_difference_at": first, "bytes_differing": diffs, "count_exact": exact, "size": [len(a), len(b)],
            "around_a": _printable(ca), "around_b": _printable(cb), "causes": causes}


# -- archives ------------------------------------------------------------------------------------------------------
def _ar_members(data):
    """The members of an ar archive (what a .deb is) -> [(name, header fields, bytes)]; None if not ar."""
    if not data.startswith(b"!<arch>\n"):
        return None
    out, pos = [], 8
    while pos + 60 <= len(data):
        h = data[pos:pos + 60]
        name = h[0:16].decode("ascii", "replace").strip().rstrip("/")
        meta = {"mtime": h[16:28].strip().decode(), "uid": h[28:34].strip().decode(), "gid": h[34:40].strip().decode(),
                "mode": h[40:48].strip().decode()}
        size = int(h[48:58].strip() or 0)
        body = data[pos + 60:pos + 60 + size]
        out.append((name, meta, body))
        pos += 60 + size + (size & 1)
    return out


def _zstd(data):
    try:
        from compression import zstd            # Python 3.14
        return zstd.decompress(data)
    except ImportError:
        pass
    exe = shutil.which("zstd")
    if not exe:
        return None
    r = subprocess.run([exe, "-d", "-c", "-q"], input=data, capture_output=True, timeout=600)
    return r.stdout if r.returncode == 0 else None


def _tar_members(data):
    """[(name, header fields, bytes or None)] of a tar archive in any common compression; None if not tar."""
    if data[:4] == b"\x28\xb5\x2f\xfd":
        data = _zstd(data)
        if data is None:
            return None
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except (tarfile.TarError, EOFError, OSError):
        return None
    out = []
    with tf:
        for m in tf.getmembers():
            meta = {"mtime": m.mtime, "uid": m.uid, "gid": m.gid, "uname": m.uname, "gname": m.gname,
                    "mode": oct(m.mode), "type": m.type.decode() if isinstance(m.type, bytes) else str(m.type),
                    "link": m.linkname}
            body = None
            if m.isfile() and m.size <= MAX_MEMBER:
                f = tf.extractfile(m)
                body = f.read() if f else None
            out.append((m.name, meta, body))
    return out


def _zip_members(data):
    if data[:2] != b"PK":
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    out = []
    with zf:
        for i in zf.infolist():
            meta = {"date_time": list(i.date_time), "mode": i.external_attr >> 16, "compress": i.compress_type}
            out.append((i.filename, meta, zf.read(i) if i.file_size <= MAX_MEMBER else None))
    return out


def members(data):
    """(kind, members) when the bytes are an archive this can open, else (None, None)."""
    for kind, fn in (("ar", _ar_members), ("zip", _zip_members), ("tar", _tar_members)):
        m = fn(data)
        if m is not None:
            return kind, m
    return None, None


def compare_bytes(a, b, name="", marks=None, depth=0):
    """[finding] for two byte strings: empty when identical. A finding names the deepest place the bytes differ."""
    if a == b:
        return []
    kind_a, ma = members(a) if depth < MAX_DEPTH else (None, None)
    kind_b, mb = members(b) if depth < MAX_DEPTH else (None, None)
    if kind_a and kind_a == kind_b:
        found = []
        na, nb = [m[0] for m in ma], [m[0] for m in mb]
        if set(na) != set(nb):
            only_a, only_b = sorted(set(na) - set(nb)), sorted(set(nb) - set(na))
            found.append({"where": name, "what": "the archives hold different files",
                          "only_in_a": only_a[:20], "only_in_b": only_b[:20]})
        elif na != nb:
            found.append({"where": name, "what": "the same files in a different order (sort the file list)"})
        db = {m[0]: m for m in mb}
        for nm, meta, body in ma:
            if nm not in db:
                continue
            _, meta_b, body_b = db[nm]
            inner = f"{name}!{nm}" if name else nm
            changed = {k: [meta[k], meta_b[k]] for k in meta if meta.get(k) != meta_b.get(k)}
            if changed:
                what = "file details differ: " + ", ".join(f"{k} {v[0]} vs {v[1]}" for k, v in changed.items())
                if set(changed) & {"mtime", "date_time"}:
                    what += " (a time stamp: set SOURCE_DATE_EPOCH, or clamp the times)"
                if set(changed) & {"uid", "gid", "uname", "gname"}:
                    what += " (the builder's user: build as root-owned with --owner=0 --group=0, or fakeroot)"
                found.append({"where": inner, "what": what})
            if body is not None and body_b is not None and body != body_b:
                found += compare_bytes(body, body_b, inner, marks, depth + 1)
            elif (body is None) != (body_b is None):
                found.append({"where": inner, "what": "a file in one archive is a different kind in the other"})
        if found:
            return found
        # every member is the same, yet the archive bytes are not: the packing itself differs
        return [{"where": name, "what": f"every file in the {kind_a} archive is the same, the packing is not "
                                         "(compression level or settings, padding)",
                 **explain_bytes(a, b, marks)}]
    return [{"where": name or "file", "what": "the bytes differ", **explain_bytes(a, b, marks)}]


# -- files and folders ------------------------------------------------------------------------------------------------
def _listing(root):
    root = Path(root)
    if root.is_file():
        return {root.name: root}
    out = {}
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            p = Path(d) / f
            if p.is_file() and not p.is_symlink():
                out[str(p.relative_to(root)).replace(os.sep, "/")] = p
    return out


def compare(a, b, roots=None, skip=()):
    """Two files or two folders -> {identical, same, differ: [{file, sha256: [a, b], findings}], only_a, only_b}."""
    la, lb = _listing(a), _listing(b)
    if Path(a).is_file() and Path(b).is_file():
        la, lb = {"file": Path(a)}, {"file": Path(b)}
    la = {k: v for k, v in la.items() if not any(k.startswith(s) for s in skip)}
    lb = {k: v for k, v in lb.items() if not any(k.startswith(s) for s in skip)}
    marks = markers(roots or ())
    same, differ = [], []
    for rel in sorted(set(la) & set(lb)):
        sa, sb = sha256(la[rel]), sha256(lb[rel])
        if sa == sb:
            same.append({"file": rel, "sha256": sa})
            continue
        try:
            findings = compare_bytes(la[rel].read_bytes(), lb[rel].read_bytes(), "", marks)
        except MemoryError:
            findings = [{"where": rel, "what": "too large to open: compare it with diffoscope"}]
        differ.append({"file": rel, "sha256": [sa, sb], "findings": findings})
    only_a, only_b = sorted(set(la) - set(lb)), sorted(set(lb) - set(la))
    return {"identical": not differ and not only_a and not only_b and bool(same), "same": same, "differ": differ,
            "only_a": only_a, "only_b": only_b, "a": str(a), "b": str(b)}


def lines(r):
    out = [f"A: {r['a']}", f"B: {r['b']}"]
    for s in r["same"]:
        out.append(f"SAME    {s['file']}  {s['sha256'][:16]}")
    for d in r["differ"]:
        out.append(f"DIFFER  {d['file']}  {d['sha256'][0][:16]} vs {d['sha256'][1][:16]}")
        for f in d["findings"][:12]:
            out.append(f"        {f['where'] or d['file']}: {f['what']}")
            if "causes" in f:
                out.append(f"          first difference at byte {f['first_difference_at']:,} "
                           f"({'' if f.get('count_exact', True) else 'at least '}{f['bytes_differing']:,} byte(s) "
                           f"differ); cause: {'; '.join(f['causes'])}")
                out.append(f"          A: {f['around_a']}")
                out.append(f"          B: {f['around_b']}")
        if len(d["findings"]) > 12:
            out.append(f"        ... {len(d['findings']) - 12} more")
    for f in r["only_a"]:
        out.append(f"ONLY A  {f}")
    for f in r["only_b"]:
        out.append(f"ONLY B  {f}")
    if r["identical"]:
        out.append(f"REPRODUCED: {len(r['same'])} file(s), byte for byte identical")
    elif not r["same"] and not r["differ"]:
        out.append("NOTHING COMPARED: no file is in both")
    else:
        out.append(f"NOT REPRODUCED: {len(r['differ'])} file(s) differ, {len(r['only_a']) + len(r['only_b'])} "
                   f"in only one build, {len(r['same'])} identical")
    return out
