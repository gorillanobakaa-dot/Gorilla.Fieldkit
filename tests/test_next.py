"""next: one decision from saved state; never runs a stage itself."""
import sys

from fieldkit.core import next as nxt
from fieldkit.core.host import host
from fieldkit.core.pipeline import Pipeline

PY = sys.executable
OTHER = "linux" if host()["is_windows"] else "windows"


def _write(p):
    return [PY, "-c", f"import pathlib; pathlib.Path(r'{p}').write_text('x')"]


def _pl(tmp_path, stages):
    return Pipeline({"name": "t", "vars": {"out": str(tmp_path)}, "stages": stages}, state_dir=tmp_path / "st")


def test_do_then_done(tmp_path):
    stages = [{"id": "a", "run": {"cmd": _write(tmp_path / "a")}, "verify": [{"files_exist": ["{out}/a"]}]},
              {"id": "b", "run": {"cmd": _write(tmp_path / "b")}, "verify": [{"files_exist": ["{out}/b"]}]}]
    p = _pl(tmp_path, stages)
    d = nxt.decide(p)
    assert d["kind"] == "DO" and d["stage"] == "a" and d["command"] == "fieldkit pipeline run t --only a"
    assert not (tmp_path / "a").exists()                               # next never runs anything
    p.run(only=["a"])
    assert nxt.decide(_pl(tmp_path, stages))["stage"] == "b"
    p.run(only=["b"])
    d = nxt.decide(_pl(tmp_path, stages))
    assert d["kind"] == "DONE" and nxt.lines(d) == ["DONE: all 2 stages verified."]


def test_blocked_names_the_known_fix(tmp_path):
    log = "dpkg-checkbuilddeps: error: Unmet build dependencies: libdw-dev:native"
    stages = [{"id": "build", "triage": "debian-kernel",
               "run": {"cmd": [PY, "-c", f"import sys; print({log!r}); sys.exit(2)"]}}]
    p = _pl(tmp_path, stages)
    p.run()
    d = nxt.decide(_pl(tmp_path, stages))
    assert d["kind"] == "BLOCKED"
    assert "Fix:" in d["why"] and "apt-get install" in d["why"]          # the known fix from the triage rules
    out = nxt.lines(d)
    assert out[0].startswith("BLOCKED: stage 'build' failed") and out[1] == "CHOOSE:" and out[-1].startswith("NEXT:")


def test_cannot_here_for_another_system(tmp_path):
    stages = [{"id": "deps", "platforms": [OTHER], "run": {"cmd": [PY, "-c", "pass"]}}]
    d = nxt.decide(_pl(tmp_path, stages))
    assert d["kind"] == "CANNOT_HERE" and OTHER in d["why"]
    stages[0]["optional"] = True
    assert nxt.decide(_pl(tmp_path, stages))["kind"] == "DONE"


def test_changed_definition_is_redone(tmp_path):
    stages = [{"id": "a", "run": {"cmd": _write(tmp_path / "a")}, "verify": [{"files_exist": ["{out}/a"]}]}]
    _pl(tmp_path, stages).run()
    stages[0]["timeout"] = 5
    d = nxt.decide(_pl(tmp_path, stages))
    assert d["kind"] == "DO" and "definition changed" in d["why"]


def test_unverified_stage_is_not_done(tmp_path):
    stages = [{"id": "a", "run": {"cmd": [PY, "-c", "pass"]}}]
    _pl(tmp_path, stages).run()
    d = nxt.decide(_pl(tmp_path, stages))
    assert d["kind"] == "DO" and "nothing proved" in d["why"]
