"""Small desktop repairs: the graphics card's control app, and a wallpaper that went black or stale.

    fieldkit laptop video [--apply]     an NVIDIA card is there: is its Control Panel app installed? --apply installs it
                                        (winget, Microsoft Store id 9NF8H0H7WMLT); no NVIDIA card: nothing to do
    fieldkit laptop wallpaper           reload the current wallpaper without restarting Explorer (Windows), or GNOME's

From the academic harness this generalises (video_doctor.py and refresh_wallpaper.py, 2026-10-07). On laptops with
DCH drivers the NVIDIA Control Panel is a Store app that a clean driver install leaves out; without it there is no
way to pick the GPU per program. The wallpaper reload is SystemParametersInfo(SPI_SETDESKWALLPAPER) with the path
Windows already has, so nothing about the wallpaper changes but its picture on screen.
"""
import subprocess
import sys

NVCP_ID = "9NF8H0H7WMLT"
NOWIN = 0x08000000


def video(apply=False, run=subprocess.run, platform=None):
    if (platform or sys.platform) != "win32":
        return {"ok": True, "status": "not-windows", "next": "on Linux the NVIDIA settings come with the driver "
                                                             "(nvidia-settings)"}
    r = run(["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, creationflags=NOWIN)
    cards = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
    if not any("NVIDIA" in c.upper() for c in cards):
        return {"ok": True, "status": "no-nvidia", "cards": cards, "next": "nothing to do"}
    listed = run(["winget", "list", "--id", NVCP_ID, "--accept-source-agreements"], capture_output=True, text=True,
                 creationflags=NOWIN)
    have = NVCP_ID.lower() in (listed.stdout or "").lower()
    if have or not apply:
        return {"ok": have, "status": "installed" if have else "missing", "cards": cards,
                "next": "nothing to do" if have else "fieldkit laptop video --apply (installs it from the Store)"}
    r = run(["winget", "install", "--id", NVCP_ID, "--accept-package-agreements", "--accept-source-agreements"],
            capture_output=True, text=True, creationflags=NOWIN)
    out = ((r.stdout or "") + (r.stderr or "")).lower()
    ok = r.returncode == 0 or "already installed" in out
    return {"ok": ok, "status": "installed" if ok else "failed", "cards": cards,
            "next": "open NVIDIA Control Panel from the Start menu" if ok else "winget failed: " + out.strip()[-200:]}


def wallpaper(platform=None, run=subprocess.run):
    if (platform or sys.platform) == "win32":
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as k:
            path, _ = winreg.QueryValueEx(k, "Wallpaper")
        ok = bool(ctypes.windll.user32.SystemParametersInfoW(20, 0, path, 0x01 | 0x02))
        return {"ok": ok, "wallpaper": path, "next": "done" if ok else "Windows refused: set it again in Settings"}
    r = run(["gsettings", "get", "org.gnome.desktop.background", "picture-uri"], capture_output=True, text=True)
    uri = (r.stdout or "").strip()
    if r.returncode != 0 or not uri:
        return {"ok": False, "next": "not a GNOME desktop: set the wallpaper again in its settings"}
    run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri.strip("'")], capture_output=True)
    return {"ok": True, "wallpaper": uri, "next": "done"}
