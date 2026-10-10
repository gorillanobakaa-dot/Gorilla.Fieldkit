#!/usr/bin/env python3
"""iconkit.py - make, inspect and apply crisp desktop icons, on any computer.

    fieldkit icons COMMAND ...          (or: python iconkit.py COMMAND ..., the same file run on its own)

Built 2026-09-28/29 from the work of recolouring the Debian-cap gorilla icons, so the next session does not
rediscover any of it; brought into Gorilla Fieldkit 2026-10-10 so it runs on any machine: nothing here names one
person's folders. Python is the one running this file; the Fieldkit folder is the one this file is in; backups and
the fix button live in <Fieldkit>/local/icons (git-ignored, per user, no administrator rights).

COMMANDS (W = Windows only; anything else answers not-this-platform, never pretends)
  frames PATH           the pictures inside an .ico (and .exe/.dll/.lnk on Windows, or anywhere with pefile):
                        sizes, format, sharpness; --out saves each as PNG
  check-ico ICO         crisp-icon gates: every frame the size it claims, no two frames identical, and (--master) each
                        frame a true Lanczos downsample (edge energy >= 75 %)
  build PROFILE         recoloured icons from a profile (source picture, crop, cap and subject outlines, colours);
                        every size rendered separately from a 1024 px master (example: example-profile.json)
  compare ICO...        side-by-side sheet at a given desktop size on the wallpaper
  cache                 soft icons although the files are sharp? The desktop's icon cache. Windows: Explorer's
                        iconcache_*.db, and which desktop icons hold a big enough frame; Linux: the GTK icon-theme
                        caches and KDE's icon cache. --apply rebuilds it (cache files moved to a backup, never deleted)
  W desktop-size        the size the desktop REALLY draws icons at (folder view), next to the stale registry value
  W shortcuts           every desktop shortcut (yours and all-users): target, icon, last changed
  W set-icon LNK... --ico ICO   point shortcuts at an icon, backing up first;  W restore BACKUP  puts them back
  W tih-check           is TextInputHost stuck (>= 80 % of a core)? --end ends it
  W install-fix-button  a "Fix blurry icons" desktop shortcut and desktop right-click entry (cache --apply);
                        uninstall-fix-button removes both

LESSONS BUILT IN
  - Blur is almost never the artwork. It is the system scaling a frame that is not the size it draws: render EVERY
    size separately from a big master with Lanczos, and include the size the desktop actually uses.
  - The Windows desktop can be set to any size (Ctrl + mouse wheel). The registry value
    HKCU\\...\\Shell\\Bags\\1\\Desktop IconSize is only written when Explorer saves state: ask the folder view.
  - Windows caches icons by file name: a new file name forces a redraw (build --unique-names).
  - Soft icons from sharp files are the cache: Explorer draws from iconcache_<size>.db, not from the files.
  - A computer-use screenshot can send TextInputHost.exe into a one-core loop; `tih-check --end` after screenshots.
  - Never touch a shortcut you were not asked to change; `shortcuts` shows last-changed dates.
  - Explorer is restarted through Windows' WMI process service, not as a child of this program: a shell started
    from an agent's session is closed with that session (2026-10-05/07: desktop and taskbar vanished overnight).

Requires: Python 3.10+, Pillow, numpy; pefile for reading .exe/.dll icons.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat, IcoImagePlugin

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                               # the Fieldkit folder (this file is fieldkit/icons/iconkit.py)
LOCAL = ROOT / "local" / "icons"                     # git-ignored: this machine's backups and launcher
MASTER = 1024
# Dense at the large end, because Windows 11 desktops are often set large and
# every missing size is a size Windows rescales itself.
DEFAULT_SIZES = (16, 20, 24, 32, 40, 48, 56, 64, 72, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224, 240, 256)
IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")


class NotThisPlatform(RuntimeError):
    pass


def windows_only(what):
    if not IS_WINDOWS:
        raise NotThisPlatform(f"{what}: Windows only (this is {sys.platform}); nothing was changed")


# --------------------------------------------------------------------------
#  small helpers
# --------------------------------------------------------------------------
def powershell(script: str) -> str:
    windows_only("PowerShell")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"powershell exit {r.returncode}")
    return r.stdout


def edge_energy(im: Image.Image) -> float:
    """Mean of the edge-detected luminance: the crisp-icon-kit sharpness proxy."""
    return ImageStat.Stat(im.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]


def hexrgb(h: str):
    import numpy as np
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)], np.float32)


# --------------------------------------------------------------------------
#  desktop-size (Windows)
# --------------------------------------------------------------------------
_FOLDERVIEW_CS = r'''
using System; using System.Runtime.InteropServices;
[ComImport, Guid("6d5140c1-7436-11ce-8034-00aa006009fa"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IKServiceProvider { [PreserveSig] int QueryService(ref Guid sid, ref Guid iid, [MarshalAs(UnmanagedType.IUnknown)] out object o); }
[ComImport, Guid("000214E2-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IKShellBrowser { void GetWindow(out IntPtr h); void ContextSensitiveHelp(int f); void InsertMenusSB(IntPtr a, IntPtr b); void SetMenuSB(IntPtr a, IntPtr b, IntPtr c); void RemoveMenusSB(IntPtr a); void SetStatusTextSB(IntPtr a); void EnableModelessSB(int f); void TranslateAcceleratorSB(IntPtr a, short b); void BrowseObject(IntPtr a, uint b); void GetViewStateStream(uint a, out IntPtr b); void GetControlWindow(uint a, out IntPtr b); void SendControlMsg(uint a, uint b, IntPtr c, IntPtr d, out IntPtr e); [PreserveSig] int QueryActiveShellView([MarshalAs(UnmanagedType.IUnknown)] out object v); }
[ComImport, Guid("1af3a467-214f-4298-908e-06b03e0b39f9"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IKFolderView2 { void GetCurrentViewMode(out uint m); void SetCurrentViewMode(uint m); void GetFolder(ref Guid r, out IntPtr p); void Item(int i, out IntPtr p); void ItemCount(uint f, out int c); void Items(uint f, ref Guid r, out IntPtr p); void GetSelectionMarkedItem(out int i); void GetFocusedItem(out int i); void GetItemPosition(IntPtr p, out long pt); void GetSpacing(out long pt); void GetDefaultSpacing(out long pt); void GetAutoArrange(); void SelectItem(int i, uint f); void SelectAndPositionItems(uint c, IntPtr a, IntPtr b, uint f); void SetGroupBy(IntPtr k, int a); void GetGroupBy(out IntPtr k, out int a); void SetViewProperty(IntPtr i, IntPtr k, IntPtr v); void GetViewProperty(IntPtr i, IntPtr k, IntPtr v); void SetTileViewProperties(IntPtr i, IntPtr p); void SetExtendedTileViewProperties(IntPtr i, IntPtr p); void SetText(int t, IntPtr s); void SetCurrentFolderFlags(uint m, uint f); void GetCurrentFolderFlags(out uint f); void GetSortColumnCount(out int c); void SetSortColumns(IntPtr s, int c); void GetSortColumns(IntPtr s, int c); void GetItem(int i, ref Guid r, out IntPtr p); void GetVisibleItem(int i, int p, out int f); void GetSelectedItem(int i, out int f); void GetSelection(int n, out IntPtr a); void GetSelectionState(IntPtr p, out uint f); void InvokeVerbOnSelection(IntPtr v); [PreserveSig] int SetViewModeAndIconSize(uint m, int s); [PreserveSig] int GetViewModeAndIconSize(out uint m, out int s); }
public static class IKDesk {
  public static int IconSize() {
    Type t = Type.GetTypeFromCLSID(new Guid("9BA05972-F6A8-11CF-A442-00A0C90A8F39"));
    object sw = Activator.CreateInstance(t);
    object disp = t.InvokeMember("FindWindowSW", System.Reflection.BindingFlags.InvokeMethod, null, sw, new object[] { 0, Type.Missing, 8, 0, 1 });
    object o; Guid sid = new Guid("4C96BE40-915C-11CF-99D3-00AA004AE837"), iid = typeof(IKShellBrowser).GUID;
    ((IKServiceProvider)disp).QueryService(ref sid, ref iid, out o);
    object v; ((IKShellBrowser)o).QueryActiveShellView(out v);
    uint mode; int size; ((IKFolderView2)v).GetViewModeAndIconSize(out mode, out size);
    return size; } }
'''


def desktop_size() -> dict:
    windows_only("desktop-size")
    ps = ("Add-Type -TypeDefinition @'\n" + _FOLDERVIEW_CS + "\n'@\n"
          "$r = [ordered]@{ folderView = [IKDesk]::IconSize();"
          " registry = (Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\Shell\\Bags\\1\\Desktop' -ErrorAction SilentlyContinue).IconSize;"
          " dpi = (Get-ItemProperty 'HKCU:\\Control Panel\\Desktop\\WindowMetrics' -ErrorAction SilentlyContinue).AppliedDPI };"
          " $r | ConvertTo-Json -Compress")
    return json.loads(powershell(ps))


def cmd_desktop_size(a):
    d = desktop_size()
    print(f"folder view says : {d['folderView']} px   (what the desktop reports)")
    print(f"registry says    : {d['registry']} px   (only saved when Explorer saves state; can be stale)")
    print(f"display scaling  : {round((d['dpi'] or 96) / 96 * 100)} %")
    if d["folderView"] != d["registry"]:
        print("NOTE: they differ. Trust neither blindly: on 2026-09-29 the folder view said 233"
              " while icons on screen measured about 96 px. Compare with what you see.")
    return 0


# --------------------------------------------------------------------------
#  shortcuts (Windows)
# --------------------------------------------------------------------------
def list_shortcuts() -> list:
    windows_only("shortcuts")
    ps = r'''$sh = New-Object -ComObject WScript.Shell
$out = foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('CommonDesktopDirectory'))) {
  Get-ChildItem -LiteralPath $d -Filter *.lnk -Force -ErrorAction SilentlyContinue | ForEach-Object {
    $l = $sh.CreateShortcut($_.FullName)
    [ordered]@{ path = $_.FullName; scope = $(if ($d -eq [Environment]::GetFolderPath('CommonDesktopDirectory')) { 'all users' } else { 'yours' });
                target = $l.TargetPath; arguments = $l.Arguments; icon = $l.IconLocation;
                changed = $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') } } }
@($out) | ConvertTo-Json -Depth 3 -Compress'''
    txt = powershell(ps).strip()
    if not txt:
        return []
    data = json.loads(txt)
    return data if isinstance(data, list) else [data]


def cmd_shortcuts(a):
    for s in list_shortcuts():
        print(f"{s['scope']:<9} {Path(s['path']).name}")
        print(f"          target : {s['target']} {s['arguments']}".rstrip())
        print(f"          icon   : {s['icon'] or '(from the target)'}")
        print(f"          changed: {s['changed']}")
    return 0


# --------------------------------------------------------------------------
#  frames: read the pictures inside .ico / .exe / .dll / .lnk
# --------------------------------------------------------------------------
def _frames_from_ico(path) -> list:
    with open(path, "rb") as fh:
        ico = IcoImagePlugin.IcoFile(fh)
        out = []
        for size in sorted(ico.sizes()):
            im = ico.getimage(size).convert("RGBA")
            out.append({"size": im.size[0], "format": "ico", "image": im})
    return out


def _frames_from_pe(path, group_index=0) -> list:
    import pefile  # declared by Fieldkit; on its own: pip install pefile
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
    icons, groups = {}, []
    for e in getattr(pe, "DIRECTORY_ENTRY_RESOURCE", type("x", (), {"entries": []})).entries:
        if e.id == 3:  # RT_ICON
            for d in e.directory.entries:
                s = d.directory.entries[0].data.struct
                icons[d.id] = pe.get_data(s.OffsetToData, s.Size)
        elif e.id == 14:  # RT_GROUP_ICON
            for d in e.directory.entries:
                s = d.directory.entries[0].data.struct
                groups.append(pe.get_data(s.OffsetToData, s.Size))
    if not groups:
        return []
    g = groups[min(group_index, len(groups) - 1)]
    out = []
    for i in range(struct.unpack_from("<H", g, 4)[0]):
        w, h, _, _, _, bpp, size, rid = struct.unpack_from("<BBBBHHIH", g, 6 + i * 14)
        w = w or 256
        data = icons[rid]
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            im, fmt = Image.open(io.BytesIO(data)).convert("RGBA"), "png"
        else:  # a BMP-format frame: wrap it as a one-frame .ico so Pillow can read it
            hdr = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", w % 256, w % 256, 0, 0, 1, bpp, len(data), 22)
            im, fmt = Image.open(io.BytesIO(hdr + data)).convert("RGBA"), "bmp"
        out.append({"size": w, "format": fmt, "image": im})
    return sorted(out, key=lambda f: f["size"])


def _resolve_lnk(path) -> tuple:
    ps = ("$l=(New-Object -ComObject WScript.Shell).CreateShortcut('" + str(path).replace("'", "''") + "');"
          " [ordered]@{ icon=$l.IconLocation; target=$l.TargetPath } | ConvertTo-Json -Compress")
    d = json.loads(powershell(ps))
    icon = d["icon"] or ""
    file, _, idx = icon.rpartition(",")
    if file.strip():
        return file, int(idx or 0)
    return d["target"], 0


def read_frames(path, group_index=0) -> list:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".lnk":
        f, idx = _resolve_lnk(p)
        return read_frames(f, idx)
    if ext == ".ico":
        return _frames_from_ico(p)
    if ext in (".exe", ".dll", ".mun"):
        return _frames_from_pe(p, group_index)
    raise ValueError(f"not an icon source: {p}")


def cmd_frames(a):
    frames = read_frames(a.path)
    if not frames:
        print("no icon found")
        return 1
    print(f"{a.path}: {len(frames)} frames")
    for f in frames:
        print(f"  {f['size']:>3} px  {f['format']:<4}  edge energy {edge_energy(f['image']):5.1f}")
        if a.out:
            Path(a.out).mkdir(parents=True, exist_ok=True)
            f["image"].save(Path(a.out) / f"{Path(a.path).stem}-{f['size']}.png")
    return 0


# --------------------------------------------------------------------------
#  check-ico: crisp-icon gates
# --------------------------------------------------------------------------
def check_ico(path, master=None, threshold=0.75) -> list:
    """Returns a list of problems (empty = pass)."""
    problems, seen = [], {}
    frames = _frames_from_ico(path)
    if not frames:
        return ["no frames"]
    for f in frames:
        im = f["image"]
        if im.size[0] != im.size[1]:
            problems.append(f"{f['size']}: not square {im.size}")
        h = hashlib.sha1(im.resize((8, 8)).tobytes() + im.tobytes()).hexdigest()
        if h in seen:
            problems.append(f"{f['size']}: identical to the {seen[h]} frame")
        seen[h] = f["size"]
        if master is not None:
            ref = master.resize(im.size, Image.LANCZOS)
            got, want = edge_energy(im), edge_energy(ref)
            if want > 0 and got < want * threshold:
                problems.append(f"{f['size']}: edge energy {got:.1f} < {threshold:.0%} of a true downsample ({want:.1f})")
    return problems


def cmd_check_ico(a):
    master = Image.open(a.master).convert("RGBA") if a.master else None
    frames = _frames_from_ico(a.ico)
    problems = check_ico(a.ico, master)
    print(f"{a.ico}: sizes {[f['size'] for f in frames]}")
    for p in problems:
        print("  FAIL", p)
    if not problems:
        print("  PASS: every frame true-sized and distinct" + (" and a true downsample" if master else ""))
    return 3 if problems else 0


# --------------------------------------------------------------------------
#  build: recoloured icons from a profile
# --------------------------------------------------------------------------
def load_profile(path) -> dict:
    p = json.loads(Path(path).read_text(encoding="utf-8"))
    for k in ("source", "crop", "colours"):
        if k not in p:
            raise ValueError(f"profile {path}: missing '{k}'")
    base = Path(path).parent
    p["source"] = str((base / p["source"]).resolve()) if not os.path.isabs(p["source"]) else p["source"]
    if not Path(p["source"]).is_file():
        raise ValueError(f"profile {path}: source picture not found: {p['source']}")
    return p


def _polygon_mask(size, poly, blur):
    import numpy as np
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).polygon([tuple(pt) for pt in poly], fill=255)
    if blur:
        m = m.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(m).astype(np.float32) / 255.0


def compose_master(profile, colour, dark=False) -> Image.Image:
    """The finished round icon at MASTER px: optional dark background, cap
    recoloured (lettering and red logo kept), circular crop and ring."""
    import numpy as np
    src = Image.open(profile["source"]).convert("RGB")
    a = np.asarray(src).astype(np.float32) / 255
    if dark and profile.get("subject_polygon"):
        subj = _polygon_mask(src.size, profile["subject_polygon"], 10)[..., None]
        a = a * subj + np.array(profile.get("dark_rgb", [14, 14, 18]), np.float32) / 255 * (1 - subj)
    if profile.get("cap_polygon"):
        cap = _polygon_mask(src.size, profile["cap_polygon"], 3)
        mx, mn = a.max(-1), a.min(-1)
        sat = (mx - mn) / (mx + 1e-6)
        lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
        keep_logo = (sat > 0.35) & (a[..., 0] > a[..., 1] * 1.3)
        keep_text = lum > profile.get("text_lum", 0.55)
        fabric = (cap * ~keep_logo * ~keep_text)[..., None]
        tinted = np.clip(hexrgb(colour) * (0.30 + 2.4 * lum[..., None]), 0, 1)
        a = a * (1 - fabric) + tinted * fabric
    im = Image.fromarray((a * 255).astype(np.uint8)).crop(tuple(profile["crop"])).resize((MASTER, MASTER), Image.LANCZOS)
    t, k = MASTER // 14, 2
    mask = Image.new("L", (MASTER * k,) * 2, 0)
    ImageDraw.Draw(mask).ellipse((t * k, t * k, (MASTER - t) * k - 1, (MASTER - t) * k - 1), fill=255)
    out = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask.resize((MASTER, MASTER), Image.LANCZOS))
    ring = Image.new("RGBA", (MASTER * k,) * 2, (0, 0, 0, 0))
    pad = MASTER // 128
    ImageDraw.Draw(ring).ellipse((pad * k, pad * k, (MASTER - pad) * k - 1, (MASTER - pad) * k - 1),
                                 outline=tuple(int(x * 255) for x in hexrgb(colour)) + (255,), width=t * k)
    return Image.alpha_composite(out, ring.resize((MASTER, MASTER), Image.LANCZOS))


def write_ico(master: Image.Image, path, sizes) -> None:
    """Every frame its own Lanczos downsample of the master, handed to the
    writer as-is, then read back and checked (sizes present, distinct)."""
    sizes = sorted(set(int(s) for s in sizes if 1 <= int(s) <= 256))
    frames = [master.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(path, format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    got = sorted(Image.open(path).info["sizes"])
    if got != [(s, s) for s in sizes]:
        raise RuntimeError(f"{path}: wrote {got}, wanted {sizes}")
    problems = check_ico(path, master)
    if problems:
        raise RuntimeError(f"{path}: " + "; ".join(problems))


def cmd_build(a):
    prof = load_profile(a.profile)
    out = Path(a.out or Path(a.profile).parent / "icons")
    out.mkdir(parents=True, exist_ok=True)
    sizes = list(DEFAULT_SIZES) + [int(s) for s in (a.extra_size or [])]
    if a.include_desktop_size:
        try:
            sizes.append(int(desktop_size()["folderView"]))
        except Exception as e:  # not fatal: the dense default list already covers it
            print(f"(could not read the desktop size: {e})")
    stamp = time.strftime("%Y%m%d-%H%M") if a.unique_names else ""
    colours = prof["colours"]
    if a.only:
        colours = {k: v for k, v in colours.items() if k in a.only}
    for name, colour in colours.items():
        master = compose_master(prof, colour, dark=a.dark)
        suffix = ("-dark" if a.dark else "") + (f"-{stamp}" if stamp else "")
        ico = out / f"{prof.get('name', 'icon')}-{name}{suffix}.ico"
        write_ico(master, ico, sizes)
        master.resize((256, 256), Image.LANCZOS).save(ico.with_suffix(".png"))
        print(f"  {ico}  ({len(set(sizes))} sizes, verified)")
    return 0


# --------------------------------------------------------------------------
#  compare: sheet at a chosen desktop size on the wallpaper
# --------------------------------------------------------------------------
def _frame_windows_would_use(frames, n):
    """Windows scales from the nearest frame; the smallest one that is at
    least n avoids upscaling, which is what we render."""
    bigger = [f for f in frames if f["size"] >= n]
    f = bigger[0] if bigger else frames[-1]
    im = f["image"]
    return (im if f["size"] == n else im.resize((n, n), Image.LANCZOS)), f["size"]


def _font(size):
    for name in ("segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def cmd_compare(a):
    font = _font(15)
    n = a.size
    cell = n + 40
    wall = Image.open(a.wallpaper).convert("RGBA") if a.wallpaper else None
    sheet = Image.new("RGBA", (len(a.icons) * cell + 20, n + 90), (24, 24, 30, 255))
    d = ImageDraw.Draw(sheet)
    for i, path in enumerate(a.icons):
        frames = read_frames(path)
        im, used = _frame_windows_would_use(frames, n)
        x = 20 + i * cell
        if wall:
            patch = wall.crop((x % max(1, wall.width - cell), 300, x % max(1, wall.width - cell) + cell - 10, 300 + n + 20))
            sheet.alpha_composite(patch, (x - 10, 20))
        sheet.alpha_composite(im, (x, 30))
        d.text((x, n + 40), Path(path).stem[:18], fill=(220, 220, 220), font=font)
        d.text((x, n + 60), f"from {used} px frame", fill=(150, 150, 150), font=font)
    sheet.convert("RGB").save(a.out)
    print(f"wrote {a.out}  (each icon at {n} px, from the frame Windows would pick)")
    return 0


# --------------------------------------------------------------------------
#  set-icon / restore (Windows)
# --------------------------------------------------------------------------
def cmd_set_icon(a):
    windows_only("set-icon")
    ico = Path(a.ico).resolve()
    if not ico.exists():
        print(f"no such icon: {ico}")
        return 2
    backup = Path(a.backup)
    backup.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(backup.read_text(encoding="utf-8")) if backup.exists() else {}
    esc = lambda s: str(s).replace("'", "''")
    for lnk in a.lnk:
        p = Path(lnk).resolve()
        ps = (f"$l=(New-Object -ComObject WScript.Shell).CreateShortcut('{esc(p)}'); $t=$l.TargetPath; $o=$l.IconLocation;"
              f" $l.IconLocation='{esc(ico)},0'; $l.Save();"
              f" $c=(New-Object -ComObject WScript.Shell).CreateShortcut('{esc(p)}');"
              " [ordered]@{ old=$o; now=$c.IconLocation; targetKept=($c.TargetPath -eq $t) } | ConvertTo-Json -Compress")
        r = json.loads(powershell(ps))
        old.setdefault(str(p), r["old"])      # first backup wins: that is the original
        print(f"  {p.name}: icon -> {ico.name}   target unchanged: {r['targetKept']}")
    backup.write_text(json.dumps(old, indent=2), encoding="utf-8")
    subprocess.run(["ie4uinit.exe", "-show"])
    print(f"backup: {backup}")
    print(f'NEXT: to undo: fieldkit icons restore "{backup}"')
    return 0


def cmd_restore(a):
    windows_only("restore")
    data = json.loads(Path(a.backup).read_text(encoding="utf-8"))
    desktop = powershell("[Environment]::GetFolderPath('Desktop')").strip()
    for lnk, icon in data.items():
        full = lnk if os.path.isabs(lnk) else str(Path(desktop) / lnk)
        esc = lambda s: str(s).replace("'", "''")
        powershell(f"$l=(New-Object -ComObject WScript.Shell).CreateShortcut('{esc(full)}'); $l.IconLocation='{esc(icon)}'; $l.Save()")
        print(f"  {Path(full).name}: icon -> {icon or '(from the target)'}")
    subprocess.run(["ie4uinit.exe", "-show"])
    return 0


# --------------------------------------------------------------------------
#  tih-check (Windows)
# --------------------------------------------------------------------------
def cmd_tih_check(a):
    windows_only("tih-check")
    ps = (f"Get-Process TextInputHost -ErrorAction SilentlyContinue | ForEach-Object {{ $c0=$_.TotalProcessorTime.TotalSeconds;"
          f" Start-Sleep {a.window}; $_.Refresh(); $pct=[math]::Round(($_.TotalProcessorTime.TotalSeconds-$c0)*100/{a.window});"
          f" $act=''; if ($pct -ge {a.threshold} -and ${str(a.end).lower()}) {{ Stop-Process -Id $_.Id -Force; $act='ended' }};"
          " \"pid $($_.Id): $pct % of one core $act\" }")
    out = powershell(ps).strip()
    print(out or "TextInputHost is not running")
    return 0


# --------------------------------------------------------------------------
#  cache: the icon cache the desktop draws from
# --------------------------------------------------------------------------
# On 2026-10-02 every desktop icon went soft at 256 px, even the Recycle Bin, while the .ico files held sharp
# 256 px frames. Explorer draws desktop icons from its own cache (iconcache_<size>.db), not from the files; a
# cache entry made from a smaller picture is stretched at every size you pick, and Ctrl + wheel cannot help.
# On Linux the same thing is a stale icon-theme cache: GTK's icon-theme.cache in each theme folder (rebuilt by
# gtk-update-icon-cache) and KDE's ~/.cache/icon-cache.kcache. The fix is the same idea: the cache files are moved
# aside (kept as a backup, never deleted) and the desktop rebuilds them.

def backup_root() -> Path:
    return LOCAL / "cache-backups"


def cache_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Explorer"


def cache_files(include_thumbs=False) -> list:
    """Windows: the icon cache files (and thumbnail caches with include_thumbs), plus the legacy IconCache.db."""
    d = cache_dir()
    pats = ["iconcache_*.db"] + (["thumbcache_*.db"] if include_thumbs else [])
    out = sorted(p for pat in pats for p in d.glob(pat))
    legacy = Path(os.environ.get("LOCALAPPDATA", "")) / "IconCache.db"
    if legacy.is_file():
        out.append(legacy)
    return out


def linux_caches(home=None, data_dirs=None) -> dict:
    """Linux: {"gtk": [theme folders the user owns that have an icon-theme.cache or an index.theme],
    "kde": [KDE icon cache files]}. System themes (/usr/share/icons) need root: listed, never touched."""
    home = Path(home or Path.home())
    user_dirs = [home / ".local" / "share" / "icons", home / ".icons"]
    gtk = []
    for base in user_dirs:
        if base.is_dir():
            for t in sorted(base.iterdir()):
                if t.is_dir() and ((t / "index.theme").is_file() or (t / "icon-theme.cache").is_file()):
                    gtk.append(t)
    system = []
    for d in (data_dirs or os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")).split(":"):
        base = Path(d) / "icons"
        if base.is_dir():
            system += [t for t in sorted(base.iterdir()) if (t / "icon-theme.cache").is_file()]
    kde = sorted((home / ".cache").glob("icon-cache.kcache")) + sorted((home / ".cache").glob("ksycoca*"))
    return {"gtk": gtk, "kde": kde, "system": system}


def resource_icon_sizes(path, index) -> list:
    """Frame sizes of icon `index` in a DLL/EXE (index >= 0: the n-th icon group; < 0: the group with that resource
    id), the way a shortcut's IconLocation names it. Windows 10/11 keep many system icons in
    SystemResources\\<name>.mun, not in the DLL: shell32.dll,238 lives in shell32.dll.mun (frames 16-256)."""
    import pefile
    path = os.path.expandvars(str(path))
    cands = [path, os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "SystemResources", Path(path).name + ".mun")]
    for c in cands:
        if not os.path.isfile(c):
            continue
        pe = pefile.PE(c, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        groups = [e for e in getattr(pe, "DIRECTORY_ENTRY_RESOURCE", type("x", (), {"entries": []})).entries
                  if e.id == pefile.RESOURCE_TYPE["RT_GROUP_ICON"]]
        if not groups:
            continue
        entries = groups[0].directory.entries
        e = entries[index] if index >= 0 else next((x for x in entries if x.id == -index), None)
        if e is None:
            raise ValueError(f"no icon {index} in {c}")
        d = e.directory.entries[0].data.struct
        raw = pe.get_data(d.OffsetToData, d.Size)
        n = struct.unpack_from("<HHH", raw)[2]
        return sorted({raw[6 + 14 * i] or 256 for i in range(n)})
    raise FileNotFoundError(f"no icon resources in {path}")


def frame_sizes(location) -> list:
    """Frame sizes of a shortcut's icon: an .ico file, or `file,index` inside a DLL/EXE (environment variables
    expanded)."""
    loc = os.path.expandvars(location or "")
    path, _, idx = loc.rpartition(",") if "," in loc else (loc, "", "")
    path = path or loc
    try:
        sizes = sorted({f["size"] for f in read_frames(path)})
    except Exception:
        sizes = []
    if not sizes and path.lower().endswith((".dll", ".exe", ".mun", ".cpl", ".ocx")):
        sizes = resource_icon_sizes(path, int(idx or 0))      # the reader above handles .ico and our .exe files
    if not sizes:
        raise ValueError(f"no icon frames read from {loc}")
    return sizes


def icon_verdicts(shortcuts, size) -> list:
    """Per shortcut: does its icon hold a frame at least `size` px? If it does, a soft desktop icon is the
    cache's fault, not the file's."""
    rows = []
    for s in shortcuts:
        loc = (s.get("icon") or "").strip()
        path, _, idx = loc.rpartition(",") if "," in loc else (loc, "", "")
        src = path.strip() or s.get("target") or ""          # ",0" = the first icon of the program it starts
        loc = f"{src},{idx}" if idx else src
        try:
            sizes = frame_sizes(loc) if src else []
        except Exception as e:                                   # unreadable icon: say so, never guess
            rows.append({"name": Path(s["path"]).name, "source": src, "largest": None, "ok": None, "why": str(e)[:120]})
            continue
        big = max(sizes) if sizes else 0
        rows.append({"name": Path(s["path"]).name, "source": src, "largest": big, "ok": big >= size,
                     "why": "" if big >= size else f"largest frame {big} px < {size} px: Windows must stretch it"})
    return rows


def _countdown(n):
    for i in range(n, 0, -1):
        print(f"  {i} ...", flush=True)
        time.sleep(1)


def _cache_linux(a):
    c = linux_caches()
    print("icon caches (Linux):")
    for t in c["gtk"]:
        print(f"  GTK theme (yours)  {t}")
    for f in c["kde"]:
        print(f"  KDE cache          {f}")
    for t in c["system"]:
        print(f"  GTK theme (system) {t}   needs root: sudo gtk-update-icon-cache -f {t}")
    if not (c["gtk"] or c["kde"]):
        print("nothing of yours to rebuild" + (": the system themes above need root (the sudo lines)" if c["system"]
              else "; soft icons then come from the icon files themselves (fieldkit icons frames FILE)"))
        return 0
    if not a.apply:
        print("NEXT: fieldkit icons cache --apply   (moves your icon caches to a backup and rebuilds them; "
              "programs and open windows are not touched)")
        return 0
    backup = Path(a.backup_dir or backup_root()) / f"icon-cache-{time.strftime('%Y%m%d-%H%M%S')}"
    print("Your icon caches will be moved to a backup and rebuilt. Ctrl+C now to stop.")
    _countdown(a.countdown)
    backup.mkdir(parents=True, exist_ok=True)
    tool = shutil.which("gtk-update-icon-cache") or shutil.which("gtk4-update-icon-cache")
    problems = []
    for t in c["gtk"]:
        cache = t / "icon-theme.cache"
        if cache.is_file():
            (backup / t.name).mkdir(exist_ok=True)
            cache.replace(backup / t.name / cache.name)
        if tool and (t / "index.theme").is_file():
            r = subprocess.run([tool, "-f", "-t", str(t)], capture_output=True, text=True)
            if r.returncode:
                problems.append(f"{t.name}: {r.stderr.strip()[:120]}")
    for f in c["kde"]:
        f.replace(backup / f.name)                      # KDE writes a fresh one when the next program draws icons
    if c["gtk"] and not tool:
        problems.append("gtk-update-icon-cache not found (Debian/Ubuntu: sudo apt-get install -y libgtk-3-bin); "
                        "GTK falls back to reading the icon files, slower but sharp")
    print(f"moved the caches to {backup}")
    for p in problems:
        print(f"  PROBLEM: {p}")
    print("Log out and back in (or restart the panel) if icons do not redraw within a minute.")
    return 3 if problems else 0


def cmd_cache(a):
    if IS_LINUX:
        return _cache_linux(a)
    if not IS_WINDOWS:
        print(f"cache: not-this-platform ({sys.platform}); nothing was changed")
        return 0
    files = cache_files(a.thumbs)
    try:
        size = desktop_size().get("folderView") or 0
    except Exception as e:
        size = 0
        print(f"desktop size: could not be read ({e})")
    print(f"desktop draws icons at {size} px")
    print(f"icon cache folder: {cache_dir()}")
    for f in files:
        st = f.stat()
        print(f"  {f.name:28} {st.st_size:>11,} bytes  {time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))}")
    if size:
        print("icons on the desktop:")
        bad = 0
        for r in icon_verdicts(list_shortcuts(), size):
            state = "ok  " if r["ok"] else ("????" if r["ok"] is None else "FILE")
            bad += r["ok"] is False
            print(f"  {state} {r['name']}: largest frame {r['largest']} px {r['why']}".rstrip())
        print("VERDICT: " + ("the icon files hold sharp frames at this size, so soft icons come from the cache."
                             if not bad else f"{bad} icon file(s) lack a frame this big: rebuild them (fieldkit icons build)."))
    if not a.apply:
        print("NEXT: fieldkit icons cache --apply   (restarts Explorer after a countdown; the taskbar and open"
              " File Explorer windows close and come back; programs and unsaved work are not touched)")
        return 0
    backup = Path(a.backup_dir or backup_root()) / f"icon-cache-{time.strftime('%Y%m%d-%H%M%S')}"
    print()
    print("WARNING: Explorer (the desktop, taskbar and File Explorer windows) will restart.")
    print("Your programs keep running and unsaved work is not touched. Ctrl+C now to stop.")
    _countdown(a.countdown)
    before = powershell("@(Get-Process explorer -ErrorAction SilentlyContinue | ForEach-Object Id) -join ','").strip()
    print(f"stopping Explorer (pid {before or 'none'})")
    subprocess.run(["taskkill", "/F", "/IM", "explorer.exe"], capture_output=True)
    time.sleep(2)
    backup.mkdir(parents=True, exist_ok=True)
    moved, locked = [], []
    for f in cache_files(a.thumbs):
        try:
            f.replace(backup / f.name)
            moved.append(f.name)
        except OSError as e:
            locked.append(f"{f.name}: {e.strerror}")
    # Start Explorer through Windows' WMI process service, NOT as our child: a shell started from an agent's
    # session belongs to that session's process tree and is closed with it (2026-10-05/07: the desktop and taskbar
    # vanished twice overnight when the Claude Code session that had restarted Explorer ended).
    explorer = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
    powershell("Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
               f"-Arguments @{{CommandLine='{explorer}'}} | Out-Null")
    time.sleep(4)
    subprocess.run(["ie4uinit.exe", "-show"], capture_output=True)
    after = powershell("@(Get-Process explorer -ErrorAction SilentlyContinue | ForEach-Object Id) -join ','").strip()
    print(f"moved {len(moved)} cache file(s) to {backup}")
    for x in locked:
        print(f"  NOT MOVED (in use): {x}")
    print(f"Explorer running again: {'yes, pid ' + after if after else 'NO - press Ctrl+Shift+Esc, File > Run new task, explorer'}")
    print("The cache rebuilds itself as icons are drawn. The backup folder can be deleted once icons look right.")
    return 0 if after and not locked else 3


# --------------------------------------------------------------------------
# The fix button (2026-10-09). The owner had to ask for the cache rebuild about ten times: installs, Windows updates
# and display changes leave Explorer drawing soft icons from its cache. One double-click (desktop shortcut) or one
# right-click on the desktop background runs `cache --apply` with a 3-second countdown. Per user (HKCU and the user's
# own Desktop folder): no administrator rights, nothing machine-wide. On Windows 11 the right-click entry is under
# "Show more options" unless the classic menu is on. Undo: uninstall-fix-button.
FIX_NAME = "Fix blurry icons"
FIX_ICON = r"%SystemRoot%\System32\shell32.dll,238"          # the blue circular arrows (looked at, 2026-10-09)
FIX_KEY = r"HKCU\Software\Classes\DesktopBackground\Shell\GorillaFixIcons"
FIX_CMD = LOCAL / "Fix-blurry-icons.cmd"


def fix_launcher(python=None, root=None) -> str:
    """The .cmd both entries run: this machine's Python and this machine's Fieldkit folder, found when the button is
    installed (2026-10-10: the first launcher named one person's Python and script paths). The window stays open on
    a failure."""
    python = python or sys.executable
    root = root or str(ROOT)
    return "\r\n".join([
        "@echo off",
        "title Fix blurry icons",
        f'cd /d "{root}"',
        f'"{python}" -m fieldkit icons cache --apply --countdown 3',
        "if errorlevel 1 (echo. & echo Something did not work - read the lines above. & pause & exit /b 1)",
        "echo. & echo Done. The icons redraw sharp within a few seconds. This window closes by itself.",
        "timeout /t 6 >nul",
    ]) + "\r\n"


def fix_registry_command(cmd=None) -> str:
    return f'"%SystemRoot%\\System32\\cmd.exe" /c ""{cmd or FIX_CMD}""'


def _reg(*args):
    r = subprocess.run(["reg", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())


def cmd_install_fix_button(a):
    windows_only("install-fix-button")
    FIX_CMD.parent.mkdir(parents=True, exist_ok=True)
    FIX_CMD.write_bytes(fix_launcher().encode("utf-8"))
    desktop = powershell("[Environment]::GetFolderPath('Desktop')").strip()
    lnk = Path(desktop) / f"{FIX_NAME}.lnk"
    powershell("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('" + str(lnk).replace("'", "''") + "'); "
               "$s.TargetPath = '" + str(FIX_CMD).replace("'", "''") + "'; "
               "$s.WorkingDirectory = '" + str(ROOT).replace("'", "''") + "'; "
               "$s.IconLocation = '" + FIX_ICON + "'; "
               "$s.Description = 'Rebuild the Windows icon cache so desktop icons are sharp again'; $s.Save()")
    _reg("add", FIX_KEY, "/ve", "/d", FIX_NAME, "/f")
    _reg("add", FIX_KEY, "/v", "Icon", "/d", FIX_ICON, "/f")
    _reg("add", FIX_KEY + r"\command", "/ve", "/t", "REG_EXPAND_SZ", "/d", fix_registry_command(), "/f")
    print(f"launcher:   {FIX_CMD}")
    print(f"shortcut:   {lnk}")
    print(f"right-click on the desktop background: '{FIX_NAME}' (Windows 11: under 'Show more options')")
    print("NEXT: double-click 'Fix blurry icons' on the desktop whenever icons look soft. "
          "Undo: fieldkit icons uninstall-fix-button")
    return 0


def cmd_uninstall_fix_button(a):
    windows_only("uninstall-fix-button")
    desktop = powershell("[Environment]::GetFolderPath('Desktop')").strip()
    lnk = Path(desktop) / f"{FIX_NAME}.lnk"
    if lnk.exists():
        lnk.unlink()
    subprocess.run(["reg", "delete", FIX_KEY, "/f"], capture_output=True)
    print(f"removed the shortcut and the right-click entry (the launcher {FIX_CMD} stays; delete it if you like)")
    return 0


# --------------------------------------------------------------------------
def build_parser(prog="iconkit.py"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n")[0],
                                 epilog="exit: 0 fine, 1 error, 2 bad input, 3 findings (a soft icon, a failed step)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("desktop-size").set_defaults(func=cmd_desktop_size)
    sub.add_parser("shortcuts").set_defaults(func=cmd_shortcuts)
    p = sub.add_parser("frames"); p.add_argument("path"); p.add_argument("--out"); p.set_defaults(func=cmd_frames)
    p = sub.add_parser("check-ico"); p.add_argument("ico"); p.add_argument("--master"); p.set_defaults(func=cmd_check_ico)
    p = sub.add_parser("build"); p.add_argument("profile"); p.add_argument("--out"); p.add_argument("--dark", action="store_true")
    p.add_argument("--only", nargs="*"); p.add_argument("--extra-size", nargs="*", type=int)
    p.add_argument("--include-desktop-size", action="store_true"); p.add_argument("--unique-names", action="store_true",
                   help="add a time stamp to file names so Windows cannot reuse cached icons")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("compare"); p.add_argument("icons", nargs="+"); p.add_argument("--size", type=int, default=96)
    p.add_argument("--wallpaper"); p.add_argument("--out", default="compare.png"); p.set_defaults(func=cmd_compare)
    p = sub.add_parser("set-icon"); p.add_argument("lnk", nargs="+"); p.add_argument("--ico", required=True)
    p.add_argument("--backup", default=str(LOCAL / "shortcut-icons-backup.json")); p.set_defaults(func=cmd_set_icon)
    p = sub.add_parser("restore"); p.add_argument("backup"); p.set_defaults(func=cmd_restore)
    p = sub.add_parser("tih-check"); p.add_argument("--end", action="store_true"); p.add_argument("--threshold", type=int, default=80)
    p.add_argument("--window", type=int, default=10); p.set_defaults(func=cmd_tih_check)
    p = sub.add_parser("cache", help="why desktop icons are soft; --apply rebuilds the icon cache")
    p.add_argument("--apply", action="store_true"); p.add_argument("--thumbs", action="store_true", help="also the picture thumbnail caches (Windows)")
    p.add_argument("--countdown", type=int, default=15)
    p.add_argument("--backup-dir", help=f"where the old cache files go (default {backup_root()})")
    p.set_defaults(func=cmd_cache)
    p = sub.add_parser("install-fix-button", help="desktop shortcut + desktop right-click entry that rebuild the icon cache")
    p.set_defaults(func=cmd_install_fix_button)
    sub.add_parser("uninstall-fix-button").set_defaults(func=cmd_uninstall_fix_button)
    return ap


def main(argv=None, prog="iconkit.py"):
    a = build_parser(prog).parse_args(argv)
    try:
        return a.func(a)
    except NotThisPlatform as e:
        print(f"not-this-platform: {e}")
        return 0
    except (ValueError, FileNotFoundError) as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
