"""observe, Linux reader: /proc, inotify (ctypes), strace, journalctl. Read-only; returns None on another system."""
import ctypes
import ctypes.util
import os
import pwd
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time

PLATFORM = "linux"
NEEDS_ADMIN = "needs root or ptrace rights (kernel.yama.ptrace_scope): run it as root; nothing was asked or changed"


def _here():
    return sys.platform.startswith("linux")


def _pids():
    return [int(n) for n in os.listdir("/proc") if n.isdigit()]


def _read(path, default=""):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return default


def _name(pid):
    return _read(f"/proc/{pid}/comm").strip()


def open_file(path):
    """Processes with PATH open (an fd, the cwd, the exe or a memory map). Other users' processes need root."""
    if not _here():
        return None
    target = os.path.realpath(path)
    rows = []
    for pid in _pids():
        how = set()
        for link in ("cwd", "exe"):
            try:
                if os.readlink(f"/proc/{pid}/{link}") == target:
                    how.add(link)
            except OSError:
                pass
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    if os.readlink(f"/proc/{pid}/fd/{fd}") == target:
                        how.add(f"fd {fd}")
                except OSError:
                    pass
        except OSError:
            pass
        if any(line.endswith(" " + target) for line in _read(f"/proc/{pid}/maps").splitlines()):
            how.add("mmap")
        if how:
            rows.append({"pid": pid, "name": _name(pid), "how": sorted(how)})
    return rows


def _sockets(n):
    """[(proto, local, remote, state, inode)] for port N, from /proc/net."""
    states = {"01": "ESTABLISHED", "06": "TIME_WAIT", "0A": "LISTEN", "07": "CLOSE"}
    out = []
    for proto in ("tcp", "tcp6", "udp", "udp6"):
        for line in _read(f"/proc/net/{proto}").splitlines()[1:]:
            f = line.split()
            if len(f) < 10:
                continue
            lport = int(f[1].rsplit(":", 1)[1], 16)
            rport = int(f[2].rsplit(":", 1)[1], 16)
            if n in (lport, rport):
                st = states.get(f[3], f[3]) if proto.startswith("tcp") else "-"
                out.append((proto, f"port {lport}", f"port {rport}", st, f[9]))
    return out


def port(n):
    """Who uses port N (as local or remote port), with the process. Other users' sockets need root for the name."""
    if not _here():
        return None
    socks = _sockets(int(n))
    owners = {}
    for pid in _pids():
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    t = os.readlink(f"/proc/{pid}/fd/{fd}")
                except OSError:
                    continue
                if t.startswith("socket:["):
                    owners.setdefault(t[8:-1], []).append(pid)
        except OSError:
            pass
    rows = []
    for proto, local, remote, st, inode in socks:
        for pid in owners.get(inode, [None]):
            rows.append({"proto": proto, "local": local, "remote": remote, "state": st, "pid": pid,
                         "name": _name(pid) if pid else "(not visible: another user's socket)"})
    return rows


def proc(pid):
    """exe, cwd, command line, user, children and the memory map summary of PID."""
    if not _here():
        return None
    pid = int(pid)
    if not os.path.isdir(f"/proc/{pid}"):
        return {"pid": pid, "error": "no such process"}

    def link(n):
        try:
            return os.readlink(f"/proc/{pid}/{n}")
        except OSError:
            return None
    status = dict(l.split(":\t", 1) for l in _read(f"/proc/{pid}/status").splitlines() if ":\t" in l)
    uid = int(status.get("Uid", "0\t").split()[0])
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = str(uid)
    kids = []
    for p in _pids():
        stat = _read(f"/proc/{p}/stat")
        if stat and stat.rsplit(")", 1)[1].split()[1] == str(pid):
            kids.append({"pid": p, "name": _name(p)})
    maps = [l.split(None, 5) for l in _read(f"/proc/{pid}/maps").splitlines()]
    files = sorted({m[5].strip() for m in maps if len(m) > 5 and m[5].startswith("/")})
    return {"pid": pid, "name": _name(pid), "exe": link("exe"), "cwd": link("cwd"), "user": user,
            "cmdline": [a for a in _read(f"/proc/{pid}/cmdline").split("\0") if a],
            "state": status.get("State", "").strip(), "rss_kb": status.get("VmRSS", "").strip(),
            "children": kids, "map_regions": len(maps), "map_files": files}


_MASKS = {0x100: "created", 0x200: "deleted", 0x2: "modified", 0x8: "closed after write", 0x40: "moved from",
          0x80: "moved to", 0x4: "attributes"}


def watch(directory, seconds):
    """Changes under DIRECTORY (the folders it has now, recursively) for SECONDS, through inotify."""
    if not _here():
        return None
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    fd = libc.inotify_init1(os.O_NONBLOCK)
    if fd < 0:
        return {"error": "inotify_init1 failed", "events": []}
    mask = 0x2 | 0x8 | 0x40 | 0x80 | 0x100 | 0x200 | 0x4
    wd = {}
    for root, dirs, _ in os.walk(directory):
        w = libc.inotify_add_watch(fd, os.fsencode(root), mask)
        if w >= 0:
            wd[w] = root
    events, end = [], time.time() + float(seconds)
    try:
        while time.time() < end:
            if not select.select([fd], [], [], max(0.0, min(0.2, end - time.time())))[0]:
                continue
            buf = os.read(fd, 65536)
            i = 0
            while i + 16 <= len(buf):
                w, m, _c, ln = struct.unpack_from("iIII", buf, i)
                name = buf[i + 16:i + 16 + ln].split(b"\0")[0].decode("utf-8", "replace")
                i += 16 + ln
                for bit, label in _MASKS.items():
                    if m & bit:
                        events.append({"t": round(time.time(), 3), "event": label,
                                       "path": os.path.join(wd.get(w, "?"), name)})
    finally:
        os.close(fd)
    return {"watched": len(wd), "events": events}


def trace(pid, seconds):
    """Files and programs PID opens or starts for SECONDS (strace -f on openat/execve)."""
    if not _here():
        return None
    if not shutil.which("strace"):
        return {"error": "strace is not installed", "events": []}
    out = tempfile.mkstemp(prefix="observe-strace-")
    os.close(out[0])
    p = subprocess.Popen(["strace", "-f", "-qq", "-e", "trace=openat,execve", "-o", out[1], "-p", str(int(pid))],
                         stderr=subprocess.PIPE, text=True)
    try:
        try:
            p.wait(timeout=1.0)                                     # exits at once when ptrace is refused
        except subprocess.TimeoutExpired:
            time.sleep(float(seconds))
            p.send_signal(2)
            p.wait(timeout=10)
        err = p.stderr.read()
        text = _read(out[1])
    finally:
        if p.poll() is None:
            p.kill()
        os.unlink(out[1])
    if not text and ("Operation not permitted" in err or "ptrace" in err):
        return {"error": NEEDS_ADMIN, "needs_admin": True, "events": []}
    events = []
    for line in text.splitlines():
        if "execve(" in line or "openat(" in line:
            events.append(line.strip()[:300])
    return {"events": events}


def usage(pid, seconds):
    """CPU %, resident memory and disk bytes of PID, once a second for SECONDS. Per-process network is not kept by
    Linux without root and eBPF, so it is not claimed."""
    if not _here():
        return None
    pid, hz = int(pid), os.sysconf("SC_CLK_TCK")

    def sample():
        stat = _read(f"/proc/{pid}/stat")
        if not stat:
            return None
        f = stat.rsplit(")", 1)[1].split()
        io = dict(l.split(": ") for l in _read(f"/proc/{pid}/io").splitlines() if ": " in l)
        rss = [l for l in _read(f"/proc/{pid}/status").splitlines() if l.startswith("VmRSS")]
        return (time.time(), (int(f[11]) + int(f[12])) / hz, int(io.get("read_bytes", 0)),
                int(io.get("write_bytes", 0)), rss[0].split()[1] if rss else "0")
    rows, prev = [], sample()
    if prev is None:
        return {"error": "no such process", "samples": []}
    for _ in range(max(1, int(float(seconds)))):
        time.sleep(1)
        cur = sample()
        if cur is None:
            break
        dt = cur[0] - prev[0]
        rows.append({"cpu_pct": round(100 * (cur[1] - prev[1]) / dt, 1), "rss_kb": int(cur[4]),
                     "read_bytes": cur[2] - prev[2], "write_bytes": cur[3] - prev[3]})
        prev = cur
    return {"samples": rows}


def events(words, limit=2000):
    """System log lines (journalctl, newest last) holding every one of WORDS, case-insensitive."""
    if not _here():
        return None
    if not shutil.which("journalctl"):
        return {"error": "journalctl is not installed", "lines": []}
    r = subprocess.run(["journalctl", "--no-pager", "-q", "-n", str(limit), "-o", "short-iso"],
                       capture_output=True, text=True, timeout=60)
    want = [w.lower() for w in words]
    lines = [l for l in r.stdout.splitlines() if all(w in l.lower() for w in want)]
    out = {"lines": lines[-200:], "searched": limit}
    if r.returncode and not r.stdout:
        out["error"] = (r.stderr or "journalctl failed").strip()[:200]
    return out
