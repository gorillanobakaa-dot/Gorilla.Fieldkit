"""P18 build loop: emerge switches capture on/off by itself and the end distils into candidates or ledger lines."""
import json

import pytest

from fieldkit.capture import loop, store as st, switch


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "store"))
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}")
    return home


def _no_sched(argv):
    return 1, "absent"


def test_begin_end_switches_on_and_off_and_ledgers_an_empty_run(env):
    tok = loop.begin("emerge x", "emerge:x:1", home=env, py="py")
    assert tok["owner"] and switch.is_on()
    assert switch.read()["owner"] == "emerge:x:1"
    r = loop.end(tok, home=env, run=_no_sched, system="linux")
    assert r["off"] == "capture OFF" and not switch.is_on()
    assert r["distil"]["ledger"], "an empty run must leave a ledger line with a reason"
    line = json.loads((st.capture_dir() / loop.LEDGER).read_text().splitlines()[-1])
    assert "no episode" in line["reason"]


def test_a_switch_someone_else_turned_on_is_left_alone(env):
    switch.turn_on("by hand", 60, home=env, py="py")
    tok = loop.begin("emerge x", "emerge:x:2", home=env, py="py")
    assert not tok["owner"]
    r = loop.end(tok, home=env, run=_no_sched, system="linux")
    assert r["off"].startswith("left alone") and switch.is_on()
    assert switch.read()["lesson"] == "by hand"


def test_manual_lesson_keeps_the_24h_cap(env):
    with pytest.raises(ValueError):
        switch.turn_on("x", 24 * 60 + 1, home=env)


def test_candidate_written_for_an_accepted_recipe(env, tmp_path):
    rec = {"id": "recipe-build", "title": "Recipe: build", "episodes": {"in_group": 3, "replayed_ok": 3},
           "steps": [{"id": "a", "command": "cargo build", "sentence": "build", "check": "exit 0"},
                     {"id": "b", "command": "cargo test", "sentence": "test", "check": "exit 0"}]}
    d = loop.write_candidate(rec, tmp_path / "c")
    from pathlib import Path
    assert (Path(d) / "recipe-build.sh").read_text().count("cargo") == 2
    assert "test:" in (Path(d) / "card.yaml").read_text()
    ns = {"__file__": str(Path(d) / "test_recipe-build.py")}
    exec(compile((Path(d) / "test_recipe-build.py").read_text(), ns["__file__"], "exec"), ns)
    ns["test_script_has_every_step_in_order"]()
    # mutation proof: a script that lost a step fails its own test
    (Path(d) / "recipe-build.sh").write_text("set -euo pipefail\ncargo build\n")
    with pytest.raises(AssertionError):
        ns["test_script_has_every_step_in_order"]()


def test_emerge_runner_calls_the_loop(env, monkeypatch):
    import inspect
    from fieldkit.emerge import runner
    src = inspect.getsource(runner.Emerge.run)
    assert "_capture_begin" in src and "finally" in src and "_capture_end" in src


def test_end_does_not_switch_off_a_switch_taken_over_meanwhile(env):
    tok = loop.begin("emerge x", "emerge:x:3", home=env, py="py")
    switch.turn_on("someone else", 60, home=env, py="py", owner="other")
    r = loop.end(tok, home=env, run=_no_sched, system="linux")
    assert r["off"].startswith("left alone") and switch.is_on() and switch.read()["owner"] == "other"
