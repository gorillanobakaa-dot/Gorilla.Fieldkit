"""Linux readers for the leak gate: every one turns a Linux tool's output into the SAME events the Windows sensors
emit (sensors.py), so gate.judge() applies one set of policies and never asks which OS it ran on.

  strace log      -> filesystem (kind file-system), process (execve)        [sensors.fs_diff / Sampler's process rows]
  /proc           -> process rows in the shape of Win32_Process            [sensors.snapshot_processes]
  ss -tunapH      -> socket tuples (proto, state, laddr, lport, raddr, rport, pid)   [sensors.snapshot_all_sockets]
  dnsmasq log     -> dns / dns-a events                                    [the controlled resolver's own log]
  tcpdump pcap    -> frames for sensors.frames_events (SNI, DNS on the wire, canaries)

Pure functions over text or a /proc-shaped folder, so tests run on recorded Linux fixtures anywhere."""
import ipaddress
import os
import re
import signal
import subprocess
import time
from pathlib import Path

# ------------------------------------------------------------------------------------------------- strace
_LINE = re.compile(r"^(?:(?P<pid>\d+)\s+)?(?:(?P<ts>\d\d:\d\d:\d\d(?:\.\d+)?)\s+)?(?P<call>[a-z_0-9]+)\((?P<args>.*)\)\s+=\s+(?P<ret>-?\d+|\?)(?P<rest>.*)$")
_UNFIN = re.compile(r"^(?:(?P<pid>\d+)\s+)?(?:(?P<ts>\d\d:\d\d:\d\d(?:\.\d+)?)\s+)?(?P<call>[a-z_0-9]+)\((?P<args>.*?)\s*<unfinished \.\.\.>\s*$")
_STR = re.compile(r'"((?:[^"\\]|\\.)*)"')
WRITE_FLAGS = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND", "O_TMPFILE")
#: the calls the gate asks strace for (linux.run_one): the file-changing ones and execve
TRACE = "network,process,open,openat,openat2,creat,unlink,unlinkat,rename,renameat,renameat2,mkdir,mkdirat,rmdir"
SKIP_PREFIX = ("/proc/", "/sys/", "/dev/")


def _unescape(s):
    return s.replace('\\"', '"').replace("\\\\", "\\")


def strace_calls(text):
    """-> [{"pid", "call", "args", "ret", "done"}]: one row per call line. A call that was `<unfinished ...>` has its
    arguments but no result (done False): it is kept, because the arguments are complete and a change that may have
    happened is reported, never dropped (fail closed)."""
    out = []
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if m:
            out.append({"pid": m["pid"], "call": m["call"], "args": m["args"], "ret": m["ret"], "done": True})
            continue
        m = _UNFIN.match(line)
        if m:
            out.append({"pid": m["pid"], "call": m["call"], "args": m["args"], "ret": None, "done": False})
    return out


def _ok(c):
    return not c["done"] or (c["ret"] not in ("-1", "?"))


def _paths(args, n_dirfd):
    """The quoted path arguments of a call; `n_dirfd` leading directory-fd arguments are skipped by quoting alone.
    A relative path next to a dirfd other than AT_FDCWD cannot be resolved from the log: it is returned as-is with
    a marker so the caller can drop it (strace -y would resolve it; not used, the cost is a log per descriptor)."""
    return [_unescape(x) for x in _STR.findall(args)]


def strace_fs_events(text, base, skip=()):
    """-> filesystem events (kind file-system, value the path) for every successful write-open, creat, unlink,
    rename and mkdir in the strace log, outside /proc, /sys, /dev and the `skip` prefixes (the profile folder: its files
    are listed whole by the profile walk). Same sensor and kind as sensors.fs_diff's, so FILESYSTEM_POLICY and
    TELEMETRY_POLICY read them unchanged. Deduplicated by path."""
    seen, out = set(), []

    def add(path, call):
        if not path.startswith("/") or path.startswith(SKIP_PREFIX) or any(path.startswith(s) for s in skip):
            return
        if path not in seen:
            seen.add(path)
            out.append({**base, "sensor": "filesystem", "kind": "file-system", "value": path, "detail": f"strace {call}"})

    for c in strace_calls(text):
        if not _ok(c):
            continue
        name, args = c["call"], c["args"]
        if name in ("open", "openat", "openat2", "creat"):
            if name != "creat" and not any(f in args for f in WRITE_FLAGS):
                continue
            for p in _paths(args, 1)[:1]:
                add(p, name)
        elif name in ("unlink", "unlinkat", "rmdir", "mkdir", "mkdirat"):
            for p in _paths(args, 1)[:1]:
                add(p, name)
        elif name in ("rename", "renameat", "renameat2"):
            for p in _paths(args, 2)[:2]:
                add(p, name)
    return out


def strace_exec_events(text, base, build_exe):
    """-> process events (sensor strace) for every successful execve, value the program's file name, detail its full
    path. Firefox starts its helper processes through /proc/self/exe: that path is the browser itself, so it is
    reported as `build_exe` (the build's own firefox), not as an unknown path under /proc. A process the 2-second
    process sampler missed is still seen here: execve is complete, sampling is not."""
    seen, out = set(), []
    for c in strace_calls(text):
        if c["call"] != "execve" or not _ok(c):
            continue
        m = _STR.search(c["args"])
        if not m:
            continue
        path = _unescape(m.group(1))
        if re.fullmatch(r"/proc/(self|\d+)/exe", path):
            path = str(build_exe)
        key = (c["pid"], path)
        if key in seen:
            continue
        seen.add(key)
        out.append({**base, "sensor": "strace", "kind": "process", "value": Path(path).name.lower(), "detail": path, "pid": int(c["pid"]) if c["pid"] else None})
    return out


STRACE_CONNECT = re.compile(r'connect\(\d+, \{sa_family=AF_INET6?, sin6?_port=htons\((\d+)\), (?:sin_addr=inet_addr\("([^"]+)"\)|inet_pton\(AF_INET6, "([^"]+)")')


def strace_connect_events(text, base, host_ips):
    """-> dest-ip events (kind dest-lan for loopback, judged in lan-probe only) for every connect() in the log; the
    gate's own host side (`host_ips`: the local test server, proxy and resolver) is not a destination."""
    out = []
    for m in STRACE_CONNECT.finditer(text or ""):
        ip = m.group(2) or m.group(3)
        if not ip or ip in host_ips:
            continue
        if ip.startswith("127.") or ip == "::1":
            out.append({**base, "sensor": "strace", "kind": "dest-lan", "value": ip, "port": int(m.group(1)), "detail": "connect()"})
        else:
            out.append({**base, "sensor": "strace", "kind": "dest-ip", "value": ip, "port": int(m.group(1)), "detail": "connect()"})
    return out


# ---------------------------------------------------------------------------------------------------- /proc
def snapshot_processes(proc_root="/proc"):
    """-> rows shaped like sensors.snapshot_processes (Win32_Process): ProcessId, ParentProcessId, Name,
    ExecutablePath, CommandLine, Created (start time in clock ticks since boot: ids are reused here too, and
    sensors.tree compares it), plus State. Zombies are listed with State Z (callers skip them for 'alive')."""
    rows = []
    for d in Path(proc_root).iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text(errors="replace")
            head, _, tail = stat.rpartition(")")                  # the name may hold spaces and parentheses
            comm = head.split("(", 1)[1] if "(" in head else "?"
            fields = tail.split()
            state, ppid, start = fields[0], int(fields[1]), int(fields[19])
        except (OSError, ValueError, IndexError):
            continue
        try:
            exe = os.readlink(d / "exe")
        except OSError:
            exe = ""
        exe = exe.removesuffix(" (deleted)")
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            cmd = ""
        rows.append({"ProcessId": int(d.name), "ParentProcessId": ppid, "Name": Path(exe).name if exe else comm,
                     "ExecutablePath": exe, "CommandLine": cmd, "Created": start, "State": state})
    return rows


def build_rows(rows, build_dir):
    """-> the live (not zombie) rows whose program lives in `build_dir`."""
    bd = str(Path(build_dir)).lower()
    return [r for r in rows if r.get("State") != "Z" and (r.get("ExecutablePath") or "").lower().startswith(bd)]


def kill_build(build_dir, snapshot=None, sig=getattr(signal, "SIGKILL", signal.SIGTERM)):
    """Signal every live process running from `build_dir` (each scenario and mode has its own copy), by PID. -> pids."""
    pids = [r["ProcessId"] for r in build_rows((snapshot or snapshot_processes)(), build_dir)]
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            pass
    return pids


def watch_after_kill(build_dir, seconds, pre_pids, snapshot=None, interval=2.0, clock=time.time, sleep=time.sleep):
    """-> rows for processes from `build_dir` that outlive the browser: alive when the `seconds` window ends, or first
    seen after the kill (a relaunch or a detached helper). Same two reasons as sensors.watch_after_kill. A process
    that is dying (zombie, or gone by the next sample) is not counted."""
    snapshot = snapshot or snapshot_processes
    seen, last, t0 = {}, {}, clock()
    while True:
        last = {}
        for r in build_rows(snapshot(), build_dir):
            seen.setdefault(r["ProcessId"], r)
            last[r["ProcessId"]] = r
        if clock() - t0 >= seconds:
            break
        sleep(interval)
    out = []
    for pid, r in seen.items():
        if pid not in pre_pids:
            out.append({**r, "why": "started after the browser was closed"})
        elif pid in last:
            out.append({**r, "why": f"still running {seconds} s after the browser was closed"})
    return out


# ------------------------------------------------------------------------------------------------------ ss
_USERS_PID = re.compile(r"pid=(\d+)")


def _split_addr(a):
    a = a.strip()
    if a in ("*", ""):
        return "", ""
    host, _, port = a.rpartition(":")
    host = host.strip("[]").split("%")[0]
    if host == "*":
        host = "0.0.0.0"
    return host, port if port != "*" else ""


def parse_ss(text):
    """-> socket tuples in the shape of sensors.snapshot_all_sockets: (proto, state, laddr, lport, raddr, rport, pid)
    with state Listen / Established / Bound / the ss state in title case. One row per socket with its owning pid
    (empty when ss cannot name one). Established connections are kept (2026-11-12: the first Linux runner dropped them)."""
    out = []
    for line in (text or "").splitlines():
        f = line.split()
        if len(f) < 6 or f[0] not in ("tcp", "udp"):
            continue
        proto, st = f[0], f[1]
        laddr, lport = _split_addr(f[4])
        raddr, rport = _split_addr(f[5])
        m = _USERS_PID.search(line)
        pid = m.group(1) if m else ""
        if proto == "udp":
            out.append((proto, "Bound", laddr, lport, "", "", pid))
            continue
        state = {"LISTEN": "Listen", "ESTAB": "Established"}.get(st, st.title().replace("-", ""))
        out.append((proto, state, laddr, lport, raddr, rport, pid))
    return out


def snapshot_all_sockets(ns):
    """-> every TCP and UDP socket in the namespace `ns` with its owning pid (ss -tunapH, run inside it)."""
    p = subprocess.run(["ip", "netns", "exec", ns, "ss", "-tunapH"], capture_output=True, text=True, errors="replace")
    return parse_ss(p.stdout)


# ------------------------------------------------------------------------------------------------- dnsmasq
DNSMASQ_Q = re.compile(r"query\[(\w+)\] (\S+) from (\S+)")
DNSMASQ_R = re.compile(r"\b(?:reply|cached) (\S+) is (\S+)")


def dnsmasq_events(text, base):
    """-> dns events (every name asked of the controlled resolver) and dns-a events (the address it answered with:
    the name -> address pair the browser's own packets are matched against)."""
    ev, seen = [], set()
    for m in DNSMASQ_Q.finditer(text or ""):
        ev.append({**base, "sensor": "dnsmasq", "kind": "dns", "value": m.group(2).lower().rstrip("."), "detail": f"type {m.group(1)}"})
    for m in DNSMASQ_R.finditer(text or ""):
        try:
            ip = str(ipaddress.ip_address(m.group(2)))
        except ValueError:
            continue                                       # <CNAME>, NXDOMAIN, NODATA,
        key = (m.group(1).lower(), ip)
        if key not in seen:
            seen.add(key)
            ev.append({**base, "sensor": "dnsmasq", "kind": "dns-a", "value": key[0].rstrip("."), "ip": ip})
    return ev


# -------------------------------------------------------------------------------------------------- pcap
def pcap_frames(path):
    """-> (timestamp, ip packet) pairs from a classic pcap of any Linux link type (Ethernet, cooked v1/v2, raw)."""
    import dpkt
    with open(path, "rb") as f:
        reader = dpkt.pcap.Reader(f)
        dl = reader.datalink()
        for ts, buf in reader:
            try:
                if dl == 1:
                    yield ts, dpkt.ethernet.Ethernet(buf).data
                elif dl == 113:
                    yield ts, dpkt.sll.SLL(buf).data
                elif dl == 276:
                    yield ts, dpkt.sll2.SLL2(buf).data
                elif dl in (101, 12):
                    yield ts, dpkt.ip.IP(buf) if buf[:1] and buf[0] >> 4 == 4 else dpkt.ip6.IP6(buf)
            except Exception:
                continue
