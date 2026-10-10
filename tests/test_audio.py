"""fieldkit audio: each check is shown to fire on a broken chain and to stay quiet on a clean one.

The facts are built by hand after the reference machine (Sony VAIO SVE, ALC269, PipeWire 1.0, Gorilla Firefox); no
real sound hardware is needed. collect() is tested against a fake /proc and fake commands.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from fieldkit.audio import chain

H = str(Path.home())          # the reader's own home, never a name written here
HW = "alsa_output.pci-0000_00_1b.0.analog-stereo"
INFO = ("Server Name: PulseAudio (on PipeWire 1.0.5)\nDefault Sink: {sink}\n"
        "Default Sample Specification: {spec}\n")
MASTER = "Simple mixer control 'Master',0\n  Mono: Playback {p} [{p}%] [0.00dB] [on]\n"


def facts(sink=HW, spec="s32le 2ch 48000Hz", procs=("pipewire", "wireplumber", "pipewire-pulse"), sinks=(HW,),
          modules=(), units=(), configs=(), master=100, gorilla=True):
    ps = [{"pid": 100 + i, "comm": c, "exe": f"/usr/bin/{c}"} for i, c in enumerate(procs)]
    if gorilla:
        ps.append({"pid": 900, "comm": "firefox", "exe": "/usr/lib/gorilla-unleashed/firefox"})
    return {"platform": "linux", "processes": ps, "paths": [],
            "pactl": {"found": True, "info": INFO.format(sink=sink, spec=spec),
                      "sinks": "".join(f"{50 + i}\t{s}\tPipeWire\t{spec}\tRUNNING\n" for i, s in enumerate(sinks)),
                      "modules": "".join(f"{i}\t{m}\n" for i, m in enumerate(modules))},
            "units": list(units),
            "cards": [{"index": 0, "name": "PCH", "desc": "HDA-Intel", "codec": "Realtek ALC269VB",
                       "master": MASTER.format(p=master), "amixer": True}],
            "configs": [{"path": p, "text": t} for p, t in configs]}


def ids(r, severity=None):
    return [f["id"] for f in r["findings"] if severity is None or f["severity"] == severity]


def test_clean_reference_chain():
    r = chain.analyse(facts())
    assert r["ok"] and r["status"] == "clean", r["findings"]
    assert ids(r, "problem") == []
    assert ids(r) == ["pulse-hop"]                      # the double hop is recorded as a note, never as a problem
    stages = [s["stage"] for s in r["chain"]]
    assert stages[0] == "browser" and "client hop" in stages and stages[-1] == "codec"
    assert "Gorilla Firefox" in r["chain"][0]["what"]


def test_loudness_sink_with_gorilla_firefox_is_double_dynamics():
    f = facts(sink="loudness_sink", sinks=(HW, "loudness_sink"),
              units=("loudness-sink.service", "loudness-volume-mirror.service"),
              configs=[(f"{H}/.config/pipewire/filter-chain.conf.d/60-loudness.conf",
                        "context.modules = [\n { name = libpipewire-module-filter-chain\n"
                        "   plugin = sc4m_1916\n   label = sc4m\n }\n]\n")])
    r = chain.analyse(f)
    assert not r["ok"]
    d = next(x for x in r["findings"] if x["id"] == "double-dynamics")
    assert "2 times" in d["title"]
    # the fix removes the compressor, puts the speakers back as default, and leaves no blank to fill in
    assert f"pactl set-default-sink {HW}" in d["fix"]
    assert any("disable --now" in l and "loudness-sink.service" in l for l in d["fix"])
    assert not any("{" in l or "<" in l for l in d["fix"])
    # the loudness filter-chain file is the compressor's, not a second, generic filter-chain stage
    assert "stage-filter-chain" not in ids(r)
    # its volume mirror is a second volume control
    assert "stage-loudness-volume-mirror" in ids(r, "problem")


def test_one_compressor_alone_is_not_double():
    r = chain.analyse(facts(sinks=(HW, "loudness_sink"), gorilla=False))
    assert "double-dynamics" not in ids(r)


def test_pulseaudio_ladspa_path_counts_once():
    """speaker-loudness-fix on PulseAudio: module-ladspa-sink with sink_name=loudness_sink is ONE stage."""
    f = facts(modules=("module-ladspa-sink\tsink_name=loudness_sink plugin=sc4m_1916 label=sc4m",),
              sinks=(HW, "loudness_sink"), gorilla=False)
    found = {s["id"] for s in chain.stages(f, chain.load_signatures())}
    assert "loudness-sink" in found and "ladspa-sink" not in found


def test_easyeffects_with_gorilla_firefox():
    r = chain.analyse(facts(procs=("pipewire", "wireplumber", "pipewire-pulse", "easyeffects")))
    assert "double-dynamics" in ids(r, "problem")


def test_master_below_100():
    r = chain.analyse(facts(master=86))
    f = next(x for x in r["findings"] if x["id"] == "master-low")
    assert "86%" in f["title"] and "amixer -c 0 sset Master 100% unmute" in f["fix"]
    assert "master-low" not in ids(chain.analyse(facts(master=100)))


def test_master_muted():
    f = facts()
    f["cards"][0]["master"] = MASTER.format(p=100).replace("[on]", "[off]")
    assert "master-low" in ids(chain.analyse(f))


def test_s24le_in_config_and_commented_s24le_is_ignored():
    path = f"{H}/.config/wireplumber/wireplumber.conf.d/51-alsa.conf"
    bad = facts(configs=[(path, "monitor.alsa.rules = [\n  { actions = { update-props = {\n"
                                "      audio.format = \"S24LE\"\n  } } }\n]\n")])
    r = chain.analyse(bad)
    f = next(x for x in r["findings"] if x["id"] == "s24le")
    assert f["title"].startswith(f"{path}:3")
    assert any(f"'3s/[Ss]24[Ll][Ee]/S32LE/'" in l for l in f["fix"])
    ok = facts(configs=[(path, "# audio.format = \"S24LE\"\n      audio.format = \"S32LE\"\n")])
    assert "s24le" not in ids(chain.analyse(ok))


def test_s24le_live_spec():
    assert "s24le-live" in ids(chain.analyse(facts(spec="s24le 2ch 48000Hz")))


def test_rate_not_48k():
    r = chain.analyse(facts(spec="s32le 2ch 44100Hz"))
    f = next(x for x in r["findings"] if x["id"] == "rate")
    assert any("default.clock.rate = 48000" in l for l in f["fix"])


def test_two_servers():
    r = chain.analyse(facts(procs=("pipewire", "wireplumber", "pipewire-pulse", "pulseaudio")))
    assert "two-servers" in ids(r, "problem")
    assert "two-servers" not in ids(chain.analyse(facts()))


def test_alsa_plugins_below_the_server():
    r = chain.analyse(facts(configs=[(f"{H}/.asoundrc", "pcm.!default {\n type softvol\n}\n"
                                                           "pcm.mix {\n type dmix\n}\n")]))
    assert "stage-alsa-softvol" in ids(r, "problem")
    assert "stage-alsa-dmix" in ids(r, "note")


def test_unknowns_are_said_not_guessed():
    f = facts()
    f["pactl"] = {"found": False, "info": None, "sinks": None, "modules": None}
    f["cards"][0].update(master=None, amixer=False)
    r = chain.analyse(f)
    assert {"no-pactl", "no-amixer"} <= set(ids(r, "unknown")) and not r["ok"]


def test_not_this_platform():
    r = chain.analyse({"platform": "win32"})
    assert r["status"] == "not-this-platform" and r["ok"]


def test_every_signature_is_complete():
    for s in chain.load_signatures():
        assert {"id", "stage", "what", "match"} <= set(s), s
        assert s["stage"] in ("dynamics", "eq", "effects", "volume", "route")
        for u in s.get("undo") or []:
            assert "<" not in u                         # no blank for the person to fill in


def test_collect_reads_a_fake_machine(tmp_path):
    root, home = tmp_path / "root", tmp_path / "home"
    for pid, comm in ((10, "pipewire"), (11, "pipewire-pulse"), (12, "easyeffects")):
        (root / f"proc/{pid}").mkdir(parents=True)
        (root / f"proc/{pid}/comm").write_text(comm + "\n")
    (root / "proc/asound/card0").mkdir(parents=True)
    (root / "proc/asound/cards").write_text(" 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n")
    (root / "proc/asound/card0/codec#0").write_text("Codec: Realtek ALC269VB\nAddress: 0\n")
    (home / ".config/pipewire/pipewire.conf.d").mkdir(parents=True)
    (home / ".config/pipewire/pipewire.conf.d/x.conf").write_text("audio.format = \"S24LE\"\n")
    calls = []

    def run(cmd):
        calls.append(cmd)
        out = {("pactl", "--version"): "pactl 16.1\n",
               ("pactl", "info"): INFO.format(sink=HW, spec="s32le 2ch 48000Hz"),
               ("pactl", "list", "short", "sinks"): f"50\t{HW}\tPipeWire\ts32le 2ch 48000Hz\tRUNNING\n",
               ("pactl", "list", "short", "modules"): "",
               ("amixer", "-c", "0", "sget", "Master"): MASTER.format(p=70)}.get(tuple(cmd))
        return None if out is None else (0, out)

    f = chain.collect(root=root, home=home, run=run)
    assert [p["comm"] for p in f["processes"]] == ["pipewire", "pipewire-pulse", "easyeffects"]
    assert f["cards"][0]["codec"] == "Realtek ALC269VB"
    assert f["units"] is None                           # systemctl missing: unknown, not "none"
    r = chain.analyse(f)
    assert {"s24le", "master-low"} <= set(ids(r, "problem"))
    assert any(s["id"] == "easyeffects" for s in r["stages"])
    assert all(c[0] in ("pactl", "amixer", "systemctl") for c in calls)   # it only asks, never sets
    assert not any(w in " ".join(c) for c in calls for w in ("set", "load", "unload", "sset"))


def test_cli_json_and_exit_code():
    p = subprocess.run([sys.executable, "-m", "fieldkit", "audio", "--json"], capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": os.getcwd()})
    r = json.loads(p.stdout)
    assert p.returncode == (0 if r["ok"] else 3)
    assert r["status"] in ("clean", "problems", "not-this-platform")


def test_without_gorilla_firefox_the_first_stage_is_kept():
    r = chain.analyse(facts(sinks=(HW, "loudness_sink"), procs=("pipewire", "pipewire-pulse", "easyeffects"),
                            gorilla=False))
    d = next(x for x in r["findings"] if x["id"] == "double-dynamics")
    assert "first one found" in d["detail"] and "SC4" in d["detail"]
    assert any("easyeffects" in l for l in d["fix"]) and not any("loudness" in l for l in d["fix"])
