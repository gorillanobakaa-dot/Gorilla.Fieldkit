"""hooks/post-merge and post-checkout recompile Python after a pull or branch switch (rules/CORE.md BUILDS)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_post_merge_calls_compileonce_and_never_fails_git():
    text = (ROOT / "hooks" / "post-merge").read_text()
    assert "compileonce build" in text
    assert "exit 1" not in text


def test_post_checkout_runs_only_on_a_branch_switch():
    text = (ROOT / "hooks" / "post-checkout").read_text()
    assert '[ "$3" = "1" ] || exit 0' in text and "post-merge" in text


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh")
def test_post_merge_compiles_a_stale_file(tmp_path):
    repo = tmp_path / "r"
    (repo / "fieldkit").mkdir(parents=True)
    (repo / "hooks").mkdir()
    shutil.copy(ROOT / "hooks" / "post-merge", repo / "hooks" / "post-merge")
    src = repo / "fieldkit" / "probe_mod.py"
    src.write_text("X = 1\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    env = dict(os.environ, PYTHONPATH=str(ROOT), PATH=os.pathsep.join([str(Path(sys.executable).parent),
                                                                      os.environ.get("PATH", "")]))
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    r = subprocess.run(["sh", "hooks/post-merge"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0
    assert list((repo / "fieldkit" / "__pycache__").glob("probe_mod.*.pyc"))
