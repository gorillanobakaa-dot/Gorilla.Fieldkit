"""fieldkit hdmi: the HDMI Harness run so an agent gets its answer, and fix can do the repair it exists for."""
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit.hdmi import cli, run


@pytest.fixture
def harness(tmp_path):
    (tmp_path / "hdmi.py").write_text("# stand-in\n", encoding="utf-8")
    return tmp_path


class Fake:
    def __init__(self, code=0, out="", child=None):
        self.code, self.out, self.child, self.calls = code, out, child, []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if self.child is not None:
            import re
            Path(re.search(r"\*> '+(.+?)'+;", argv[-1]).group(1)).write_text(self.child, encoding="utf-8")
        return subprocess.CompletedProcess(argv, self.code, self.out, "")


def test_read_only_commands_run_unelevated_and_never_relaunch(harness):
    fake = Fake(0, "TV on cable : yes")
    code, text = run.run("status", path=harness, runner=fake, admin=False)
    assert code == 0 and text == "TV on cable : yes"
    argv = fake.calls[0]
    assert argv[2:] == ["--no-elevate", "status"]          # top-level flag before the subcommand (its parser)
    assert "RunAs" not in " ".join(argv)


def test_doctor_is_passed_as_the_harness_runs_it(harness):
    argv, elevate = run.argv_for("doctor", path=harness)
    assert argv[2:] == ["doctor"] and elevate is False     # hdmi.py runs doctor before any elevation logic


def test_fix_is_elevated_waited_for_and_its_output_returned(harness):
    """Without admin rights the harness refuses the GPU-driver restart; the launcher must not drop it."""
    fake = Fake(0, child="  activation failed -> GPU driver looks stuck\n  GPU driver restart (VEN_10DE): OK")
    code, text = run.run("fix", path=harness, runner=fake, admin=False)
    assert code == 0 and "GPU driver restart (VEN_10DE): OK" in text
    cmd = " ".join(fake.calls[0])
    assert "-Verb RunAs -Wait -PassThru" in cmd and "exit $p.ExitCode" in cmd
    assert "''--no-elevate''" in cmd and "--pause" not in cmd   # the elevated copy does not relaunch or wait for ENTER


def test_already_elevated_runs_directly(harness):
    fake = Fake(0, "stable 20s")
    assert run.run("test-quick", ["--only", "H01"], path=harness, runner=fake, admin=True) == (0, "stable 20s")
    assert fake.calls[0][2:] == ["--no-elevate", "test", "quick", "--only", "H01"]


def test_unknown_and_missing(harness, tmp_path):
    with pytest.raises(ValueError):
        run.argv_for("hotspot", path=harness)
    with pytest.raises(FileNotFoundError):
        run.run("status", path=tmp_path / "nowhere", runner=Fake())


@pytest.mark.skipif(sys.platform == "win32", reason="the refusal is for other systems")
def test_refuses_off_windows():
    assert cli.main(["status"]) == 2
