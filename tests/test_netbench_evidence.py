"""netbench --moz-log / --keep-profile / --no-images: the evidence a person reads after a bench (one MOZ_LOG file per
visit, every profile with its cache) kept beside the result, and pictures switched off on top of a mode (2026-10-04:
two throwaway scripts patched Bench.visit and browser.discard to get this). No browser, no server is started."""
import json
import threading
from pathlib import Path

import pytest

from fieldkit import cli
from fieldkit.netbench import benches, browser, report


class FakeRun:
    def __init__(self):
        self.done = threading.Event()
        self.done.set()
        self.marks, self.final = [], None


class FakeLab:
    class origin:
        @staticmethod
        def new_run(rid, steps):
            return FakeRun()

    @staticmethod
    def snap():
        return {}


class FakeBrowser:
    seen = []

    def __init__(self, exe, profile, url, env_extra=None):
        FakeBrowser.seen.append(dict(env_extra or {}))

    def alive(self):
        return True

    def stop(self):
        return []


def test_every_visit_writes_its_own_log_and_a_bench_s_own_log_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "Browser", FakeBrowser)
    FakeBrowser.seen.clear()
    b = benches.Bench(FakeLab, "firefox.exe", {}, say=lambda m: None, moz_log="cache2:5,nsHttp:5", keep_dir=tmp_path)
    b.visit("b1a-1-0", [("cold", "u"), ("warm", "u")], tmp_path / "p", 5, grace=0)
    b.visit("b1b-2-0", [("restart", "u")], tmp_path / "p", 5, grace=0)
    b.visit("b5-3-0", [("idle", "u")], tmp_path / "p", 5, env={"MOZ_LOG": "timestamp,nsHttp:5", "MOZ_LOG_FILE": "own.log"}, grace=0)
    assert [e.get("MOZ_LOG_FILE") for e in FakeBrowser.seen] == [
        str(tmp_path / "visit001-b1a-1-0-cold.log"), str(tmp_path / "visit002-b1b-2-0-restart.log"), "own.log"]
    assert FakeBrowser.seen[0]["MOZ_LOG"] == "timestamp,cache2:5,nsHttp:5"
    assert b.kept == [str(tmp_path / "visit001-b1a-1-0-cold.log"), str(tmp_path / "visit002-b1b-2-0-restart.log")]
    with pytest.raises(ValueError):
        benches.Bench(FakeLab, "firefox.exe", {}, moz_log="cache2:5")          # evidence with nowhere to keep it


def test_a_kept_profile_is_copied_without_its_lock_before_it_is_deleted(tmp_path, monkeypatch):
    gone = []
    monkeypatch.setattr(browser, "discard", gone.append)
    prof = tmp_path / "gnetbench_x"
    (prof / "cache2" / "entries").mkdir(parents=True)
    (prof / "cache2" / "entries" / "ABC").write_bytes(b"cached page")
    (prof / "parent.lock").write_bytes(b"")
    b = benches.Bench(FakeLab, "firefox.exe", {}, say=lambda m: None, keep_dir=tmp_path / "kept", keep_profile=True)
    b.discard(prof, "B1-austere")
    copy = tmp_path / "kept" / "profile-001-B1-austere"
    assert (copy / "cache2" / "entries" / "ABC").read_bytes() == b"cached page" and not (copy / "parent.lock").exists()
    assert gone == [prof] and b.kept == [str(copy)]
    plain = benches.Bench(FakeLab, "firefox.exe", {}, say=lambda m: None)
    plain.discard(prof, "B1-austere")
    assert gone == [prof, prof] and plain.kept == []                          # no option: deleted, nothing kept


def test_the_run_switches_images_off_records_it_and_keeps_its_evidence_beside_the_result(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(browser, "build_info", lambda d: {"build_id": "1", "codename": "Gorilla", "version": "157.0"})
    monkeypatch.setattr(browser, "wait_not_running", lambda d, max_wait=0, say=None: True)
    monkeypatch.setattr(browser, "install_prefs", lambda d: {})
    monkeypatch.setattr(browser, "copy_install", lambda d, ca, say=None: (tmp_path / "copyroot", tmp_path / "copyroot" / "app"))
    monkeypatch.setattr(browser, "discard", lambda d: True)
    monkeypatch.setattr(browser.throwaway, "profile", lambda prefix, user_js=None: tmp_path / "work")
    monkeypatch.setattr(report.fixtures, "build", lambda: {})
    monkeypatch.setattr(report.fixtures, "manifest", lambda f: {"article_set_bytes_compressed": 1, "digest": "d", "files": {}})
    monkeypatch.setattr(report.certs, "make", lambda d: {"ca": "ca.pem"})

    class Relay:
        def set_link(self, *a, **k):
            pass

        def refused_hosts(self):
            return {}

    class Lab:
        h3_error, ports = None, {"h3": 1}

        def __init__(self, *a, **k):
            pass

        def start(self):
            return self

        def stop(self):
            pass
    monkeypatch.setattr(report.relay, "Relay", Relay)
    monkeypatch.setattr(report.servers, "Lab", Lab)

    def b3(self):
        seen.update(prefs=dict(self.mode_prefs), moz_log=self.moz_log, keep=self.keep_dir)
        return {"h3_requests": 1, "recv_buffer_granted": 1}
    monkeypatch.setattr(benches.Bench, "b3", b3)
    result, jp, yp = report.run_all(tmp_path / "install", ["B3"], mode="satellite", reps=1, label="cachelog",
                                    out_dir=tmp_path / "out", say=lambda m: None, moz_log="cache2:5", keep_profile=True,
                                    images=False)
    assert seen["prefs"] == {"gorilla.linkmode": 1, "permissions.default.image": 2} and seen["moz_log"] == "timestamp,cache2:5"
    assert seen["keep"].parent == tmp_path / "out" and seen["keep"].name.startswith("netbench-cachelog-")
    assert seen["keep"].name.endswith("-kept") and seen["keep"].is_dir()
    saved = json.loads(Path(jp).read_text(encoding="utf-8"))
    assert saved["mode_prefs"]["permissions.default.image"] == 2
    assert saved["evidence"] == {"moz_log": "timestamp,cache2:5", "keep_profile": True, "images": "off",
                                 "kept_dir": str(seen["keep"]), "kept": []}


def test_the_command_line_takes_the_three_switches():
    a = cli.build_parser().parse_args(["build-harness", "netbench", "--bench", "B1", "--profile", "satellite",
                                        "--moz-log", "cache2:5,nsHttp:5", "--keep-profile", "--no-images"])
    assert a.moz_log == "cache2:5,nsHttp:5" and a.keep_profile and a.no_images
