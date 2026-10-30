"""Low sound on a Windows laptop: why, and the safe fix, the same way every time. Plus the master volume anywhere.

    fieldkit laptop sound diagnose          read-only report: the sound chip and its driver, the audio services,
                                            master volume and mute, every app's own volume, call ducking
    fieldkit laptop sound fix [--apply]     the plan; with --apply: master 100%, unmuted, every app at 100%, Windows
                                            call ducking off; an undo file is written first
    fieldkit laptop sound undo              put back what the last fix changed
    fieldkit laptop sound open sound|devices|settings|maker   a window on the person's real desktop
    fieldkit laptop sound drivers           the installed driver and its exact hardware ID, and what Microsoft Update
                                            Catalog lists for that ID (read only: nothing is downloaded or installed)
    fieldkit laptop volume [--set N]        the master volume (Windows Core Audio; Linux wpctl or pactl)

Born from the academic harness this generalises (audio_doctor.py, 2026-10-05), without one laptop's chip, vendor
app or catalog: the chip is whatever HD Audio codec is present, and the maker's sound app is the one named in
fieldkit.local.json ({"laptop": {"sound_app": "C:\\\\...\\\\MaxxAudioPro.exe"}}). Lessons kept from that day:
  * agents run commands on a hidden desktop; a window started directly never appears on the person's screen, so
    `open` goes through Task Scheduler with an interactive principal, which puts it on the real desktop;
  * from that hidden desktop a program started this way may not show in the process list: "not listed" does not
    mean "did not open"; ask the person.
Not taken over: downloading and installing a newer driver. It needs the machine it runs on to be tested, and a
driver is the one change here a restart cannot undo; `drivers` shows what exists, the person installs it.
"""
import datetime as _dt
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from ..core import settings
from .winps import ps_json, q

DUCKING = {0: "mute other sounds", 1: "reduce by 80%", 2: "reduce by 50%", 3: "do nothing"}
OPEN = {"sound": (r"C:\Windows\System32\control.exe", "mmsys.cpl,,0"),
        "devices": (r"C:\Windows\System32\mmc.exe", "devmgmt.msc"),
        "settings": (r"C:\Windows\explorer.exe", "ms-settings:sound")}
CATALOG = "https://www.catalog.update.microsoft.com/Search.aspx?q="

AUDIO_CS = r"""
using System; using System.Runtime.InteropServices; using System.Collections.Generic;
namespace FieldkitAudio {
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume { int f1(); int f2(); int f3(); int f4();
  int SetMasterVolumeLevelScalar(float l, ref Guid c); int f5();
  int GetMasterVolumeLevelScalar(out float l); int f6(); int f7(); int f8(); int f9();
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool m, ref Guid c);
  int GetMute([MarshalAs(UnmanagedType.Bool)] out bool m); }
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref Guid id, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int f1(); int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice d); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorCo { }
[Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionManager2 { int f1(); int f2(); int GetSessionEnumerator(out IAudioSessionEnumerator e); }
[Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionEnumerator { int GetCount(out int c); int GetSession(int i, out IAudioSessionControl2 s); }
[Guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionControl2 { int f1(); int f2(); int f3(); int f4(); int f5(); int f6();
  int f7(); int f8(); int f9(); int f10(); int f11(); int GetProcessId(out uint pid); }
[Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ISimpleAudioVolume { int SetMasterVolume(float l, ref Guid c); int GetMasterVolume(out float l);
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool m, ref Guid c); int GetMute([MarshalAs(UnmanagedType.Bool)] out bool m); }
public static class Audio {
  static object Act(Guid g) {
    var e = (IMMDeviceEnumerator)(new MMDeviceEnumeratorCo()); IMMDevice d;
    Marshal.ThrowExceptionForHR(e.GetDefaultAudioEndpoint(0, 1, out d));
    object o; Marshal.ThrowExceptionForHR(d.Activate(ref g, 23, IntPtr.Zero, out o)); return o; }
  static IAudioEndpointVolume EV() { return (IAudioEndpointVolume)Act(typeof(IAudioEndpointVolume).GUID); }
  public static float GetVolume() { float v; EV().GetMasterVolumeLevelScalar(out v); return v; }
  public static bool GetMute() { bool m; EV().GetMute(out m); return m; }
  public static void SetVolume(float v) { Guid g = Guid.Empty; Marshal.ThrowExceptionForHR(EV().SetMasterVolumeLevelScalar(v, ref g)); }
  public static void SetMute(bool m) { Guid g = Guid.Empty; Marshal.ThrowExceptionForHR(EV().SetMute(m, ref g)); }
  static List<IAudioSessionControl2> All() {
    var r = new List<IAudioSessionControl2>();
    var m = (IAudioSessionManager2)Act(typeof(IAudioSessionManager2).GUID);
    IAudioSessionEnumerator se; m.GetSessionEnumerator(out se); int n; se.GetCount(out n);
    for (int i = 0; i < n; i++) { IAudioSessionControl2 s; se.GetSession(i, out s); r.Add(s); }
    return r; }
  public static List<string> Sessions() {
    var r = new List<string>();
    foreach (var s in All()) { uint pid; s.GetProcessId(out pid); var v = (ISimpleAudioVolume)s;
      float l; v.GetMasterVolume(out l); bool mu; v.GetMute(out mu);
      r.Add(pid + "|" + (int)Math.Round(l * 100) + "|" + mu); }
    return r; }
  public static int SetAllSessions(float level) {
    int c = 0; Guid g = Guid.Empty;
    foreach (var s in All()) { var v = (ISimpleAudioVolume)s; v.SetMasterVolume(level, ref g); v.SetMute(false, ref g); c++; }
    return c; }
}}
"""


def with_audio(body):
    return "Add-Type -TypeDefinition @'\n" + AUDIO_CS + "\n'@\n" + body


STATE_PS = r"""
$r = [ordered]@{}
$cs = Get-CimInstance Win32_ComputerSystem; $os = Get-CimInstance Win32_OperatingSystem
$r.manufacturer = $cs.Manufacturer; $r.model = $cs.Model; $r.os = $os.Caption; $r.build = [int]$os.BuildNumber
$r.codec = @(Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.HardWareID -like 'HDAUDIO\FUNC_01*' } | ForEach-Object {
  [ordered]@{ name = $_.DeviceName; version = $_.DriverVersion; provider = $_.DriverProviderName
              date = $(if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { '' })
              inf = $_.InfName; hwid = $_.HardWareID } })
$r.services = [ordered]@{}
foreach ($n in 'Audiosrv','AudioEndpointBuilder') {
  $s = Get-Service $n -ErrorAction SilentlyContinue; $r.services[$n] = $(if ($s) { $s.Status.ToString() } else { 'Missing' }) }
$r.volume = [int][math]::Round([FieldkitAudio.Audio]::GetVolume() * 100)
$r.muted = [FieldkitAudio.Audio]::GetMute()
$r.sessions = @([FieldkitAudio.Audio]::Sessions() | ForEach-Object {
  $p = $_.Split('|')
  $name = $(if ($p[0] -eq '0') { 'System sounds' } else { (Get-Process -Id ([int]$p[0]) -ErrorAction SilentlyContinue).ProcessName })
  [ordered]@{ pid = [int]$p[0]; name = "$name"; volume = [int]$p[1]; muted = ($p[2] -eq 'True') } })
$d = Get-ItemProperty 'HKCU:\Software\Microsoft\Multimedia\Audio' -Name UserDuckingPreference -ErrorAction SilentlyContinue
$r.ducking = $(if ($d) { [int]$d.UserDuckingPreference } else { $null })
$r | ConvertTo-Json -Depth 5 -Compress
"""

FIX_PS = r"""
[FieldkitAudio.Audio]::SetVolume(1.0)
[FieldkitAudio.Audio]::SetMute($false)
$n = [FieldkitAudio.Audio]::SetAllSessions(1.0)
New-Item -Path 'HKCU:\Software\Microsoft\Multimedia\Audio' -Force | Out-Null
Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Multimedia\Audio' -Name UserDuckingPreference -Value 3 -Type DWord
[ordered]@{ sessions = $n } | ConvertTo-Json -Compress
"""


def _windows(platform=None):
    if (platform or sys.platform) != "win32":
        raise RuntimeError("this is the Windows sound doctor; on Linux: fieldkit audio (every stage the sound "
                           "passes through)")


def _dir():
    d = settings.state_root() / "state" / "sound"
    d.mkdir(parents=True, exist_ok=True)
    return d


# -- pure parts --------------------------------------------------------------------------------------------------------
def version_key(text):
    return tuple(int(p) for p in re.findall(r"\d+", text or ""))


def parse_hwid(hwid):
    """'HDAUDIO\\FUNC_01&VEN_10EC&DEV_0295&SUBSYS_102807B0&REV_1000' -> the match, the catalog search key, the vendor."""
    m = re.search(r"(HDAUDIO\\FUNC_01&VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})&SUBSYS_([0-9A-F]{4})([0-9A-F]{4}))",
                  hwid or "", re.I)
    if not m:
        return None
    return {"match": m.group(1).upper(), "search": f"VEN_{m.group(2)}&DEV_{m.group(3)}&SUBSYS_{m.group(4)}{m.group(5)}"
            .upper(), "chip_vendor": m.group(2).upper(), "subsys_vendor": m.group(4).upper(),
            "system_id": m.group(5).upper()}


def windows_label(build):
    if build >= 26200:
        return "Windows 11 Client, version 25H2"
    if build >= 26100:
        return "Windows 11 Client, version 24H2"
    if build >= 22621:
        return "Windows 11 Client, version 22H2"
    if build >= 22000:
        return "Windows 11 Client, version 21H2"
    return "Windows 10"


_ROW = re.compile(r'<tr id="([0-9a-f\-]{36})_R\d+".*?</tr>', re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)


def parse_catalog(page):
    import html
    clean = lambda raw: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
    rows, seen = [], set()
    for m in _ROW.finditer(page or ""):
        uid = m.group(1).lower()
        cells = [clean(c) for c in _CELL.findall(m.group(0))]
        if uid in seen or len(cells) < 7 or not re.fullmatch(r"\d+(\.\d+)+", cells[5]):
            continue
        seen.add(uid)
        rows.append({"update_id": uid, "title": cells[1], "products": cells[2], "classification": cells[3],
                     "date": cells[4], "version": cells[5], "size": cells[6]})
    return rows


def evaluate(state):
    """A diagnose snapshot -> [(status, what, detail)]: OK, FAIL (fix it) or CHECK (look)."""
    out = [("OK", "computer", f"{state.get('manufacturer')} {state.get('model')}, {state.get('os')} "
                              f"(build {state.get('build')})")]
    codecs = state.get("codec") or []
    if codecs:
        c = codecs[0]
        out.append(("OK", "sound chip driver", f"{c.get('name')}: {c.get('version')} ({c.get('date')}), "
                                              f"{c.get('provider')}, {c.get('inf')}"))
    else:
        out.append(("FAIL", "sound chip driver", "no HD Audio sound chip found: Device Manager, Sound"))
    for name, st in (state.get("services") or {}).items():
        out.append(("OK" if st == "Running" else "FAIL", f"service {name}", st))
    if state.get("muted"):
        out.append(("FAIL", "muted", "the speakers are muted"))
    vol = state.get("volume")
    if vol is not None:
        out.append(("OK" if vol >= 90 else "CHECK", "master volume", f"{vol}%" + ("" if vol >= 90 else
                                                                                   " (fix raises it to 100%)")))
    low = [s for s in state.get("sessions") or [] if s.get("volume", 100) < 100 or s.get("muted")]
    out.append(("CHECK", "app volumes", ", ".join(f"{s['name']} {s['volume']}%{' muted' if s['muted'] else ''}"
                                                   for s in low)) if low else ("OK", "app volumes", "all at 100%"))
    duck = state.get("ducking")
    out.append(("OK", "lowering sound during calls", "off") if duck == 3 else
               ("CHECK", "lowering sound during calls", f"{DUCKING.get(duck if duck is not None else 1)} "
                                                        "(fix turns it off)"))
    return out


# -- the commands ------------------------------------------------------------------------------------------------------
def get_state(ps=ps_json):
    return ps(with_audio(STATE_PS))


def diagnose(ps=ps_json, platform=None, now=None):
    _windows(platform)
    st = get_state(ps)
    rows = evaluate(st)
    snap = _dir() / (now or _dt.datetime.now()).strftime("diagnose_%Y%m%d_%H%M%S.json")
    snap.write_text(json.dumps(st, indent=1), encoding="utf-8")
    return {"ok": not any(r[0] == "FAIL" for r in rows), "rows": rows, "saved": str(snap),
            "next": "fieldkit laptop sound fix (shows the plan; --apply does it)"}


def fix(apply=False, ps=ps_json, platform=None, now=None):
    _windows(platform)
    st = get_state(ps)
    plan = [("master volume", f"{st.get('volume')}% -> 100%"), ("muted", f"{'yes' if st.get('muted') else 'no'} -> no"),
            ("app volumes", "all -> 100%"),
            ("lowering sound during calls", f"{DUCKING.get(st.get('ducking') if st.get('ducking') is not None else 1)} "
                                            "-> do nothing")]
    if not apply:
        return {"ok": True, "status": "plan", "plan": plan, "next": "fieldkit laptop sound fix --apply"}
    undo = _dir() / (now or _dt.datetime.now()).strftime("undo_%Y%m%d_%H%M%S.json")
    undo.write_text(json.dumps({k: st.get(k) for k in ("volume", "muted", "ducking", "sessions")}, indent=1),
                    encoding="utf-8")
    ps(with_audio(FIX_PS))
    after = get_state(ps)
    ok = after.get("volume") == 100 and not after.get("muted") and after.get("ducking") == 3
    return {"ok": ok, "status": "fixed" if ok else "not-fixed", "plan": plan, "undo": str(undo),
            "after": {k: after.get(k) for k in ("volume", "muted", "ducking")},
            "next": "fieldkit laptop sound undo puts it back" if ok else "fieldkit laptop sound diagnose"}


def undo(ps=ps_json, platform=None):
    _windows(platform)
    files = sorted(_dir().glob("undo_*.json"))
    if not files:
        return {"ok": True, "status": "nothing", "next": "nothing to undo"}
    old = json.loads(files[-1].read_text(encoding="utf-8"))
    duck = old.get("ducking")
    duck_ps = ("Remove-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Multimedia\\Audio' -Name UserDuckingPreference "
               "-ErrorAction SilentlyContinue" if duck is None else
               "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Multimedia\\Audio' -Name UserDuckingPreference "
               f"-Value {int(duck)} -Type DWord")
    ps(with_audio(f"[FieldkitAudio.Audio]::SetVolume({(old.get('volume') or 0) / 100.0})\n"
                  f"[FieldkitAudio.Audio]::SetMute(${'true' if old.get('muted') else 'false'})\n{duck_ps}\n"
                  "[ordered]@{ ok = $true } | ConvertTo-Json -Compress"))
    files[-1].rename(files[-1].with_suffix(".undone"))
    return {"ok": True, "status": "undone", "volume": old.get("volume"), "ducking": DUCKING.get(duck if duck is not None
                                                                                                 else 1),
            "next": "done"}


def launch_script(exe, arguments=""):
    """Start a program on the signed-in person's real desktop: a one-off scheduled task, interactive principal."""
    args = f" -Argument {q(arguments)}" if arguments else ""
    return ("$name = 'FieldkitOpen_' + [guid]::NewGuid().ToString('N').Substring(0, 8)\n"
            f"$act = New-ScheduledTaskAction -Execute {q(exe)}{args}\n"
            "$pr = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) "
            "-LogonType Interactive -RunLevel Limited\n"
            "Register-ScheduledTask -TaskName $name -Action $act -Principal $pr -Force | Out-Null\n"
            "Start-ScheduledTask -TaskName $name; Start-Sleep -Seconds 4\n"
            "$info = Get-ScheduledTaskInfo -TaskName $name; Unregister-ScheduledTask -TaskName $name -Confirm:$false\n"
            "[ordered]@{ task = $name; last_result = [int64]$info.LastTaskResult } | ConvertTo-Json -Compress")


def open_window(what, ps=ps_json, platform=None):
    _windows(platform)
    if what == "maker":
        app = (settings.local_settings().get("laptop") or {}).get("sound_app")
        if not app:
            return {"ok": False, "status": "ask", "next": 'name the maker\'s sound app: {"laptop": {"sound_app": '
                                                           '"C:\\\\...\\\\App.exe"}} in fieldkit.local.json'}
        exe, arguments = app, ""
    elif what in OPEN:
        exe, arguments = OPEN[what]
    else:
        raise ValueError(f"open {what!r}: sound, devices, settings or maker")
    if not Path(exe).is_file():
        return {"ok": False, "status": "missing", "next": f"not on this machine: {exe}"}
    r = ps(launch_script(exe, arguments), timeout=60)
    return {"ok": True, "status": "started", "result": r.get("last_result"),
            "next": "look at the screen: from an agent's hidden desktop the window cannot be seen; ask the person"}


def drivers(ps=ps_json, platform=None, fetch=None):
    _windows(platform)
    st = get_state(ps)
    codec = (st.get("codec") or [None])[0]
    if not codec:
        return {"ok": False, "status": "no-chip", "next": "no HD Audio sound chip found"}
    ids = parse_hwid(codec.get("hwid") or "")
    out = {"ok": True, "installed": codec.get("version"), "inf": codec.get("inf"), "hardware_id": ids and ids["match"],
           "windows": windows_label(st.get("build") or 0), "catalog": []}
    if ids:
        try:
            page = (fetch or (lambda u: urllib.request.urlopen(urllib.request.Request(
                u, headers={"User-Agent": "Mozilla/5.0 Fieldkit"}), timeout=60).read()))(
                CATALOG + urllib.parse.quote(ids["search"]))
            rows = parse_catalog(page.decode("utf-8", "replace") if isinstance(page, bytes) else page)
            fit = [r for r in rows if out["windows"].lower() in r["products"].lower()]
            out["catalog"] = sorted(fit, key=lambda r: version_key(r["version"]), reverse=True)[:10]
        except Exception as e:
            out["catalog_error"] = str(e)[:160]
    newer = [r for r in out["catalog"] if version_key(r["version"]) > version_key(codec.get("version"))]
    out["newer"] = newer[:3]
    out["next"] = (f"a newer driver exists ({newer[0]['version']}): install it from the Microsoft Update Catalog page "
                   "for this exact hardware ID, after a restore point" if newer else "the newest listed is installed")
    return out


# -- the master volume, on both systems ---------------------------------------------------------------------------------
def volume(set_to=None, platform=None, ps=ps_json, run=subprocess.run, which=shutil.which):
    """-> {percent, muted} after setting it (set_to 0-100) when asked."""
    if (platform or sys.platform) == "win32":
        body = (f"[FieldkitAudio.Audio]::SetMute($false); [FieldkitAudio.Audio]::SetVolume({min(100, int(set_to)) / 100.0})\n"
                if set_to is not None else "")
        r = ps(with_audio(body + "[ordered]@{ percent = [int][math]::Round([FieldkitAudio.Audio]::GetVolume() * 100); "
                                 "muted = [FieldkitAudio.Audio]::GetMute() } | ConvertTo-Json -Compress"))
        return {"ok": True, "percent": r["percent"], "muted": bool(r["muted"])}
    if which("wpctl"):
        if set_to is not None:
            run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], capture_output=True)
            run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{min(100, int(set_to))}%"], capture_output=True)
        out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True).stdout or ""
        m = re.search(r"Volume:\s*([\d.]+)", out)
        return {"ok": bool(m), "percent": round(float(m.group(1)) * 100) if m else None, "muted": "MUTED" in out}
    if which("pactl"):
        if set_to is not None:
            run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], capture_output=True)
            run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{min(100, int(set_to))}%"], capture_output=True)
        v = run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], capture_output=True, text=True).stdout or ""
        mu = run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], capture_output=True, text=True).stdout or ""
        m = re.search(r"(\d+)%", v)
        return {"ok": bool(m), "percent": int(m.group(1)) if m else None, "muted": "yes" in mu.lower()}
    return {"ok": False, "percent": None, "muted": None, "next": "no wpctl or pactl on this machine"}


def lines(r):
    out = [f"  {s:<5} {w:<30} {d}" for s, w, d in r.get("rows", [])]
    for w, d in r.get("plan", []):
        out.append(f"  PLAN  {w:<30} {d}")
    if r.get("installed"):
        out.append(f"installed: {r['installed']} ({r['inf']}); hardware ID {r['hardware_id']}; {r['windows']}")
        out += [f"  catalog: {c['version']:<14} {c['date']:<11} {c['title'][:60]}" for c in r.get("catalog", [])]
        if r.get("catalog_error"):
            out.append(f"  catalog not reachable: {r['catalog_error']}")
    if "percent" in r:
        out.append(f"volume {r['percent']}%{' (muted)' if r.get('muted') else ''}")
    for k in ("saved", "undo"):
        if r.get(k):
            out.append(f"{k}: {r[k]}")
    return out + [f"NEXT: {r['next']}"] if r.get("next") else out
