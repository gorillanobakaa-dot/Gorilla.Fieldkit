"""gpudecode - prove whether a browser really decodes video on the GPU's video engine (Windows).

Built 2026-10-22. The proof uses Microsoft's own tools: the Windows kernel's graphics scheduler
records every piece of GPU work (ETW, recorded with wpr.exe, which ships with Windows), and the
Windows Performance Analyzer exporter (wpaexporter.exe, Windows ADK, Windows Performance Toolkit)
turns the trace into one CSV row per GPU work packet. A browser that decodes in hardware sends
work to a VideoDecode engine, about one packet per video frame; one that decodes in software
does not. Procedure: skill fieldkit-gpudecode.

  gpudecode.py doctor [--json]                 GPUs and drivers, the no-driver trap, which methods work here
  gpudecode.py engines [--save FILE] [--json]  engine number -> type (3D, VideoDecode, ...) per GPU
  gpudecode.py live [--seconds 30] [--process firefox] [--json]
                                               per-process engine use from the built-in counters (no admin)
  gpudecode.py trace [--seconds 30] [--out DIR] --yes
                                               one UAC prompt: record GPU work with wpr, export, verdict
  gpudecode.py read ETL [--out DIR]            a .etl recorded by hand -> export + verdict + RESULTS.md
  gpudecode.py export ETL [--out DIR]          an existing .etl -> packets CSV (needs the toolkit)
  gpudecode.py verdict CSV --engines FILE [--process firefox] [--json]
                                               the answer: VideoDecode packets per process, rate, timing
  gpudecode.py adk plan | adk install --yes    the Windows Performance Toolkit only (download, signature, UAC)
  gpudecode.py explain                         the whole procedure, step by step, for a person or a small model

The engine map matters: WPA numbers the engines of a GPU from 1 (its A/N/E column, "node"), the
Windows counters from 0. Node N is counter engine N-1. Measured 2026-10-22 on Intel Iris Xe:
Firefox's packets went to nodes 1, 2 and 4 = 3D, VideoDecode and VideoProcessing.

Trace size: about 40 MB per requested second (30 s = 1.2 GB; wpr stops ~24 s after; measured). Never convert
a whole trace to XML with tracerpt: 30 s of trace became more than 43 GB of XML before it was stopped.

Doors: none in doctor/engines/live/export/verdict (no network, no install). trace needs one UAC
click (wpr records the whole machine's GPU work, kept on this computer). adk install downloads
Microsoft's adksetup.exe, refuses it unless its Authenticode signature is valid and Microsoft's,
and installs only the Windows Performance Toolkit with CEIP off. WPA can fetch symbols from
Microsoft's symbol server: this tool never asks for symbols.

Exit codes: 0 ok (verdict: hardware decoding seen), 1 failure, 2 usage, 3 verdict: not seen / not ready.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = HERE / "gpu-packets.wpaProfile"
ADK_URL = "https://go.microsoft.com/fwlink/?linkid=2289980"   # adksetup.exe (checked 2026-10-21)
ADK_FEATURE = "OptionId.WindowsPerformanceToolkit"
BYTES_PER_SECOND = 40 << 20      # per requested second: 30 s gave 1.2 GB (2026-10-22, working trace)
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
VIDEO_ENGINE = "VideoDecode"
MIN_TRACE_PACKETS = 100        # fewer in a whole trace = an idle screen (measured: 2 packets, 68 vsyncs in 30 s), never a "no"
SOFTWARE = "software (CPU)"     # Windows' software renderer: every node of it is the CPU
INSTANCE_RE = re.compile(
    r"pid_(?P<pid>\d+)_luid_(?P<luid>0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)_phys_(?P<phys>\d+)"
    r"_eng_(?P<eng>\d+)_engtype_(?P<type>[A-Za-z0-9]*)")


def out(obj, as_json, text):
    if as_json:
        print(json.dumps(obj, indent=2))
    else:
        print(text)


def run(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


def powershell(script, timeout=120):
    return run([str(SYSTEM32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"), "-NoProfile",
                "-NonInteractive", "-Command", script], timeout)


# ── engines ─────────────────────────────────────────────────────────────────

def parse_engine_instances(lines):
    """Counter instance names -> {luid: {engine_index: type}}. A blank type is kept as ''."""
    gpus = {}
    for line in lines:
        m = INSTANCE_RE.search(line)
        if not m:
            continue
        eng = gpus.setdefault(m["luid"], {})
        idx = int(m["eng"])
        # one engine can be listed blank for one process and typed for another
        if m["type"] or idx not in eng:
            eng[idx] = m["type"]
    return gpus


def video_gpus(gpus):
    """The GPUs that have a video decode engine. Windows' software renderer lists only 3D."""
    return {l: e for l, e in gpus.items() if VIDEO_ENGINE in e.values()}


def read_engines():
    code, so, se = run([str(SYSTEM32 / "typeperf.exe"), "-qx", "GPU Engine"], timeout=60)
    if code != 0:
        return {}
    return parse_engine_instances(so.splitlines())


def cmd_engines(a):
    gpus = read_engines()
    data = {"gpus": {l: {str(k): v for k, v in sorted(e.items())} for l, e in gpus.items()},
            "note": "WPA node N = engine N-1"}
    if a.save:
        Path(a.save).write_text(json.dumps(data, indent=2), encoding="utf-8")
    lines = []
    for luid, eng in gpus.items():
        kind = "has a video decoder" if VIDEO_ENGINE in eng.values() else "no video decoder (software renderer?)"
        lines.append(f"GPU {luid}: {kind}")
        for idx in sorted(eng):
            lines.append(f"  engine {idx:2d} = WPA node {idx + 1:2d}: {eng[idx] or '(unnamed)'}")
    if not gpus:
        lines.append("no GPU Engine counters: the driver is older than WDDM 2.0 or none is installed "
                     "(the trace method still works)")
    out(data, a.json, "\n".join(lines))
    return 0 if gpus else 3


# ── doctor ──────────────────────────────────────────────────────────────────

def toolkit_dir():
    """The Windows Performance Toolkit folder, from the Windows Kits registry root."""
    candidates = []
    try:
        import winreg
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                   r"SOFTWARE\Microsoft\Windows Kits\Installed Roots", 0,
                                   winreg.KEY_READ | view)
                root, _ = winreg.QueryValueEx(k, "KitsRoot10")
                candidates.append(Path(root) / "Windows Performance Toolkit")
            except OSError:
                pass
    except ImportError:
        pass
    candidates.append(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
                      / "Windows Kits" / "10" / "Windows Performance Toolkit")
    for c in candidates:
        if (c / "wpaexporter.exe").is_file():
            return c
    return None


def gpu_adapters():
    code, so, _ = powershell("Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,"
                             "Status,AdapterCompatibility,PNPDeviceID | ConvertTo-Json")
    if code != 0 or not so.strip():
        return []
    data = json.loads(so)
    return data if isinstance(data, list) else [data]


def adapter_findings(adapters):
    """What is wrong with the display adapters for hardware decoding, in plain words."""
    findings = []
    for ad in adapters:
        name = ad.get("Name") or ""
        if "Basic Display" in name:
            findings.append(f"{name}: Windows' fallback display driver - NO hardware video decoding is possible "
                            "until the GPU maker's driver is installed")
        elif ad.get("Status") not in (None, "OK"):
            findings.append(f"{name}: device status {ad.get('Status')}")
    return findings


def find_browsers():
    """firefox.exe one level under Program Files and the home folder (Gorilla installs there), plus
    any running copy. Measured 2026-10-22: Gorilla 157 lives in ~/Gorilla Unleashed/."""
    found = set()
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), str(Path.home())):
        if not base or not Path(base).is_dir():
            continue
        try:
            for d in Path(base).iterdir():
                exe = d / "firefox.exe"
                if exe.is_file():
                    found.add(str(exe))
        except OSError:
            pass
    code, so, _ = powershell("Get-Process firefox -ErrorAction SilentlyContinue | "
                             "Select-Object -ExpandProperty Path -Unique", timeout=60)
    found.update(l.strip() for l in so.splitlines() if l.strip().lower().endswith("firefox.exe"))
    return sorted(found)


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def doctor():
    adapters = gpu_adapters()
    gpus = read_engines()
    tk = toolkit_dir()
    free = shutil.disk_usage(tempfile.gettempdir()).free
    d = {
        "windows": sys.getwindowsversion().build if hasattr(sys, "getwindowsversion") else None,
        "adapters": [{k: a.get(k) for k in ("Name", "DriverVersion", "Status", "AdapterCompatibility")}
                     for a in adapters],
        "adapter_findings": adapter_findings(adapters),
        "counters_video_decode": bool(video_gpus(gpus)),
        "wpr": (SYSTEM32 / "wpr.exe").is_file(),
        "toolkit": str(tk) if tk else None,
        "free_bytes": free,
        "admin": is_admin(),
        "browsers": find_browsers(),
    }
    methods = []
    if d["counters_video_decode"]:
        methods.append("live (built-in counters, no admin)")
    if d["wpr"]:
        methods.append("trace (wpr + one UAC prompt)" + ("" if tk else " - record only; export needs the toolkit"))
    if tk:
        methods.append("export/verdict (wpaexporter)")
    d["methods"] = methods
    d["ready"] = bool(d["wpr"] and tk and not d["adapter_findings"])
    return d


def cmd_doctor(a):
    d = doctor()
    lines = [f"Windows build: {d['windows']}"]
    for ad in d["adapters"]:
        lines.append(f"GPU: {ad['Name']}  driver {ad['DriverVersion']}  status {ad['Status']}")
    for f in d["adapter_findings"]:
        lines.append(f"PROBLEM: {f}")
    lines.append(f"Video-engine counters: {'yes' if d['counters_video_decode'] else 'NO (older driver or none; use trace)'}")
    lines.append(f"wpr.exe (records the trace, ships with Windows): {'yes' if d['wpr'] else 'MISSING'}")
    lines.append(f"Windows Performance Toolkit (WPA + wpaexporter): {d['toolkit'] or 'not installed (gpudecode adk plan)'}")
    lines.append(f"Free space for a trace: {d['free_bytes'] // (1 << 30)} GB (30 s needs about 1.2 GB)")
    lines.append(f"Browsers found: {', '.join(d['browsers']) or 'none in Program Files'}")
    lines.append(f"Methods that work here: {'; '.join(d['methods']) or 'none'}")
    lines.append("VERDICT: READY for trace + verdict" if d["ready"] else
                 "VERDICT: NOT READY - fix the PROBLEM lines or install the toolkit (gpudecode adk plan)")
    out(d, a.json, "\n".join(lines))
    return 0 if d["ready"] else 3


# ── live counters ───────────────────────────────────────────────────────────

def parse_typeperf_csv(text):
    """typeperf -f CSV output -> {(pid, luid, eng, type): [samples]}."""
    rows = list(csv.reader(text.splitlines()))
    rows = [r for r in rows if r]
    if not rows:
        return {}
    header = rows[0]
    keys = []
    for h in header[1:]:
        m = INSTANCE_RE.search(h)
        keys.append((int(m["pid"]), m["luid"], int(m["eng"]), m["type"]) if m else None)
    series = {}
    for r in rows[1:]:
        for k, v in zip(keys, r[1:]):
            if k is None:
                continue
            try:
                series.setdefault(k, []).append(float(v))
            except ValueError:
                pass
    return series


def process_names():
    code, so, _ = run([str(SYSTEM32 / "tasklist.exe"), "/FO", "CSV", "/NH"], timeout=60)
    names = {}
    for r in csv.reader(so.splitlines()):
        if len(r) >= 2 and r[1].isdigit():
            names[int(r[1])] = r[0]
    return names


def summarize_live(series, names, process):
    """Per process and engine type: average and peak utilization %, for processes matching."""
    agg = {}
    for (pid, luid, eng, typ), samples in series.items():
        name = names.get(pid, f"pid {pid}")
        if process and process.lower() not in name.lower():
            continue
        k = (name, pid, typ or f"engine {eng}")
        a = agg.setdefault(k, [0.0, 0.0, 0])
        a[0] += sum(samples)
        a[1] = max(a[1], max(samples) if samples else 0.0)
        a[2] = max(a[2], len(samples))
    rows = [{"process": n, "pid": p, "engine": t, "avg": round(s / c, 2) if c else 0.0, "peak": round(pk, 2)}
            for (n, p, t), (s, pk, c) in agg.items()]
    rows.sort(key=lambda r: (-r["avg"], r["process"]))
    return rows


def cmd_live(a):
    tmp = Path(tempfile.mkdtemp(prefix="gpudecode-"))
    csvp = tmp / "live.csv"
    print(f"Sampling the GPU engine counters for {a.seconds} s. Play the video NOW.", flush=True)
    code, so, se = run([str(SYSTEM32 / "typeperf.exe"), r"\GPU Engine(*)\Utilization Percentage",
                        "-si", "1", "-sc", str(a.seconds), "-f", "CSV", "-o", str(csvp), "-y"],
                       timeout=a.seconds + 120)
    if code != 0 or not csvp.is_file():
        print(f"typeperf failed ({code}): {se.strip() or so.strip()[-300:]}", file=sys.stderr)
        return 1
    rows = summarize_live(parse_typeperf_csv(csvp.read_text(encoding="utf-8", errors="replace")),
                          process_names(), a.process)
    decode = [r for r in rows if r["engine"] == VIDEO_ENGINE and r["avg"] > 0]
    verdict = ("HARDWARE DECODE SEEN: " + ", ".join(f"{r['process']} {r['pid']} VideoDecode avg {r['avg']}%"
                                                    for r in decode)) if decode else \
        f"NOT SEEN: no {a.process} process used a VideoDecode engine in {a.seconds} s"
    text = [f"{r['process']:<16} {r['pid']:>6}  {r['engine']:<16} avg {r['avg']:6.2f}%  peak {r['peak']:6.2f}%"
            for r in rows if r["avg"] > 0 or r["peak"] > 0][:30]
    out({"rows": rows, "verdict": verdict}, a.json, "\n".join(text + [f"VERDICT: {verdict}"]))
    return 0 if decode else 3


# ── verdict from the packet export ──────────────────────────────────────────

def num(s):
    return float(str(s).replace(",", "").strip() or 0)


def read_packets(path):
    """wpaexporter CSV of the gpu-packets profile -> list of packet dicts."""
    packets = []
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        for r in csv.DictReader(f):
            ane = (r.get("A/N/E") or "").split("/")
            if len(ane) != 3 or not all(x.isdigit() for x in ane):
                continue
            packets.append({
                "adapter": r.get("Adapter", ""),
                "process": r.get("Process", ""),
                "node": int(ane[1]),
                "count": int(num(r.get("Count", 1))),
                "gpu_us": num(r.get("GPU Time (µs)") or r.get("GPU Time (us)") or 0),
                "start": num(r.get("Start Execution (s)", 0)),
            })
    return packets


def engine_map_for(engines, adapter_names):
    """{adapter name: {node: type}}. Several GPUs with a video decoder cannot be told apart from the
    counters alone; then every such adapter gets None and the verdict says so."""
    vg = video_gpus({l: {int(k): v for k, v in e.items()} for l, e in engines.get("gpus", {}).items()})
    out_map = {}
    for name in adapter_names:
        if "Basic Render" in name:
            # Windows' software renderer (WARP): drawing on the CPU, never a video engine
            out_map[name] = SOFTWARE
        elif len(vg) == 1:
            out_map[name] = {i + 1: t for i, t in next(iter(vg.values())).items()}
        else:
            out_map[name] = None
    return out_map



def judge(packets, engines, process="firefox"):
    adapters = sorted({p["adapter"] for p in packets})
    emap = engine_map_for(engines, adapters)
    groups = {}
    for p in packets:
        if process and process.lower() not in p["process"].lower():
            continue
        m = emap.get(p["adapter"])
        if m is None:
            typ = "?"
        elif m == SOFTWARE:
            typ = SOFTWARE
        else:
            typ = m.get(p["node"]) or "(unnamed)"
        k = (p["adapter"], p["process"], p["node"], typ)
        g = groups.setdefault(k, {"packets": 0, "gpu_s": 0.0, "first": None, "last": None, "bins": {}})
        g["packets"] += p["count"]
        sec = int(p["start"])
        g["bins"][sec] = g["bins"].get(sec, 0) + p["count"]
        g["gpu_s"] += p["gpu_us"] / 1e6
        g["first"] = p["start"] if g["first"] is None else min(g["first"], p["start"])
        g["last"] = p["start"] if g["last"] is None else max(g["last"], p["start"])
    rows = []
    for (adapter, proc, node, typ), g in groups.items():
        # The steady rate is the median packets per whole second, over the seconds with work: start-up,
        # buffering and pauses dilute an average (measured: 53.8 average, 60.0 steady, a 60 fps clip).
        busy = sorted(g["bins"].values())
        rows.append({"adapter": adapter, "process": proc, "node": node, "engine": typ,
                     "packets": g["packets"], "gpu_seconds": round(g["gpu_s"], 3),
                     "first_s": round(g["first"], 3), "last_s": round(g["last"], 3),
                     "per_second": busy[len(busy) // 2] if len(busy) >= 3 else None,
                     "busy_seconds": len(busy)})
    rows.sort(key=lambda r: (r["process"], r["node"]))
    decode = [r for r in rows if r["engine"] == VIDEO_ENGINE]
    unknown = [r for r in rows if r["engine"] == "?"]
    if decode:
        best = max(decode, key=lambda r: r["packets"])
        verdict = (f"HARDWARE DECODE: {best['process']} sent {best['packets']} packets to the VideoDecode engine "
                   f"(WPA node {best['node']}) from {best['first_s']} s to {best['last_s']} s, "
                   f"steady {best['per_second']} per second over {best['busy_seconds']} s - one per video frame")
        code = 0
    elif sum(p["count"] for p in packets) < MIN_TRACE_PACKETS:
        verdict = (f"INCOMPLETE TRACE: only {sum(p['count'] for p in packets)} GPU packets in the whole recording "
                   "(a playing video makes hundreds a second): the screen was idle - the video was paused, or "
                   "the browser window was hidden or minimized (it then stops drawing). Play the video in a "
                   "VISIBLE window and record again")
        code = 1
    elif unknown:
        verdict = ("UNKNOWN: engine names could not be matched to WPA nodes (no engine map, or two GPUs with "
                   "video engines); run 'gpudecode engines --save' on the machine that recorded the trace")
        code = 3
    else:
        verdict = (f"NO HARDWARE DECODE: no {process or 'process'} packets reached a VideoDecode engine "
                   "(software decoding, or the video was not playing during the recording)")
        code = 3
    return {"rows": rows, "verdict": verdict}, code


def cmd_verdict(a):
    engines = json.loads(Path(a.engines).read_text(encoding="utf-8"))
    result, code = judge(read_packets(a.csv), engines, a.process)
    lines = ["process                  node engine            packets  GPU s    first s   last s   per s"]
    for r in result["rows"]:
        lines.append(f"{r['process'][:24]:<24} {r['node']:>4} {str(r['engine'])[:16]:<16} {r['packets']:>8} "
                     f"{r['gpu_seconds']:>7} {r['first_s']:>9} {r['last_s']:>8} {r['per_second']}")
    lines.append(f"VERDICT: {result['verdict']}")
    out(result, a.json, "\n".join(lines))
    return code


# ── export and trace ────────────────────────────────────────────────────────

def export(etl, outdir):
    tk = toolkit_dir()
    if not tk:
        raise SystemExit("the Windows Performance Toolkit is not installed: gpudecode adk plan")
    outdir.mkdir(parents=True, exist_ok=True)
    code, so, se = run([str(tk / "wpaexporter.exe"), "-i", str(etl), "-profile", str(PROFILE),
                        "-outputfolder", str(outdir)], timeout=1800)
    csvs = sorted(outdir.glob("GPU_Utilization*.csv"))
    if not csvs:
        raise SystemExit(f"wpaexporter wrote no GPU table ({code}): {(se or so).strip()[-400:]}")
    return csvs[0]


def cmd_export(a):
    etl = Path(a.etl)
    outdir = Path(a.out) if a.out else etl.with_suffix("")
    print(export(etl, outdir))
    return 0


def trace_script(etl, seconds, marker):
    """The PowerShell script run elevated. Refuses if another trace is running (never cancels it)."""
    wpr = SYSTEM32 / "wpr.exe"
    return "\r\n".join([
        "$ErrorActionPreference = 'Continue'",
        f"$status = & '{wpr}' -status 2>&1 | Out-String",
        "if ($status -match 'WPR is recording') {",
        f"  'BUSY another trace is already running; nothing was started' | Out-File -Encoding utf8 '{marker}'",
        "  exit 3",
        "}",
        # Memory mode, the exact command proven by hand (2026-10-22: 30 s, 3,220 VideoDecode packets).
        f"& '{wpr}' -start GPU -start Video",
        f"if ($LASTEXITCODE -ne 0) {{ \"FAILED start $LASTEXITCODE\" | Out-File -Encoding utf8 '{marker}'; exit 1 }}",
        f"Start-Sleep -Seconds {int(seconds)}",
        f"& '{wpr}' -stop '{etl}'",
        f"\"DONE $LASTEXITCODE\" | Out-File -Encoding utf8 '{marker}'",
    ]) + "\r\n"


def cmd_trace(a):
    outdir = Path(a.out or Path.home() / "Desktop" / time.strftime("gpudecode-%Y%m%d-%H%M%S")).resolve()
    need = BYTES_PER_SECOND * a.seconds * 2
    free = shutil.disk_usage(outdir.anchor or ".").free
    if free < need:
        print(f"refused: {free >> 20} MB free, {need >> 20} MB needed for {a.seconds} s", file=sys.stderr)
        return 1
    if not a.yes:
        print(f"This records ALL GPU work on this computer for {a.seconds} s (about {a.seconds * 40} MB), "
              "after one Windows admin prompt (UAC). Re-run with --yes to start.")
        return 2
    outdir.mkdir(parents=True, exist_ok=True)
    etl, marker, ps1 = outdir / "gpu.etl", outdir / "trace.done", outdir / "trace.ps1"
    (outdir / "engines.json").write_text(json.dumps(
        {"gpus": {l: {str(k): v for k, v in e.items()} for l, e in read_engines().items()},
         "note": "WPA node N = engine N-1"}, indent=2), encoding="utf-8")
    ps1.write_text(trace_script(etl, a.seconds, marker), encoding="utf-8-sig")
    print(f"Click YES on the Windows prompt, then play the video. Recording {a.seconds} s.", flush=True)
    # A normal, visible admin window, like the run that worked by hand. Measured 2026-10-22: launched
    # minimized with -NoProfile, three 30 s traces held 2-5 GPU packets while the counters showed
    # Firefox's VideoDecode engine busy; the same commands typed into an admin window held 3,220.
    code, so, se = powershell(f"Start-Process -FilePath powershell.exe -Verb RunAs -Wait "
                              f"-ArgumentList '-ExecutionPolicy','Bypass','-File','\"{ps1}\"'",
                              timeout=a.seconds + 600)
    status = marker.read_text(encoding="utf-8-sig", errors="replace").strip() if marker.is_file() else ""
    if not status.startswith("DONE 0") or not etl.is_file():
        print(f"trace failed: {status or 'no result (the admin prompt was refused?)'} {se.strip()}", file=sys.stderr)
        return 1
    print(f"trace: {etl} ({etl.stat().st_size >> 20} MB)")
    if not toolkit_dir():
        print("VERDICT: NOT READY - recorded, but reading it needs the toolkit: gpudecode adk plan")
        return 3
    return read_and_judge(etl, outdir, a.process, a.seconds)


def manual_command(etl, seconds):
    """The command a person pastes into an administrator PowerShell: the method proven by hand."""
    return (f"cd $HOME; C:\\Windows\\System32\\wpr.exe -start GPU -start Video; Start-Sleep -Seconds {int(seconds)}; "
            f"C:\\Windows\\System32\\wpr.exe -stop \"{etl}\"")


def read_and_judge(etl, outdir, process, seconds=None):
    engines_file = outdir / "engines.json"
    if not engines_file.is_file():
        engines_file.write_text(json.dumps(
            {"gpus": {l: {str(k): v for k, v in e.items()} for l, e in read_engines().items()},
             "note": "WPA node N = engine N-1"}, indent=2), encoding="utf-8")
    csvp = export(etl, outdir / "export")
    result, code = judge(read_packets(csvp), json.loads(engines_file.read_text(encoding="utf-8")), process)
    (outdir / "verdict.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    write_results_md(outdir, result, seconds)
    print(f"VERDICT: {result['verdict']}\nresults: {outdir / 'RESULTS.md'}")
    if result["verdict"].startswith("INCOMPLETE"):
        print("If the video WAS playing in a visible window, record by hand instead: open PowerShell as "
              "administrator, paste the line below, then run: gpudecode read FILE\n"
              + manual_command(Path.home() / "Desktop" / "gpu-30s.etl", seconds or 30))
    return code


def cmd_read(a):
    etl = Path(a.etl).resolve()
    if not etl.is_file():
        print(f"no such trace: {etl}", file=sys.stderr)
        return 1
    outdir = Path(a.out).resolve() if a.out else etl.with_name(etl.stem + "-gpudecode")
    outdir.mkdir(parents=True, exist_ok=True)
    return read_and_judge(etl, outdir, a.process)


def write_results_md(outdir, result, seconds):
    lines = ["# GPU video decoding check", "",
             (f"Recorded {seconds} s with wpr.exe (Windows)" if seconds else "Recorded with wpr.exe (Windows)")
             + ", read with wpaexporter.exe (Windows Performance Toolkit).",
             "", f"**VERDICT:** {result['verdict']}", "",
             "| process | WPA node | engine | packets | GPU seconds | per second |", "|---|---|---|---|---|---|"]
    for r in result["rows"]:
        lines.append(f"| {r['process']} | {r['node']} | {r['engine']} | {r['packets']} | {r['gpu_seconds']} "
                     f"| {r['per_second']} |")
    lines += ["", "Open gpu.etl in Windows Performance Analyzer to see it yourself: Video > GPU Utilization,",
              "group by Process then A/N/E. The engine numbers are in engines.json (WPA node = engine + 1)."]
    (outdir / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── the toolkit (ADK, Windows Performance Toolkit only) ─────────────────────

def adk_install_args(log):
    return ["/quiet", "/norestart", "/ceip", "off", "/features", ADK_FEATURE, "/log", str(log)]


def signature_ok(sig):
    """Get-AuthenticodeSignature JSON -> (ok, why). Valid, and signed by Microsoft Corporation."""
    status = str(sig.get("Status"))
    subject = str((sig.get("SignerCertificate") or {}).get("Subject", ""))
    if status not in ("0", "Valid"):
        return False, f"signature status {status}"
    if "O=Microsoft Corporation" not in subject:
        return False, f"signed by {subject or 'nobody'}, not Microsoft"
    return True, subject


def cmd_adk(a):
    if a.action == "plan":
        tk = toolkit_dir()
        print("\n".join([
            f"Installed now: {tk or 'no'}",
            f"Download: {ADK_URL} (Microsoft's ADK online installer, adksetup.exe, a few MB)",
            f"Installs: only {ADK_FEATURE} (WPA, wpaexporter, xperf), about 450 MB on disk",
            "Checks: Authenticode signature must be Valid and signed by Microsoft Corporation, else refused",
            "Runs: adksetup.exe " + " ".join(adk_install_args("<log>")) + "  (one UAC prompt; CEIP off)",
            "Doors: the download and the installer's own download from Microsoft, once, when you run it.",
            "Next: gpudecode adk install --yes",
        ]))
        return 0
    if not a.yes:
        print("refused without --yes: this downloads from Microsoft and installs as administrator")
        return 2
    work = Path(tempfile.mkdtemp(prefix="gpudecode-adk-"))
    setup = work / "adksetup.exe"
    with urllib.request.urlopen(ADK_URL, timeout=120) as r, open(setup, "wb") as f:
        shutil.copyfileobj(r, f)
    digest = hashlib.sha256(setup.read_bytes()).hexdigest()
    code, so, _ = powershell(f"Get-AuthenticodeSignature -LiteralPath '{setup}' | "
                             "Select-Object Status,@{n='SignerCertificate';e={@{Subject=$_.SignerCertificate.Subject}}} "
                             "| ConvertTo-Json")
    ok, why = signature_ok(json.loads(so) if so.strip() else {})
    print(f"adksetup.exe sha256 {digest}; signature: {why}")
    if not ok:
        print("refused: the installer is not validly signed by Microsoft; nothing was run", file=sys.stderr)
        return 1
    log = work / "adksetup.log"
    args = ",".join(f"'{x}'" for x in adk_install_args(log))
    code, so, se = powershell(f"$p = Start-Process -FilePath '{setup}' -ArgumentList {args} -Verb RunAs -Wait "
                              "-PassThru; exit $p.ExitCode", timeout=3600)
    tk = toolkit_dir()
    print(f"installer exit {code}; toolkit: {tk or 'NOT found'}; log {log}")
    return 0 if tk else 1


# ── explain ─────────────────────────────────────────────────────────────────

EXPLAIN = """How to prove that a browser decodes video on the GPU (Windows). Run each line, read its VERDICT.

1. python -I gpudecode.py doctor
   - PROBLEM "Basic Display": stop. Install the GPU maker's driver first; no hardware decoding is possible.
   - "toolkit: not installed": go to step 2. Otherwise go to step 3.
2. python -I gpudecode.py adk plan          (read it to the person)
   python -I gpudecode.py adk install --yes (only with the person's yes; one Windows admin prompt)
3. Open the browser on an H.264 video (1080p). Old Intel GPUs (HD 4000 and older) decode ONLY H.264
   in hardware: VP9 and AV1 are always software there, so a VP9 YouTube video proves nothing.
4. python -I gpudecode.py trace --seconds 30 --yes
   - The person clicks YES on the Windows prompt and plays the video during the 30 seconds.
5. Read the VERDICT line:
   - HARDWARE DECODE: the browser sent work to the GPU's video decoder, about one packet per frame. Done.
   - NO HARDWARE DECODE: software decoding, or the video was not playing. Check it was H.264 and playing.
   - UNKNOWN: two GPUs; say so and give the person the RESULTS.md file.
6. To see it with their own eyes: open gpu.etl in Windows Performance Analyzer (wpa.exe), Video >
   GPU Utilization, group by Process then A/N/E. The browser's row on the VideoDecode node is the proof.
Quick check without admin (newer GPUs only): python -I gpudecode.py live --seconds 20
Never convert the .etl with tracerpt to XML: it is gigabytes. Never delete the person's files.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("engines"); p.add_argument("--save"); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_engines)
    p = sub.add_parser("live"); p.add_argument("--seconds", type=int, default=30)
    p.add_argument("--process", default="firefox"); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_live)
    p = sub.add_parser("trace"); p.add_argument("--seconds", type=int, default=30); p.add_argument("--out")
    p.add_argument("--process", default="firefox"); p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_trace)
    p = sub.add_parser("export"); p.add_argument("etl"); p.add_argument("--out"); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("read"); p.add_argument("etl"); p.add_argument("--out")
    p.add_argument("--process", default="firefox"); p.set_defaults(fn=cmd_read)
    p = sub.add_parser("verdict"); p.add_argument("csv"); p.add_argument("--engines", required=True)
    p.add_argument("--process", default="firefox"); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_verdict)
    p = sub.add_parser("adk"); p.add_argument("action", choices=["plan", "install"])
    p.add_argument("--yes", action="store_true"); p.set_defaults(fn=cmd_adk)
    p = sub.add_parser("explain"); p.set_defaults(fn=lambda a: print(EXPLAIN) or 0)
    a = ap.parse_args(argv)
    if a.cmd == "trace" and not 5 <= a.seconds <= 120:
        ap.error("--seconds must be 5..120 (about 40 MB per second)")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
