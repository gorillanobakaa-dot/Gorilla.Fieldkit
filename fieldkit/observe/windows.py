"""observe, Windows reader: Restart Manager, GetExtendedTcpTable, Toolhelp, ReadDirectoryChangesW (ctypes), Get-WinEvent.
Read-only; returns None on another system. The file-holder and tracing code is kits/wintrace's, loaded from there."""
import ctypes
import importlib.util
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

PLATFORM = "win32"
NEEDS_ADMIN = "needs administrator (a kernel trace): run it from an elevated shell; nothing was asked or changed"
WINTRACE = Path(__file__).resolve().parents[2] / "kits" / "wintrace" / "wintrace.py"


def _here():
    return sys.platform == "win32"


def _wintrace():
    spec = importlib.util.spec_from_file_location("wintrace", WINTRACE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _toolhelp():
    """{pid: (ppid, exe name)} from a Toolhelp32 snapshot."""
    from ctypes import wintypes

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    out, e = {}, PE()
    e.dwSize = ctypes.sizeof(PE)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


def open_file(path):
    """Processes with PATH open or locked, through the Restart Manager (RmGetList only)."""
    if not _here():
        return None
    return [{"pid": pid, "name": app, "how": [kind] + ([f"service {svc}"] if svc else [])}
            for pid, app, svc, kind in _wintrace().fuser([path])]


def _tcp_table(udp=False):
    """Rows (local port, remote port, state, pid, proto) from GetExtendedTcpTable / GetExtendedUdpTable (IPv4)."""
    iph = ctypes.WinDLL("iphlpapi")
    size = ctypes.c_ulong(0)
    fn = iph.GetExtendedUdpTable if udp else iph.GetExtendedTcpTable
    fn(None, ctypes.byref(size), False, socket.AF_INET, 1 if udp else 5, 0)   # OWNER_PID / OWNER_PID_ALL
    buf = ctypes.create_string_buffer(size.value)
    if fn(buf, ctypes.byref(size), False, socket.AF_INET, 1 if udp else 5, 0):
        return []
    n = struct.unpack_from("I", buf, 0)[0]
    states = {2: "LISTEN", 5: "ESTABLISHED", 11: "TIME_WAIT", 1: "CLOSED"}
    rows, width = [], 12 if udp else 24
    for i in range(n):
        f = struct.unpack_from("I" * (width // 4), buf, 4 + i * width)
        if udp:
            rows.append((socket.ntohs(f[1] & 0xFFFF), 0, "-", f[2], "udp"))
        else:
            rows.append((socket.ntohs(f[2] & 0xFFFF), socket.ntohs(f[4] & 0xFFFF), states.get(f[0], str(f[0])),
                         f[5], "tcp"))
    return rows


def port(n):
    if not _here():
        return None
    n, th = int(n), _toolhelp()
    return [{"proto": pr, "local": f"port {lp}", "remote": f"port {rp}", "state": st, "pid": pid,
             "name": th.get(pid, (0, ""))[1]}
            for udp in (False, True) for lp, rp, st, pid, pr in _tcp_table(udp) if n in (lp, rp)]


def proc(pid):
    if not _here():
        return None
    import psutil
    pid = int(pid)
    try:
        p = psutil.Process(pid)
        th = _toolhelp()
        return {"pid": pid, "name": p.name(), "exe": p.exe(), "cwd": p.cwd(), "user": p.username(),
                "cmdline": p.cmdline(), "children": [{"pid": c, "name": v[1]} for c, v in th.items() if v[0] == pid],
                "map_regions": len(p.memory_maps()), "map_files": sorted({m.path for m in p.memory_maps()})}
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return {"pid": pid, "error": str(e)}


def watch(directory, seconds):
    """ReadDirectoryChangesW on DIRECTORY (and below) with overlapped I/O, for SECONDS."""
    if not _here():
        return None
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    h = k32.CreateFileW(str(directory), 0x1, 7, None, 3, 0x02000000 | 0x40000000, None)   # BACKUP_SEMANTICS | OVERLAPPED
    if h in (None, wintypes.HANDLE(-1).value, ctypes.c_void_p(-1).value):
        return {"error": "cannot open the folder", "events": []}

    class OV(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t), ("Offset", wintypes.DWORD),
                    ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]
    labels = {1: "created", 2: "deleted", 3: "modified", 4: "moved from", 5: "moved to"}
    ev, end, events = [], time.time() + float(seconds), []
    ov, buf, got = OV(), ctypes.create_string_buffer(65536), wintypes.DWORD(0)
    k32.CreateEventW.restype = wintypes.HANDLE
    ov.hEvent = k32.CreateEventW(None, True, False, None)
    try:
        while time.time() < end:
            k32.ResetEvent(ov.hEvent)
            k32.ReadDirectoryChangesW(h, buf, len(buf), True, 0x1 | 0x2 | 0x8 | 0x10, None, ctypes.byref(ov), None)
            wait = int(max(0, end - time.time()) * 1000)
            if k32.WaitForSingleObject(ov.hEvent, wait) != 0:
                k32.CancelIoEx(h, ctypes.byref(ov))
                break
            k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(got), False)
            off = 0
            while got.value:
                nxt, action, ln = struct.unpack_from("III", buf, off)
                name = buf.raw[off + 12:off + 12 + ln].decode("utf-16-le")
                events.append({"t": round(time.time(), 3), "event": labels.get(action, str(action)),
                               "path": str(Path(directory) / name)})
                if not nxt:
                    break
                off += nxt
    finally:
        k32.CloseHandle(ov.hEvent)
        k32.CloseHandle(h)
    return {"events": events}


def trace(pid, seconds):
    """Files PID opens, for SECONDS: kits/wintrace opensnoop (ETW Kernel-File; administrator)."""
    if not _here():
        return None
    if not ctypes.windll.shell32.IsUserAnAdmin():
        return {"error": NEEDS_ADMIN, "needs_admin": True, "events": []}
    r = subprocess.run([sys.executable, str(WINTRACE), "opensnoop", "--seconds", str(int(float(seconds))),
                        "--pid", str(int(pid)), "--yes", "--json"], capture_output=True, text=True, timeout=300)
    return {"events": r.stdout.splitlines()[-200:], "error": r.stderr.strip()[:200] or None}


def usage(pid, seconds):
    if not _here():
        return None
    import psutil
    try:
        p = psutil.Process(int(pid))
        p.cpu_percent(None)
        rows = []
        for _ in range(max(1, int(float(seconds)))):
            io0 = p.io_counters()
            time.sleep(1)
            io1 = p.io_counters()
            rows.append({"cpu_pct": p.cpu_percent(None), "rss_kb": p.memory_info().rss // 1024,
                         "read_bytes": io1.read_bytes - io0.read_bytes, "write_bytes": io1.write_bytes - io0.write_bytes})
        return {"samples": rows}
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return {"error": str(e), "samples": []}


def events(words):
    """Event log lines (System, Application; the last 2000 each) holding every one of WORDS."""
    if not _here():
        return None
    cond = " -and ".join(f"$_.Message -like '*{w.replace(chr(39), '')}*'" for w in words) or "$true"
    ps = ("$ErrorActionPreference='SilentlyContinue'; 'System','Application' | ForEach-Object { "
          "Get-WinEvent -LogName $_ -MaxEvents 2000 } | Where-Object { " + cond + " } | Select-Object -First 200 | "
          "ForEach-Object { '{0} {1} {2}: {3}' -f $_.TimeCreated.ToString('s'), $_.LogName, $_.ProviderName, "
          "($_.Message -split \"`n\")[0] }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=180)
    return {"lines": r.stdout.splitlines()}
