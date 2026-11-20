"""Windows ownership recovery for one explicitly selected file or directory."""
import argparse
import csv
import re
import stat
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path, PureWindowsPath

VERB = r"Software\Classes\{}\shell\Fieldkit.TakeOwnership"
DEVICE = re.compile(r"(?i)(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?")


def target_path(value):
    path = PureWindowsPath(value)
    if not re.fullmatch(r"[A-Za-z]:", path.drive) or not path.is_absolute() or len(path.parts) < 2:
        raise ValueError("Select a file or directory on a local drive.")
    for part in path.parts[1:]:
        if (part == ".." or part.endswith((" ", ".")) or DEVICE.fullmatch(part)
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)):
            raise ValueError("Select a literal file or directory path.")
    return path


def _native():
    import ctypes as C
    from ctypes import wintypes as W

    def bind(dll, name, result, *params):
        api = getattr(C.WinDLL(dll, use_last_error=True), name)
        api.restype = result
        api.argtypes = params
        return api

    close = bind("kernel32", "CloseHandle", W.BOOL, W.HANDLE)
    local_free = bind("kernel32", "LocalFree", W.HLOCAL, W.HLOCAL)
    create = bind("kernel32", "CreateFileW", W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID,
                  W.DWORD, W.DWORD, W.HANDLE)
    attrs = bind("kernel32", "GetFileInformationByHandleEx", W.BOOL, W.HANDLE, C.c_int, W.LPVOID, W.DWORD)
    security = bind("advapi32", "GetSecurityInfo", W.DWORD, W.HANDLE, C.c_int, W.DWORD,
                    W.LPVOID, W.LPVOID, W.LPVOID, W.LPVOID, C.POINTER(W.LPVOID))
    sid_string = bind("advapi32", "ConvertSidToStringSidW", W.BOOL, W.LPVOID, C.POINTER(W.LPWSTR))
    as_string = bind("advapi32", "ConvertSecurityDescriptorToStringSecurityDescriptorW", W.BOOL,
                     W.LPVOID, W.DWORD, W.DWORD, C.POINTER(W.LPWSTR), W.LPVOID)
    system_dir = bind("kernel32", "GetSystemDirectoryW", W.UINT, W.LPWSTR, W.UINT)
    drive_type = bind("kernel32", "GetDriveTypeW", W.UINT, W.LPCWSTR)
    wait = bind("kernel32", "WaitForSingleObject", W.DWORD, W.HANDLE, W.DWORD)
    exit_code = bind("kernel32", "GetExitCodeProcess", W.BOOL, W.HANDLE, C.POINTER(W.DWORD))

    class Execute(C.Structure):
        _fields_ = [("cbSize", W.DWORD), ("fMask", W.ULONG), ("hwnd", W.HWND), ("lpVerb", W.LPCWSTR),
                    ("lpFile", W.LPCWSTR), ("lpParameters", W.LPCWSTR), ("lpDirectory", W.LPCWSTR),
                    ("nShow", C.c_int), ("hInstApp", W.HINSTANCE), ("lpIDList", W.LPVOID),
                    ("lpClass", W.LPCWSTR), ("hkeyClass", W.HKEY), ("dwHotKey", W.DWORD),
                    ("hIcon", W.HANDLE), ("hProcess", W.HANDLE)]

    execute = bind("shell32", "ShellExecuteExW", W.BOOL, C.POINTER(Execute))

    def lock(path, stack, *, selected=False):
        # Pin each directory before opening the next component. FILE_SHARE_READ excludes
        # renames and opens for file data writes. WRITE_OWNER and READ_CONTROL are metadata access.
        flags = 0x02000000 | 0x00200000  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        handle = create(str(path), 0x20000 if selected else 0, 1, None, 3, flags, None)
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        stack.callback(close, handle)
        info = (W.DWORD * 2)()
        if not attrs(handle, 9, C.byref(info), C.sizeof(info)):  # FileAttributeTagInfo
            raise C.WinError(C.get_last_error())
        if info[0] & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Refusing a reparse point: {path}")
        return handle

    def descriptor(handle):
        sd = W.LPVOID()
        sid = W.LPVOID()
        error = security(handle, 1, 5, C.byref(sid), None, None, None, C.byref(sd))  # OWNER | DACL
        if error:
            raise C.WinError(error)
        try:
            owner = W.LPWSTR()
            if not sid_string(sid, C.byref(owner)):
                raise C.WinError(C.get_last_error())
            try:
                acl = W.LPWSTR()
                if not as_string(sd, 1, 4, C.byref(acl), None):
                    raise C.WinError(C.get_last_error())
                try:
                    return owner.value, acl.value
                finally:
                    local_free(C.cast(acl, W.HLOCAL))
            finally:
                local_free(C.cast(owner, W.HLOCAL))
        finally:
            local_free(sd)

    def elevate(exe, path):
        request = Execute()
        request.cbSize = C.sizeof(request)
        request.fMask = 0x40 | 0x100 | 0x400  # NOCLOSEPROCESS | NOASYNC | FLAG_NO_UI
        request.lpVerb = "runas"
        request.lpFile = str(exe)
        request.lpParameters = subprocess.list2cmdline(["/f", str(path)])
        request.lpDirectory = str(exe.parent)
        request.nShow = 0
        if not execute(C.byref(request)):
            raise C.WinError(C.get_last_error())
        if not request.hProcess:
            raise OSError("Windows returned no takeown process handle.")
        try:
            code = W.DWORD()
            if wait(request.hProcess, 0xFFFFFFFF) != 0 or not exit_code(request.hProcess, C.byref(code)):
                raise C.WinError(C.get_last_error())
            return code.value
        finally:
            close(request.hProcess)

    folder = C.create_unicode_buffer(32768)
    if not 0 < system_dir(folder, len(folder)) < len(folder):
        raise C.WinError(C.get_last_error())
    return Path(folder.value), drive_type, lock, descriptor, elevate


def take(value):
    target = target_path(value)
    system, drive_type, lock, descriptor, elevate = _native()
    if drive_type(target.anchor) not in (2, 3, 6):
        raise ValueError("Select a file or directory on a local drive.")
    user = subprocess.run([str(system / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
                          capture_output=True, text=True, timeout=5, check=True)
    sid = next(csv.reader(user.stdout.splitlines()))[1]
    if not re.fullmatch(r"S-1-(?:\d+-)+\d+", sid):
        raise OSError("Could not verify the requesting user's SID.")
    path = Path(target)
    with ExitStack() as stack:
        for parent in reversed(path.parents):
            lock(parent, stack)
        handle = lock(path, stack, selected=True)
        if path.is_file() and path.lstat().st_nlink != 1:
            raise ValueError("Refusing a file with multiple hard links.")
        _, before_acl = descriptor(handle)
        if code := elevate(system / "takeown.exe", path):
            raise OSError(f"takeown exited {code}.")
        after_owner, after_acl = descriptor(handle)
        if after_acl != before_acl:
            raise OSError("Ownership action completed, but the discretionary ACL changed.")
        if after_owner != sid:
            raise OSError("Ownership action completed, but the owner did not match the requesting user's SID.")
    return f"Ownership verified for {path}. Discretionary ACL unchanged."


def install_explorer():
    import winreg

    exe = str(Path(sys.executable).absolute())
    if any(c in exe for c in '"%\r\n'):
        raise ValueError("The Python executable path cannot be used in this Explorer verb.")
    command = f'"{exe}" -I -m fieldkit ownership take --explorer "%1"'
    for item_type in ("*", "Directory"):
        key_name = VERB.format(item_type)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_name) as key:
            for name, value in (("", "Take Ownership"), ("HasLUAShield", ""), ("MultiSelectModel", "Single"),
                                ("NeverDefault", ""), ("NoWorkingDirectory", "")):
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_name + r"\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
            if winreg.QueryValueEx(key, "")[0] != command:
                raise OSError("Explorer registration could not be verified.")
    return "Registered Fieldkit Take Ownership for individual files and folders."


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fieldkit ownership")
    sub = parser.add_subparsers(dest="action", required=True)
    single = sub.add_parser("take", help="request UAC and verify the owner and discretionary ACL")
    single.add_argument("path")
    single.add_argument("--explorer", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("install-explorer", help="register the current user's Take Ownership Explorer action")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("Take Ownership is available on Windows.")
        return 2
    code = 0
    try:
        message = take(args.path) if args.action == "take" else install_explorer()
    except (OSError, ValueError, subprocess.SubprocessError, IndexError, StopIteration) as exc:
        cancelled = getattr(exc, "winerror", None) == 1223
        message, code = ("Take Ownership cancelled.", 2) if cancelled else (str(exc), 3)
    if getattr(args, "explorer", False):
        from ctypes import windll

        windll.user32.MessageBoxW(None, message, "Fieldkit Take Ownership", 0x10 if code == 3 else 0x40)
    else:
        print(message, file=sys.stderr if code else sys.stdout)
    return code
