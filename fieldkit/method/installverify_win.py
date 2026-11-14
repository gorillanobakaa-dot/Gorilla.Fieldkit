"""fieldkit install verify, Windows half for any app `fieldkit install app` (or the vendor's own installer) put in place.

The gorilla-opencode verifier (installverify_os.verify_windows) knows one exe and one .ico. This one checks any
product by its APP_META entry (fieldkit/method/appinstall.py), with no release pin needed, so `fieldkit install verify
antigravity` works on Windows the way it already works on Linux (owner, 2026-11-14: "has to work from both sides").

Checks (each a row: check, ok, line, fix):
  binary.exe          the program exists in the per-user or machine install folder
  shortcut.desktop    the Desktop .lnk exists, launches that exe, and its icon file exists
  shortcut.startmenu  the same for the Start-menu .lnk
  shortcut.stale      no other shortcut for the product points at a program that no longer exists
  registry.uninstall  every Uninstall entry for the product points at files that exist (a stale entry blocks a reinstall)
  icon.source         the app's own icon is found (icon.png inside resources/app.asar, or the exe's own)
  icon.ico            when fieldkit wrote <app>.ico, it holds exactly the frames built from that real icon
Deterministic: file existence, sha256 and registry values; no network, no model. Every input comes from an
environment dict, so the tests run the same checks on a Linux CI runner with a fake Windows tree.
"""
import hashlib
import ntpath
import os
import re
import sys
from pathlib import Path


def _row(check, ok, line, fix="", info=False):
    r = {"check": check, "ok": bool(ok), "line": line, "fix": "" if ok else fix}
    if info:
        r["info"] = True
    return r


def _sha(p):
    h = hashlib.sha256()
    with open(str(p), "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _same(a, b):
    try:
        return os.path.samefile(str(a), str(b))
    except OSError:
        return ntpath.normcase(str(a)) == ntpath.normcase(str(b))


def _exists(p):
    return bool(p) and os.path.isfile(str(p))


def _path_in(value):
    """The file a registry value names: `"C:\\x\\y.exe" /S`, `C:\\x\\y.exe,0` or a bare path -> C:\\x\\y.exe."""
    v = (value or "").strip()
    if not v:
        return ""
    if v.startswith('"'):
        end = v.find('"', 1)
        return v[1:end] if end > 0 else v[1:]
    m = re.match(r"(?i)^(.*?\.(exe|ico|dll|cmd|bat))\b", v)
    if m:
        return m.group(1)
    return v.rsplit(",", 1)[0]


def read_uninstall(display_name):
    """-> [{key, name, version, location, icon, uninstall}] from HKCU and HKLM (both views) for one product."""
    try:
        import winreg
    except ImportError:
        return []
    out = []
    roots = ((winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", "HKCU"),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM32"))
    want = display_name.lower()
    for hive, sub, label in roots:
        try:
            root = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, name) as k:
                        def val(n):
                            try:
                                return str(winreg.QueryValueEx(k, n)[0])
                            except OSError:
                                return ""
                        dn = val("DisplayName")
                        if not dn.lower().startswith(want):
                            continue
                        out.append({"key": "%s\\%s\\%s" % (label, sub, name), "name": dn,
                                    "version": val("DisplayVersion"), "location": val("InstallLocation"),
                                    "icon": val("DisplayIcon"), "uninstall": val("UninstallString")})
                except OSError:
                    continue
    return out


def environment(product, env=None):
    """Where to look on THIS computer (or, with `env`, in a fake tree for the tests)."""
    from . import appinstall
    from . import installverify_os as osx
    meta = appinstall.app_meta(product)
    live = env is None
    e = dict(os.environ if live else env)
    local = Path(e.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    roam = Path(e.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    prof = Path(e.get("USERPROFILE") or Path.home())
    desk = ([Path(e["OneDrive"]) / "Desktop"] if e.get("OneDrive") else []) + [prof / "Desktop"]
    sm = Path("Microsoft") / "Windows" / "Start Menu" / "Programs"
    pd = Path(e.get("ProgramData") or "C:/ProgramData")
    pub = Path(e.get("PUBLIC") or Path(e.get("SystemDrive", "C:") + "/") / "Users" / "Public")
    names = list(dict.fromkeys([meta["win_dir"], meta["display_name"], meta["id"]]))
    install_dirs = [local / "Programs" / n for n in names]
    for pf in ("ProgramFiles", "ProgramFiles(x86)"):
        if e.get(pf):
            install_dirs += [Path(e[pf]) / n for n in names]
    return {
        "meta": meta,
        "install_dirs": install_dirs,
        "own_lnk": {"desktop": [d / (meta["display_name"] + ".lnk") for d in desk],
                    "startmenu": [roam / sm / (meta["display_name"] + ".lnk"),
                                  roam / sm / meta["display_name"] / (meta["display_name"] + ".lnk")]},
        "lnk_dirs": desk + [roam / sm, pub / "Desktop", pd / sm],
        "read_lnk": e.get("__read_lnk") or osx.read_lnk,
        "uninstall": e["__uninstall"] if "__uninstall" in e else (read_uninstall(meta["display_name"]) if live else []),
    }


def _exe_in(meta, d):
    for n in (meta["win_exe"], meta["display_name"] + ".exe", meta["id"] + ".exe"):
        if (d / n).is_file():
            return d / n
    return None


def verify(product, e=None):
    """-> rows for one product installed on Windows."""
    from . import appinstall
    e = e or environment(product)
    meta = e["meta"]
    rows = []
    found = [(d, _exe_in(meta, d)) for d in e["install_dirs"] if d.is_dir()]
    found = [(d, x) for d, x in found if x]
    seen = []
    for d, x in found:
        if not any(_same(x, s) for s in seen):
            seen.append(x)
    exe = seen[0] if seen else None
    folder = exe.parent if exe else None
    rows.append(_row("binary.exe", exe is not None,
                     ("%s sha256 %s" % (exe, _sha(exe))) if exe else
                     "no %s in %s" % (meta["win_exe"], ", ".join(str(d) for d in e["install_dirs"])),
                     "fieldkit install app SRC --name %s --apply" % meta["id"]))
    if len(seen) > 1:
        rows.append(_row("binary.second-copy", False, "more than one installed copy: " + ", ".join(map(str, seen)),
                         "fieldkit install purge %s --apply, then install once" % meta["id"]))

    def lnk_row(kind):
        cands = [p for p in e["own_lnk"][kind] if p.is_file()]
        if not cands:
            return _row("shortcut." + kind, False, "missing: " + " or ".join(map(str, e["own_lnk"][kind])),
                        "fieldkit install app SRC --name %s --replace --apply" % meta["id"]), None
        p = cands[0]
        info = e["read_lnk"](p) or {}
        tgt = info.get("target") or ""
        icon = _path_in(info.get("icon") or "") or tgt          # an empty IconLocation uses the target's own icon
        ok = bool(exe) and _exists(tgt) and _same(tgt, exe) and _exists(icon)
        return _row("shortcut." + kind, ok, "%s runs %s, icon %s" % (p, tgt or "(nothing)", icon or "(none)"),
                    "fieldkit install app SRC --name %s --replace --apply (rewrites both shortcuts)" % meta["id"]), p

    own = []
    for kind in ("desktop", "startmenu"):
        r, p = lnk_row(kind)
        rows.append(r)
        if p:
            own.append(p)
    stale = []
    key = meta["display_name"].lower()
    for d in e["lnk_dirs"]:
        if not Path(d).is_dir():
            continue
        for p in sorted(Path(d).glob("*.lnk")):
            if key not in p.name.lower() or any(_same(p, o) for o in own):
                continue
            info = e["read_lnk"](p) or {}
            if not _exists(info.get("target")):
                stale.append("%s -> %s" % (p, info.get("target") or "(nothing)"))
    rows.append(_row("shortcut.stale", not stale, "no shortcut points at a missing program" if not stale
                     else "dead shortcuts: " + "; ".join(stale),
                     "fieldkit size recycle each dead .lnk (or fieldkit install purge %s --apply)" % meta["id"]))
    bad = []
    for u in e["uninstall"]:
        for field in ("icon", "uninstall"):
            f = _path_in(u.get(field))
            if f and not _exists(f):
                bad.append("%s %s -> %s (missing)" % (u["key"], field, f))
        loc = (u.get("location") or "").strip().strip('"')
        if loc and not os.path.isdir(loc):
            bad.append("%s InstallLocation -> %s (missing)" % (u["key"], loc))
    regline = ("%d Uninstall entr%s, all point at existing files: %s" % (
        len(e["uninstall"]), "y" if len(e["uninstall"]) == 1 else "ies",
        ", ".join("%s %s" % (u["name"], u.get("version") or "") for u in e["uninstall"]))
        if e["uninstall"] else "no Uninstall entry (a portable or fieldkit copy needs none)")
    rows.append(_row("registry.uninstall", not bad, regline if not bad else "stale: " + "; ".join(bad),
                     "fieldkit install purge %s --apply (exports, then removes the stale keys)" % meta["id"]))
    src_png = appinstall.extract_source_icon_png(folder) if folder else None
    rows.append(_row("icon.source", True,
                     ("the app's own icon: resources/app.asar icon.png sha256 %s (%d bytes)" % (
                         hashlib.sha256(src_png).hexdigest()[:12], len(src_png))) if src_png else
                     "no icon.png in resources/app.asar: the shortcuts use the icon embedded in the exe", info=True))
    ico = folder / (meta["id"] + ".ico") if folder else None
    if ico is not None and ico.is_file():
        want = hashlib.sha256(appinstall.make_ico(appinstall.WIN_ICO_SIZES, src_png=src_png)).hexdigest()
        got = _sha(ico)
        rows.append(_row("icon.ico", got == want, "%s sha256 %s %s the one built from the real icon %s" % (
            ico, got[:12], "==" if got == want else "!=", want[:12]),
            "fieldkit install app SRC --name %s --replace --apply (rewrites the .ico)" % meta["id"]))
    return rows


if __name__ == "__main__":
    for r in verify(sys.argv[1] if len(sys.argv) > 1 else "antigravity"):
        print(("INFO" if r.get("info") else "OK  " if r["ok"] else "FAIL"), r["check"] + ":", r["line"])
