"""A local model starts fast: analyse the host and the GGUF, pick the load settings, load through LM Studio (P22).

    fieldkit models tune MODEL [--context N] [--apply] [--json]   MODEL: a .gguf path, or part of a model's name in LM Studio's folder
    fieldkit models tune --host [--json]                          only the host probe
    fieldkit models tune --record MODEL --first-word S --tps T [--prompt-tokens N]    one line per run
    fieldkit models tune --report [--json]

WHY (owner 2026-11-09, idea I-24): through Gorilla.opencode a 35B MoE took 5m15s to the first word on a 16k prompt at 9.6 t/s
and llama-server held 47.6 of 64 GB for a 262k context while 18k were used. The settings were LM Studio's defaults: the
model's maximum context, all threads (efficiency cores included), nothing chosen for this machine.

The rules are data (tune.yaml); the host readers are per OS behind sys.platform; the rule functions are pure. `--apply`
runs `lms load` with only the flags the installed `lms load --help` prints (never guessed), then checks `lms ps`.
Nothing leaves the machine: no network, no telemetry. Who can act through it: LM Studio's own local server.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

DATA = Path(__file__).with_name("tune.yaml")


def load_rules(path=None):
    try:
        return yaml.safe_load(Path(path or DATA).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"tune rules unreadable: {e}") from e


# ---- host readers: text in, facts out (pure, so a test can feed them real output) ---------------------------------------

def cpu_list(text):
    """'0-3,8,10-11' -> 7 (how many CPUs a Linux cpulist names)."""
    n = 0
    for part in (text or "").strip().split(","):
        if re.fullmatch(r"\d+", part):
            n += 1
        elif m := re.fullmatch(r"(\d+)-(\d+)", part):
            n += int(m[2]) - int(m[1]) + 1
    return n


def parse_lscpu(text):
    """-> {model, logical, physical}. From `lscpu` output."""
    d = {}
    for ln in (text or "").splitlines():
        k, _, v = ln.partition(":")
        d[k.strip()] = v.strip()
    sockets = int(d.get("Socket(s)") or 1)
    per = int(d.get("Core(s) per socket") or 0)
    return {"model": d.get("Model name", "unknown"), "logical": int(d.get("CPU(s)") or 0), "physical": per * sockets or None}


def parse_efficiency_classes(buf):
    """Windows GetLogicalProcessorInformationEx(RelationProcessorCore) buffer -> {efficiency_class: cores}. Each record:
    Relationship u32, Size u32, Flags u8, EfficiencyClass u8 ...; a higher class is a faster core."""
    out, off = {}, 0
    while off + 10 <= len(buf):
        size = int.from_bytes(buf[off + 4:off + 8], "little")
        if size < 10:
            break
        cls = buf[off + 9]
        out[cls] = out.get(cls, 0) + 1
        off += size
    return out


def parse_nvidia_smi(text):
    """`nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader,nounits` -> [gpu]."""
    gpus = []
    for ln in (text or "").splitlines():
        p = [x.strip() for x in ln.split(",")]
        if len(p) >= 3 and p[1].replace(".", "", 1).isdigit():
            gpus.append({"name": p[0], "vendor": "nvidia", "vram_gb": round(float(p[1]) / 1024, 1),
                         "free_gb": round(float(p[2]) / 1024, 1), "shared": False})
    return gpus


def parse_drm(cards):
    """cards: [(vendor_id, name, vram_total_bytes or None)] from /sys/class/drm -> [gpu]. Intel = shared system memory."""
    names = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
    out = []
    for vid, name, vram in cards:
        v = names.get((vid or "").lower(), "other")
        shared = v == "intel" or not vram
        out.append({"name": name or v, "vendor": v, "vram_gb": 0.0 if shared else round(vram / 2 ** 30, 1),
                    "free_gb": None, "shared": shared})
    return out


def parse_win_video(js):
    """JSON from the Windows probe: [{Name, VramBytes}] -> [gpu]. Dedicated VRAM comes from the driver's registry
    qwMemorySize (AdapterRAM is capped at 4 GB)."""
    try:
        rows = json.loads(js) if isinstance(js, str) else js
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    out = []
    for r in rows or []:
        name = str(r.get("Name") or "")
        low = name.lower()
        vendor = "nvidia" if "nvidia" in low else "amd" if ("amd" in low or "radeon" in low) else "intel" if "intel" in low else "other"
        vram = r.get("VramBytes") or 0
        shared = vendor == "intel" or vram <= 0
        out.append({"name": name, "vendor": vendor, "vram_gb": 0.0 if shared else round(vram / 2 ** 30, 1),
                    "free_gb": None, "shared": shared})
    return out


def _run(argv, timeout=20):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, errors="replace")
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


_WIN_PS = r"""
$k='HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
$mem=@{}
Get-ChildItem $k -ErrorAction SilentlyContinue | ForEach-Object { $p=Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
  if ($p.DriverDesc -and $p.'HardwareInformation.qwMemorySize') { $mem[$p.DriverDesc]=[int64]$p.'HardwareInformation.qwMemorySize' } }
Get-CimInstance Win32_VideoController | ForEach-Object { [pscustomobject]@{Name=$_.Name; VramBytes=$(if($mem.ContainsKey($_.Name)){$mem[$_.Name]}else{0})} } | ConvertTo-Json -Compress
"""


def _cpu_linux():
    info = parse_lscpu(_run(["lscpu"]))
    logical = info["logical"] or os.cpu_count() or 1
    phys = info["physical"] or logical
    perf = eff = None
    try:
        pc = Path("/sys/devices/cpu_core/cpus")
        ec = Path("/sys/devices/cpu_atom/cpus")
        if pc.is_file() and ec.is_file():
            pl, el = cpu_list(pc.read_text()), cpu_list(ec.read_text())
            # P cores run two threads each, E cores one
            perf, eff = (pl // 2 if pl > phys - el else pl), el
            if perf + eff != phys:
                perf, eff = max(phys - el, 1), el
    except OSError:
        pass
    return {"model": info["model"], "logical": logical, "physical": phys, "performance": perf or phys, "efficiency": eff or 0}


def _cpu_windows():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    n = wintypes.DWORD(0)
    k32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(n))
    buf = ctypes.create_string_buffer(n.value)
    if not k32.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(n)):
        raise OSError("GetLogicalProcessorInformationEx failed")
    classes = parse_efficiency_classes(buf.raw[:n.value])
    total = sum(classes.values())
    perf = classes.get(max(classes), total) if classes else total
    model = os.environ.get("PROCESSOR_IDENTIFIER", "unknown")
    return {"model": model, "logical": os.cpu_count() or total, "physical": total, "performance": perf, "efficiency": total - perf}


def _cpu_mac():
    def sc(k):
        v = _run(["sysctl", "-n", k]).strip()
        return int(v) if v.isdigit() else None
    phys = sc("hw.physicalcpu") or os.cpu_count() or 1
    perf = sc("hw.perflevel0.physicalcpu") or phys
    return {"model": _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or "unknown",
            "logical": sc("hw.logicalcpu") or phys, "physical": phys, "performance": perf, "efficiency": phys - perf}


def _gpus_linux():
    g = parse_nvidia_smi(_run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"]))
    if g:
        return g
    cards = []
    for d in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        dev = d / "device"
        try:
            vid = (dev / "vendor").read_text().strip()
        except OSError:
            continue
        vram = None
        try:
            vram = int((dev / "mem_info_vram_total").read_text().strip())
        except (OSError, ValueError):
            pass
        cards.append((vid, d.name, vram))
    return parse_drm(cards)


def _gpus_mac():
    try:
        rows = json.loads(_run(["system_profiler", "SPDisplaysDataType", "-json"]) or "{}").get("SPDisplaysDataType", [])
    except ValueError:
        rows = []
    return [{"name": r.get("sppci_model", "gpu"), "vendor": "apple" if "apple" in str(r.get("sppci_model", "")).lower() else "other",
             "vram_gb": 0.0, "free_gb": None, "shared": True} for r in rows]


def _ram():
    """-> (total_gb, free_gb)."""
    if sys.platform.startswith("linux"):
        m = {}
        for ln in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = ln.partition(":")
            m[k] = int(v.split()[0]) * 1024 if v.split() else 0
        return m["MemTotal"] / 2 ** 30, m.get("MemAvailable", m.get("MemFree", 0)) / 2 ** 30
    if sys.platform == "win32":
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("tx", ctypes.c_ulonglong), ("ax", ctypes.c_ulonglong),
                        ("ex", ctypes.c_ulonglong)]
        s = MS()
        s.l = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        return s.tp / 2 ** 30, s.ap / 2 ** 30
    total = int((_run(["sysctl", "-n", "hw.memsize"]).strip() or 0))
    vm = _run(["vm_stat"])
    page = int((re.search(r"page size of (\d+)", vm) or [0, 4096])[1])
    free = sum(int((re.search(rf"Pages {k}:\s+(\d+)", vm) or [0, 0])[1]) for k in ("free", "inactive")) * page
    return total / 2 ** 30, free / 2 ** 30


def probe_host(bandwidth_gbps=None):
    """-> {os, cpu, gpus, ram_gb, ram_free_gb, bandwidth_gbps}. A reader that fails leaves its part empty and says so in notes."""
    notes = []
    try:
        cpu = _cpu_windows() if sys.platform == "win32" else _cpu_mac() if sys.platform == "darwin" else _cpu_linux()
    except Exception as e:  # noqa: BLE001
        cpu = {"model": "unknown", "logical": os.cpu_count() or 1, "physical": os.cpu_count() or 1,
               "performance": os.cpu_count() or 1, "efficiency": 0}
        notes.append(f"cpu core types unreadable ({type(e).__name__}): all cores treated as performance cores")
    try:
        gpus = parse_win_video(_run(["powershell", "-NoProfile", "-Command", _WIN_PS], 60)) if sys.platform == "win32" else \
            _gpus_mac() if sys.platform == "darwin" else _gpus_linux()
    except Exception as e:  # noqa: BLE001
        gpus = []
        notes.append(f"gpu unreadable ({type(e).__name__})")
    try:
        total, free = _ram()
    except Exception as e:  # noqa: BLE001
        total = free = 0.0
        notes.append(f"ram unreadable ({type(e).__name__})")
    if bandwidth_gbps is None:
        from . import gguf
        bandwidth_gbps = gguf.bandwidth()[0]
    return {"os": sys.platform, "cpu": cpu, "gpus": gpus, "ram_gb": round(total, 1), "ram_free_gb": round(free, 1),
            "bandwidth_gbps": round(bandwidth_gbps, 1), "notes": notes}


# ---- the rules (pure) ----------------------------------------------------------------------------------------------------

def kv_bytes_per_token(info, kv_type="f16"):
    """Bytes of KV cache per context token: 2 (K and V) x attention layers x kv heads x head size x bytes. A hybrid model
    that keeps full attention in every Nth layer only pays for those layers."""
    layers = info.get("layers") or 0
    interval = info.get("attention_interval") or 1
    attn_layers = max(layers // interval, 1) if layers else 0
    heads_kv = info.get("heads_kv") or info.get("heads") or 8
    emb, heads = info.get("embedding") or 0, info.get("heads") or 0
    head = info.get("key_length") or ((emb // heads) if emb and heads else 128)
    per = {"f16": 2.0, "q8_0": 34 / 32, "q4_0": 18 / 32}.get(kv_type, 2.0)
    return int(2 * attn_layers * heads_kv * head * per)


def pick_context(info, free_ram_gb, rules, wanted=None):
    """The job's need (default 32k), never above the model's maximum; shrunk until its f16 KV cache fits the budget."""
    c = rules["context"]
    ctx = int(wanted or c["default"])
    if info.get("context"):
        ctx = min(ctx, info["context"])
    budget = free_ram_gb * c["kv_budget_share"]
    per = kv_bytes_per_token(info, "f16")
    why = f"the job needs {ctx}" if wanted else f"default job size {ctx}, never the model maximum ({info.get('context')})"
    if per and budget * 2 ** 30 < ctx * per:
        fit = int(budget * 2 ** 30 // per)
        ctx = max(fit // c["round_to"] * c["round_to"], c["minimum"])
        why = f"shrunk to {ctx} so the KV cache fits {budget:.1f} GB"
    return ctx, why


def pick_threads(host, rules):
    cpu = host["cpu"]
    n = cpu.get("performance") if rules["threads"]["use"] == "performance_cores" else None
    return int(n or cpu.get(rules["threads"]["fallback"]) or cpu.get("logical") or 1)


def pick_gpu(host, info, weights_gb, rules):
    """-> (fraction 0..1, why). Offload only to a discrete GPU with enough VRAM; an iGPU shares system RAM."""
    g = rules["gpu"]
    best = None
    for x in host["gpus"]:
        if x.get("shared") or x["vendor"] in g["shared_memory_vendors"]:
            continue
        if x["vram_gb"] >= g["min_vram_gb"] and (best is None or x["vram_gb"] > best["vram_gb"]):
            best = x
    if not best:
        return 0.0, "no discrete GPU with enough VRAM (an integrated GPU shares system RAM: offload gives no bandwidth)"
    usable = max(best["vram_gb"] - g["vram_reserve_gb"], 0)
    frac = round(min(1.0, usable / weights_gb), 2) if weights_gb else 0.0
    return frac, f"{best['name']}: {usable:.1f} GB usable of {best['vram_gb']} GB for {weights_gb:.1f} GB of weights"


def pick_kv_type(host, weights_gb, kv_f16_gb, rules):
    k = rules["kv_cache"]
    total = host["ram_gb"] or 1
    if (weights_gb + kv_f16_gb) / total > k["tight_ram_share"]:
        return k["tight_type"], f"model + KV {weights_gb + kv_f16_gb:.1f} GB is over {k['tight_ram_share']:.0%} of {total:.0f} GB RAM"
    return k["roomy_type"], f"model + KV {weights_gb + kv_f16_gb:.1f} GB fits {total:.0f} GB RAM"


def decide(host, info, rules=None, wanted_ctx=None):
    """-> settings dict with a reason per setting and warnings. Pure: no probe, no load."""
    r = rules or load_rules()
    weights = info["tensor_bytes"] / 2 ** 30
    ctx, ctx_why = pick_context(info, host["ram_free_gb"], r, wanted_ctx)
    kv_gb = kv_bytes_per_token(info, "f16") * ctx / 2 ** 30
    kv_type, kv_why = pick_kv_type(host, weights, kv_gb, r)
    gpu, gpu_why = pick_gpu(host, info, weights, r)
    mtp = bool(r["speculative"]["mtp"] and info.get("mtp_tensors") and host["ram_free_gb"] - weights >= r["speculative"]["min_free_ram_gb"])
    kvq = kv_bytes_per_token(info, kv_type) * ctx / 2 ** 30
    warn = []
    if gpu == 0 and weights > host["ram_free_gb"]:
        warn.append(f"the weights ({weights:.1f} GB) are larger than the free RAM ({host['ram_free_gb']:.1f} GB): it will swap; close programs or pick a smaller quant")
    s = {"context": ctx, "threads": pick_threads(host, r), "gpu": gpu, "kv_cache": kv_type,
         "flash_attention": bool(r["flash_attention"]), "mtp": mtp, "ttl": int(r["ttl_seconds"]),
         "kv_gb": round(kvq, 2), "weights_gb": round(weights, 2), "expected_ram_gb": round(weights * (1 - gpu) + kvq, 1)}
    why = {"context": ctx_why, "threads": f"{s['threads']} performance cores of {host['cpu']['logical']} logical (efficiency cores slow a memory-bound decode)"
           if host["cpu"].get("efficiency") else f"{s['threads']} physical cores",
           "gpu": gpu_why, "kv_cache": kv_why,
           "flash_attention": "on: needed for a quantized KV cache and cheaper at long context",
           "mtp": (f"{info['mtp_tensors']} MTP tensors in the file" if mtp else
                   "no MTP tensors in the file" if not info.get("mtp_tensors") else "too little free RAM for a draft")}
    return {"settings": s, "why": why, "warnings": warn}


# ---- lms ---------------------------------------------------------------------------------------------------------------

def find_lms():
    w = shutil.which("lms")
    if w:
        return w
    home = Path.home()
    for c in (home / ".lmstudio" / "bin" / ("lms.exe" if sys.platform == "win32" else "lms"),
              home / ".cache" / "lm-studio" / "bin" / "lms"):
        if c.is_file():
            return str(c)
    return None


def supported_flag(help_text, names):
    """The first of `names` that the installed `lms load --help` prints as a whole flag, else None."""
    for n in names:
        if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", help_text or ""):
            return n
    return None


def build_load(model, s, help_text, rules=None, identifier=None):
    """-> (argv tail, skipped [setting]). Only flags the help prints are passed; the rest are reported, never guessed."""
    f = (rules or load_rules())["lms_flags"]
    argv, skipped = [model, "-y"], []

    def add(setting, value):
        flag = supported_flag(help_text, f.get(setting, []))
        if not flag:
            skipped.append(setting)
        elif value is True:
            argv.append(flag)
        elif value is not None:
            argv.extend([flag, str(value)])
    add("context", s["context"])
    add("gpu", "off" if s["gpu"] == 0 else "max" if s["gpu"] >= 1 else s["gpu"])
    add("ttl", s["ttl"])
    add("threads", s["threads"])
    if s["flash_attention"]:
        add("flash_attention", True)
    if s["kv_cache"] != "f16":
        add("kv_cache", s["kv_cache"])
    if s["mtp"]:
        add("mtp", True)
    if identifier:
        add("identifier", identifier)
    return argv, skipped


def models_dirs():
    h = Path.home()
    return [h / ".lmstudio" / "models", h / ".cache" / "lm-studio" / "models"]


def resolve_model(arg, dirs=None):
    """-> (gguf path, key for lms). A file path, or a name part found under LM Studio's models folder (one match only)."""
    p = Path(arg)
    if p.is_file():
        return p, arg
    want = arg.lower()
    hits = []
    for d in dirs or models_dirs():
        if d.is_dir():
            for f in d.rglob("*.gguf"):
                rel = f.relative_to(d).as_posix()
                if want in rel.lower() and "mmproj" not in f.name.lower():
                    hits.append((f, rel))
    if len(hits) != 1:
        raise ValueError(f"{len(hits)} model file(s) match {arg!r}; give the .gguf path" +
                         ("".join(f"\n  {h[1]}" for h in hits[:10])))
    f, rel = hits[0]
    return f, re.sub(r"\.gguf$", "", rel, flags=re.I)


def apply(model_key, s, rules=None, run=_run, lms=None):
    """Load through `lms load`; verify with `lms ps`. -> (ok, lines)."""
    lms = lms or find_lms()
    if not lms:
        return False, ["lms (LM Studio's command line) was not found: install it from LM Studio (Developer > lms) or put it on PATH"]
    base = model_key.split("/")[-1].lower()
    ps0 = run([lms, "ps"]) or ""
    if base in ps0.lower() or model_key.lower() in ps0.lower():
        return True, ["ALREADY LOADED  lms ps lists it: never a second copy", ps0.strip()[:400]]
    help_text = run([lms, "load", "--help"])
    if not help_text:
        return False, [f"`{lms} load --help` printed nothing: flags are not guessed, nothing loaded"]
    argv, skipped = build_load(model_key, s, help_text, rules)
    lines = [f"RUN  lms load {' '.join(argv)}"] + ([f"NOT PASSED (this lms has no such flag): {', '.join(skipped)}"] if skipped else [])
    try:
        r = subprocess.run([lms, "load"] + argv, capture_output=True, text=True, timeout=1800, errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        return False, lines + [f"lms load failed: {e}"]
    if r.returncode != 0:
        return False, lines + [f"lms load exited {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}"]
    ps = run([lms, "ps"])
    base = model_key.split("/")[-1].lower()
    ok = base in ps.lower() or model_key.lower() in ps.lower()
    return ok, lines + [("VERIFIED  lms ps lists it" if ok else "NOT VERIFIED  lms ps does not list the model"), ps.strip()[:400]]


# ---- runs: first-word time and tokens/s, one line per run ------------------------------------------------------------------

def runs_path(rules=None):
    from .friction import state_dir
    d = state_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d / (rules or load_rules())["runs"]["file"]


def record_run(model, first_word_s, tps, prompt_tokens=None, path=None):
    if first_word_s < 0 or tps < 0:
        raise ValueError("first word seconds and tokens/s must not be negative")
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model, "first_word_s": round(first_word_s, 1),
           "tokens_per_s": round(tps, 1), "prompt_tokens": prompt_tokens}
    with open(path or runs_path(), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_runs(path=None):
    p = Path(path) if path else runs_path()
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def report_runs(rows):
    """-> {model: {runs, first_word_s_best, first_word_s_last, tps_last}}; the best first word shows what the cache gave."""
    t = {}
    for r in rows:
        c = t.setdefault(r.get("model", "?"), {"runs": 0, "first_word_s_best": None})
        c["runs"] += 1
        fw = r.get("first_word_s")
        if fw is not None and (c["first_word_s_best"] is None or fw < c["first_word_s_best"]):
            c["first_word_s_best"] = fw
        c["first_word_s_last"], c["tps_last"] = fw, r.get("tokens_per_s")
    return dict(sorted(t.items()))


# ---- command -------------------------------------------------------------------------------------------------------------------

def _render_host(host):
    c = host["cpu"]
    gpus = "; ".join(f"{g['name']} ({'shared RAM' if g['shared'] else str(g['vram_gb']) + ' GB VRAM'})" for g in host["gpus"]) or "none found"
    return [f"host   {c['model']}: {c['performance']} performance + {c['efficiency']} efficiency cores ({c['logical']} threads); "
            f"GPU {gpus}; RAM {host['ram_free_gb']} free of {host['ram_gb']} GB; memory {host['bandwidth_gbps']} GB/s"] + \
           [f"  note   {n}" for n in host["notes"]]


def _render(model, host, plan):
    s, w = plan["settings"], plan["why"]
    out = _render_host(host) + [f"model  {model}", "settings (each with its reason):",
            f"  context        {s['context']}   {w['context']}",
            f"  threads        {s['threads']}   {w['threads']}",
            f"  gpu offload    {s['gpu']}   {w['gpu']}",
            f"  kv cache       {s['kv_cache']}   {w['kv_cache']}",
            f"  flash attn     {'on' if s['flash_attention'] else 'off'}   {w['flash_attention']}",
            f"  mtp/draft      {'on' if s['mtp'] else 'off'}   {w['mtp']}",
            f"expected RAM held: about {s['expected_ram_gb']} GB (weights {s['weights_gb']} + KV cache {s['kv_gb']})"]
    out += [f"WARNING  {x}" for x in plan["warnings"]]
    return out


def main(argv):
    if argv[:1] != ["tune"]:
        return None
    a, as_json = argv[1:], "--json" in argv
    if "--help" in a or "-h" in a:
        print("usage: fieldkit models tune MODEL [--context N] [--apply] [--json]\n"
              "       fieldkit models tune --host | --report | --record MODEL --first-word S --tps N [--prompt-tokens N]\n"
              "  MODEL  a .gguf path, or part of a model's name under LM Studio's models folder\n"
              "  --apply  load it through `lms load` with the chosen settings and check `lms ps`\n"
              "NEXT: fieldkit models tune MODEL, read the reasons, then --apply")
        return 0
    a = [x for x in a if x != "--json"]
    if "--report" in a:
        rep = report_runs(read_runs())
        if as_json:
            print(json.dumps(rep, indent=1))
        for m, c in rep.items():
            if not as_json:
                print(f"{m:<40} {c['runs']} run(s)  first word best {c['first_word_s_best']} s, last {c['first_word_s_last']} s, last {c['tps_last']} t/s")
        if not rep and not as_json:
            print("no run recorded")
        print("NEXT: the target is a first word under 30 s on the second session")
        return 0
    if "--record" in a:
        def opt(k, cast=float, default=None):
            return cast(a[a.index(k) + 1]) if k in a and a.index(k) + 1 < len(a) else default
        try:
            model = a[a.index("--record") + 1]
            rec = record_run(model, opt("--first-word"), opt("--tps"), opt("--prompt-tokens", int))
        except (IndexError, TypeError, ValueError) as e:
            print(f"ERROR  --record MODEL --first-word SECONDS --tps N [--prompt-tokens N]: {e}")
            return 2
        print(json.dumps(rec) if as_json else f"recorded: {rec['model']} first word {rec['first_word_s']} s, {rec['tokens_per_s']} t/s")
        print("NEXT: fieldkit models tune --report")
        return 0
    if "--host" in a:
        h = probe_host()
        print(json.dumps(h, indent=1) if as_json else "\n".join(_render_host(h)))
        print("NEXT: fieldkit models tune MODEL")
        return 0
    wanted, rest, i = None, [], 0
    while i < len(a):
        if a[i] == "--context" and i + 1 < len(a):
            try:
                wanted, i = int(a[i + 1]), i + 1
            except ValueError:
                print("ERROR  --context needs a number")
                return 2
        elif a[i] != "--apply" and a[i].startswith("--"):
            print(f"ERROR  unknown option {a[i]}")
            return 2
        elif a[i] != "--apply":
            rest.append(a[i])
        i += 1
    if len(rest) != 1:
        print("usage: fieldkit models tune MODEL [--context N] [--apply] [--json] | --host | --record MODEL --first-word S --tps T | --report")
        return 2
    from . import gguf
    try:
        path, key = resolve_model(rest[0])
        s = gguf.LocalSource(path)
        try:
            hdr = gguf.parse(s)
        finally:
            s.close()
        info = gguf.analyze(hdr)
    except (ValueError, OSError) as e:
        print(f"ERROR  {e}")
        return 1
    host = probe_host()
    plan = decide(host, info, wanted_ctx=wanted)
    ok = True
    lines = []
    if "--apply" in a:
        ok, lines = apply(key, plan["settings"])
    if as_json:
        print(json.dumps({"model": key, "host": host, **plan, "applied": "--apply" in a and ok, "apply_log": lines}, indent=1))
    else:
        print("\n".join(_render(key, host, plan) + lines))
    print("NEXT: " + ("record each run: fieldkit models tune --record MODEL --first-word S --tps N" if "--apply" in a and ok else
                      "fieldkit models tune MODEL --apply  (loads it with these settings)" if "--apply" not in a else
                      "fix the lms line above, then run it again"))
    return 0 if ok else 1
