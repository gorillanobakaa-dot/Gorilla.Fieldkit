# Version: 1.0.0 · updated 26-10-18-17-10
"""The hard lines of an autopilot run, held against what an unattended model actually did on 2026-10-17/18."""
import pytest

from fieldkit.autopilot import hardlines as hl


@pytest.fixture
def nostop(tmp_path):
    return tmp_path / "STOP"


def d(tool, args, stop):
    return hl.decide(tool, args, stop_file=stop)[0]


@pytest.mark.parametrize("cmd", [
    "git push origin master v158.0-win64",
    'gh release create v158.0-win64 dist/x.exe --notes-file body.md',
    "gh pr merge 75 --squash",
    "gh api repos/o/r/releases -X POST -f tag_name=v1",
    "gh api repos/o/r/git/refs -f ref=refs/tags/x -f sha=abc",
    "twine upload dist/*",
    "git remote set-url origin https://evil.example/r.git",
])
def test_publishing_is_never_the_models(cmd, nostop):
    assert d("Bash", {"command": cmd}, nostop) == "deny"


@pytest.mark.parametrize("cmd", [
    "gh release view v158.0-win64 --json assets",
    "gh api repos/o/r/releases/latest",
    "gh pr list",
    "git status && git log --oneline -3",
    "python -m fieldkit build-harness build-run firefox-158.0 --force",
])
def test_reading_and_building_run_freely(cmd, nostop):
    assert d("Bash", {"command": cmd}, nostop) == "allow"


@pytest.mark.parametrize("cmd", [
    "git checkout HEAD -- patches/21.PORT.FIXES.158/003.patch",
    "python -c \"import shutil; shutil.rmtree(r'D:/work/x/Documents/Gorilla.firefox/gorilla-patchset/quarantine')\"",
    "Remove-Item -Recurse -Force D:\\work\\x\\Documents\\Build.Work",
    "git reset --hard HEAD",
    "git clean -fdx",
])
def test_discarding_work_outside_scratch_is_refused(cmd, nostop):
    assert d("Bash", {"command": cmd}, nostop) == "deny"


def test_a_delete_inside_temp_is_scratch(nostop, monkeypatch, tmp_path):
    monkeypatch.setenv("TEMP", str(tmp_path))
    assert d("Bash", {"command": f"Remove-Item -Recurse -Force {tmp_path}\\gk021"}, nostop) == "allow"


@pytest.mark.parametrize("tool,args", [
    ("Edit", {"file_path": "D:/work/x/Documents/Gorilla.firefox/leakgate/allow.json", "old_string": "a", "new_string": "*"}),
    ("Write", {"file_path": "D:/work/x/Documents/Gorilla.firefox/gorilla-patchset/decisions/PRODUCT-DECISIONS.yaml", "content": "x"}),
    ("Edit", {"file_path": "D:/work/x/Documents/Gorilla.firefox/harness/lib/preflight.py", "old_string": "a", "new_string": "b"}),
    ("replace_file_content", {"TargetFile": "D:\\work\\x\\.gemini\\GEMINI.md"}),
    ("Bash", {"command": "echo quarantine_hand_export/ >> .git/info/exclude"}),
    ("Bash", {"command": "Set-Content D:\\work\\x\\.claude\\settings.json '{}'"}),
])
def test_protections_are_never_changed_by_a_model(tool, args, nostop):
    assert d(tool, args, nostop) == "deny"


@pytest.mark.parametrize("cmd", [
    "python -m fieldkit build-harness leakgate-approve firefox-158.0",
    "python harness/record_decisions.py --write",
    "$env:GORILLA_OWNER_APPROVED=1; git commit -m x",
    "python -c \"from fieldkit.leakgate import dispositions; dispositions.approve_from('x')\"",
])
def test_owner_approvals_stay_the_owners(cmd, nostop):
    assert d("Bash", {"command": cmd}, nostop) == "deny"


def test_credentials_are_not_even_read(nostop):
    assert d("Read", {"file_path": "D:/work/x/AppData/Roaming/GitHub CLI/hosts.yml"}, nostop) == "deny"
    assert d("Bash", {"command": "type .env"}, nostop) == "deny"


def test_normal_edits_and_local_commits_are_allowed(nostop):
    assert d("Edit", {"file_path": "D:/work/x/Documents/Build.Work/firefox/158.0/browser/x.css",
                      "old_string": "a", "new_string": "b"}, nostop) == "allow"
    assert d("Bash", {"command": "git commit -m 'fix popup colours'"}, nostop) == "allow"


def test_the_owners_stop_file_leaves_only_reads(tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("owner: no more tests")
    assert d("Bash", {"command": "python -m pytest -q"}, stop) == "deny"
    assert d("Edit", {"file_path": "C:/x/y.py", "old_string": "a", "new_string": "b"}, stop) == "deny"
    assert d("Read", {"file_path": "C:/x/y.py"}, stop) == "allow"
