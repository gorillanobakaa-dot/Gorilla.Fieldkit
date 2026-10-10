"""Every DO:/NEXT: line in Fieldkit that names a fieldkit command names one that exists (2026-10-10). A small model
copies those lines exactly; a misspelt command or a removed action there is a dead end it cannot reason its way out
of."""
import contextlib
import io
import re
import shlex
from pathlib import Path

from fieldkit.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
CMD = re.compile(r"(?:DO|NEXT|CHOOSE)[^\n\"']*?(fieldkit [a-z][a-z-]*(?: [a-z][a-z-]*)?)")


def _named():
    out = set()
    for p in (ROOT / "fieldkit").rglob("*.py"):
        for m in CMD.finditer(p.read_text(encoding="utf-8", errors="replace")):
            out.add((m.group(1), str(p.relative_to(ROOT))))
    return out


def _invalid(cmd):
    """-> the parser's complaint when `cmd` names a command or action that does not exist, else None."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            build_parser().parse_args(shlex.split(cmd)[1:])
    except SystemExit:
        if "invalid choice" in err.getvalue():
            return err.getvalue().strip().splitlines()[-1]
    return None


def test_there_are_lines_to_check():
    assert len(_named()) >= 5


def test_every_named_command_exists():
    bad = [(cmd, where, why) for cmd, where in sorted(_named()) if (why := _invalid(cmd))]
    assert not bad, bad


def test_a_misspelt_one_would_be_caught():
    assert _invalid("fieldkit release-page compse")
    assert _invalid("fieldkit kernel migrate-chek")
    assert not _invalid("fieldkit kernel migrate-apply")


def test_the_leakgate_selftest_runs_without_a_build_job(tmp_path, monkeypatch):
    """Documented as `fieldkit leakgate-linux selftest` since 2026-10-02 and never connected; now a build-harness action
    that needs no job (it only reads the machine)."""
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "fieldkit", "build-harness", "leakgate-selftest", "--json"],
                       capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert r.returncode in (0, 3) and '"missing_required"' in r.stdout, r.stdout[-300:] + r.stderr[-300:]
