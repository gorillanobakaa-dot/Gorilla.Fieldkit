"""P26: project jobs (a kernel / Firefox / OpenCode pipeline, a Firefox migration) switch capture like an emerge run."""
import pytest

from fieldkit.capture import loop, switch
from fieldkit.migrate import plan as mplan
from fieldkit.buildh import task


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("FIELDKIT_EMERGE_NO_CAPTURE", raising=False)    # conftest sets it for every other test
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}")
    monkeypatch.setenv("FIELDKIT_CAPTURE_HOME", str(home))
    return home


def test_project_of_sorts_by_project():
    assert loop.project_of("emerge debian-kernel build 12-3") == "kernel"
    assert loop.project_of("pipeline gorilla-opencode-release") == "opencode"
    assert loop.project_of("firefox migrate T-1") == "firefox"
    assert loop.project_of("emerge fieldkit") is None


def test_job_switches_on_names_the_project_and_off_again(env, monkeypatch):
    ended = []
    monkeypatch.setattr(loop, "end", lambda tok, **k: ended.append(tok))
    with loop.job("debian-kernel", "pipeline debian-kernel") as tok:
        assert tok["owner"] and switch.is_on()
        assert switch.read()["lesson"].startswith("kernel ") and switch.read()["owner"].startswith("kernel:")
    assert len(ended) == 1


def test_job_leaves_a_hand_set_capture_alone_and_distils_nothing(env, monkeypatch):
    switch.turn_on("by hand", 60, home=env, py="py")
    ended = []
    monkeypatch.setattr(loop, "end", lambda tok, **k: ended.append(tok))
    with loop.job("gorilla-opencode", "pipeline x") as tok:
        assert not tok["owner"]
    assert not ended and switch.read()["lesson"] == "by hand" and switch.is_on()


def test_job_of_no_project_records_nothing(env):
    with loop.job("fieldkit", "pipeline fieldkit") as tok:
        assert tok is None


def test_job_never_fails_the_job(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(loop, "begin", boom)
    with loop.job("debian-kernel", "p") as tok:
        assert tok is None


def test_job_is_off_under_the_tests_switch(env, monkeypatch):
    monkeypatch.setenv("FIELDKIT_EMERGE_NO_CAPTURE", "1")
    with loop.job("debian-kernel", "p") as tok:
        assert tok is None


class _M:
    tid = "T-1"
    t = {"id": "T-1"}

    def __init__(self, stage="S9"):
        self._p = {"task": "T-1", "stage": stage, "history": [{"entered": "2026-11-10 10:00:00"}],
                   "capture": {"owner": True, "who": "firefox:migrate:T-1", "since": "2026-11-10T10:00:00+00:00", "note": "x"}}

    def plan(self):
        return self._p


def test_migrate_close_ends_capture_and_distils(env, monkeypatch):
    m, saved, calls = _M(), [], []
    monkeypatch.setattr(mplan, "gate_S9", lambda m: [{"id": "S9.x", "ok": True}])
    monkeypatch.setattr(mplan, "save_plan", lambda tid, p: saved.append(p))
    monkeypatch.setattr(task, "journal", lambda *a, **k: None)
    monkeypatch.setattr(loop, "end", lambda tok, **k: calls.append(tok) or {"off": "capture OFF", "distil": {"episodes": 3, "candidates": [1], "ledger": []}})
    out = mplan.close(m)
    assert calls and calls[0]["who"] == "firefox:migrate:T-1"
    assert out["capture"] == {"off": "capture OFF", "episodes": 3, "candidates": 1, "ledger": 0} and saved[0]["capture"] is None


def test_migrate_close_refuses_before_s9_green(env, monkeypatch):
    monkeypatch.setattr(loop, "end", lambda *a, **k: pytest.fail("capture ended on a red gate"))
    with pytest.raises(task.Refused):
        mplan.close(_M("S7"))
    monkeypatch.setattr(mplan, "gate_S9", lambda m: [{"id": "S9.privacy", "ok": False}])
    with pytest.raises(task.Refused):
        mplan.close(_M("S9"))


def test_migrate_capture_begin_is_on_with_the_project_lesson(env):
    tok = mplan.capture_begin("T-9")
    assert tok["owner"] and switch.read()["lesson"] == "firefox migrate T-9"


def test_pipeline_run_of_a_kernel_pipeline_is_captured(env, monkeypatch, tmp_path):
    import sys
    from fieldkit.core.pipeline import Pipeline
    seen = []
    monkeypatch.setattr(loop, "end", lambda tok, **k: seen.append(("end", tok["who"])))
    real = loop.begin
    monkeypatch.setattr(loop, "begin", lambda lesson, who, **k: seen.append(("begin", lesson)) or real(lesson, who, **k))
    s = {"name": "debian-kernel-x", "vars": {"out": str(tmp_path)}, "stages": [
        {"id": "a", "run": {"cmd": [sys.executable, "-c", "pass"]}}]}
    Pipeline(s, state_dir=tmp_path / "st").run()
    assert seen[0] == ("begin", "kernel pipeline debian-kernel-x") and seen[1][0] == "end"


def test_migrate_init_puts_the_capture_token_in_the_plan(env, monkeypatch):
    from fieldkit.migrate import sitrep
    class M(_M):
        def plan(self):
            return None

        def previous(self):
            return None
    g = {s: {"ok": True, "red": []} for s in mplan.IDS}
    saved = []
    monkeypatch.setattr(mplan, "all_gates", lambda m: g)
    monkeypatch.setattr(mplan, "save_plan", lambda tid, p: saved.append(p))
    monkeypatch.setattr(sitrep, "build", lambda m, gates=None: {})
    monkeypatch.setattr(sitrep, "journal_block", lambda r: "")
    monkeypatch.setattr(task, "journal", lambda *a, **k: None)
    p = mplan.init(M(), at="S0")
    assert p["capture"]["owner"] and saved[0]["capture"]["who"] == "firefox:migrate:T-1" and switch.is_on()
