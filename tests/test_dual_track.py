"""dual_track.py (Scripts/DualTrackAgent, same file as toolbox/dual-track-doc-generator): the offline parts.

prep and precheck never call a model or the network, so they are tested for
real: the defects the pre-check exists to catch are caught, a clean file is
clean, and prep writes both tracks with the source inside them.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit.core import settings

COPIES = [Path(settings.expand("${DOCUMENTS}")) / "Scripts" / "DualTrackAgent" / "dual_track.py",
          Path(settings.expand("${FIELDKIT}")) / "toolbox" / "dual-track-doc-generator" / "dual_track.py"]
COPIES = [c for c in COPIES if c.is_file()]
pytestmark = pytest.mark.skipif(not COPIES, reason="dual_track.py not on this machine")


@pytest.fixture(params=COPIES, ids=lambda p: p.parent.name)
def tool(request):
    return request.param


def run(tool, *args):
    r = subprocess.run([sys.executable, str(tool), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, r.stdout + r.stderr


def src(tmp_path, body):
    p = tmp_path / "calc.py"
    p.write_text(body, encoding="utf-8")
    return p


CLEAN = 'def add(a, b):\n    """Add two numbers."""\n    return a + b\n'


def test_precheck_clean_file_has_no_findings(tool, tmp_path):
    code, out = run(tool, "precheck", src(tmp_path, CLEAN), "--output-dir", tmp_path / "out")
    assert code == 0 and "P0: 0" in out and "P2: 0" in out
    assert json.loads((tmp_path / "out" / "PRECHECK.json").read_text(encoding="utf-8")) == []


def test_precheck_catches_left_behind_todo(tool, tmp_path):
    code, out = run(tool, "precheck", src(tmp_path, CLEAN + "# TO" + "DO: handle overflow\n"),
                    "--output-dir", tmp_path / "out")
    found = json.loads((tmp_path / "out" / "PRECHECK.json").read_text(encoding="utf-8"))
    assert [d["severity"] for d in found] == ["P2"] and "P2: 1" in out


def test_precheck_catches_a_patch_that_only_deletes(tool, tmp_path):
    p = tmp_path / "drop.patch"
    p.write_text("--- a/x.js\n+++ b/x.js\n@@ -1,2 +1 @@\n keep\n-remove me\n", encoding="utf-8")
    run(tool, "precheck", p, "--output-dir", tmp_path / "out")
    found = json.loads((tmp_path / "out" / "PRECHECK.json").read_text(encoding="utf-8"))
    assert [d["severity"] for d in found] == ["P1"]


def test_prep_writes_both_tracks_with_the_source_inside(tool, tmp_path):
    code, out = run(tool, "code", "prep", src(tmp_path, CLEAN), "--output-dir", tmp_path / "docs")
    assert code == 0, out
    for track in ("layman", "developer"):
        prep = json.loads((tmp_path / "docs" / f"calc_{track}.prep.json").read_text(encoding="utf-8"))
        assert "return a + b" in prep["user_prompt"] and prep["json_schema"]["type"] == "object"


def test_prep_prints_a_render_command_that_runs(tool, tmp_path):
    """prep prints the render command to run next, with --validate; render accepts it (fixed in DualTrackAgent
    2026-10-02, in dual-track-doc-generator 2026-10-11)."""
    code, out = run(tool, "code", "prep", src(tmp_path, CLEAN), "--output-dir", tmp_path / "docs")
    cmd = re.search(r"dual_track\.py (code render .+)", out).group(1).split()
    code, out = run(tool, *cmd)
    assert "unrecognized arguments" not in out


# -- git without a shell, project rules named (dual-track-doc-generator 2026-10-11) -------------------------------
GEN = Path(settings.expand("${FIELDKIT}")) / "toolbox" / "dual-track-doc-generator" / "dual_track.py"
needs_gen = pytest.mark.skipif(not GEN.is_file(), reason="dual-track-doc-generator not gathered")


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.org", "PATH": __import__("os").environ["PATH"],
                        "HOME": str(repo)})


@pytest.fixture
def gen():
    import importlib.util
    spec = importlib.util.spec_from_file_location("dual_track_under_test", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    for i in range(3):
        (r / "f.txt").write_text(f"v{i}\n", encoding="utf-8")
        git(r, "add", "f.txt")
        git(r, "commit", "-q", "-m", f"commit {i}")
    return r


@needs_gen
def test_a_since_value_never_reaches_a_shell_or_git_as_an_option(gen, repo, tmp_path):
    marker = tmp_path / "pwned"
    for since in (f"HEAD; touch {marker}", f"$(touch {marker})", f"--output={marker}"):
        assert gen._resolve_commit(since, repo) == ""
    assert not marker.exists()
    assert gen._resolve_commit("HEAD~1", repo) == gen._git_out(["rev-parse", "HEAD~1"], cwd=repo)


@needs_gen
def test_the_session_log_lists_the_commits_since_not_before(gen, repo):
    import inspect
    fn = next(f for n, f in inspect.getmembers(gen, inspect.isfunction)
              if "since_arg" in inspect.getsource(f) and "GIT LOG" in inspect.getsource(f))
    text = fn(*([repo] + [None] * (len(inspect.signature(fn).parameters) - 2) + ["HEAD~1"]))
    text = text[0] if isinstance(text, tuple) else text
    log = text.split("GIT LOG")[1]
    assert "commit 2" in log and "commit 0" not in log


@needs_gen
def test_git_names_with_quotes_are_one_argument(gen, repo):
    (repo / "it's.md").write_text("x\n", encoding="utf-8")
    git(repo, "add", "it's.md")
    assert gen._git(repo, "ls-files", "--", "it's.md") == "it's.md"


@needs_gen
def test_a_project_rules_file_is_named_before_it_runs_and_no_rules_skips_it(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    marker = tmp_path / "ran"
    (proj / ".dual-track-rules.py").write_text(
        f"open({str(marker)!r}, 'w').close()\ndef rules(infos, collect):\n    pass\n", encoding="utf-8")
    (proj / "calc.py").write_text(CLEAN, encoding="utf-8")
    code, out = run(GEN, "precheck", proj / "calc.py", "--output-dir", tmp_path / "o1", "--no-rules")
    assert code == 0 and "skipped (--no-rules)" in out and not marker.exists()
    code, out = run(GEN, "precheck", proj / "calc.py", "--output-dir", tmp_path / "o2")
    assert "Project rules: running" in out and ".dual-track-rules.py" in out and marker.exists()
