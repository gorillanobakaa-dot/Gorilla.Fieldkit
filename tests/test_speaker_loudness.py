"""speaker_loudness_setup.py, PulseAudio path, against a simulated pactl (runs on any OS).

The script's promises are tested, not its sound: the compressor sink becomes the
default, the user's own default.pa lines survive with a timestamped backup, a
second install does not duplicate anything, uninstall puts everything back, and
a failed verification reverts the default sink. The PipeWire path and the real
audio result belong to the Debian run.
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fieldkit.core import settings

TOOL = Path(settings.expand("${FIELDKIT}")) / "toolbox" / "speaker-loudness-fix" / "speaker_loudness_setup.py"
pytestmark = pytest.mark.skipif(not TOOL.is_file(), reason="speaker-loudness-fix not gathered")
HW = "alsa_output.pci-0000_00_1b.0.analog-stereo"


class FakePulse:
    def __init__(self, default_sticks=True):
        self.sinks, self.default, self.modules = [HW], HW, {}
        self.default_sticks = default_sticks

    def __call__(self, cmd, **kw):
        out, rc = "", 0
        if cmd[:2] == ["pactl", "info"]:
            out = "Server Name: pulseaudio\n"
        elif cmd[:4] == ["pactl", "list", "short", "sinks"]:
            out = "".join(f"{i}\t{s}\tmodule\tfloat32le\tRUNNING\n" for i, s in enumerate(self.sinks))
        elif cmd[:4] == ["pactl", "list", "short", "modules"]:
            out = "".join(f"{n}\tmodule-ladspa-sink\t{a}\n" for n, a in self.modules.items())
        elif cmd[:3] == ["pactl", "load-module", "module-ladspa-sink"]:
            self.modules[str(20 + len(self.modules))] = " ".join(cmd[3:])
            self.sinks.append("loudness_sink")
        elif cmd[:2] == ["pactl", "unload-module"]:
            self.modules.pop(cmd[2], None)
            self.sinks = [s for s in self.sinks if s != "loudness_sink"]
        elif cmd[:2] == ["pactl", "set-default-sink"]:
            if self.default_sticks or cmd[2] == HW:
                self.default = cmd[2]
        elif cmd[:2] == ["pactl", "get-default-sink"]:
            out = self.default + "\n"
        else:
            rc = 127
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")


@pytest.fixture
def sl(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("speaker_loudness_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    home = tmp_path / "home"
    for name, rel in (("PW_CONF", ".config/pipewire/filter-chain.conf.d/60-loudness.conf"),
                      ("PW_UNIT", ".config/systemd/user/loudness-sink.service"),
                      ("PA_USER_DEFAULT", ".config/pulse/default.pa"),
                      ("MIRROR_BIN", ".local/bin/loudness-volume-mirror"),
                      ("MIRROR_UNIT", ".config/systemd/user/loudness-volume-mirror.service")):
        monkeypatch.setattr(mod, name, home / rel)
    (tmp_path / "ladspa").mkdir()
    (tmp_path / "ladspa" / "sc4m_1916.so").write_bytes(b"")
    monkeypatch.setattr(mod, "LADSPA_DIRS", [str(tmp_path / "ladspa")])
    monkeypatch.setattr(mod.shutil, "which", lambda n: "pactl" if n == "pactl" else None)   # no systemd here
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    mod.pulse = FakePulse()
    monkeypatch.setattr(mod, "run", mod.pulse)
    mod.home = home
    return mod


def main(mod, monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["speaker_loudness_setup.py", *args])
    return mod.main()


def marked(mod):
    return [l for l in mod.PA_USER_DEFAULT.read_text().splitlines() if mod.MARK in l]


def test_install_makes_the_compressor_the_default_and_persists_it(sl, monkeypatch, capsys):
    main(sl, monkeypatch)
    assert sl.pulse.default == "loudness_sink"
    text = sl.PA_USER_DEFAULT.read_text().splitlines()
    assert text[0] == ".include /etc/pulse/default.pa" and len(marked(sl)) == 2
    assert f"sink_master={HW}" in marked(sl)[0]
    assert "[DONE] loudness compressor active" in capsys.readouterr().out


def test_second_install_duplicates_nothing(sl, monkeypatch):
    main(sl, monkeypatch)
    main(sl, monkeypatch)
    assert len(marked(sl)) == 2


def test_users_own_default_pa_is_kept_and_backed_up(sl, monkeypatch):
    sl.PA_USER_DEFAULT.parent.mkdir(parents=True)
    sl.PA_USER_DEFAULT.write_text(".include /etc/pulse/default.pa\nload-module module-echo-cancel\n")
    main(sl, monkeypatch)
    assert "load-module module-echo-cancel" in sl.PA_USER_DEFAULT.read_text()
    baks = list(sl.PA_USER_DEFAULT.parent.glob("default.pa.bak-*"))
    assert len(baks) == 1 and "echo-cancel" in baks[0].read_text() and sl.MARK not in baks[0].read_text()


def test_uninstall_puts_everything_back(sl, monkeypatch):
    sl.PA_USER_DEFAULT.parent.mkdir(parents=True)
    sl.PA_USER_DEFAULT.write_text(".include /etc/pulse/default.pa\nload-module module-echo-cancel\n")
    main(sl, monkeypatch)
    main(sl, monkeypatch, "--uninstall")
    assert sl.pulse.default == HW and "loudness_sink" not in sl.pulse.sinks and not sl.pulse.modules
    assert marked(sl) == [] and "module-echo-cancel" in sl.PA_USER_DEFAULT.read_text()


def test_failed_verification_reverts_the_default_sink(sl, monkeypatch):
    sl.pulse.default_sticks = False            # the server refuses to switch to the compressor
    with pytest.raises(SystemExit) as e:
        main(sl, monkeypatch)
    assert e.value.code == 1 and sl.pulse.default == HW


def test_no_audio_server_is_a_clear_refusal(sl, monkeypatch, capsys):
    monkeypatch.setattr(sl.shutil, "which", lambda n: None)
    with pytest.raises(SystemExit):
        main(sl, monkeypatch)
    assert "pactl not found" in capsys.readouterr().out and not sl.home.exists()
