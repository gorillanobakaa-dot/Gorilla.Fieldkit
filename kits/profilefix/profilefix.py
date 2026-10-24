"""profilefix - repair a renamed Windows profile, and purge a program completely (the Windows "apt-get purge").

Built 2026-10-24 from a real fault: a Windows account and its folder were renamed (C:\\Users\\<OLD> -> C:\\Users\\<NEW>)
by changing only ProfileImagePath and the folder. Thousands of registry values kept the old path, among them the
legacy "Shell Folders" cache. 32-bit NSIS installers (electron-builder: Antigravity, and many others) resolve
$LOCALAPPDATA from that cache, so every update tried to write into C:\\Users\\<OLD> and failed with
"Error opening file for writing". Reinstalling the program does not help; the cache has to be repaired.

  profilefix.py doctor                     what this machine is: Windows? admin? profiles, current user
  profilefix.py scan [--old NAME]          read-only: profile folder names the registry still points at that no
                                           longer exist, and every value/key that names one. Exit 3 = stale found
  profilefix.py fix [--old NAME] [--apply] rewrite \\Users\\OLD -> \\Users\\NEW in value data and value names;
                                           delete keys whose NAME holds the old path (caches). Dry run without
                                           --apply. HKLM needs an elevated shell (HKCU is fixed without)
  profilefix.py undo MANIFEST.json         put back exactly what a fix or purge-app run changed
  profilefix.py purge-app NAME [--apply]   uninstall a program and remove what it leaves: install folder, AppData
                                           folders, updater cache, shortcuts, its registry keys and caches.
                                           Files go to the Recycle Bin (checked); keys are exported first

Nothing names a person or a machine: the profiles folder, the current user and the stale names are read from the
machine it runs on. Deterministic: same registry in, same plan out (sorted). Python 3.8+ standard library only;
copy this one file anywhere and run it. Windows only (the registry); elsewhere it says not-this-platform, exit 2.
Exit codes: 0 ok, 1 failure, 2 usage / not this platform, 3 scan found stale entries.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import winreg
else:  # pragma: no cover - the module still imports so the pure functions can be tested anywhere
    winreg = None

VERSION = "1.0.0"
NOT_PROFILES = {"public", "default", "default user", "all users", "defaultapppool"}
# key paths never walked: links that would walk the same data twice, and hives nobody can read
SKIP_KEYS = ("HKLM\\SYSTEM\\CurrentControlSet", "HKLM\\SAM", "HKLM\\SECURITY", "HKLM\\HARDWARE",
             "HKLM\\BCD00000000", "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList")
# a profile folder name inside any path form: C:\Users\<x>, \Device\HarddiskVolume3\Users\<x>, file:///C:/Users/<x>,  privacy-scan: allow
# c%3A/Users/<x>, C:\[guid]\Users\<x>. Ends at a separator, a quote, a list separator or the end.
NAME_RE = r"[^\\/\"'\x00,;|<>*?:]+?"
END_RE = r"(?=[\\/\"'\x00,;|]|$)"


# -- pure functions (tested without a registry) ------------------------------------------------------------------
def users_segment(profiles_dir):
    """'C:\\Users' -> 'Users': the folder name that precedes a profile name in every path form."""
    return PureWindowsPath(profiles_dir).name or "Users"


def name_pattern(seg, name):
    return re.compile(r"(?i)(?<=[\\/])(" + re.escape(seg) + r"[\\/])" + re.escape(name) + END_RE)


def profile_names_in(text, seg):
    """Every profile folder name a string mentions, in order."""
    return [m.group(1) for m in re.finditer(r"(?i)(?<=[\\/])" + re.escape(seg) + r"[\\/](" + NAME_RE + ")" + END_RE,
                                            text)]


def rewrite(text, seg, old, new):
    """Replace the profile folder name OLD by NEW wherever it follows <seg>\\ ; keeps the separator and case of seg."""
    return name_pattern(seg, old).sub(lambda m: m.group(1) + new, text)


def rewrite_data(kind, data, seg, old, new):
    """(changed, new_data) for a registry value; only string types are touched."""
    if kind in ("REG_SZ", "REG_EXPAND_SZ") and isinstance(data, str):
        out = rewrite(data, seg, old, new)
        return out != data, out
    if kind == "REG_MULTI_SZ" and isinstance(data, list):
        out = [rewrite(s, seg, old, new) for s in data]
        return out != data, out
    return False, data


def stale_candidates(counts, existing, current):
    """Names seen in the registry that are not a live profile folder, most-mentioned first, then by name."""
    live = {e.lower() for e in existing} | {current.lower()} | NOT_PROFILES
    rows = [(n, c) for n, c in counts.items() if n.lower() not in live and not re.search(r"[%$]", n)]
    return sorted(rows, key=lambda r: (-r[1], r[0].lower()))


def choose_old(candidates, shell_folder_names, explicit):
    """The old name to repair: --old if given; else the one the Shell Folders cache still uses; else refuse."""
    if explicit:
        return explicit, "given with --old"
    stale = {n.lower() for n, _ in candidates}
    hits = sorted({n for n in shell_folder_names if n.lower() in stale}, key=str.lower)
    if len(hits) == 1:
        return hits[0], "the Shell Folders cache still points at it"
    if not candidates:
        return None, "no stale profile folder name found"
    if len(candidates) == 1:
        return candidates[0][0], "the only profile folder name the registry mentions that does not exist"
    return None, "several candidates and none in the Shell Folders cache: name one with --old"


def uninstall_target(cmd):
    """The program path inside an UninstallString: '"C:\\a b\\u.exe" /x' -> 'C:\\a b\\u.exe'."""
    cmd = (cmd or "").strip()
    if cmd.startswith('"'):
        return cmd[1:].split('"', 1)[0]
    m = re.match(r"(?i)(.+?\.exe)\b", cmd)
    return m.group(1) if m else cmd.split(" ")[0]


def name_variants(display_name, install_dir):
    """Folder/shortcut names a program uses: 'Antigravity 2.19.1' + ...\\antigravity -> {'antigravity'}."""
    base = re.sub(r"\s+v?\d+(\.\d+)+.*$", "", display_name or "").strip()
    out = {base.lower()} if base else set()
    if install_dir:
        out.add(PureWindowsPath(install_dir).name.lower())  # Windows path on any system (CI runs on Linux)
    return sorted(o for o in out if o)


# -- registry access ---------------------------------------------------------------------------------------------
HIVES = {"HKCU": "HKEY_CURRENT_USER", "HKLM": "HKEY_LOCAL_MACHINE", "HKU": "HKEY_USERS"}
TYPES = {}
if IS_WINDOWS:
    TYPES = {winreg.REG_SZ: "REG_SZ", winreg.REG_EXPAND_SZ: "REG_EXPAND_SZ", winreg.REG_MULTI_SZ: "REG_MULTI_SZ",
             winreg.REG_DWORD: "REG_DWORD", winreg.REG_QWORD: "REG_QWORD", winreg.REG_BINARY: "REG_BINARY",
             winreg.REG_NONE: "REG_NONE"}
TYPE_IDS = {v: k for k, v in TYPES.items()}
TEXT_TYPES = ("REG_SZ", "REG_EXPAND_SZ", "REG_MULTI_SZ", "REG_DWORD", "REG_QWORD")


def type_id(kind):
    return TYPE_IDS[kind] if kind in TYPE_IDS else int(kind)


def from_json(kind, data):
    """Manifest data back to what winreg writes: bytes for binary-like types (stored as hex)."""
    if kind in TEXT_TYPES or data is None:
        return data
    return bytes.fromhex(data) if isinstance(data, str) else data


def hive_handle(path):
    hive, _, sub = path.partition("\\")
    return getattr(winreg, HIVES[hive]), sub


def open_key(path, write=False):
    h, sub = hive_handle(path)
    access = (winreg.KEY_ALL_ACCESS if write else winreg.KEY_READ) | winreg.KEY_WOW64_64KEY
    return winreg.OpenKey(h, sub, 0, access)


def enum_values(k):
    i = 0
    while True:
        try:
            name, data, t = winreg.EnumValue(k, i)
        except OSError:
            return
        yield name, data, TYPES.get(t, str(t))
        i += 1


def enum_keys(k):
    out, i = [], 0
    while True:
        try:
            out.append(winreg.EnumKey(k, i))
        except OSError:
            return sorted(out, key=str.lower)
        i += 1


def walk(root, stats):
    """(key_path, [(name, data, type)]) for every readable key under root, depth first, sorted (deterministic)."""
    stack = [root]
    while stack:
        path = stack.pop()
        if any(path.lower() == s.lower() for s in SKIP_KEYS):
            continue
        try:
            with open_key(path) as k:
                values = list(enum_values(k))
                subs = enum_keys(k)
        except OSError:
            stats["unreadable"] += 1
            continue
        stats["keys"] += 1
        yield path, values
        stack.extend(path + "\\" + s for s in reversed(subs))


def get_value(path, name):
    try:
        with open_key(path) as k:
            data, t = winreg.QueryValueEx(k, name)
            return data, TYPES.get(t, str(t))
    except OSError:
        return None, None


def set_value(path, name, kind, data):
    h, sub = hive_handle(path)
    with winreg.CreateKeyEx(h, sub, 0, winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY) as k:
        winreg.SetValueEx(k, name, 0, type_id(kind), data)


def delete_value(path, name):
    with open_key(path, write=True) as k:
        winreg.DeleteValue(k, name)


def reg_exe(*args):
    exe = Path(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "reg.exe")
    return subprocess.run([str(exe), *args], capture_output=True, text=True, errors="replace")


def export_key(path, dest):
    r = reg_exe("export", path.replace(path.split("\\")[0], HIVES[path.split("\\")[0]], 1), str(dest), "/y")
    if r.returncode != 0:
        raise OSError(f"reg export failed for {path}: {r.stderr.strip() or r.stdout.strip()}")


def delete_key(path):
    r = reg_exe("delete", path.replace(path.split("\\")[0], HIVES[path.split("\\")[0]], 1), "/f")
    if r.returncode != 0:
        raise OSError(f"reg delete failed for {path}: {r.stderr.strip() or r.stdout.strip()}")


def jsonable(data):
    return data.hex() if isinstance(data, bytes) else data


# -- machine facts -----------------------------------------------------------------------------------------------
PROFILE_LIST = "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList"
SHELL_FOLDERS = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Shell Folders"


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def current_sid():
    r = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, errors="replace")
    return r.stdout.strip().split(",")[-1].strip('"') if r.returncode == 0 else ""


def machine():
    pdir, _ = get_value(PROFILE_LIST, "ProfilesDirectory")
    pdir = os.path.expandvars(pdir or r"%SystemDrive%\Users")
    sid = current_sid()
    profiles = {}
    with open_key(PROFILE_LIST) as k:
        for s in enum_keys(k):
            p, _ = get_value(PROFILE_LIST + "\\" + s, "ProfileImagePath")
            if p:
                profiles[s] = os.path.expandvars(p)
    cur = profiles.get(sid) or os.environ.get("USERPROFILE", "")
    existing = sorted(p.name for p in Path(pdir).iterdir() if p.is_dir()) if Path(pdir).is_dir() else []
    return {"profiles_dir": pdir, "segment": users_segment(pdir), "sid": sid, "current": Path(cur).name,
            "current_path": cur, "profile_list": profiles, "existing": existing, "admin": is_admin()}


def shell_folder_names(seg):
    try:
        with open_key(SHELL_FOLDERS) as k:
            vals = list(enum_values(k))
    except OSError:
        return []
    return [n for _, d, t in vals if isinstance(d, str) for n in profile_names_in(d, seg)]


# -- scan / fix ----------------------------------------------------------------------------------------------------
def roots_for(hives):
    return [h for h in ("HKCU", "HKLM") if h in hives]


def count_names(roots, seg, stats):
    counts = {}
    for path, values in (kv for r in roots for kv in walk(r, stats)):
        texts = [path.split("\\")[-1]]
        for name, data, kind in values:
            texts.append(name)
            if isinstance(data, str):
                texts.append(data)
            elif isinstance(data, list):
                texts.extend(x for x in data if isinstance(x, str))
        for t in texts:
            for n in profile_names_in(t, seg):
                counts[n] = counts.get(n, 0) + 1
    return counts


def plan_fix(roots, seg, old, new, stats):
    """Ordered operations: value rewrites/renames first, key deletions deepest first."""
    pat = name_pattern(seg, old)
    ops, keys = [], []
    for path, values in (kv for r in roots for kv in walk(r, stats)):
        # a key named by the path (TileProperties\W~C:\Users\<OLD>): the name ends in this key's own segment
        if any(m.end() > path.rfind("\\") for m in pat.finditer(path)):
            keys.append(path)
            continue
        if any(path.lower().startswith(k.lower() + "\\") for k in keys):
            continue
        for name, data, kind in values:
            changed, new_data = rewrite_data(kind, data, seg, old, new)
            if pat.search(name or ""):
                ops.append({"op": "rename-value", "key": path, "name": name, "new_name": rewrite(name, seg, old, new),
                            "type": kind, "data": jsonable(data), "new_data": jsonable(new_data)})
            elif changed:
                ops.append({"op": "set-value", "key": path, "name": name, "type": kind,
                            "data": data, "new_data": new_data})
    ops += [{"op": "delete-key", "key": k} for k in sorted(keys, key=lambda k: (-k.count("\\"), k.lower()))]
    return ops


def describe(op):
    if op["op"] == "set-value":
        return f"set     {op['key']} [{op['name'] or '(Default)'}]\n          {op['data']!s:.160}\n       -> {op['new_data']!s:.160}"
    if op["op"] == "rename-value":
        return f"rename  {op['key']}\n          [{op['name']:.160}]\n       -> [{op['new_name']:.160}]"
    if op["op"] == "delete-key":
        return f"delkey  {op['key']}"
    if op["op"] == "delete-value":
        return f"delval  {op['key']} [{op['name']}]"
    if op["op"] == "recycle":
        return f"recycle {op['path']}"
    if op["op"] == "uninstall":
        return f"run     {op['command']}"
    if op["op"] == "stop":
        return f"stop    PID {op['pid']} {op['exe']}"
    return json.dumps(op)


def backup_root(arg):
    base = Path(arg) if arg else Path(os.environ.get("LOCALAPPDATA", Path.home()), "profilefix")
    d = base / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


class Manifest:
    """Written after every operation, so an interrupted run is still undoable."""

    def __init__(self, folder, command, info):
        self.path = folder / "manifest.json"
        self.doc = {"tool": "profilefix", "version": VERSION, "command": command, "info": info, "done": []}
        self.flush()

    def add(self, op):
        self.doc["done"].append(op)
        self.flush()

    def flush(self):
        self.path.write_text(json.dumps(self.doc, indent=1, ensure_ascii=False), encoding="utf-8")


def apply_op(op, folder, man):
    if op["op"] == "set-value":
        set_value(op["key"], op["name"], op["type"], op["new_data"])
    elif op["op"] == "rename-value":
        data = from_json(op["type"], op["new_data"])
        _, existing = get_value(op["key"], op["new_name"])
        if existing is None:  # a value already under the new name is newer: keep it
            set_value(op["key"], op["new_name"], op["type"], data)
            op["created"] = True
        delete_value(op["key"], op["name"])
    elif op["op"] in ("delete-key",):
        dest = folder / f"key-{len(man.doc['done']):05d}.reg"
        export_key(op["key"], dest)
        op["export"] = str(dest)
        delete_key(op["key"])
    elif op["op"] == "delete-value":
        delete_value(op["key"], op["name"])
    man.add(op)


def run_ops(ops, folder, man, admin):
    done = failed = skipped = 0
    for op in ops:
        if op.get("key", "").startswith("HKLM") and not admin:
            skipped += 1
            continue
        try:
            apply_op(op, folder, man)
            done += 1
        except OSError as e:
            failed += 1
            print(f"FAILED  {describe(op).splitlines()[0]}: {e}")
    return done, failed, skipped


def cmd_scan(a, fix=False):
    m = machine()
    seg = m["segment"]
    roots = a.key or roots_for(a.hive)
    stats = {"keys": 0, "unreadable": 0}
    counts = count_names(roots, seg, stats)
    cands = stale_candidates(counts, m["existing"] + [Path(p).name for p in m["profile_list"].values()], m["current"])
    old, why = choose_old(cands, shell_folder_names(seg), a.old)
    new = a.new or m["current"]
    print(f"profiles folder {m['profiles_dir']}   current user's folder {m['current']}   admin {m['admin']}")
    print(f"walked {stats['keys']} keys in {', '.join(roots)} ({stats['unreadable']} unreadable)")
    if cands:
        print("profile folder names the registry mentions that do not exist on this machine:")
        for n, c in cands:
            print(f"  {n:<24} {c} mention(s)")
    print(f"old name: {old or '-'} ({why})")
    if not old:
        print("NEXT: nothing to repair" if not cands else "NEXT: rerun with --old NAME")
        return 0 if not cands else 3
    if old.lower() == new.lower():
        print("refused: old and new are the same folder name")
        return 2
    stats = {"keys": 0, "unreadable": 0}
    ops = plan_fix(roots, seg, old, new, stats)
    sf = [o for o in ops if o["key"].lower() == SHELL_FOLDERS.lower()]
    by = {}
    for o in ops:
        by[(o["key"][:4], o["op"])] = by.get((o["key"][:4], o["op"]), 0) + 1
    print(f"plan: {seg}\\{old} -> {seg}\\{new}: {len(ops)} change(s) " +
          ", ".join(f"{h} {k} {n}" for (h, k), n in sorted(by.items())))
    if sf:
        print(f"  includes the Shell Folders cache ({len(sf)} values): installers that read it write into the "
              f"old folder until it is fixed")
    if a.verbose or (fix and not a.apply):
        for o in ops[: a.limit or len(ops)]:
            print(describe(o))
        if a.limit and len(ops) > a.limit:
            print(f"... {len(ops) - a.limit} more (--limit 0 shows all)")
    if not fix:
        print("NEXT: profilefix fix" + (f" --old {a.old}" if a.old else "") + " --apply  (dry run first: without --apply)")
        return 3 if ops else 0
    if not a.apply:
        print("dry run: nothing changed. NEXT: add --apply")
        return 0
    hklm = sum(1 for o in ops if o["key"].startswith("HKLM"))
    folder = backup_root(a.backup_dir)
    man = Manifest(folder, "fix", {"old": old, "new": new, "segment": seg, "sid": m["sid"]})
    done, failed, skipped = run_ops(ops, folder, man, m["admin"])
    print(f"applied {done}, failed {failed}, skipped {skipped} (HKLM without admin)")
    print(f"undo: profilefix undo \"{man.path}\"")
    if skipped:
        print(f"NEXT: {hklm} HKLM change(s) need an elevated shell: rerun 'profilefix fix --old {old} --hive HKLM "
              f"--apply' as administrator")
    return 1 if failed else 0


def cmd_undo(a):
    doc = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    admin = is_admin()
    bad = 0
    for op in reversed(doc["done"]):
        try:
            if op.get("key", "").startswith("HKLM") and not admin:
                raise OSError("needs an elevated shell")
            if op["op"] == "set-value":
                set_value(op["key"], op["name"], op["type"], op["data"])
            elif op["op"] == "rename-value":
                set_value(op["key"], op["name"], op["type"], from_json(op["type"], op["data"]))
                if op.get("created"):
                    delete_value(op["key"], op["new_name"])
            elif op["op"] in ("delete-key",):
                r = reg_exe("import", op["export"])
                if r.returncode != 0:
                    raise OSError(r.stderr.strip())
            elif op["op"] == "delete-value":
                set_value(op["key"], op["name"], op["type"], from_json(op["type"], op["data"]))
            elif op["op"] == "recycle":
                print(f"files: restore from the Recycle Bin by hand: {op['path']}")
            else:
                print(f"not undoable here: {describe(op).splitlines()[0]}")
        except OSError as e:
            bad += 1
            print(f"FAILED  undo {describe(op).splitlines()[0]}: {e}")
    print(f"undone {len(doc['done']) - bad} of {len(doc['done'])}")
    return 1 if bad else 0


# -- purge-app -------------------------------------------------------------------------------------------------------
UNINSTALL_ROOTS = ("HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
                   "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
                   "HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall")
CACHE_KEYS = ("HKCU\\Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\Shell\\MuiCache",
              "HKCU\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AppCompatFlags\\Compatibility Assistant\\Store")


def find_installs(pattern):
    rx = re.compile(pattern, re.I)
    out = []
    for root in UNINSTALL_ROOTS:
        try:
            with open_key(root) as k:
                subs = enum_keys(k)
        except OSError:
            continue
        for s in subs:
            key = root + "\\" + s
            try:
                with open_key(key) as k:
                    v = {n: d for n, d, _ in enum_values(k)}
            except OSError:
                continue
            if rx.search(str(v.get("DisplayName", ""))):
                loc = v.get("InstallLocation") or ""
                if not loc:
                    tgt = uninstall_target(v.get("UninstallString") or "") or \
                        str(v.get("DisplayIcon", "")).split(",")[0].strip('"')
                    loc = str(Path(tgt).parent) if tgt else ""
                out.append({"key": key, "name": v.get("DisplayName"), "version": v.get("DisplayVersion"),
                            "dir": loc.rstrip("\\"), "quiet": v.get("QuietUninstallString") or ""})
    return out


def app_keys(install_dir):
    """Top-level Software\\<x> keys whose InstallLocation is this folder (electron-builder writes Software\\<GUID>)."""
    out = []
    for root in ("HKCU\\Software", "HKLM\\SOFTWARE", "HKLM\\SOFTWARE\\WOW6432Node"):
        try:
            with open_key(root) as k:
                subs = enum_keys(k)
        except OSError:
            continue
        for s in subs:
            loc, _ = get_value(root + "\\" + s, "InstallLocation")
            if isinstance(loc, str) and loc.rstrip("\\").lower() == install_dir.lower():
                out.append(root + "\\" + s)
    return out


def known_dirs():
    env = os.environ
    return {"APPDATA": env.get("APPDATA", ""), "LOCALAPPDATA": env.get("LOCALAPPDATA", ""),
            "PROGRAMS": str(Path(env.get("LOCALAPPDATA", ""), "Programs")),
            "STARTMENU": str(Path(env.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")),
            "COMMONSTARTMENU": str(Path(env.get("ProgramData", r"C:\ProgramData"), "Microsoft", "Windows",
                                        "Start Menu", "Programs")),
            "DESKTOP": str(Path(env.get("USERPROFILE", ""), "Desktop")),
            "PUBLICDESKTOP": str(Path(env.get("PUBLIC", r"C:\Users\Public"), "Desktop")),  # privacy-scan: allow
            "TASKBAR": str(Path(env.get("APPDATA", ""), "Microsoft", "Internet Explorer", "Quick Launch",
                                "User Pinned", "TaskBar"))}


def leftover_paths(variants, install_dir, dirs):
    out = set()
    if install_dir and Path(install_dir).is_dir():
        out.add(install_dir)
        parent = Path(install_dir).parent
        leaf = Path(install_dir).name.lower()
        for p in parent.iterdir():  # earlier copies the updater kept: <leaf>.<version>-backup, <leaf>.old ...
            if p.is_dir() and p.name.lower().startswith(leaf + ".") and p.name.lower() != leaf:
                out.add(str(p))
    for base in (dirs["APPDATA"], dirs["LOCALAPPDATA"]):
        for v in variants:
            for name in (v, v + "-updater"):
                p = Path(base, name)
                if base and p.exists():
                    out.add(str(p))
    for base in (dirs["STARTMENU"], dirs["COMMONSTARTMENU"], dirs["DESKTOP"], dirs["PUBLICDESKTOP"], dirs["TASKBAR"]):
        if not base or not Path(base).is_dir():
            continue
        for p in Path(base).iterdir():
            stem = p.stem.lower() if p.suffix.lower() == ".lnk" else p.name.lower()
            if any(stem == v or stem == "uninstall " + v for v in variants):
                out.add(str(p))
    return sorted(out, key=str.lower)


def processes_under(folder):
    ps = ("Get-CimInstance Win32_Process | Where-Object ExecutablePath | "
          "Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, errors="replace")
    try:
        rows = json.loads(r.stdout or "[]")
    except ValueError:
        return []
    rows = rows if isinstance(rows, list) else [rows]
    f = folder.lower().rstrip("\\") + "\\"
    return sorted(({"pid": x["ProcessId"], "exe": x["ExecutablePath"]} for x in rows
                   if str(x["ExecutablePath"]).lower().startswith(f)), key=lambda x: x["pid"])


def recycle(path):
    """Send to the Recycle Bin through the Windows shell, then check the path is really gone."""
    kind = "DeleteDirectory" if Path(path).is_dir() else "DeleteFile"
    lit = path.replace("'", "''")
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::"
          f"{kind}('{lit}','OnlyErrorDialogs','SendToRecycleBin')")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, errors="replace")
    if Path(path).exists():
        raise OSError(f"still there after recycling: {r.stderr.strip()[:300]}")


def cmd_purge(a):
    found = find_installs(a.name)
    if a.key:
        found = [f for f in found if f["key"].lower() == a.key.lower()]
    if not found:
        print(f"no installed program matches {a.name!r} (Uninstall keys in HKCU and HKLM, 64 and 32 bit)")
        return 1 if not a.dir else cmd_purge_dir(a)
    if len(found) > 1:
        print("several programs match; pick one with --key:")
        for f in found:
            print(f"  {f['key']}  {f['name']}  {f['dir']}")
        return 2
    app = found[0]
    variants = name_variants(app["name"], app["dir"])
    dirs = known_dirs()
    procs = processes_under(app["dir"]) if app["dir"] else []
    paths = leftover_paths(variants, app["dir"], dirs)
    keys = sorted({app["key"], *app_keys(app["dir"])}, key=str.lower) if app["dir"] else [app["key"]]
    cache = []
    for ck in CACHE_KEYS:
        try:
            with open_key(ck) as k:
                for n, d, t in enum_values(k):
                    if app["dir"] and app["dir"].lower() + "\\" in (n or "").lower():
                        cache.append({"op": "delete-value", "key": ck, "name": n, "type": t, "data": jsonable(d)})
        except OSError:
            pass
    ops = [{"op": "stop", **p} for p in procs]
    if app["quiet"] and not a.no_uninstaller:
        ops.append({"op": "uninstall", "command": app["quiet"]})
    ops += [{"op": "recycle", "path": p} for p in paths]
    ops += [{"op": "delete-key", "key": k} for k in keys] + cache
    ver = app["version"] if app["version"] and app["version"] not in str(app["name"]) else ""
    print(f"program: {app['name']} {ver}  folder {app['dir'] or '-'}  name(s) {', '.join(variants)}")
    for o in ops:
        print(describe(o))
    home = Path(os.environ.get("USERPROFILE", ""))
    kept = sorted(str(p) for v in variants for p in (home / ("." + v),) if p.exists())
    if kept:
        print("NOT touched (your data in your home folder; remove by hand if you mean it): " + ", ".join(kept))
    if not a.apply:
        print("dry run: nothing changed. NEXT: add --apply")
        return 0
    if any(o.get("key", "").startswith("HKLM") for o in ops) and not is_admin():
        print("refused: this program has machine-wide (HKLM) entries; run elevated")
        return 2
    folder = backup_root(a.backup_dir)
    man = Manifest(folder, "purge-app", {"program": app})
    bad = 0
    for o in ops:
        try:
            if o["op"] == "stop":
                subprocess.run(["taskkill", "/PID", str(o["pid"]), "/F"], capture_output=True)
                man.add(o)
            elif o["op"] == "uninstall":
                r = subprocess.run(o["command"], shell=True, capture_output=True, timeout=a.timeout)
                o["exit"] = r.returncode
                # NSIS uninstallers copy themselves to TEMP and return at once: wait for the real one to finish
                exe = uninstall_target(o["command"])
                t0 = dt.datetime.now()
                while (Path(exe).exists() and get_key_exists(app["key"])
                       and (dt.datetime.now() - t0).total_seconds() < a.timeout):
                    import time
                    time.sleep(2)
                man.add(o)
            elif o["op"] == "recycle":
                if Path(o["path"]).exists():
                    recycle(o["path"])
                man.add(o)
            elif o["op"] == "delete-key":
                if get_key_exists(o["key"]):
                    apply_op(o, folder, man)
            else:
                if get_value(o["key"], o["name"])[1] is not None:
                    apply_op(o, folder, man)
        except (OSError, subprocess.TimeoutExpired) as e:
            bad += 1
            print(f"FAILED  {describe(o).splitlines()[0]}: {e}")
    left = [p for p in paths if Path(p).exists()] + [k for k in keys if get_key_exists(k)]
    print(f"done; failed {bad}; still present {len(left)}" + "".join(f"\n  {x}" for x in left))
    print(f"manifest (registry undo): profilefix undo \"{man.path}\"; files are in the Recycle Bin")
    return 1 if bad or left else 0


def cmd_purge_dir(a):
    print("NEXT: the program is not registered; remove its folder by hand after checking it")
    return 1


def get_key_exists(path):
    try:
        with open_key(path):
            return True
    except OSError:
        return False


def folder_state(p):
    try:
        os.stat(p)
    except PermissionError:
        return "exists (no access)"
    except OSError:
        return "MISSING"
    return "exists"


def cmd_doctor(a):
    m = machine()
    print(f"profilefix {VERSION}  python {sys.version.split()[0]}  admin {m['admin']}")
    print(f"profiles folder {m['profiles_dir']}  current SID {m['sid']}  folder {m['current_path']}")
    for sid, p in sorted(m["profile_list"].items()):
        print(f"  {sid:<48} {p}  {folder_state(p)}")
    sf = sorted(set(shell_folder_names(m["segment"])), key=str.lower)
    bad = [n for n in sf if n.lower() != m["current"].lower()]
    print(f"Shell Folders cache names: {', '.join(sf) or '-'}" + (f"   STALE: {', '.join(bad)}" if bad else "   ok"))
    print("NEXT: profilefix scan" if bad else "NEXT: nothing stale in the Shell Folders cache")
    return 3 if bad else 0


def main(argv=None, prog="profilefix"):
    p = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    for name in ("scan", "fix"):
        s = sub.add_parser(name)
        s.add_argument("--old", help="the old profile folder name (default: found from the Shell Folders cache)")
        s.add_argument("--new", help="the folder name to point at (default: the current user's)")
        s.add_argument("--hive", nargs="+", default=["HKCU", "HKLM"], choices=["HKCU", "HKLM"])
        s.add_argument("--key", nargs="+", help="only these keys and below, e.g. HKCU\\Software\\Python (instead of --hive)")
        s.add_argument("--limit", type=int, default=40, help="changes to list (0 = all)")
        s.add_argument("--verbose", action="store_true", help="list the changes on scan too")
        if name == "fix":
            s.add_argument("--apply", action="store_true", help="make the changes (without: dry run)")
            s.add_argument("--backup-dir", help="where manifests and .reg exports go (default %%LOCALAPPDATA%%\\profilefix)")
        s.set_defaults(fn=(lambda a, f=(name == "fix"): cmd_scan(a, fix=f)))
    u = sub.add_parser("undo")
    u.add_argument("manifest")
    u.set_defaults(fn=cmd_undo)
    pa = sub.add_parser("purge-app")
    pa.add_argument("name", help="regular expression matched against the program's name in Apps")
    pa.add_argument("--key", help="the Uninstall key, when several programs match")
    pa.add_argument("--apply", action="store_true", help="do it (without: dry run)")
    pa.add_argument("--no-uninstaller", action="store_true", help="skip the program's own silent uninstaller")
    pa.add_argument("--timeout", type=int, default=300, help="seconds for the program's uninstaller (300)")
    pa.add_argument("--backup-dir")
    pa.add_argument("--dir", help=argparse.SUPPRESS)
    pa.set_defaults(fn=cmd_purge)
    a = p.parse_args(argv)
    if not IS_WINDOWS:
        print("not-this-platform: profilefix repairs the Windows registry; on Linux a renamed home is fixed with "
              "usermod -d -m and a grep of ~/.config")
        return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
