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


@pytest.mark.xfail(strict=True, reason="known fault: prep prints a render command with --validate, a flag "
                                       "render no longer has (validation is on by default)")
def test_prep_prints_a_render_command_that_runs(tool, tmp_path):
    code, out = run(tool, "code", "prep", src(tmp_path, CLEAN), "--output-dir", tmp_path / "docs")
    cmd = re.search(r"dual_track\.py (code render .+)", out).group(1).split()
    code, out = run(tool, *cmd)
    assert "unrecognized arguments" not in out
