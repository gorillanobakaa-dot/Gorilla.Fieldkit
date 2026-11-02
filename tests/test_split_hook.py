"""R15: what hooks/pre-commit does with the shares fieldkit/tests_split.py sent to other devices.
A stub tests_split stands in for the devices; the real hook runs in a scratch repository."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "pre-commit"
SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(not SH, reason="needs sh")

STUB = '''import json, os, sys
mode, st = os.environ["STUB_MODE"], sys.argv[3]
if sys.argv[1] == "start":
    json.dump({"pending": [1] if mode != "none" else [], "all": ["tests/test_ok.py"], "key": "k"}, open(st, "w"))
    if mode in ("none", "all-sent"):
        pass
    print("tests/test_ok.py") if mode != "all-sent" else None
elif sys.argv[1] == "finish":
    if mode == "fail":
        print("split: FAIL on VAIO: 1 failed", file=sys.stderr); sys.exit(1)
    if mode == "back":
        print("tests/test_back.py")
    print("split: PASS on VAIO: 1 passed", file=sys.stderr)
elif sys.argv[1] == "done":
    open(st + ".done", "w").write("x")
'''


def run_hook(tmp_path, mode):
    r = tmp_path / "repo"
    (r / "hooks").mkdir(parents=True)
    (r / "tests").mkdir()
    shutil.copy(HOOK, r / "hooks" / "pre-commit")
    (r / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n")
    (r / "tests" / "test_back.py").write_text("def test_back():\n    assert False\n")
    (r / "tests" / "test_boom.py").write_text("def test_boom():\n    assert False\n")
    (r / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    stub = tmp_path / "stub" / "fieldkit"
    stub.mkdir(parents=True)
    real = ROOT / "fieldkit"
    init = [f"__path__.append({str(real)!r})", f"exec(open({str(real / '__init__.py')!r}).read())"]
    (stub / "__init__.py").write_text(os.linesep.join(init) + os.linesep)
    (stub / "tests_split.py").write_text(STUB)
    d = tmp_path / "bin"
    d.mkdir()
    for n in ("python", "python3"):
        (d / n).write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        (d / n).chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "FIELDKIT_FULL_SUITE"}
    env.update(PATH=f"{d}:{env.get('PATH', '')}", PYTHONPATH=f"{tmp_path / 'stub'}{os.pathsep}{ROOT}",
               STUB_MODE=mode, FIELDKIT_TESTCOST_DIR=str(tmp_path / "tc"))
    env.pop("VIRTUAL_ENV", None)
    subprocess.run(["git", "init", "-q", str(r)], check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=r, check=True, env=env)
    p = subprocess.run([SH, "hooks/pre-commit"], cwd=r, capture_output=True, text=True, env=env, timeout=600)
    return p.returncode, p.stdout + p.stderr


def test_a_failure_on_another_device_refuses_the_commit(tmp_path):
    code, out = run_hook(tmp_path, "fail")
    assert code == 1 and "a test failed on another device - commit refused" in out


def test_a_share_that_came_back_runs_here_and_its_failure_refuses(tmp_path):
    code, out = run_hook(tmp_path, "back")
    assert code == 1 and "commit refused: tests/test_back.py" in out


def test_every_share_passed_is_recorded_and_the_hook_passes(tmp_path):
    code, out = run_hook(tmp_path, "pass")
    assert code == 0 and "split: PASS on VAIO" in out, out
    assert list((tmp_path / "repo" / ".git").glob("fk-split.*.done"))     # `tests_split done` ran


def test_everything_sent_elsewhere_does_not_run_the_whole_suite_here(tmp_path):
    code, out = run_hook(tmp_path, "all-sent")
    assert code == 0, out
