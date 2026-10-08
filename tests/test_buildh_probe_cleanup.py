"""A probe run leaves no browser and no copy behind (2026-10-08: two visible walk browsers outlived their runs because
firefox.exe on Windows is a launcher that exits after starting the browser; 66 copies of the build piled up in the
temp folder because rmtree right after the kill met locked files)."""
import time

from fieldkit.buildh import probe


def test_launch_keeps_the_launcher_until_the_browser_ends_and_stops_the_copy(monkeypatch, tmp_path):
    seen = {}

    class P:
        pid = 4242
        stdout = iter(["GPROBE hello\n", "GPROBE-DONE\n"])

    def popen(cmd, **kw):
        seen["cmd"] = cmd
        return P()
    stopped = []
    monkeypatch.setattr(probe.subprocess, "Popen", popen)
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(probe, "stop_in", lambda folder: stopped.append(str(folder)) or [])
    monkeypatch.setattr(probe.throwaway, "profile", lambda *a, **k: tmp_path / "prof")
    monkeypatch.setattr(probe.throwaway, "discard", lambda p: None)
    r = probe.launch(tmp_path / "app", headless=False)
    assert "-wait-for-browser" in seen["cmd"] and "-headless" not in seen["cmd"]
    assert r["lines"] == ["hello"] and r["done"]
    assert stopped == [str(tmp_path / "app")]


def test_remove_copy_stops_what_runs_from_it_and_retries(monkeypatch, tmp_path):
    copy = tmp_path / "gprobe_app_x"
    copy.mkdir()
    calls = {"rm": 0, "stop": 0}
    real = probe.shutil.rmtree

    def rmtree(p, ignore_errors=False):
        calls["rm"] += 1
        if calls["rm"] >= 2:                       # the first try meets a held file
            real(p, ignore_errors=True)
    monkeypatch.setattr(probe.shutil, "rmtree", rmtree)
    monkeypatch.setattr(probe, "stop_in", lambda f: calls.__setitem__("stop", calls["stop"] + 1) or [])
    assert probe.remove_copy(copy, sleep=lambda s: None) and not copy.exists()
    assert calls == {"rm": 2, "stop": 1}


def test_sweep_removes_only_old_idle_probe_copies(monkeypatch, tmp_path):
    monkeypatch.setattr(probe.tempfile, "gettempdir", lambda: str(tmp_path))
    old, fresh, busy, foreign = (tmp_path / f"gprobe_app_{n}" for n in ("old", "fresh", "busy", "foreign"))
    for d in (old, fresh, busy):
        d.mkdir()
        (d / probe.COPY_MARK).write_text("x")
    foreign.mkdir()                                 # no mark, no app/firefox.exe: not ours to judge
    long_ago = time.time() - 7200
    import os
    for d in (old, busy, foreign):
        os.utime(d, (long_ago, long_ago))
    monkeypatch.setattr(probe, "processes_in", lambda d: [1] if d.name == "gprobe_app_busy" else [])
    monkeypatch.setattr(probe, "stop_in", lambda f: [])
    assert probe.sweep_copies(say=lambda m: None) == 1
    assert not old.exists() and fresh.exists() and busy.exists() and foreign.exists()
