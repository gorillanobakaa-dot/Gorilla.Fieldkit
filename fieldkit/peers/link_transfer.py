"""Big transfers over the link: a whole folder, a big file or a git bundle, streamed on ONE connection.

    fieldkit peers link put PATH [PATH ...] [--device NAME] [--retries N]   a file or a whole folder, any size
    fieldkit peers link put --bundle REPO [--device NAME]                     a git bundle of every ref of REPO

Stage O2 of the household orchestrator (local/PROJECT-MANDATE-PLAN.md section 19). Before it, `put` read the whole
file into memory, stopped at 50 MB and opened one TCP connection per 40 KB chunk; a kernel tarball (158 MB) or a
source tree (GB) could not cross the cable.

The wire, one TCP connection after the signed first line:
  1. sender:   a signed JSON line {verb: put-stream, tid, kind, name, manifest_size, manifest_sha256}
  2. receiver: {"ok": true, "go": "manifest"}, or a refusal
  3. sender:   the manifest, manifest_size bytes of JSON {name, kind, files: [[path, size, exec, mtime]], dirs: [...]}
  4. receiver: {"ok": true, "have": [bytes already here, per file]} (left by an earlier attempt that dropped), or
               the paths this computer cannot hold (names differing only by case on a case-insensitive disk, names
               Windows reserves); nothing is renamed, a source tree with renamed files would not build
  5. sender:   per file, in manifest order: its bytes from that offset to the end, then 32 bytes, the sha256 of the
               WHOLE file
  6. receiver: {"ok": true, "saved": PATH, "files": N, "bytes": B, "digest": D}, D the sha256 of the lines
               "<sha256>  <path>"; the sender compares D with its own. Or the files whose sha256 did not match: they
               are discarded and the next run sends them again.

Resume: tid is the sha256 of the manifest (paths, sizes, mtimes), so running the same put again after a drop finds
the half-received folder and the receiver says how far each file got. The sender re-reads (hashes) what is already
there without sending it. A file that changed in between gets a new tid, so it starts clean.

Never overwrites: the finished file or folder is moved into received/ under a free name (x, x-1, x-2...).
Not authenticated yet (security is for later, owner 2026-10-25): the first line and the manifest are signed with the
household key; the data bytes are checked by sha256 against damage, not against a forger.
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

BLOCK = 1024 * 1024                 # read, hash and send in 1 MiB blocks: memory stays flat at any size
MAX_MANIFEST = 256 * 1024 * 1024    # about 2.5 million files
LINE = 1024 * 1024                  # the longest JSON line on the wire (a refusal listing paths)
TIMEOUT = 120
_ACTIVE, _ACTIVE_LOCK = {}, threading.Lock()   # tid -> the socket it is arriving on
WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)
WINDOWS_BAD = re.compile(r'[<>:"|?*]')


def _home():
    from . import link
    return link.home()


# ---- the manifest -------------------------------------------------------------------------------------------------
def manifest_for(path):
    """-> (manifest dict, [absolute source paths in manifest order], [skipped: (path, why)])."""
    p = Path(path)
    if p.is_symlink() or not (p.is_file() or p.is_dir()):
        raise ValueError(f"{path}: not a file or folder (links are not followed)")
    if p.is_file():
        st = p.stat()
        return ({"name": p.name, "kind": "file", "files": [[p.name, st.st_size, bool(st.st_mode & 0o111),
                                                             int(st.st_mtime)]], "dirs": []}, [p], [])
    files, srcs, dirs, skipped = [], [], [], []
    for top, dnames, fnames in os.walk(p, followlinks=False):
        dnames.sort()
        rel_top = Path(top).relative_to(p).as_posix()
        rel = (lambda n: n) if rel_top == "." else (lambda n, t=rel_top: f"{t}/{n}")
        for d in list(dnames):
            if (Path(top) / d).is_symlink():
                skipped.append((rel(d), "a link (not followed)"))
                dnames.remove(d)
        if not dnames and not fnames and rel_top != ".":
            dirs.append(rel_top)
        for n in sorted(fnames):
            fp = Path(top) / n
            if fp.is_symlink() or not fp.is_file():
                skipped.append((rel(n), "a link or not a regular file"))
                continue
            st = fp.stat()
            files.append([rel(n), st.st_size, bool(st.st_mode & 0o111), int(st.st_mtime)])
            srcs.append(fp)
    return {"name": p.name, "kind": "dir", "files": files, "dirs": dirs}, srcs, skipped


def manifest_bytes(man):
    return json.dumps(man, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def tid_of(raw):
    return hashlib.sha256(raw).hexdigest()[:16]


def digest(rows):
    """The one number both ends compare: sha256 over '<sha256>  <path>' lines in manifest order."""
    return hashlib.sha256("\n".join(f"{s}  {p}" for p, s in rows).encode("utf-8")).hexdigest()


def bad_path(rel, windows=False):
    """-> why a path from the other computer cannot be written here, or None."""
    if not isinstance(rel, str) or not rel or len(rel) > 4096 or rel.startswith("/") or "\\" in rel:
        return "not a relative path with / separators"
    if re.match(r"^[A-Za-z]:", rel):
        return "a drive letter"
    for part in rel.split("/"):
        if part in ("", ".", ".."):
            return "an empty, . or .. component"
        if any(ord(ch) < 32 for ch in part):
            return "a control character"
        if windows and (WINDOWS_RESERVED.match(part) or WINDOWS_BAD.search(part) or part[-1] in " ."):
            return f"{part!r}: a name Windows cannot hold"
    return None


def cannot_hold(man, case_insensitive, windows):
    """-> [(path, why)] for every path this computer cannot store as it is."""
    out, seen = [], {}
    paths = [f[0] for f in man.get("files", [])] + list(man.get("dirs", []))
    if man.get("kind") == "file" and len(man.get("files", [])) != 1:
        return [("", "a file transfer carries exactly one file")]
    for rel in paths:
        why = bad_path(rel, windows)
        if why:
            out.append((rel, why))
            continue
        if case_insensitive:
            low = rel.lower()
            if low in seen and seen[low] != rel:
                out.append((rel, f"differs from {seen[low]!r} only by case, and this disk ignores case"))
            seen.setdefault(low, rel)
    return out


def disk_ignores_case(folder):
    folder.mkdir(parents=True, exist_ok=True)
    probe = folder / f".CaseProbe-{os.getpid()}"
    probe.write_bytes(b"")
    try:
        return (folder / probe.name.lower()).exists()
    finally:
        probe.unlink(missing_ok=True)


# ---- the wire -----------------------------------------------------------------------------------------------------
class Wire:
    """JSON lines and exact byte counts on one socket."""

    def __init__(self, sock, pre=b""):
        self.s, self.buf = sock, bytearray(pre)
        self.out = bytearray()

    def line(self):
        while b"\n" not in self.buf:
            if len(self.buf) > LINE:
                raise ValueError("a line longer than 1 MiB")
            c = self.s.recv(65536)
            if not c:
                raise ConnectionError("the other side closed the connection")
            self.buf += c
        i = self.buf.index(b"\n")
        out = bytes(self.buf[:i])
        del self.buf[:i + 1]
        return json.loads(out.decode("utf-8"))

    def stream(self, n, sink):
        """Pass exactly n bytes to sink(bytes), never holding more than one block."""
        while n > 0:
            if self.buf:
                take = bytes(self.buf[:n])
                del self.buf[:len(take)]
            else:
                take = self.s.recv(min(BLOCK, n))
                if not take:
                    raise ConnectionError("the other side closed the connection mid-file")
            sink(take)
            n -= len(take)

    def exact(self, n):
        parts = []
        self.stream(n, parts.append)
        return b"".join(parts)

    def send(self, obj):
        self.flush()
        self.s.sendall(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")

    def write(self, data):
        """Buffered, so a tree of small files does not cost one system call per file."""
        self.out += data
        if len(self.out) >= BLOCK:
            self.flush()

    def flush(self):
        if self.out:
            self.s.sendall(self.out)
            self.out = bytearray()


# ---- the receiving side -------------------------------------------------------------------------------------------
def _free(folder, name):
    from . import link
    return link._free(folder, name)


def _save_done(part, done):
    tmp = part / "done.json.tmp"
    tmp.write_text(json.dumps(done), encoding="utf-8")
    os.replace(tmp, part / "done.json")


def receive_stream(sock, req, pre=b"", windows=None, case_insensitive=None):
    """The receiving side of one put-stream connection (req already verified). -> the last answer sent."""
    from . import link
    w = Wire(sock, pre)
    windows = (os.name == "nt") if windows is None else windows
    tid = str(req.get("tid", ""))
    try:
        msize = int(req.get("manifest_size", -1))
    except (TypeError, ValueError):
        msize = -1
    if not re.fullmatch(r"[0-9a-f]{16}", tid) or not 0 < msize <= MAX_MANIFEST:
        ans = {"ok": False, "error": "a put-stream needs a 16-hex tid and a manifest of 1 byte to 256 MB"}
        w.send(ans)
        return ans
    # a retry after a drop: the old connection may still sit in recv (a pulled cable sends no FIN), so the newer
    # one wins: the old socket is shut, and its thread closes its files before this one reads them
    with _ACTIVE_LOCK:
        old = _ACTIVE.get(tid)
    if old is not None:
        try:
            old.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        end = time.time() + 15
        while time.time() < end and tid in _ACTIVE:
            time.sleep(0.05)
    with _ACTIVE_LOCK:
        if tid in _ACTIVE:
            ans = {"ok": False, "error": f"transfer {tid} is still arriving on another connection"}
            w.send(ans)
            return ans
        _ACTIVE[tid] = sock
    try:
        return _receive(w, req, tid, msize, windows, case_insensitive, link)
    finally:
        with _ACTIVE_LOCK:
            if _ACTIVE.get(tid) is sock:
                del _ACTIVE[tid]


def _receive(w, req, tid, msize, windows, case_insensitive, link):
    w.send({"ok": True, "go": "manifest"})
    raw = w.exact(msize)
    if hashlib.sha256(raw).hexdigest() != str(req.get("manifest_sha256", "")).lower() or tid_of(raw) != tid:
        ans = {"ok": False, "error": "the manifest does not match its signed sha256: refused"}
        w.send(ans)
        return ans
    man = json.loads(raw.decode("utf-8"))
    received = link.home() / "received"
    part = received / ".partial" / tid
    tree = part / "tree"
    ci = disk_ignores_case(received / ".partial") if case_insensitive is None else case_insensitive
    problems = cannot_hold(man, ci, windows)
    if problems:
        ans = {"ok": False, "count": len(problems), "cannot_hold": [list(x) for x in problems[:50]],
               "error": f"{len(problems)} path(s) this computer cannot hold as they are (nothing is renamed: a "
                        "source tree with renamed files would not build); send it as a tarball or a git bundle"}
        w.send(ans)
        return ans
    tree.mkdir(parents=True, exist_ok=True)
    (part / "manifest.json").write_bytes(raw)
    try:
        done = json.loads((part / "done.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        done = {}
    files = man["files"]
    have = []
    for i, (rel, size, _x, _mt) in enumerate(files):
        fp = tree / rel
        o = fp.stat().st_size if fp.is_file() else 0
        if o > size:                                  # longer than the file can be: start that one again
            fp.unlink()
            o = 0
            done.pop(str(i), None)
        have.append(o)
    resumed = sum(have)
    w.send({"ok": True, "have": have})
    try:
        bad, rows, total = _files(w, files, tree, have, done, part, windows)
    finally:
        _save_done(part, done)
    return _finish(w, req, tid, man, files, rows, bad, resumed, total, received, part, tree, link)


def _files(w, files, tree, have, done, part, windows):
    """Each file's bytes from its offset, then its sha256. -> (bad, rows, bytes received)."""
    bad, rows, last_save, total = [], [], time.time(), 0
    for i, (rel, size, exe, mtime) in enumerate(files):
        fp = tree / rel
        known = done.get(str(i)) if have[i] == size else None
        h = hashlib.sha256()
        out, err = None, None
        try:
            fp.parent.mkdir(parents=True, exist_ok=True)
            if known is None and have[i]:
                with open(fp, "rb") as f:
                    for blk in iter(lambda: f.read(BLOCK), b""):
                        h.update(blk)
            out = open(fp, "ab")
        except OSError as e:                          # e.g. a path too long for this disk: the rest still arrives
            err = f"cannot be written here ({e.strerror or e})"

        def sink(b):
            h.update(b)
            if out:
                out.write(b)
        try:
            w.stream(size - have[i], sink)
        finally:
            if out:
                out.close()
        total += size - have[i]
        sent = w.exact(32).hex()
        got = known or h.hexdigest()
        if err or got != sent:
            bad.append(f"{rel}: {err}" if err else rel)
            if not err:
                fp.unlink(missing_ok=True)
            done.pop(str(i), None)
            continue
        done[str(i)] = got
        rows.append((rel, got))
        try:
            if exe and not windows:
                os.chmod(fp, os.stat(fp).st_mode | 0o111)
            os.utime(fp, (mtime, mtime))
        except OSError:
            pass
        if time.time() - last_save > 2:
            _save_done(part, done)
            last_save = time.time()
    return bad, rows, total


def _finish(w, req, tid, man, files, rows, bad, resumed, total, received, part, tree, link):
    if bad:
        ans = {"ok": False, "bad": bad[:50], "count": len(bad),
               "error": f"{len(bad)} file(s) did not arrive whole (sha256 mismatch: discarded; or not writable "
                        "here: the reason is beside the path); run the same put again to send just those"}
        link._log(f"stream {tid} {man.get('name')}: {len(bad)} file(s) did not arrive whole: {bad[:3]}")
        w.send(ans)
        return ans
    for d in man.get("dirs", []):
        (tree / d).mkdir(parents=True, exist_ok=True)
    name = link.safe_name(man.get("name"))
    dest = _free(received, name)
    os.replace(tree / files[0][0] if man["kind"] == "file" else tree, dest)
    shutil.rmtree(part, ignore_errors=True)           # this transfer's own scratch: manifest.json, done.json
    nbytes = sum(f[1] for f in files)
    rec = link.home() / "receipts"
    rec.mkdir(parents=True, exist_ok=True)
    receipt = {"tid": tid, "name": man.get("name"), "kind": man["kind"], "saved": str(dest), "files": len(files),
               "bytes": nbytes, "from": req.get("from_host"), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "digest": digest(rows), "sha256": rows}
    (rec / f"{time.strftime('%Y%m%d-%H%M%S')}-{tid}.json").write_text(json.dumps(receipt, indent=0), encoding="utf-8")
    ans = {"ok": True, "saved": [str(dest)], "files": len(files), "bytes": nbytes, "resumed_bytes": resumed,
           "received_bytes": total, "digest": receipt["digest"]}
    if man["kind"] == "file" and len(rows) == 1:
        ans["sha256"] = rows[0][1]
        if req.get("to_desktop"):
            ans["saved"] += link.to_desktop_copy(dest, name)
    if name.endswith(".bundle"):
        ans["bundle"] = bundle_heads(dest)
    link._log(f"stream {man['kind']} {name} ({len(files)} files, {nbytes} bytes, resumed {resumed}) from "
              f"{req.get('from_host')}: {dest}")
    link.store("file", {"from_host": req.get("from_host"),
                        "text": f"{man['kind']} {name} ({len(files)} files, {nbytes} bytes, every sha256 checked) "
                                f"saved: {dest}"})
    w.send(ans)
    return ans


def bundle_heads(path):
    """A received git bundle's refs, read by git itself; '' when git is not here."""
    git = shutil.which("git")
    if not git:
        return "not checked: no git on this computer"
    try:
        r = subprocess.run([git, "bundle", "list-heads", str(path)], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"not checked: {e}"
    if r.returncode:
        return f"NOT A VALID BUNDLE: {(r.stderr or r.stdout).strip()[:300]}"
    return f"{len(r.stdout.splitlines())} ref(s)"


# ---- the sending side ---------------------------------------------------------------------------------------------
def make_bundle(repo, out_dir=None):
    """-> the path of a git bundle with every ref of repo (a temporary file the caller removes)."""
    git = shutil.which("git")
    if not git:
        raise ValueError("no git on this computer")
    top = subprocess.run([git, "-C", str(repo), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode:
        raise ValueError(f"{repo}: not a git repository")
    name = Path(top.stdout.strip()).name
    d = Path(out_dir or tempfile.mkdtemp(prefix="fieldkit-bundle-"))
    out = d / f"{name}.bundle"
    r = subprocess.run([git, "-C", str(repo), "bundle", "create", str(out), "--all"], capture_output=True, text=True)
    if r.returncode or not out.is_file():
        raise ValueError(f"git bundle create failed: {(r.stderr or r.stdout).strip()[:300]}")
    return out


def put_stream(path, device=None, retries=3, progress=None, sleep=time.sleep, connect=None, man=None,
               to_desktop=False):
    """Send a file or a folder; resume after a drop (up to `retries` more tries). -> the receiver's answer, plus
    attempts, seconds and MB/s measured here."""
    if man is None:
        man, srcs, skipped = manifest_for(path)
    else:
        man, srcs, skipped = man
    raw = manifest_bytes(man)
    last = None
    t0 = time.time()
    for attempt in range(1, retries + 2):
        try:
            r = _send_once(man, raw, srcs, device, progress, connect, to_desktop)
            r.update(attempts=attempt, skipped=[list(x) for x in skipped[:50]], skipped_count=len(skipped))
            secs = max(time.time() - t0, 1e-6)
            if r.get("ok"):
                r["seconds"] = round(secs, 2)
                r["mb_per_s"] = round(r.get("sent_bytes", 0) / secs / 1e6, 1)
            return r
        except (OSError, ConnectionError, ValueError) as e:
            last = e
            if attempt <= retries:
                sleep(min(2 * attempt, 10))
    return {"ok": False, "error": f"the connection dropped {retries + 1} time(s) ({last}); run the same put again "
                                  "and it carries on from where it stopped"}


def _send_once(man, raw, srcs, device, progress, connect, to_desktop=False):
    from . import link
    if connect is None:
        sock, k = link.connect_device(device, timeout=TIMEOUT)
    else:
        sock, k = connect()
    with sock:
        sock.settimeout(TIMEOUT)
        w = Wire(sock)
        tid = tid_of(raw)
        head = link.sign({"verb": "put-stream", "tid": tid, "kind": man["kind"], "name": man["name"],
                          "manifest_size": len(raw), "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                          "files": len(man["files"]), "to_desktop": bool(to_desktop)}, k)
        sock.sendall(json.dumps(head, ensure_ascii=False).encode("utf-8") + b"\n")
        r = w.line()
        if not r.get("ok") or r.get("go") != "manifest":
            r.setdefault("ok", False)
            return r
        sock.sendall(raw)
        r = w.line()
        if not r.get("ok"):
            return r
        have = r["have"]
        if len(have) != len(man["files"]):
            return {"ok": False, "error": "the receiver answered for a different number of files"}
        total = sum(f[1] for f in man["files"]) - sum(have)
        sent, rows, tick = 0, [], time.time()
        for (rel, size, _x, _mt), src, off in zip(man["files"], srcs, have):
            h = hashlib.sha256()
            with open(src, "rb") as f:
                left = off
                while left > 0:                       # already there: hash it, do not send it
                    blk = f.read(min(BLOCK, left))
                    if not blk:
                        break
                    h.update(blk)
                    left -= len(blk)
                n = 0
                for blk in iter(lambda: f.read(BLOCK), b""):
                    if off + n + len(blk) > size:     # the file grew since the manifest: send what it promised
                        blk = blk[:size - off - n]
                    h.update(blk)
                    w.write(blk)
                    n += len(blk)
                    if off + n >= size:
                        break
                if off + n != size:
                    raise ValueError(f"{rel} changed while it was being sent ({off + n} of {size} bytes)")
            sent += n
            w.write(h.digest())
            rows.append((rel, h.hexdigest()))
            if progress and time.time() - tick > 2:
                progress(sent, total)
                tick = time.time()
        w.flush()
        r = w.line()
        r["sent_bytes"] = sent
        if r.get("ok"):
            mine = digest(rows)
            r["verified_both_ends"] = r.get("digest") == mine
            if not r["verified_both_ends"]:
                r.update(ok=False, error=f"the receiver's digest {str(r.get('digest'))[:12]} differs from this "
                                         f"side's {mine[:12]}")
        return r
