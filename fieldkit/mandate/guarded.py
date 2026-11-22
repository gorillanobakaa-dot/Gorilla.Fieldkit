# Version: 1.3.0 · updated 26-11-22-12-00
"""The mandate's trust store and stop list, where the agent's own OS user cannot write.

The Windows review of M1.1 (2026-10-23): the hard lines read COMMAND TEXT, and a model runs code as the same OS user,
so a write by indirection (a script in scratch, Path.home() / ".fieldkit" / "mandate", an environment variable built
from pieces) reached the trusted keys, the stop list and the log. A regex cannot close that; the OS can. What decides
trust lives here:

    trusted/<fp>.pub      the owner's public signing key(s)                     (fieldkit mandate trust)
    reviewers/<fp>.pub    the other computer's REVIEW key (it signs APPROVE)   (fieldkit mandate trust-reviewer)
    stopped.txt           sha256 of every mandate the owner stopped            (fieldkit mandate stop)

Linux: /etc/fieldkit/mandate-trust; macOS: /Library/Application Support/Fieldkit/mandate-trust (2026-10-26; an older
/etc/fieldkit/mandate-trust is still read when the new one is absent). Owned by root, written only through pkexec
(Linux) or sudo (macOS), so every write is the owner typing their password; every folder above it must be root's too. Windows: %ProgramData%\\Fieldkit\\mandate-trust with an
Administrators-only write ACL, written through the elevate kit (the Windows side's part, stubbed below).

A reader refuses a root this user can write (`guarded()`): if the protection is not there, nothing in it counts.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

HEXLINE = re.compile(r"[0-9a-f]{64}( [^\n]{0,200})?")
NAME = re.compile(r"(trusted|reviewers)/[0-9a-f]{4}(-[0-9a-f]{4}){3}\.pub|stopped\.txt")


LINUX_ROOT = "/etc/fieldkit/mandate-trust"
# macOS (2026-10-26, owner: "make sure that works on all the operating systems we are working with. OSX windows
# Linux"): the Mac's place for machine-wide data, created root-owned by sudo; /etc (= /private/etc) is read as a
# fallback only, for a Mac pinned before this existed
MAC_ROOT = "/Library/Application Support/Fieldkit/mandate-trust"


def root(platform=None, exists=os.path.exists):
    platform = platform or sys.platform
    if platform == "win32":
        return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Fieldkit" / "mandate-trust"
    if platform == "darwin":
        return Path(LINUX_ROOT) if not exists(MAC_ROOT) and exists(LINUX_ROOT) else Path(MAC_ROOT)
    return Path(LINUX_ROOT)


def _parents_safe(top, stat=os.stat, access=os.access):
    """-> (ok, why): every folder above the store is root's and not writable by this user. Otherwise this user could
    rename the store away and put a folder of their own in its place (the store's own mode would then prove nothing)."""
    for q in Path(top).parents:
        try:
            st = stat(q)
        except OSError as e:
            return False, f"{q} cannot be read ({e}): nothing in the store counts"
        if st.st_uid != 0 or access(q, os.W_OK):
            return False, f"{q} (above the store) can be changed without the owner's password (owner uid " \
                          f"{st.st_uid}): nothing in the store counts"
    return True, "parents guarded"


def guarded(path=None, stat=os.stat, access=os.access, exists=os.path.exists):
    """-> (ok, why): the root (or a file in it) exists and this OS user cannot change it, nor any folder above it.
    stat/access/exists are replaced in tests (the macOS paths are faked on the Windows and Ubuntu CI)."""
    p = Path(path) if path else root()
    if sys.platform == "win32":
        return _guarded_windows(p)
    if not exists(p):
        return False, f"{p} does not exist (the owner sets it up: fieldkit mandate trust)"
    for q in [p] + ([] if p == root() else [root()]):
        st = stat(q)
        if st.st_uid != 0 or access(q, os.W_OK) or st.st_mode & 0o022:
            return False, f"{q} can be changed without the owner's password (owner uid {st.st_uid}, " \
                          f"mode {oct(st.st_mode & 0o777)}): nothing in it counts"
    return _parents_safe(root(), stat, access)


def read(name):
    """The text of a guarded file, or "" when it is absent or not guarded."""
    p = root() / name
    if not p.is_file() or not guarded(p)[0]:
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def listing(folder):
    d = root() / folder
    if not d.is_dir() or not guarded(d)[0]:
        return []
    return sorted(q for q in d.glob("*.pub") if guarded(q)[0])


def write(name, line, append=False, run=subprocess.run):
    """Write one line to a guarded file, as the owner (a password prompt). `name` and `line` are validated: hex and a
    short note only, so nothing reaches the privileged shell but what is shown here."""
    if not NAME.fullmatch(name) or not HEXLINE.fullmatch(line) or "'" in line:
        raise ValueError(f"refused: {name!r} / {line!r} is not a trust-store line")
    if sys.platform == "win32":
        return _write_windows(name, line, append)
    target = root() / name
    script = (f"umask 022; mkdir -p {shlex.quote(str(target.parent))} && "
              f"printf '%s\\n' {shlex.quote(line)} {'>>' if append else '>'} {shlex.quote(str(target))}")
    lift = ["pkexec"] if sys.platform.startswith("linux") else ["sudo"]
    r = run(lift + ["/bin/sh", "-c", script])
    if getattr(r, "returncode", 1) != 0:
        return False, "not written (the password was not given, or the write failed)"
    # the artefact: what is there now, read back, is what was meant
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    ok = line in text.splitlines() and guarded(target)[0]
    return ok, (f"written: {target}" if ok else f"{target} does not hold the line, or is not guarded")


# ---- Windows: %ProgramData%\Fieldkit\mandate-trust, Administrators-only write (owner, 2026-10-23: "construieste
# partea windows a folderului protejat"; the Windows side's facts, link note #67) ------------------------------------
ADMINS, SYSTEM_ = ("BUILTIN\\Administrators", "S-1-5-32-544"), ("NT AUTHORITY\\SYSTEM", "S-1-5-18")
WIDE = ("BUILTIN\\Users", "S-1-5-32-545", "NT AUTHORITY\\Authenticated Users", "S-1-5-11", "Everyone", "S-1-1-0",
        "NT AUTHORITY\\INTERACTIVE", "S-1-5-4")
WRITE_RIGHTS = ("FullControl", "Modify", "Write", "CreateFiles", "CreateDirectories", "AppendData", "Delete",
                "ChangePermissions", "TakeOwnership")


def acl_verdict(acl, me=None):
    """acl: {"owner": str, "access": ["IDENTITY|RIGHTS|Allow or Deny", ...]} (Get-Acl, as the reader returns it).
    -> (ok, why). os.access(W_OK) on Windows sees only the read-only attribute, never the ACL (Windows, #67), so the
    ACL itself decides: owned by Administrators or SYSTEM, and no Allow entry giving write to this user, Users,
    Authenticated Users, Everyone or INTERACTIVE."""
    owner = str(acl.get("owner") or "")
    if not any(owner.lower() == x.lower() for x in ADMINS + SYSTEM_):
        return False, f"owned by {owner or 'nobody known'}, not Administrators or SYSTEM"
    who = [w.lower() for w in WIDE + ((me,) if me else ())]
    for entry in acl.get("access") or []:
        ident, _, rest = str(entry).partition("|")
        rights, _, kind = rest.partition("|")
        if kind.strip().lower() != "allow" or ident.strip().lower() not in who:
            continue
        if any(r.lower() in rights.lower() for r in WRITE_RIGHTS):
            return False, f"{ident.strip()} may write ({rights.strip()})"
    return True, "Administrators-only write"


def _windows_powershell_home():
    import ctypes

    directory = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetSystemDirectoryW(directory, len(directory))
    if not 0 < size < len(directory):
        raise OSError("could not locate the Windows system directory")
    return Path(directory.value) / "WindowsPowerShell" / "v1.0"


# FileSystemRights bit -> the .NET name Get-Acl prints (acl_verdict matches WRITE_RIGHTS by name). Generic bits are
# named so that they count as write too: Get-Acl printed GENERIC_ALL as a bare number, which no name matched.
_RIGHT_BITS = ((0x00000001, "ReadData"), (0x00000002, "CreateFiles"), (0x00000004, "AppendData"),
               (0x00000008, "ReadExtendedAttributes"), (0x00000010, "WriteExtendedAttributes"),
               (0x00000020, "ExecuteFile"), (0x00000040, "DeleteSubdirectoriesAndFiles"),
               (0x00000080, "ReadAttributes"), (0x00000100, "WriteAttributes"), (0x00010000, "Delete"),
               (0x00020000, "ReadPermissions"), (0x00040000, "ChangePermissions"), (0x00080000, "TakeOwnership"),
               (0x00100000, "Synchronize"), (0x02000000, "MaximumAllowed, FullControl"),
               (0x10000000, "GenericAll, FullControl"), (0x20000000, "GenericExecute"),
               (0x40000000, "GenericWrite"), (0x80000000, "GenericRead"))
_ACE_KIND = {0: "Allow", 1: "Deny", 9: "Allow", 10: "Deny"}   # ACCESS_{ALLOWED,DENIED}[_CALLBACK]_ACE_TYPE


def rights_names(mask):
    names = [n for bit, n in _RIGHT_BITS if mask & bit]
    if mask & 0x001F01FF == 0x001F01FF:
        names.insert(0, "FullControl")
    return ", ".join(names) or str(mask)


def _read_acl(p, run=None):
    """The owner and DACL of p, read in this process with the Win32 security API (advapi32), in the shape Get-Acl gave:
    {"owner": SID, "access": ["IDENTITY|RIGHTS|Allow or Deny", ...]}, each ACE once by account name and once by SID.
    2026-11-22: the PowerShell reader below started one powershell.exe + one conhost per path, 12 per prompt from the
    room hook in every session (3.5 s each prompt, hundreds of console hosts an hour). Fails closed: any API error, a
    NULL DACL (everyone may write) or an ACE type it does not know raises or reads as write for Everyone."""
    if run is not None:                                   # a test of the old reader's failure handling
        return _read_acl_powershell(p, run=run)
    import ctypes
    from ctypes import wintypes as W

    adv, k32 = ctypes.WinDLL("advapi32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = [W.LPCWSTR, ctypes.c_int, W.DWORD, ctypes.POINTER(ctypes.c_void_p),
                                          ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                          ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    adv.GetNamedSecurityInfoW.restype = W.DWORD
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(W.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = W.BOOL
    adv.LookupAccountSidW.argtypes = [W.LPCWSTR, ctypes.c_void_p, W.LPWSTR, ctypes.POINTER(W.DWORD), W.LPWSTR,
                                      ctypes.POINTER(W.DWORD), ctypes.POINTER(ctypes.c_int)]
    adv.LookupAccountSidW.restype = W.BOOL
    adv.GetAce.argtypes = [ctypes.c_void_p, W.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    adv.GetAce.restype = W.BOOL
    adv.IsValidSid.argtypes = [ctypes.c_void_p]
    adv.IsValidSid.restype = W.BOOL
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree.restype = ctypes.c_void_p

    def sid_text(sid):
        if not sid or not adv.IsValidSid(sid):
            raise OSError("invalid SID in the security descriptor")
        out = W.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(out)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return out.value
        finally:
            k32.LocalFree(out)

    def account(sid):
        name, dom = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        n, d, use = W.DWORD(256), W.DWORD(256), ctypes.c_int()
        if not adv.LookupAccountSidW(None, sid, name, ctypes.byref(n), dom, ctypes.byref(d), ctypes.byref(use)):
            return None                                   # an orphaned SID: the SID entry still judges it
        return f"{dom.value}\\{name.value}" if dom.value else name.value

    owner, dacl, sd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    # SE_FILE_OBJECT = 1; OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION = 1 | 4
    err = adv.GetNamedSecurityInfoW(str(p), 1, 1 | 4, ctypes.byref(owner), None, ctypes.byref(dacl), None,
                                    ctypes.byref(sd))
    if err:
        raise ctypes.WinError(err)
    try:
        acl = {"owner": sid_text(owner), "access": []}
        if not dacl.value:                                # NULL DACL: full access for everyone
            acl["access"].append("S-1-1-0|FullControl|Allow")
            return acl
        count = ctypes.c_uint16.from_address(dacl.value + 4).value      # ACL: rev, sbz1, size(2), AceCount(2)
        for i in range(count):
            ace = ctypes.c_void_p()
            if not adv.GetAce(dacl, i, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            kind = _ACE_KIND.get(ctypes.c_ubyte.from_address(ace.value).value)
            if kind is None:
                raise OSError(f"ACE type {ctypes.c_ubyte.from_address(ace.value).value} not understood")
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            sid = ctypes.c_void_p(ace.value + 8)
            rights = rights_names(mask)
            for ident in (account(sid), sid_text(sid)):
                if ident:
                    acl["access"].append(f"{ident}|{rights}|{kind}")
        return acl
    finally:
        k32.LocalFree(sd)


def _read_acl_powershell(p, run=subprocess.run):
    """The first reader (Get-Acl in Windows PowerShell 5). Kept for the parity test on the Windows CI only: it starts
    a powershell.exe and a console host per call, so nothing on a hook path may use it."""
    shell = _windows_powershell_home()
    ps = ("$ErrorActionPreference = 'Stop'; "
          "$a = Microsoft.PowerShell.Security\\Get-Acl -LiteralPath $env:FK_PATH; "
          "[pscustomobject]@{owner=$a.Owner; access=@($a.Access | "
          "ForEach-Object { $_.IdentityReference.Value + '|' + $_.FileSystemRights + '|' + $_.AccessControlType })} | "
          "ConvertTo-Json -Compress")
    # A Python process launched by PowerShell 7 can pass its module paths to
    # Windows PowerShell 5 unchanged. Only load the native system modules.
    r = run([str(shell / "powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, env=dict(os.environ, FK_PATH=str(p),
                                                    PSModulePath=str(shell / "Modules")), timeout=60)
    if r.returncode != 0 or r.stderr.strip():
        detail = next((line.strip() for line in r.stderr.splitlines() if line.strip()), "no ACL response")
        raise OSError(f"Windows PowerShell ACL probe failed (exit {r.returncode}): {detail}")
    acl = json.loads(r.stdout)
    if not isinstance(acl, dict) or not acl.get("owner") or not isinstance(acl.get("access"), list):
        raise ValueError("Windows PowerShell returned incomplete ACL data")
    return acl


def _guarded_windows(p, acl_reader=None):
    import os
    me = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
    for q in [p] + ([] if p == root() else [root()]):
        if not Path(q).exists():
            return False, f"{q} does not exist (the owner sets it up: fieldkit mandate trust, one UAC click)"
        try:
            acl = (acl_reader or _read_acl)(q)
        except Exception as e:  # noqa: BLE001 - unreadable: fail closed
            return False, f"the ACL of {q} could not be read ({e}): nothing in it counts"
        if not acl:
            return False, f"the ACL of {q} could not be read: nothing in it counts"
        ok, why = acl_verdict(acl, me)
        if not ok:
            return False, f"{q} can be changed without elevation: {why}: nothing in it counts"
    return True, "guarded"


def ps1_for(target_root, name, line, append):
    """The elevated script: creates the folder WITH its ACL before anything is written (nothing unelevated touches
    it, not even the first mkdir; Windows, #67), shows what it pins (the UAC prompt is the owner's act), writes one
    validated line. SIDs, not names, so a Windows in another language works."""
    return "\r\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$root = '{target_root}'",
        "if (-not (Test-Path -LiteralPath $root)) {",
        "  New-Item -ItemType Directory -Path $root -Force | Out-Null",
        "  icacls $root /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' "
        "'*S-1-5-32-545:(OI)(CI)RX' | Out-Null",
        "}",
        f"$t = Join-Path $root '{name.replace('/', chr(92))}'",
        "New-Item -ItemType Directory -Path (Split-Path $t) -Force | Out-Null",
        f"Write-Host 'Fieldkit pins into the protected store: {name} = {line[:16]}...'",
        ("Add-Content" if append else "Set-Content") + f" -LiteralPath $t -Value '{line}' -Encoding ascii",
        "Write-Host 'FIELDKIT-GUARDED-DONE'",
        ""])


def _elevate_kits():
    # Elevate was adopted into the owner's kits on 2026-10-19 and Documents\Scripts\Elevate recycled (as in
    # leakgate/sensors.py, link_setup.py): this checkout's local/, the main checkout's (a worktree has no local/), the
    # old place last.
    return (Path(__file__).resolve().parents[2] / "local" / "kits" / "Elevate" / "elevate.py",
            Path.home() / "Documents" / "Scripts" / "Fieldkit" / "local" / "kits" / "Elevate" / "elevate.py",
            Path.home() / "Documents" / "Scripts" / "Elevate" / "elevate.py")


def _elevate_kit(candidates=None):
    kits = [Path(k) for k in (candidates or _elevate_kits())]
    return next((k for k in kits if k.is_file()), kits[-1])


def _write_windows(name, line, append, run=subprocess.run):
    """Through the elevate kit: one UAC Yes from the owner, then read back and checked like Linux."""
    import tempfile
    kit = _elevate_kit()
    if not kit.is_file():
        return False, f"the elevate kit is not on this computer ({kit})"
    d = Path(tempfile.mkdtemp(prefix="fk-guarded-"))
    ps1, log = d / "pin.ps1", d / "pin.log"
    ps1.write_text(ps1_for(str(root()), name, line, append), encoding="utf-8")
    run([sys.executable, str(kit), "run", str(ps1), "--log", str(log)])
    w = run([sys.executable, str(kit), "wait", str(log), "--marker", "FIELDKIT-GUARDED-DONE", "--timeout", "15"])
    if getattr(w, "returncode", 1) != 0:
        return False, f"not written: the elevated step did not finish (UAC declined, or see {log})"
    target = root() / name
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    ok = line in text.splitlines() and guarded(target)[0]
    return ok, (f"written: {target}" if ok else f"{target} does not hold the line, or is not guarded")
