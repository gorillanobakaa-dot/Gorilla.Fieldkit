"""fieldkit audio: every stage the sound passes through on this Linux machine, and what is done twice.

    fieldkit audio [--json]

Born 2026-10-10 from the Gorilla Firefox audio work. The browser shapes the sound itself (AudioStream: bass x1.8,
FastTanh soft-clip at 0.9, 48 kHz pin), then hands it to cubeb's PulseAudio backend, which on a PipeWire system goes
through pipewire-pulse (the "double hop"), PipeWire and WirePlumber, ALSA and the codec. Anything else on that path
that shapes the sound again (speaker-loudness-fix's compressor sink, EasyEffects, an ALSA plugin) is processing done
twice, and a few settings silently break the chain: audio.format S24LE silences the ALC269, ALSA Master below 100%
starves the DSP, a server clock other than 48 kHz resamples what the browser pinned to 48 kHz, and PulseAudio running
beside pipewire-pulse fights it for the same socket.

It reads only: /proc (processes, and the ALSA cards and codecs under /proc/asound), the PipeWire / WirePlumber / PulseAudio / ALSA config
files, `pactl info` and `pactl list short sinks|modules`, `amixer -c N sget Master`, and the active user services.
It changes nothing. Each problem comes with the exact commands that fix it, filled in for this machine.

What it recognises is data: signatures.yaml. Exit 0 when there is no problem, 3 when there is one (findings).
On Windows it reports not-this-platform: the Windows sound chain is a different thing, and is not faked here.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

SIGNATURES = Path(__file__).with_name("signatures.yaml")
WANT_RATE = 48000                    # the rate Gorilla Firefox pins (CubebUtils / AudioContext) and the ALC269 runs
MAX_READ = 256 * 1024                # a config file larger than this is read up to here
HOME_CONFIGS = [".config/pipewire/**/*.conf", ".config/wireplumber/**/*.conf", ".config/wireplumber/**/*.lua",
                ".config/pulse/default.pa", ".config/pulse/daemon.conf", ".asoundrc"]
ROOT_CONFIGS = ["etc/pipewire/**/*.conf", "etc/wireplumber/**/*.conf", "etc/wireplumber/**/*.lua",
                "etc/pulse/default.pa", "etc/pulse/default.pa.d/*.pa", "etc/pulse/daemon.conf", "etc/asound.conf"]
S24_RX = re.compile(r"(audio\.format\s*=\s*\"?s24|default-sample-format\s*=\s*s24)", re.I)
RATE_RX = re.compile(r"default\.clock\.rate\s*=\s*(\d+)")


# -- collect: everything read from the machine, as plain data (tests pass their own) ---------------------------------
def _run(cmd):
    """-> (exit code, stdout) or None when the program is not installed."""
    if not shutil.which(cmd[0]):
        return None
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.returncode, p.stdout


def _strip(line):
    """A config line without its comment (# and ; for PulseAudio/ALSA/PipeWire, -- for WirePlumber's Lua)."""
    s = line.strip()
    if s.startswith(("#", ";", "--")):
        return ""
    return s.split("#", 1)[0].strip()


def collect(root="/", home=None, run=None):
    root, home, run = Path(root), Path(home or Path.home()), run or _run
    procs = []
    for d in sorted((root / "proc").glob("[0-9]*")):
        try:
            comm = (d / "comm").read_text(errors="replace").strip()
        except OSError:
            continue
        try:
            exe = os.readlink(d / "exe")
        except OSError:
            exe = ""
        procs.append({"pid": int(d.name), "comm": comm, "exe": exe})

    configs = []
    for base, pats in ((home, HOME_CONFIGS), (root, ROOT_CONFIGS)):
        for pat in pats:
            for f in sorted(base.glob(pat)):
                if f.is_file():
                    try:
                        with open(f, encoding="utf-8", errors="replace") as fh:
                            configs.append({"path": str(f), "text": fh.read(MAX_READ)})
                    except OSError:
                        pass

    cards = []
    try:
        cards_txt = (root / "proc/asound/cards").read_text(errors="replace")
    except OSError:
        cards_txt = ""
    for m in re.finditer(r"^\s*(\d+)\s+\[([^\]]+)\]:\s*(.*)$", cards_txt, re.M):
        idx = int(m.group(1))
        codec = ""
        for c in sorted((root / f"proc/asound/card{idx}").glob("codec#*")):
            try:
                first = c.read_text(errors="replace").splitlines()[:1]
            except OSError:
                first = []
            if first and first[0].startswith("Codec:"):
                codec = first[0].split(":", 1)[1].strip()
                break
        r = run(["amixer", "-c", str(idx), "sget", "Master"])
        cards.append({"index": idx, "name": m.group(2).strip(), "desc": m.group(3).strip(), "codec": codec,
                      "master": None if r is None or r[0] != 0 else r[1], "amixer": r is not None})

    def out(cmd):
        r = run(cmd)
        return None if r is None or r[0] != 0 else r[1]

    units = out(["systemctl", "--user", "list-units", "--type=service", "--state=active", "--no-legend", "--plain"])
    paths = [str(p) for p in ("usr/lib/gorilla-unleashed/firefox",) if (root / p).exists()]
    return {"platform": "linux", "processes": procs, "configs": configs, "cards": cards, "paths": paths,
            "pactl": {"found": run(["pactl", "--version"]) is not None, "info": out(["pactl", "info"]),
                      "sinks": out(["pactl", "list", "short", "sinks"]),
                      "modules": out(["pactl", "list", "short", "modules"])},
            "units": None if units is None else [l.split()[0] for l in units.splitlines() if l.strip()]}


# -- parse --------------------------------------------------------------------------------------------------------------
def _info(text):
    d = {}
    for line in (text or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    return d


def _rows(text):
    return [l.split("\t") for l in (text or "").splitlines() if l.strip()]


def _master(text):
    """amixer sget output -> {"percent": lowest channel, "off": any channel muted} or None."""
    pc = [int(x) for x in re.findall(r"\[(\d+)%\]", text or "")]
    if not pc:
        return None
    return {"percent": min(pc), "off": "[off]" in text}


def load_signatures(path=None):
    return (yaml.safe_load(Path(path or SIGNATURES).read_text(encoding="utf-8")) or {}).get("signatures", [])


# -- analyse ------------------------------------------------------------------------------------------------------------
def _evidence(facts):
    """Every piece of evidence once: (kind, key, text, where)."""
    ev = []
    for p in facts.get("processes", []):
        ev.append(("process", f"pid {p['pid']}", p["comm"], f"process {p['comm']} (pid {p['pid']})"))
        if p.get("exe"):
            ev.append(("exe", f"pid {p['pid']}", p["exe"], f"process {p['exe']} (pid {p['pid']})"))
    for r in _rows(facts.get("pactl", {}).get("sinks")):
        if len(r) > 1:
            ev.append(("sink", r[1], r[1], f"sink {r[1]}"))
    for r in _rows(facts.get("pactl", {}).get("modules")):
        if len(r) > 1:
            text = " ".join(r[1:])
            ev.append(("module", r[0], text, f"module {r[1]} (#{r[0]})"))
    for u in facts.get("units") or []:
        ev.append(("unit", u, u, f"user service {u}"))
    for c in facts.get("configs", []):
        for n, line in enumerate(c["text"].splitlines(), 1):
            s = _strip(line)
            if s:
                ev.append(("config", f"{c['path']}:{n}", s, f"{c['path']}:{n}"))
    for p in facts.get("paths", []):
        ev.append(("path", p, p, f"installed /{p}"))
    return ev


def stages(facts, sigs):
    """-> [{id, group, stage, what, evidence: [where], undo}] for every signature with evidence."""
    owner, found = {}, {}
    ev = _evidence(facts)
    for sig in sigs:
        for kind, key, text, where in ev:
            rx = (sig.get("match") or {}).get(kind)
            fkey = ("file", key.rsplit(":", 1)[0]) if kind == "config" else None
            if not rx or owner.get((kind, key), sig["id"]) != sig["id"] or \
                    owner.get(fkey, sig["id"]) != sig["id"] or not re.search(rx, text):
                continue
            owner[(kind, key)] = sig["id"]
            if fkey and sig.get("claims") == "file":
                owner[fkey] = sig["id"]                    # every other line of this file is this stage's too
            found.setdefault(sig["id"], {"id": sig["id"], "group": sig.get("group", sig["id"]),
                                         "stage": sig["stage"], "what": sig["what"], "evidence": [],
                                         "undo": list(sig.get("undo") or [])})["evidence"].append(where)
    return list(found.values())


def _hw_sink(facts):
    """The default sink if it is a real device, else the first ALSA sink: the speakers."""
    sinks = [r[1] for r in _rows(facts.get("pactl", {}).get("sinks")) if len(r) > 1]
    default = _info(facts.get("pactl", {}).get("info")).get("Default Sink", "")
    for s in [default] + sinks:
        if s.startswith("alsa_output."):
            return s
    return ""


def analyse(facts, sigs=None):
    sigs = load_signatures() if sigs is None else sigs
    if facts.get("platform") != "linux":
        return {"platform": facts.get("platform"), "status": "not-this-platform", "chain": [], "stages": [],
                "findings": [], "ok": True,
                "next": "nothing to do here: fieldkit audio reads a Linux sound chain (PipeWire, PulseAudio, ALSA)"}
    found = stages(facts, sigs)
    hw = _hw_sink(facts)
    for s in found:
        s["undo"] = [u.replace("{hw_sink}", hw) for u in s["undo"] if "{hw_sink}" not in u or hw]
    comms = {p["comm"] for p in facts.get("processes", [])}
    pactl = facts.get("pactl", {})
    info = _info(pactl.get("info"))
    server = info.get("Server Name", "")
    findings = []

    def add(fid, severity, title, detail, fix=None, evidence=None):
        findings.append({"id": fid, "severity": severity, "title": title, "detail": detail,
                         "evidence": evidence or [], "fix": fix or []})

    # A1 two sound servers
    if "pulseaudio" in comms and ("pipewire-pulse" in comms or "PipeWire" in server):
        add("two-servers", "problem", "PulseAudio and PipeWire's PulseAudio server are both running",
            "Both want the same socket: apps land on either one, and the sound goes through two mixers.",
            ["systemctl --user disable --now pulseaudio.service pulseaudio.socket",
             "systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service"],
            ["process pulseaudio", "process pipewire-pulse" if "pipewire-pulse" in comms else f"server {server}"])

    # A2 the double hop (a note: true of every PipeWire system; recorded, not a problem)
    if "on PipeWire" in server:
        add("pulse-hop", "note", "Apps that speak PulseAudio reach PipeWire through pipewire-pulse",
            "Firefox's cubeb uses its PulseAudio backend, so its sound is converted once more by pipewire-pulse "
            "before PipeWire. This is how every PipeWire system works; it does not shape the sound.",
            evidence=[f"server {server}"])

    # A3 sound shaped twice
    dyn = {}
    for s in found:
        if s["stage"] == "dynamics":
            dyn.setdefault(s["group"], s)
    if len(dyn) >= 2:
        names = [s["what"] for s in dyn.values()]
        keep = dyn.get("gorilla-firefox") or next(iter(dyn.values()))
        fix = [u for s in dyn.values() if s is not keep for u in s["undo"]]
        why = ("the browser's own DSP (compiled into Gorilla Firefox; it cannot be switched off without a rebuild)"
               if keep["group"] == "gorilla-firefox" else f"the first one found ({keep['what']})")
        add("double-dynamics", "problem", f"The sound is compressed or limited {len(dyn)} times",
            "Each of these squeezes the loudness on its own: " + " | ".join(names) +
            f". Keep one. The commands below remove every stage except {why}.",
            fix, [e for s in dyn.values() for e in s["evidence"]])

    # A4 ALSA Master below 100% or muted
    for c in facts.get("cards", []):
        m = _master(c.get("master"))
        if m and (m["percent"] < 100 or m["off"]):
            add("master-low", "problem",
                f"ALSA Master on card {c['index']} ({c['name']}) is at {m['percent']}%" + (", muted" if m["off"] else ""),
                "The volume belongs in PipeWire; Master below 100% is a ceiling under it that starves the "
                "browser's DSP (86% is about -9 dB). On many machines PipeWire moves Master with the speaker "
                "volume: if it drops again after you change the volume, that is why.",
                [f"amixer -c {c['index']} sset Master 100% unmute", "sudo alsactl store"],
                [f"amixer -c {c['index']} sget Master"])

    # A5 S24LE anywhere
    for c in facts.get("configs", []):
        for n, line in enumerate(c["text"].splitlines(), 1):
            s = _strip(line)
            if s and S24_RX.search(s):
                add("s24le", "problem", f"{c['path']}:{n} sets a 24-bit sample format",
                    "S24LE silences all sound on the ALC269 in this chain; S32LE is the format that works.",
                    [("" if c["path"].startswith(str(Path.home())) else "sudo ") +
                     f"sed -i.fieldkit-bak '{n}s/[Ss]24[Ll][Ee]/S32LE/' {shlex.quote(c['path'])}",
                     "systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service"],
                    [f"{c['path']}:{n}: {s}"])
    spec = info.get("Default Sample Specification", "")
    if spec.lower().startswith("s24"):
        add("s24le-live", "problem", f"The running server's default format is {spec.split()[0]}",
            "S24LE silences all sound on the ALC269 in this chain; S32LE is the format that works.",
            evidence=[f"pactl info: Default Sample Specification: {spec}"])

    # A6 the server clock is not 48 kHz
    rate = re.search(r"(\d+)Hz", spec)
    if rate and int(rate.group(1)) != WANT_RATE:
        conf = "~/.config/pipewire/pipewire.conf.d/60-fieldkit-48k.conf"
        add("rate", "problem", f"The sound server runs at {rate.group(1)} Hz, not {WANT_RATE}",
            f"Gorilla Firefox pins {WANT_RATE} Hz so nothing is resampled; a server at {rate.group(1)} Hz resamples "
            "every stream once more.",
            ["mkdir -p ~/.config/pipewire/pipewire.conf.d",
             f"printf 'context.properties = {{ default.clock.rate = {WANT_RATE} }}\\n' > {conf}",
             "systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service"]
            if "PipeWire" in server or "pipewire" in comms else
            ["mkdir -p ~/.config/pulse", "touch ~/.config/pulse/daemon.conf",
             "sed -i '/^ *default-sample-rate *=/d' ~/.config/pulse/daemon.conf",
             f"echo 'default-sample-rate = {WANT_RATE}' >> ~/.config/pulse/daemon.conf", "pulseaudio -k"],
            [f"pactl info: Default Sample Specification: {spec}"])

    # A7 extra stages that change the sound (not dynamics): eq, effects, volume, ALSA routing
    for s in found:
        if s["stage"] in ("eq", "effects", "volume", "route"):
            add(f"stage-{s['id']}", "problem" if s["stage"] in ("eq", "volume") else "note",
                f"Extra {s['stage']} stage: {s['what']}",
                "It sits in the sound path beside the browser's own processing.", s["undo"], s["evidence"])

    # what could not be seen is said, never guessed
    if not pactl.get("found"):
        add("no-pactl", "unknown", "pactl is not installed: the sound server could not be asked",
            "Sinks, modules, the format and the rate were not checked.",
            ["sudo apt-get install -y pulseaudio-utils"])
    if facts.get("cards") and not any(c.get("amixer") for c in facts["cards"]):
        add("no-amixer", "unknown", "amixer is not installed: ALSA Master could not be read",
            "The Master level was not checked.", ["sudo apt-get install -y alsa-utils"])

    chain = _chain(facts, found, server, info)
    problems = [f for f in findings if f["severity"] in ("problem", "unknown")]
    nxt = ("run the fix lines of the first problem, then fieldkit audio again" if problems else
           "nothing to fix: play something and listen")
    return {"platform": "linux", "status": "problems" if problems else "clean", "chain": chain, "stages": found,
            "findings": findings, "ok": not problems, "next": nxt}


def _chain(facts, found, server, info):
    """The path the sound takes, in order, each with what was seen."""
    comms = {p["comm"] for p in facts.get("processes", [])}
    by = {s["id"]: s for s in found}
    out = []
    g = by.get("gorilla-firefox")
    out.append(("browser", "Gorilla Firefox DSP (bass, soft-clip, 48 kHz)" if g else "browser (no Gorilla Firefox found)",
                g["evidence"] if g else []))
    if "on PipeWire" in server:
        out.append(("client hop", "pipewire-pulse (PulseAudio protocol -> PipeWire)", [f"server {server}"]))
    for s in found:
        if s["id"] != "gorilla-firefox" and s["stage"] != "route":
            out.append((s["stage"], s["what"], s["evidence"]))
    srv = [n for n in ("pipewire", "wireplumber", "pulseaudio") if n in comms]
    out.append(("server", ", ".join(srv) or "no sound server process seen",
                [f"pactl info: {k}: {info[k]}" for k in ("Server Name", "Default Sink", "Default Sample Specification")
                 if k in info]))
    for s in found:
        if s["stage"] == "route":
            out.append(("alsa plugin", s["what"], s["evidence"]))
    for c in facts.get("cards", []):
        m = _master(c.get("master"))
        out.append(("alsa", f"card {c['index']} {c['name']}: Master " +
                    (f"{m['percent']}%{' muted' if m['off'] else ''}" if m else "not read"), []))
        if c.get("codec"):
            out.append(("codec", c["codec"], [f"/proc/asound/card{c['index']}"]))
    return [{"stage": a, "what": b, "evidence": e} for a, b, e in out]


def lines(r):
    if r["status"] == "not-this-platform":
        return [f"audio: not-this-platform ({r['platform']})", f"NEXT: {r['next']}"]
    out = ["audio: the path the sound takes on this machine"]
    for i, s in enumerate(r["chain"], 1):
        out.append(f"  {i:>2}. {s['stage']:<11} {s['what']}")
        out += [f"        - {e}" for e in s["evidence"][:4]]
    for f in r["findings"]:
        out.append(f"{f['severity'].upper():<8} {f['title']}")
        out.append(f"         {f['detail']}")
        out += [f"         seen: {e}" for e in f["evidence"][:6]]
        if f["fix"]:
            out.append("         FIX (run these as yourself, in any folder):")
            out += [f"           {l}" for l in f["fix"]]
    out.append("CLEAN" if r["ok"] else f"PROBLEMS - {sum(f['severity'] != 'note' for f in r['findings'])}")
    out.append(f"NEXT: {r['next']}")
    return out


def run(facts=None):
    facts = facts or (collect() if sys.platform.startswith("linux") else {"platform": sys.platform})
    return analyse(facts)
