"""Security review 2026-10-02 of the Gorilla Firefox build harness: each hole has a test here (install/post-install/
restore ones live in test_buildh_install.py, leak gate ones in test_leakgate.py, `thermal watch` in test_thermal.py).
Everything runs on fakes: no browser starts, no process is killed, no registry is written."""
import shutil
import subprocess

import pytest

from fieldkit.buildh import buildrun, creepfix, export, handedit, repair, task, throwaway

from tests.test_buildh_repair import MANGLED, _urlbar

LLAMA = "mozilla/llama/LlamaRuntimeLinker.h"
CPP = '#  include "mozilla/llama/LlamaRuntimeLinker.h"\n  mozilla::llama::LlamaRuntimeLinker::Init();\n'


# ---------------------------------------------------------------- hole 1: creep fix writes only inside the working copy
def test_creep_fix_refuses_a_file_outside_the_working_copy(tmp_path, monkeypatch):
    w = tmp_path / "work"
    (w / "ipc").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "Victim.cpp"
    victim.write_text(CPP, encoding="utf-8")
    monkeypatch.setattr(handedit, "record", lambda *a, **k: pytest.fail("nothing outside the tree may be recorded"))
    t = {"id": "t1", "workdir": str(w), "meta": {}}
    for named in (str(victim), "../outside/Victim.cpp", "ipc/../../outside/Victim.cpp"):
        line = f" 69:06.70 E {named}(1,12): fatal error: '{LLAMA}' file not found"
        ok, what = buildrun.fix_creep_include(t, tmp_path, lambda m: None, [line])
        assert not ok and "outside the working copy" in what, what
    assert victim.read_text(encoding="utf-8") == CPP
    assert creepfix.inside_tree("ipc/X.cpp", w) == (w / "ipc/X.cpp").resolve()
    assert creepfix.inside_tree(str(w / "ipc" / "X.cpp"), w) == (w / "ipc/X.cpp").resolve()
    assert creepfix.inside_tree("../outside/Victim.cpp", w) is None


# ---------------------------------------------------------------- hole 2: a missing node fails the repair proof
def test_a_missing_node_fails_the_repair_and_restores_the_file(tmp_path, monkeypatch):
    (tmp_path / "browser/components").mkdir(parents=True)
    p = tmp_path / "browser/components/DesktopActorRegistry.sys.mjs"
    p.write_text(MANGLED, encoding="utf-8")
    before = p.read_bytes()
    monkeypatch.setattr(repair.shutil, "which", lambda name: None)
    assert repair.prove(p) == "node is not installed: the repair cannot be proven"
    ok, what = repair.dangling_excision(tmp_path, "browser/components/DesktopActorRegistry.sys.mjs", 15)
    assert not ok and "node is not installed" in what and "restored" in what
    assert p.read_bytes() == before
    rel = _urlbar(tmp_path)
    before = (tmp_path / rel).read_bytes()
    ok, what = repair.moved_member(tmp_path, rel, "UrlbarUtils", "RESULT_SOURCE")
    assert not ok and "node is not installed" in what and (tmp_path / rel).read_bytes() == before


# ---------------------------------------------------------------- hole 8: throwaway profiles are deleted, only ours
def _track(monkeypatch):
    made = []
    real = throwaway.profile

    def profile(*a, **k):
        made.append(real(*a, **k))
        return made[-1]
    monkeypatch.setattr(throwaway, "profile", profile)
    return made


class FakeProc:
    pid = 4242424

    def __init__(self, *a, **k):
        pass

    def wait(self, timeout=None):
        return 0


def _no_processes(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: calls.append(cmd) or type("R", (), {"stdout": "", "returncode": 0})())
    return calls


def test_throwaway_discards_only_its_own_profiles(tmp_path):
    prof = throwaway.profile("gproof_")
    try:
        assert (prof / throwaway.MARK).is_file() and (prof / "user.js").is_file() and throwaway.ours(prof)
        (prof / "stderr.txt").write_text("log", encoding="utf-8")
        kept = throwaway.keep(prof, "stderr.txt")
        assert (kept / "stderr.txt").read_text(encoding="utf-8") == "log"
        assert throwaway.discard(prof) and not prof.exists()
    finally:
        shutil.rmtree(prof, ignore_errors=True)
        shutil.rmtree(throwaway.root() / "fieldkit-logs" / prof.name, ignore_errors=True)
    # a folder with the prefix but no marker, and a marked folder outside the temp folder, are never deleted
    stranger = throwaway.root() / "gproof_not_ours_test"
    stranger.mkdir(exist_ok=True)
    elsewhere = tmp_path / "gproof_x"
    elsewhere.mkdir()
    (elsewhere / throwaway.MARK).write_text("", encoding="utf-8")
    try:
        assert not throwaway.discard(stranger) and stranger.is_dir()
        assert not throwaway.discard(elsewhere) and elsewhere.is_dir()
        assert not throwaway.discard(tmp_path) and tmp_path.is_dir()
    finally:
        stranger.rmdir()
    with pytest.raises(ValueError):
        throwaway.profile("anything_")


def test_proof_rows_delete_their_throwaway_profiles_and_keep_the_logs(tmp_path, monkeypatch):
    from fieldkit.buildh import proof
    made = _track(monkeypatch)
    calls = _no_processes(monkeypatch)
    try:
        row = proof.startup_row(tmp_path, seconds=0)
        assert row["ok"] and (throwaway.root() / "fieldkit-logs" / made[-1].name).is_dir()
        proof.egress_row(tmp_path, seconds=0)
        proof.adblock_row(tmp_path, seconds=0)
        assert [p.name[:p.name.index("_") + 1] for p in made] == ["gproof_", "gegress_", "gadblock_"]
        assert not any(p.exists() for p in made)
        # only the PIDs these checks started were stopped
        assert all(c[:3] == ["taskkill", "/PID", str(FakeProc.pid)] for c in calls)
    finally:
        for p in made:
            shutil.rmtree(p, ignore_errors=True)
            shutil.rmtree(throwaway.root() / "fieldkit-logs" / p.name, ignore_errors=True)


def test_leaks_and_capture_delete_their_throwaway_profiles(tmp_path, monkeypatch):
    from fieldkit.buildh import capture, install, leaks
    made = _track(monkeypatch)
    _no_processes(monkeypatch)
    monkeypatch.setattr(capture.time, "sleep", lambda s: None)
    try:
        assert leaks.measure(tmp_path, seconds=0) is None
        capture.run_browser(tmp_path / "firefox.exe", "about:blank", 0, sample_sockets=False)
        res = capture.dns_run(tmp_path / "firefox.exe", scenarios=(("idle", "about:blank", 0),), say=lambda m: None)
        assert res["idle"]["names"] == []
        install.startup_errors(tmp_path, seconds=0, say=lambda m: None)
        assert [p.name[:p.name.index("_") + 1] for p in made] == ["gleaks_", "gcap_", "gcap_", "gstartup_"]
        assert not any(p.exists() for p in made)
    finally:
        for p in made:
            shutil.rmtree(p, ignore_errors=True)
            shutil.rmtree(throwaway.root() / "fieldkit-logs" / p.name, ignore_errors=True)


# ---------------------------------------------------------------- hole 9: the patch kind is explicit
def test_handedit_accepts_only_privacy_or_port():
    assert handedit.check_kind(None) is None and handedit.check_kind("port") == "port"
    with pytest.raises(task.Refused, match="privacy, port"):
        handedit.check_kind("security")


def test_export_honours_the_recorded_kind_and_prints_it(tmp_path, monkeypatch):
    commits = [
        ("c1", ["a/Telemetry.cpp"], "egress: cut the telemetry sender", "d", "privacy"),
        ("c2", ["b/Sync.mjs"], "port repair: Sync member moved upstream", "d", "port"),          # keyword says privacy
        ("c3", ["c/plain.cpp"], "rename for 157", "d", None),                                     # nothing recorded
        ("c4", ["d/glean.rs"], "metrics stub", "d", None),                                        # keyword guess
        ("c5", ["a/Telemetry.cpp"], "follow-up build fix", "d", "port"),                          # file of an earlier cut
    ]
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": str(tmp_path)})
    monkeypatch.setattr(export, "hand_commits", lambda t: commits)
    monkeypatch.setattr(export, "_git", lambda w, *a: "diff --git a/x b/x\n+x\n")
    said = []
    out = export.export("t1", tmp_path / "patchset", "157", say=said.append)
    kinds = {n.split("-", 1)[0]: (k, how) for n, k, how in out["kinds"]}
    assert kinds["001"] == ("privacy", "recorded") and kinds["002"] == ("port", "recorded")
    assert kinds["003"] == ("port", "keyword guess, nothing recorded: review")
    assert kinds["004"] == ("privacy", "keyword guess, nothing recorded: review")
    assert kinds["005"][0] == "privacy" and "KEPT privacy" in kinds["005"][1]
    assert any(m.startswith("  [port fix] 002-") for m in said) and any(m.startswith("  [privacy cut] 001-") for m in said)
    assert any("REVIEW the kind of every patch" in m and "2 were guessed" in m for m in said)
    assert (tmp_path / "patchset/21.PORT.FIXES.157").is_dir() and (tmp_path / "patchset/22.EGRESS.LOCKDOWN.157").is_dir()


def test_hand_commits_read_the_recorded_kind_from_the_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path)
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1/journal.jsonl").write_text(
        '{"event": "hand-edit", "files": ["x.cpp"], "why": ["Sync rename"], "kind": "port"}\n', encoding="utf-8")

    def git(w, *a):
        if a[0] == "log":
            return "c1\t2026-10-02\tcheckpoint: hand edit (hand): x.cpp\n"
        return "x.cpp\n"
    monkeypatch.setattr(export, "_git", git)
    assert export.hand_commits({"id": "t1", "workdir": str(tmp_path)}) == [("c1", ["x.cpp"], "Sync rename", "2026-10-02", "port")]


# ---------------------------------------------------------------- hole 10: every give-up prints the guidance
def test_every_stop_the_loop_gives_up_on_prints_guidance():
    for fix, attempt, repeat, why in ((None, 1, False, "no known fix"), (buildrun.fix_retry, 2, True, "no progress"),
                                      (buildrun.fix_retry, buildrun.RETRIES + 1, False, "fix attempt(s)")):
        text = buildrun.stop_guidance(fix, attempt, repeat)
        assert why in text and "write the tool, add it to STOPS, run again" in text


def test_record_takes_the_kind_as_a_kind_token(monkeypatch):
    import argparse
    from fieldkit.buildh import cli as bh
    got = []
    monkeypatch.setattr(handedit, "record", lambda tid, files, note, kind=None: got.append((tid, files, note, kind)) or ["s1"])
    a = argparse.Namespace(action="record", args=["t1", "kind=privacy", "a/x.cpp", "b/y.cpp"], task=None, note="cut")
    assert bh.run(a, lambda o, f: None) == 0 and got == [("t1", ["a/x.cpp", "b/y.cpp"], "cut", "privacy")]
    a = argparse.Namespace(action="record", args=["t1", "kind=port", "kind=privacy", "a/x.cpp"], task=None, note="x")
    with pytest.raises(task.Refused, match="one kind="):
        bh.run(a, lambda o, f: None)
