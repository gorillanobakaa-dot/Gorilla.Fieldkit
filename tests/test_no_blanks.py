"""Commands a reader pastes have no blanks to fill in (2026-10-10). `cd "<your Fieldkit folder>"`, pasted as it was
given into PowerShell in C:\\WINDOWS\\system32, failed with "Illegal characters in path" and every line after it ran in
the wrong place. Documents say `cd (fieldkit where)`; the layman checker refuses a command with a <blank>."""
import subprocess
import sys

from fieldkit.gdocs import checks


def test_fieldkit_where_prints_the_folder_from_anywhere(tmp_path):
    r = subprocess.run([sys.executable, "-m", "fieldkit", "where"], capture_output=True, text=True, timeout=60,
                       cwd=str(tmp_path))
    from fieldkit.core import settings
    assert r.returncode == 0 and r.stdout.strip() == str(settings.ROOT)


def _blanks(md):
    return [l for b in checks.code_blocks(md) for l in b.splitlines()
            if checks.BLANK_RX.search(l) and checks.COMMAND_RX.match(l)]


def test_a_command_with_a_blank_is_caught_and_sample_output_is_not():
    md = ("Step 2: go there.\n\n```powershell\ncd \"<your Fieldkit folder>\"\n```\n\n"
          "You should see:\n\n```\nDONE and verified: office-scrub (run <run number>)\n```\n\n"
          "```powershell\nfieldkit office check \"<your file>\"\n```\n\n```powershell\ncd (fieldkit where)\n```\n")
    assert _blanks(md) == ['cd "<your Fieldkit folder>"', 'fieldkit office check "<your file>"']


def test_the_published_guides_have_no_blank_in_a_command():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "docs" / "dual-track"
    bad = {str(p.relative_to(root)): _blanks(p.read_text(encoding="utf-8")) for p in root.glob("*/*_layman.md")}
    assert not {k: v for k, v in bad.items() if v}
