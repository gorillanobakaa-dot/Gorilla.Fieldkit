"""`fieldkit compileonce check` (fieldkit/compileonce): fails on a per-call `python -c` / `go run` / bytecode-off, and the
thermal proof collects the load processes it kills (2026-11-13: four defunct python children held for 83 h)."""
import shutil
import subprocess
import sys

import pytest

from fieldkit import compileonce
from fieldkit.thermal import sensors

DATA = compileonce.load()


@pytest.mark.parametrize("rule", DATA["rules"], ids=lambda r: r["id"])
def test_every_rule_matches_its_own_sample(rule):
    assert compileonce.scan_text(rule["sample"], [rule]) == [(1, rule["id"])]


@pytest.mark.parametrize("line", [
    'subprocess.run([py, "-c", "import x"])',
    'run([sys.executable, "-P", "-c", "print(1)"])',
    'cmd = "python3 -c \'print(1)\'"',
    'x=$(python -c "print(1)")',
    '"hooks": {"command": "python -c \\"import x\\""}',
    'subprocess.run(["go", "run", "./cmd/x"])',
    'run: go run ./cmd/x',
])
def test_per_call_compile_is_found(line):
    assert compileonce.scan_text(line, DATA["rules"])


@pytest.mark.parametrize("line", [
    'subprocess.run([sys.executable, "-m", "fieldkit.thermal.burn"])',
    'subprocess.run([exe, "-d", "-c", "-q"])',          # xz, not an interpreter
    'subprocess.run(["go", "build", "-o", out, "./cmd/x"])',
    'env["PYTHONPYCACHEPREFIX"] = cache',
])
def test_compiled_once_is_not_flagged(line):
    assert compileonce.scan_text(line, DATA["rules"]) == []


def test_this_checkout_is_clean():
    res = compileonce.check(compileonce.Path(__file__).resolve().parents[1])
    assert res == {"findings": [], "stale_allow": []}


def test_new_hit_outside_allow_fails_and_stale_allow_fails(tmp_path):
    (tmp_path / "fieldkit").mkdir()
    (tmp_path / "fieldkit" / "hot.py").write_text('import subprocess\nsubprocess.run([py, "-c", "x"])\n')
    d = dict(DATA, allow=[{"file": "fieldkit/gone.py", "rule": "go-run", "why": "x"}])
    (tmp_path / "fieldkit" / "gone.py").write_text("pass\n")
    res = compileonce.check(tmp_path, d)
    assert [(f["file"], f["line"], f["rule"]) for f in res["findings"]] == [("fieldkit/hot.py", 2, "python-dash-c")]
    assert res["stale_allow"] == [{"file": "fieldkit/gone.py", "rule": "go-run"}]


def test_cli_exit_codes(tmp_path):
    (tmp_path / "fieldkit").mkdir()
    assert compileonce.main(["check", "--root", str(tmp_path)]) == 0
    (tmp_path / "fieldkit" / "hot.py").write_text('run(["go", "run", "x"])\n')
    assert compileonce.main(["check", "--root", str(tmp_path)]) == 3


@pytest.mark.skipif(sys.platform == "win32", reason="zombie state is a Linux/macOS notion")
def test_prove_live_collects_what_it_kills():
    procs = sensors._load(2)
    sensors._stop_load(procs)
    assert all(p.poll() is not None for p in procs)     # collected: no defunct child left


def test_load_is_a_module_not_dash_c():
    src = sensors._load.__code__.co_consts
    assert "-c" not in src and "fieldkit.thermal.burn" in src
