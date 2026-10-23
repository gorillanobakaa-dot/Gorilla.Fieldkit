"""wintrace: the Linux incident-response reflexes Windows lacks - ss -tulnp, fuser, opensnoop, listdlls, "who drew this".

DEFENSIVE AND READ-ONLY. This tool inspects the owner's own Windows computer for signs of intrusion. It sends nothing
anywhere, and it disables, stops, kills or changes nothing: it only reads what Windows already reports through its own
APIs and tools (psutil, Get-AuthenticodeSignature, Win32_Service, the Restart Manager, EnumWindows, logman, tracerpt).
No downloads.

Owner, 2026-10-22, with an unexplained "Please quit and reopen Claude to finish installing an important security
update" banner on screen: "have you run an ss -tulnp and opensnoop-bpf or a fuser on the banner event though? ...
Windows harness lacks some of the most important tools for situations like these".

    wintrace ss [--listen] [--pid P] [--json]       sockets with their process, path, signer and the services inside
                                                    that process (ss -tulnp + which svchost service it is)
    wintrace fuser PATH [PATH ...] [--json]         which processes have a file open (Restart Manager API, no admin;
                                                    only RmGetList is called, never RmShutdown)
    wintrace modules PID [--all] [--json]           every DLL/EXE/.node mapped into a process and its Authenticode signer
    wintrace svc PID [--json]                       the Windows services living in a process (svchost groups)
    wintrace whodraws [--over PID|NAME | --at X Y] [--json]
                                                    visible windows in Z order with their owner; which windows of OTHER
                                                    processes lie over a process's window, or which are at a screen
                                                    point (overlays, fake prompts). Never takes a screenshot.
    wintrace opensnoop --seconds N [--pid P] [--match RE] [--keep] --yes [--json]
                                                    files opened, by process, for N seconds (1..120): a short ETW trace
                                                    of Windows' own Microsoft-Windows-Kernel-File provider (logman),
                                                    converted with tracerpt to CSV (NEVER XML: one trace grew to 43 GB).
                                                    Admin = one UAC click through the elevate kit. Deletes nothing but
                                                    its own temporary trace files.
    wintrace doctor [--json]

Every command ends with one VERDICT line and one NEXT line.
Exit codes: 0 fine, 1 error, 2 bad usage / refused, 3 findings (unsigned code, an overlay, a broken trace).

Measured with these on 2026-10-22: the banner was drawn inside Claude's own window (no window of another process lay
over it), and all 157 modules of that process were validly signed (155 Microsoft, 2 Anthropic).
"""
import argparse
import csv
import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path, PureWindowsPath

VERSION = "1.0.0"
WINDOWS = sys.platform == "win32"
HERE = Path(__file__).resolve().parent
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
POWERSHELL = SYSTEM32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"
MAX_SECONDS = 120
ETW_PROVIDER = "Microsoft-Windows-Kernel-File"
ETW_KEYWORD = "0x80"          # KERNEL_FILE_KEYWORD_CREATE: one event per file open (event 12, has FileName)
ETW_MAX_MB = 512              # the trace stops growing at this size (sequential mode), whatever happens
ELEVATE_MARKER = "ELEVATE: script finished"
# Code that can run inside a process. A mapped file is also treated as code when it starts with "MZ" (a PE image),
# whatever its name: a DLL renamed .dat is still a DLL.
CODE_EXT = {".dll", ".exe", ".node", ".sys", ".drv", ".ocx", ".cpl", ".ax", ".ime", ".acm", ".tsp", ".pyd", ".scr"}
KERNEL_PIDS = (0, 4)          # System Idle and System: no image file, owned by the kernel
MIN_OVERLAP_PX = 32 * 32      # smaller overlaps (a 1 px helper window, a frame edge) cannot carry a banner: listed as slivers
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED = 0x8, 0x20, 0x80, 0x80000


def elevate_candidates():
    """Where the elevate kit lives: this checkout's local/, then the owner's main Fieldkit, then the old place."""
    home = Path.home()
    return [HERE.parents[1] / "local" / "kits" / "Elevate" / "elevate.py",
            home / "Documents" / "Scripts" / "Fieldkit" / "local" / "kits" / "Elevate" / "elevate.py",
            home / "Documents" / "Scripts" / "Elevate" / "elevate.py"]


def find_elevate(candidates=None):
    for p in candidates if candidates is not None else elevate_candidates():
        if Path(p).is_file():
            return Path(p)
    return None


def emit(obj, as_json, lines, verdict, nxt):
    """Print the result; the last two lines are always VERDICT and NEXT."""
    obj = dict(obj, verdict=verdict, next=nxt)
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
    else:
        for line in lines:
            print(line)
        print(f"VERDICT: {verdict}")
        print(f"NEXT: {nxt}")


def ps_json(script, stdin=None, run=subprocess.run, timeout=300):
    """Run a read-only PowerShell query and return its JSON output as a list."""
    r = run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", script], input=stdin,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    text = (r.stdout or "").strip()
    if not text:
        return []
    d = json.loads(text)
    return d if isinstance(d, list) else [d]


# ---- signatures and services, shared ------------------------------------------------------------------------------
def org_of(subject):
    """'CN=Microsoft Windows, O=Microsoft Corporation, ...' -> 'Microsoft Corporation' (else the CN, else '')."""
    for key in ("O", "CN"):
        m = re.search(r'(?:^|,\s*)' + key + r'=("([^"]*)"|[^,]*)', subject or "")
        if m:
            return (m.group(2) if m.group(2) is not None else m.group(1)).strip()
    return ""


def signatures(paths, run=subprocess.run):
    """{path: {"status", "subject", "org"}} through Get-AuthenticodeSignature (catalog-signed Windows files
    included). A file that cannot be read gets status 'Unreadable', never 'Valid'."""
    paths = [p for p in dict.fromkeys(paths) if p]
    if not paths:
        return {}
    rows = ps_json("$r = @(); foreach ($p in $input) { $s = Get-AuthenticodeSignature -LiteralPath $p "
                   "-ErrorAction SilentlyContinue; $r += [ordered]@{ path = [string]$p; status = [string]$s.Status; "
                   "subject = [string]$s.SignerCertificate.Subject } }; ConvertTo-Json -InputObject $r -Depth 3",
                   stdin="\n".join(paths), run=run)
    out = {}
    for r in rows:
        subject = r.get("subject") or ""
        out[r["path"]] = {"status": r.get("status") or "Unreadable", "subject": subject, "org": org_of(subject)}
    for p in paths:
        out.setdefault(p, {"status": "Unreadable", "subject": "", "org": ""})
    return out


def services_by_pid(run=subprocess.run):
    """{pid: [{name, display, start, state}]} from Win32_Service.ProcessId (readable without admin)."""
    rows = ps_json("$r = @(Get-CimInstance Win32_Service | Where-Object { $_.ProcessId -gt 0 } | ForEach-Object { "
                   "[ordered]@{ pid = [int]$_.ProcessId; name = $_.Name; display = $_.DisplayName; "
                   "start = $_.StartMode; state = $_.State } }); ConvertTo-Json -InputObject $r -Depth 3", run=run)
    out = {}
    for r in rows:
        out.setdefault(int(r["pid"]), []).append(r)
    return out


def process_facts(pid):
    """{name, exe, cmdline} of a process; fields the current user may not read stay ''."""
    import psutil
    facts = {"pid": pid, "name": "", "exe": "", "cmdline": ""}
    try:
        p = psutil.Process(pid)
    except psutil.Error:
        return facts
    for key, getter in (("name", p.name), ("exe", p.exe), ("cmdline", lambda: " ".join(p.cmdline()))):
        try:
            facts[key] = getter() or ""
        except (psutil.Error, OSError):
            pass
    return facts


# ---- ss -tulnp ----------------------------------------------------------------------------------------------------
def is_loopback(ip):
    return ip.startswith("127.") or ip in ("::1", "::ffff:127.0.0.1") or ip.startswith("::ffff:127.")


def sockets():
    import psutil
    rows = []
    for c in psutil.net_connections(kind="inet"):
        tcp = c.type == 1   # SOCK_STREAM
        v6 = c.family not in (2,)  # AF_INET is 2 everywhere
        rows.append({"proto": ("tcp" if tcp else "udp") + ("6" if v6 else ""),
                     "state": c.status if tcp else "UNCONN",
                     "lip": c.laddr.ip if c.laddr else "", "lport": c.laddr.port if c.laddr else 0,
                     "rip": c.raddr.ip if c.raddr else "", "rport": c.raddr.port if c.raddr else 0,
                     "pid": c.pid or 0})
    return rows


def judge_sockets(rows, procs, sigs, svcs, listen_only=False, pid=None):
    """Decide each socket. A listener reachable from other machines (bound to anything but loopback) is FLAGGED when
    the binary that owns it is not validly signed; one whose binary cannot be read is UNVERIFIED (never assumed
    good). The kernel (pid 0/4: http.sys, SMB) owns no image file and is reported as 'kernel'."""
    out = []
    for r in rows:
        listening = r["state"] in ("LISTEN", "UNCONN")
        if listen_only and not listening:
            continue
        if pid is not None and r["pid"] != pid:
            continue
        f = procs.get(r["pid"], {})
        exe = f.get("exe", "")
        sig = sigs.get(exe, {"status": "Unreadable", "org": ""}) if exe else {"status": "", "org": ""}
        exposed = listening and not is_loopback(r["lip"])
        if r["pid"] in KERNEL_PIDS:
            judgement = "kernel"
        elif not exposed:
            judgement = ""
        elif not exe:
            judgement = "UNVERIFIED"
        elif sig["status"] != "Valid":
            judgement = "FLAGGED"
        else:
            judgement = "ok"
        out.append(dict(r, name=f.get("name", ""), exe=exe, sig=sig.get("status", ""), signer=sig.get("org", ""),
                        services=[s["name"] for s in svcs.get(r["pid"], [])], exposed=exposed, judgement=judgement))
    out.sort(key=lambda s: (not s["exposed"], s["state"] not in ("LISTEN", "UNCONN"), s["proto"], s["lport"]))
    return out


def sockets_verdict(judged):
    flagged = [s for s in judged if s["judgement"] == "FLAGGED"]
    unverified = [s for s in judged if s["judgement"] == "UNVERIFIED"]
    exposed = [s for s in judged if s["exposed"]]
    if flagged:
        who = ", ".join(sorted({f"{s['name'] or '?'} ({s['pid']}, {s['sig'] or 'unsigned'})" for s in flagged}))
        return (f"FLAGGED: {len(flagged)} listener(s) reachable from other machines owned by a binary that is not "
                f"validly signed: {who}", 3)
    if unverified:
        who = ", ".join(sorted({f"{s['name'] or '?'} ({s['pid']})" for s in unverified}))
        return (f"UNVERIFIED: {len(unverified)} exposed listener(s) whose binary this user cannot read: {who}; "
                f"the other {len(exposed) - len(unverified)} are validly signed or the kernel's", 3)
    return (f"OK: {len(exposed)} listener(s) reachable from other machines, every one owned by a validly signed "
            f"binary or the kernel; {len(judged)} socket(s) shown", 0)


def cmd_ss(a):
    rows = sockets()
    pids = sorted({r["pid"] for r in rows})
    procs = {p: process_facts(p) for p in pids}
    sigs = signatures([f["exe"] for f in procs.values() if f["exe"]])
    judged = judge_sockets(rows, procs, sigs, services_by_pid(), a.listen, a.pid)
    verdict, code = sockets_verdict(judged)
    lines = [f"{'proto':<5} {'state':<12} {'local':<30} {'remote':<30} {'pid':>6} {'process':<22} {'signature':<12} "
             f"judgement  services"]
    for s in judged:
        local = f"[{s['lip']}]:{s['lport']}" if ":" in s["lip"] else f"{s['lip']}:{s['lport']}"
        remote = "" if not s["rip"] else (f"[{s['rip']}]:{s['rport']}" if ":" in s["rip"] else f"{s['rip']}:{s['rport']}")
        lines.append(f"{s['proto']:<5} {s['state']:<12} {local:<30} {remote:<30} {s['pid']:>6} {s['name'][:22]:<22} "
                     f"{(s['sig'] + ' ' + s['signer'])[:12]:<12} {s['judgement']:<10} {','.join(s['services'])[:60]}")
    nxt = ("wintrace modules PID for each FLAGGED/UNVERIFIED pid, then wintrace svc PID" if code else
           "wintrace whodraws --over PID or wintrace modules PID for a process you do not recognize")
    emit({"sockets": judged}, a.json, lines, verdict, nxt)
    return code


# ---- fuser (Restart Manager) --------------------------------------------------------------------------------------
if WINDOWS:
    from ctypes import wintypes

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [("Process", RM_UNIQUE_PROCESS), ("strAppName", ctypes.c_wchar * 256),
                    ("strServiceShortName", ctypes.c_wchar * 64), ("ApplicationType", ctypes.c_int),
                    ("AppStatus", wintypes.ULONG), ("TSSessionId", wintypes.DWORD), ("bRestartable", wintypes.BOOL)]

RM_APP_TYPES = {0: "unknown", 1: "main window", 2: "other window", 3: "service", 4: "explorer", 5: "console",
                1000: "critical"}


def fuser(paths):
    """[(pid, app name, service short name, app type)] of processes holding any of PATHS open. Read-only: a Restart
    Manager session is opened, the files registered, the list read and the session ended. RmShutdown (the only call
    that acts on processes) is never called."""
    rm = ctypes.WinDLL("rstrtmgr.dll")
    handle, key = wintypes.DWORD(0), ctypes.create_unicode_buffer(33)
    err = rm.RmStartSession(ctypes.byref(handle), 0, key)
    if err:
        raise OSError(err, f"RmStartSession failed ({err})")
    try:
        files = (wintypes.LPCWSTR * len(paths))(*[str(Path(p).resolve()) for p in paths])
        err = rm.RmRegisterResources(handle, len(paths), files, 0, None, 0, None)
        if err:
            raise OSError(err, f"RmRegisterResources failed ({err})")
        needed, count, reasons = wintypes.UINT(0), wintypes.UINT(0), wintypes.DWORD(0)
        for _ in range(5):                       # the list can grow between calls: ERROR_MORE_DATA (234), try again
            arr = (RM_PROCESS_INFO * max(needed.value, 1))()
            count.value = len(arr)
            err = rm.RmGetList(handle, ctypes.byref(needed), ctypes.byref(count), arr, ctypes.byref(reasons))
            if err != 234:
                break
        if err:
            raise OSError(err, f"RmGetList failed ({err})")
        return [(arr[i].Process.dwProcessId, arr[i].strAppName, arr[i].strServiceShortName,
                 RM_APP_TYPES.get(arr[i].ApplicationType, str(arr[i].ApplicationType))) for i in range(count.value)]
    finally:
        rm.RmEndSession(handle)


def cmd_fuser(a):
    missing = [p for p in a.paths if not Path(p).exists()]
    if missing:
        emit({"missing": missing}, a.json, [], f"ERROR: no such file: {', '.join(missing)}",
             "check the path; fuser needs files that exist")
        return 1
    rows, failed = [], {}
    for path in a.paths:                          # one session per file, so each holder is tied to its file
        try:
            holders = fuser([path])
        except OSError as e:                      # 6 for a system DLL: a holder this user may not open
            failed[path] = e.strerror
            continue
        for pid, app, svc, kind in holders:
            f = process_facts(pid)
            rows.append({"path": str(Path(path).resolve()), "pid": pid, "name": f["name"] or app, "exe": f["exe"],
                         "service": svc, "type": kind})
    lines = [f"{r['path']}: pid {r['pid']} {r['name']} [{r['type']}{', service ' + r['service'] if r['service'] else ''}]"
             f" {r['exe']}" for r in rows]
    lines += [f"{p}: UNKNOWN - {why}" for p, why in failed.items()]
    holders = sorted({r["pid"] for r in rows})
    if failed:                                    # fail closed: a list that could not be read is never "free"
        emit({"holders": rows, "failed": failed}, a.json, lines,
             f"UNKNOWN: the Restart Manager could not list the holders of {len(failed)} file(s) (a protected or "
             f"another user's process holds them); {len(rows)} hold(s) found on the rest",
             "run the same command from an elevated shell, or wintrace opensnoop --match NAME --yes")
        return 3
    verdict = (f"HELD: {len(rows)} hold(s) on {len({r['path'] for r in rows})} file(s) by pid(s) "
               f"{', '.join(map(str, holders))}" if rows else f"FREE: no process holds {len(a.paths)} file(s) open")
    emit({"holders": rows, "failed": failed}, a.json, lines, verdict,
         "wintrace modules PID / wintrace svc PID on a holder you do not recognize" if rows else
         "a file opened and closed quickly will not show here: wintrace opensnoop --seconds 30 --match NAME --yes")
    return 0


# ---- modules -----------------------------------------------------------------------------------------------------
def is_pe(path):
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def mapped_files(pid):
    import psutil
    return sorted({m.path for m in psutil.Process(pid).memory_maps(grouped=True) if m.path})


def judge_modules(paths, sigs, pe=is_pe, all_files=False):
    """-> (rows, counts by signer, bad). A module is code when its extension says so or it starts with MZ. Code that
    is not validly signed is bad; with all_files, data files are listed too but never judged."""
    rows, by_org, bad = [], Counter(), []
    for p in paths:
        code = Path(p).suffix.lower() in CODE_EXT or pe(p)
        if not code and not all_files:
            continue
        s = sigs.get(p, {"status": "Unreadable", "org": ""})
        row = {"path": p, "code": code, "status": s["status"], "signer": s["org"]}
        rows.append(row)
        if not code:
            continue
        if s["status"] == "Valid":
            by_org[s["org"] or "?"] += 1
        else:
            bad.append(row)
    return rows, by_org, bad


def modules_verdict(pid, rows, by_org, bad):
    n = sum(1 for r in rows if r["code"])
    if bad:
        return (f"UNSIGNED: {len(bad)} of {n} modules in pid {pid} are not validly signed: "
                + "; ".join(f"{Path(r['path']).name} ({r['status']})" for r in bad[:8]), 3)
    if not n:
        return f"UNKNOWN: no modules read for pid {pid}", 3
    return (f"OK: {n} modules in pid {pid}, all validly signed ("
            + ", ".join(f"{c} {o}" for o, c in by_org.most_common()) + ")", 0)


def cmd_modules(a):
    import psutil
    try:
        paths = mapped_files(a.pid)
    except psutil.NoSuchProcess:
        emit({}, a.json, [], f"ERROR: no process {a.pid}", "wintrace whodraws or ss to find the pid")
        return 1
    except psutil.AccessDenied:
        emit({}, a.json, [], f"ERROR: this user may not read the memory map of pid {a.pid} (protected or another "
             "user's process)", "wintrace svc PID still works; a SYSTEM process needs an elevated shell")
        return 1
    candidates = paths if a.all else [p for p in paths if Path(p).suffix.lower() in CODE_EXT or is_pe(p)]
    sigs = signatures(candidates)
    rows, by_org, bad = judge_modules(paths, sigs, all_files=a.all)
    verdict, code = modules_verdict(a.pid, rows, by_org, bad)
    f = process_facts(a.pid)
    lines = [f"pid {a.pid} {f['name']} {f['exe']}"]
    lines += [f"{r['status']:<12} {r['signer'][:30]:<30} {r['path']}{'' if r['code'] else '  (data)'}" for r in rows]
    emit({"pid": a.pid, "process": f, "modules": rows, "by_signer": dict(by_org)}, a.json, lines, verdict,
         "fuser PATH / opensnoop --match NAME on an unsigned module to see who else uses it" if bad else
         "wintrace whodraws --over PID to see whether another process draws over this one")
    return code


# ---- svc -----------------------------------------------------------------------------------------------------------
def svchost_group(cmdline):
    m = re.search(r"\s-k\s+(\S+)", cmdline or "", re.I)
    return m.group(1) if m else ""


def cmd_svc(a):
    f = process_facts(a.pid)
    if not f["name"]:
        emit({}, a.json, [], f"ERROR: no process {a.pid}", "wintrace ss or whodraws to find the pid")
        return 1
    svcs = services_by_pid().get(a.pid, [])
    group = svchost_group(f["cmdline"])
    lines = [f"pid {a.pid} {f['name']} {f['exe']}" + (f" (svchost -k {group})" if group else "")]
    lines += [f"{s['name']:<32} {s['state']:<10} {s['start']:<10} {s['display']}" for s in svcs]
    verdict = (f"{len(svcs)} service(s) in pid {a.pid}: {', '.join(s['name'] for s in svcs)}" if svcs else
               f"NO SERVICES: pid {a.pid} ({f['name']}) hosts no Windows service")
    emit({"pid": a.pid, "process": f, "group": group, "services": svcs}, a.json, lines, verdict,
         "wintrace modules PID to see what code is loaded in that process")
    return 0


# ---- whodraws ------------------------------------------------------------------------------------------------------
def enum_windows():
    """Visible top-level windows in Z order, top first. Reads window facts only; never captures pixels."""
    u32, dwm, k32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("dwmapi"), ctypes.WinDLL("kernel32")
    try:
        u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor v2: rectangles in physical pixels
    except (AttributeError, OSError):
        pass
    get_long = getattr(u32, "GetWindowLongPtrW", u32.GetWindowLongW)
    get_long.restype, get_long.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int]
    u32.GetForegroundWindow.restype = wintypes.HWND
    k32.OpenProcess.restype = wintypes.HANDLE
    names = {}

    def image(pid):
        if pid not in names:
            h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION, read-only
            buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
            ok = h and k32.QueryFullProcessImageNameW(wintypes.HANDLE(h), 0, buf, ctypes.byref(size))
            if h:
                k32.CloseHandle(wintypes.HANDLE(h))
            names[pid] = buf.value if ok else ""
        return names[pid]

    hwnds = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(lambda h, _: hwnds.append(h) or True)
    u32.EnumWindows(proc, 0)
    fg = u32.GetForegroundWindow()
    out = []
    for z, h in enumerate(hwnds):
        h = wintypes.HWND(h)
        if not u32.IsWindowVisible(h):
            continue
        r = wintypes.RECT()
        # the frame the compositor draws; GetWindowRect adds an invisible resize border (8 px on a maximized window)
        if dwm.DwmGetWindowAttribute(h, 9, ctypes.byref(r), ctypes.sizeof(r)):   # DWMWA_EXTENDED_FRAME_BOUNDS
            u32.GetWindowRect(h, ctypes.byref(r))
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        title, cls = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(h, title, 256)
        u32.GetClassNameW(h, cls, 256)
        cloaked = wintypes.DWORD(0)
        dwm.DwmGetWindowAttribute(h, 14, ctypes.byref(cloaked), 4)   # DWMWA_CLOAKED: not drawn by the compositor
        ex = get_long(h, -20)                                        # GWL_EXSTYLE
        alpha, flags = ctypes.c_ubyte(255), wintypes.DWORD(0)
        has_alpha = (ex & WS_EX_LAYERED) and u32.GetLayeredWindowAttributes(h, None, ctypes.byref(alpha),
                                                                             ctypes.byref(flags)) and flags.value & 2
        out.append({"z": z, "hwnd": h.value or 0, "pid": pid.value, "exe": image(pid.value), "title": title.value,
                    "class": cls.value, "rect": [r.left, r.top, r.right, r.bottom],
                    "topmost": bool(ex & WS_EX_TOPMOST), "layered": bool(ex & WS_EX_LAYERED),
                    "clickthrough": bool(ex & WS_EX_TRANSPARENT), "tool": bool(ex & WS_EX_TOOLWINDOW),
                    "alpha": alpha.value if has_alpha else None, "cloaked": bool(cloaked.value),
                    "minimized": bool(u32.IsIconic(h)),
                    "foreground": (h.value or 0) == (fg or 0)})
    return out


def area(rect):
    return max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])


def intersect(a, b):
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]


def drawn(w):
    """A window the compositor actually draws: not cloaked, not minimized, not empty, not fully transparent."""
    return not w["cloaked"] and not w.get("minimized") and area(w["rect"]) > 0 and w["alpha"] != 0


def judge_over(windows, target_pids):
    """Windows of OTHER processes that lie above (earlier in Z order than) a drawn window of the target processes and
    overlap it by at least MIN_OVERLAP_PX. -> (target windows, [(over window, target hwnd, percent of the target)],
    slivers: the same for smaller overlaps)."""
    targets = [w for w in windows if w["pid"] in target_pids and drawn(w)]
    over, slivers = [], []
    for t in targets:
        for w in windows:
            if w["z"] >= t["z"]:
                break
            if w["pid"] in target_pids or w["cloaked"] or w.get("minimized") or area(w["rect"]) == 0:
                continue                                  # an alpha-0 window still counts: it can take the clicks
            inter = area(intersect(w["rect"], t["rect"]))
            if inter:
                (over if inter >= MIN_OVERLAP_PX else slivers).append((w, t["hwnd"], round(100 * inter / area(t["rect"]), 2)))
    return targets, over, slivers


def judge_at(windows, x, y):
    """Drawn windows that contain the screen point, top first."""
    return [w for w in windows if not w["cloaked"] and not w.get("minimized") and area(w["rect"])
            and w["rect"][0] <= x < w["rect"][2] and w["rect"][1] <= y < w["rect"][3]]


def wline(w, sigs=None):
    s = (sigs or {}).get(w["exe"], {})
    flags = ",".join(k for k in ("topmost", "layered", "clickthrough", "tool", "cloaked", "minimized", "foreground") if w[k])
    if w["alpha"] is not None:
        flags += f",alpha={w['alpha']}"
    return (f"z{w['z']:<4} pid {w['pid']:<6} {PureWindowsPath(w['exe']).name or '?':<24} {str(w['rect']):<26} "
            f"{flags.strip(','):<28} {(s.get('status', '') + ' ' + s.get('org', ''))[:28]:<28} "
            f"{w['class'][:28]!s:<28} {w['title'][:50]!r}")


def resolve_targets(spec, windows):
    """--over takes a pid or a process image name (claude.exe): every pid with that name."""
    if spec.isdigit():
        return {int(spec)}
    # a Windows path is read as one: on another OS Path() does not split at "\\" (the CI's Linux runner failed here)
    return {w["pid"] for w in windows if PureWindowsPath(w["exe"]).name.lower() == spec.lower()}


def cmd_whodraws(a):
    windows = enum_windows()
    if a.over:
        pids = resolve_targets(a.over, windows)
        targets, over, slivers = judge_over(windows, pids)
        sigs = signatures([w["exe"] for w, _, _ in over + slivers] + [w["exe"] for w in targets])
        lines = ["target windows:"] + [wline(w, sigs) for w in targets]
        lines += ["windows of other processes above them:"] + [f"{wline(w, sigs)}  covers {pct}% of target"
                                                                for w, _, pct in over]
        lines += [f"slivers (under {MIN_OVERLAP_PX} px of overlap):"] + [f"{wline(w, sigs)}  covers {pct}% of target"
                                                                          for w, _, pct in slivers]
        if not targets:
            mine = sorted({w["pid"] for w in windows if Path(w["exe"]).name.lower() ==
                           Path(process_facts(next(iter(pids), 0))["exe"]).name.lower()} - pids) if pids else []
            emit({"targets": [], "over": []}, a.json, lines, f"NO WINDOW: {a.over} draws no visible window",
                 f"try the pid that owns the window: {', '.join(map(str, mine)) or 'wintrace whodraws'}")
            return 1
        unsigned = [w for w, _, _ in over if sigs.get(w["exe"], {}).get("status") != "Valid"]
        if over:
            verdict = (f"OVER: {len({w['hwnd'] for w, _, _ in over})} window(s) of other processes lie over "
                       f"{a.over}'s window ({len(unsigned)} not validly signed): "
                       + ", ".join(sorted({f"{Path(w['exe']).name or '?'} ({w['pid']})" for w, _, _ in over})))
        else:
            verdict = (f"NOTHING OVER: no window of another process lies over {a.over}'s "
                       f"{len(targets)} visible window(s); what is on it is drawn by {a.over} itself")
        emit({"targets": targets, "over": [dict(w, target=t, percent=p) for w, t, p in over],
              "slivers": [dict(w, target=t, percent=p) for w, t, p in slivers], "signatures": sigs},
             a.json, lines, verdict,
             "wintrace modules PID of each window above it" if over else f"wintrace modules {a.over} to check the "
             "code that draws it" if a.over.isdigit() else "wintrace modules PID of the window's process")
        return 3 if unsigned else 0
    if a.at:
        x, y = a.at
        hits = judge_at(windows, x, y)
        sigs = signatures([w["exe"] for w in hits])
        lines = [wline(w, sigs) for w in hits]
        top = hits[0] if hits else None
        verdict = (f"AT ({x},{y}): the top window is {Path(top['exe']).name or '?'} (pid {top['pid']}, "
                   f"{sigs.get(top['exe'], {}).get('status', '?')}) '{top['title'][:60]}'; {len(hits)} window(s) "
                   "stack there" if top else f"AT ({x},{y}): no window there")
        emit({"at": [x, y], "windows": hits, "signatures": sigs}, a.json, lines, verdict,
             f"wintrace modules {top['pid']}" if top else "check the coordinates (physical pixels)")
        return 0
    shown = [w for w in windows if drawn(w)]
    sigs = signatures([w["exe"] for w in shown if w["topmost"]])
    lines = [wline(w, sigs) for w in shown]
    topmost = [w for w in shown if w["topmost"]]
    unsigned = [w for w in topmost if sigs.get(w["exe"], {}).get("status") != "Valid"]
    verdict = (f"{len(shown)} drawn window(s) from {len({w['pid'] for w in shown})} process(es); {len(topmost)} "
               f"always-on-top, {len(unsigned)} of them not validly signed"
               + (": " + ", ".join(sorted({f"{Path(w['exe']).name or '?'} ({w['pid']})" for w in unsigned}))
                  if unsigned else ""))
    emit({"windows": shown, "signatures": sigs}, a.json, lines, verdict,
         "wintrace whodraws --over PID for the window in question, or --at X Y for a point on it")
    return 3 if unsigned else 0


# ---- opensnoop -----------------------------------------------------------------------------------------------------
def volume_map():
    """{'\\Device\\HarddiskVolume3': 'C:'} for every drive letter (QueryDosDevice, read-only)."""
    k32, out = ctypes.WinDLL("kernel32"), {}
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        buf = ctypes.create_unicode_buffer(1024)
        if k32.QueryDosDeviceW(f"{letter}:", buf, 1024):
            out[buf.value] = f"{letter}:"
    return out


def to_drive(path, volmap):
    for dev, drive in volmap.items():
        if path.lower().startswith(dev.lower() + "\\"):
            return drive + path[len(dev):]
    return path


def trace_script(name, etl, csv_path, seconds):
    """The PowerShell script run elevated. It only records file-open events for SECONDS and converts them to CSV. It
    never touches another trace session (a session of the same name already running = BUSY, nothing started)."""
    logman, tracerpt = SYSTEM32 / "logman.exe", SYSTEM32 / "tracerpt.exe"
    return "\r\n".join([
        "$ErrorActionPreference = 'Continue'",
        f"& '{logman}' query '{name}' -ets *> $null",
        "if ($LASTEXITCODE -eq 0) { Write-Host 'WINTRACE: BUSY a session of this name is running; nothing started';"
        " return }",
        f"& '{logman}' start '{name}' -p '{ETW_PROVIDER}' {ETW_KEYWORD} 0x4 -o '{etl}' -ets -max {ETW_MAX_MB} "
        "-bs 1024 -nb 64 256",
        "if ($LASTEXITCODE -ne 0) { Write-Host \"WINTRACE: FAILED start $LASTEXITCODE\"; return }",
        "Write-Host 'WINTRACE: STARTED'",
        f"try {{ Start-Sleep -Seconds {int(seconds)} }} finally {{ & '{logman}' stop '{name}' -ets | Out-Null;"
        " Write-Host \"WINTRACE: STOPPED $LASTEXITCODE\" }",
        # CSV, never XML: XML of a large trace once grew to 43 GB.
        f"& '{tracerpt}' '{etl}' -o '{csv_path}' -of CSV -y | Out-Null",
        "Write-Host \"WINTRACE: CONVERTED $LASTEXITCODE\"",
    ]) + "\r\n"


def parse_opens(lines, volmap):
    """tracerpt CSV of Kernel-File Create events -> [(pid, path)]. The header names the columns; only the provider's
    event 12 (Create) rows count: the trace's own EventTrace header rows carry the .etl path and a PID too (measured
    2026-10-22). The file name is the event's last path field (\\Device\\...), mapped to a drive letter."""
    rows, col = [], None
    for row in csv.reader(lines, skipinitialspace=True):
        cells = [c.strip() for c in row]
        if col is None:
            if "PID" in cells and "Event Name" in cells and "Event ID" in cells:
                col = {k: cells.index(k) for k in ("Event Name", "Event ID", "PID")}
            continue
        if len(cells) <= max(col.values()):
            continue
        if cells[col["Event Name"]] != ETW_PROVIDER or cells[col["Event ID"]] != "12":
            continue
        pid_col = col["PID"]
        path = next((c for c in reversed(cells) if c.startswith("\\") or re.match(r"^[A-Za-z]:\\", c)), "")
        if not path:
            continue
        try:
            pid = int(cells[pid_col], 0)
        except ValueError:
            continue
        rows.append((pid, to_drive(path, volmap)))
    return rows


def summarize_opens(opens, names, pid=None, match=None):
    """-> {pid: {"name", "opens", "files": Counter}} after the --pid and --match filters."""
    rx = re.compile(match, re.I) if match else None
    out = {}
    for p, path in opens:
        if pid is not None and p != pid:
            continue
        if rx and not rx.search(path):
            continue
        e = out.setdefault(p, {"name": names.get(p, "?"), "opens": 0, "files": Counter()})
        e["opens"] += 1
        e["files"][path] += 1
    return out


def opens_verdict(total, summary, seconds, match):
    if total == 0:
        return f"INCOMPLETE: the trace holds no file-open events in {seconds} s; it did not record", 3
    n = sum(e["opens"] for e in summary.values())
    files = len({f for e in summary.values() for f in e["files"]})
    top = sorted(summary.items(), key=lambda kv: -kv[1]["opens"])[:5]
    who = ", ".join(f"{e['name']} ({p}) {e['opens']}" for p, e in top)
    if match:
        return (f"MATCH: {n} open(s) of {files} file(s) matching /{match}/ by {len(summary)} process(es): {who}"
                if n else f"NO MATCH: none of {total} file opens in {seconds} s matched /{match}/"), 0
    return f"{n} file open(s) of {files} file(s) by {len(summary)} process(es) in {seconds} s; busiest: {who}", 0


def work_dir(name, root=None):
    """A new folder that INHERITS the temp folder's access list. Not tempfile.mkdtemp: on Python 3.12 its 0o700 sets
    a list whose only user entry is OWNER RIGHTS, so the files the elevated script creates (owner: Administrators)
    could not be read back by this user (measured 2026-10-22: the trace and its log were 'Access is denied')."""
    root = Path(root or tempfile.gettempdir())
    for i in range(100):
        p = root / f"{name}-{time.strftime('%H%M%S')}-{i}"
        try:
            p.mkdir()                             # default mode: Windows applies the inherited access list
            return p
        except FileExistsError:
            continue
    raise OSError(f"no free folder name under {root}")


def process_names():
    import psutil
    out = {}
    for p in psutil.process_iter(["pid", "name"]):
        out[p.info["pid"]] = p.info["name"] or "?"
    return out


def cmd_opensnoop(a):
    if not a.yes:
        emit({}, a.json, [f"This records every file open on this computer for {a.seconds} s (Windows' own ETW "
                          f"provider {ETW_PROVIDER}, at most {ETW_MAX_MB} MB, kept in a temporary folder and deleted "
                          "afterwards). It needs ONE Windows admin prompt (UAC). Nothing is sent anywhere."],
             "REFUSED: not started without --yes", f"wintrace opensnoop --seconds {a.seconds} --yes")
        return 2
    elevate = find_elevate()
    if not elevate:
        emit({}, a.json, [], "NOT READY: the elevate kit (local/kits/Elevate/elevate.py) is not on this machine",
             "wintrace doctor")
        return 1
    name = f"wintrace-opensnoop-{os.getpid()}"
    work = work_dir(name)
    etl, csv_path, ps1, log = work / "open.etl", work / "open.csv", work / "trace.ps1", work / "elevated.log"
    ps1.write_text(trace_script(name, etl, csv_path, a.seconds), encoding="utf-8-sig")
    names = process_names()
    print(f"A Windows admin prompt (UAC) is on screen now: click Yes to record file opens for {a.seconds} s. "
          f"The script it runs: {ps1}", flush=True)
    py = sys.executable
    r = subprocess.run([py, "-I", str(elevate), "run", str(ps1), "--log", str(log), "--close"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0 and subprocess.run(
        [py, "-I", str(elevate), "wait", str(log), "--marker", ELEVATE_MARKER, "--timeout",
         str(round((a.seconds + 300) / 60, 1)), "--tail", "5"], capture_output=True).returncode == 0
    try:
        status = log.read_bytes().decode("utf-8", "replace").replace("\x00", "") if log.is_file() else ""
    except OSError as e:
        status, ok = f"WINTRACE: log unreadable ({e})", False
    marks = re.findall(r"WINTRACE: [^\r\n]*", status)
    try:
        if not ok or not csv_path.is_file():
            emit({"log": marks}, a.json, marks, "FAILED: no trace (was Yes clicked?) " + (r.stdout + r.stderr).strip()[:300],
                 "wintrace doctor, then run again and click Yes on the admin prompt")
            return 1
        names.update(process_names())
        with open(csv_path, encoding="utf-8-sig", errors="replace", newline="") as f:
            opens = parse_opens(f, volume_map())
        summary = summarize_opens(opens, names, a.pid, a.match)
        verdict, code = opens_verdict(len(opens), summary, a.seconds, a.match)
        lines = list(marks)
        for p, e in sorted(summary.items(), key=lambda kv: -kv[1]["opens"]):
            lines.append(f"pid {p} {e['name']}: {e['opens']} open(s) of {len(e['files'])} file(s)")
            lines += [f"    {c:>5}  {path}" for path, c in e["files"].most_common(a.top)]
        emit({"seconds": a.seconds, "total_opens": len(opens),
              "processes": {p: {"name": e["name"], "opens": e["opens"], "files": dict(e["files"].most_common(a.top))}
                            for p, e in summary.items()}}, a.json, lines, verdict,
             "wintrace modules PID / fuser PATH for a process or file you did not expect" if code == 0 else
             "wintrace doctor; the trace session started but recorded nothing")
        return code
    finally:
        if not a.keep:                            # only the files this run created, then the folder if empty
            for p in (etl, csv_path, ps1, log):
                try:
                    p.unlink()
                except OSError:
                    pass
            try:
                work.rmdir()
            except OSError:
                pass


# ---- doctor --------------------------------------------------------------------------------------------------------
def cmd_doctor(a):
    checks = []

    def check(name, ok, why):
        checks.append({"check": name, "ok": bool(ok), "why": why})

    check("windows", WINDOWS, sys.platform)
    try:
        import psutil
        check("psutil", True, psutil.__version__)
    except ImportError:
        check("psutil", False, "pip install psutil")
    check("powershell", POWERSHELL.is_file(), str(POWERSHELL))
    for tool in ("logman.exe", "tracerpt.exe"):
        check(tool, (SYSTEM32 / tool).is_file(), str(SYSTEM32 / tool))
    try:
        ctypes.WinDLL("rstrtmgr.dll")
        check("restart manager", True, "rstrtmgr.dll")
    except (AttributeError, OSError) as e:
        check("restart manager", False, str(e))
    el = find_elevate()
    check("elevate kit (opensnoop only)", el, str(el or "not found: " + ", ".join(map(str, elevate_candidates()))))
    lines = [f"{'ok ' if c['ok'] else 'NO '} {c['check']:<30} {c['why']}" for c in checks]
    bad = [c["check"] for c in checks if not c["ok"]]
    emit({"version": VERSION, "checks": checks}, a.json, lines,
         "READY: every wintrace command can run here" if not bad else f"NOT READY: {', '.join(bad)}",
         "wintrace ss --listen, then wintrace whodraws" if not bad else "fix the NO lines")
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ss"); p.add_argument("--listen", action="store_true"); p.add_argument("--pid", type=int)
    p.set_defaults(fn=cmd_ss)
    p = sub.add_parser("fuser"); p.add_argument("paths", nargs="+"); p.set_defaults(fn=cmd_fuser)
    p = sub.add_parser("modules"); p.add_argument("pid", type=int); p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_modules)
    p = sub.add_parser("svc"); p.add_argument("pid", type=int); p.set_defaults(fn=cmd_svc)
    p = sub.add_parser("whodraws"); g = p.add_mutually_exclusive_group()
    g.add_argument("--over", metavar="PID|NAME"); g.add_argument("--at", nargs=2, type=int, metavar=("X", "Y"))
    p.set_defaults(fn=cmd_whodraws)
    p = sub.add_parser("opensnoop"); p.add_argument("--seconds", type=int, required=True)
    p.add_argument("--pid", type=int); p.add_argument("--match"); p.add_argument("--top", type=int, default=15)
    p.add_argument("--keep", action="store_true", help="keep the trace folder (it is printed)")
    p.add_argument("--yes", action="store_true"); p.set_defaults(fn=cmd_opensnoop)
    p = sub.add_parser("doctor"); p.set_defaults(fn=cmd_doctor)
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "opensnoop":
        if not 1 <= a.seconds <= MAX_SECONDS:
            ap.error(f"--seconds must be 1..{MAX_SECONDS}")
        if a.match:
            try:
                re.compile(a.match)
            except re.error as e:
                ap.error(f"--match: {e}")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if not WINDOWS and a.cmd != "doctor":
        print("not-this-platform: wintrace reads Windows APIs; on Linux use ss -tulnp, fuser, opensnoop-bpf",
              file=sys.stderr)
        return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
