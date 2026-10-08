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


def test_tree_since_maps_a_fluent_source_to_its_shipped_member(tmp_path):
    """Fluent files ship with comments stripped, so they never match byte for byte: the en-US source is mapped to
    localization/en-US/<rest> (2026-10-08: string fixes could only be judged after the build)."""
    import subprocess
    import zipfile
    w = tmp_path / "tree"
    (w / "browser" / "locales" / "en-US" / "browser").mkdir(parents=True)
    f = w / "browser" / "locales" / "en-US" / "browser" / "aboutDialog.ftl"
    f.write_bytes(b"# licence\nversion = { $v }\n")
    g = lambda *a: subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)
    g("init", "-q")
    g("add", ".")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "a")
    f.write_bytes(b"# licence\nversion = { $v } built { $b }\n")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "b")
    inst = tmp_path / "inst"
    (inst / "browser").mkdir(parents=True)
    with zipfile.ZipFile(inst / "browser" / "omni.ja", "w") as z:
        z.writestr("localization/en-US/browser/aboutDialog.ftl", "version = { $v }\n")      # comments stripped
    zipfile.ZipFile(inst / "omni.ja", "w").close()
    got, skipped = probe.tree_since(w, "HEAD~1", inst, tmp_path / "out")
    assert list(got) == ["browser/omni.ja:localization/en-US/browser/aboutDialog.ftl"] and not skipped
    assert got["browser/omni.ja:localization/en-US/browser/aboutDialog.ftl"].read_bytes().endswith(b"built { $b }\n")
