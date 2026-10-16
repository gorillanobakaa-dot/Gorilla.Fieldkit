# Version: 1.0.0 · updated 26-10-16-03-20
"""The suite runs with no git identity, as the GitHub runners do (conftest.py). This proves the rule is in force:
on 2026-10-16 it was claimed and had silently not been applied (the edit sat behind a failed command)."""
import subprocess


def test_git_knows_no_author_while_the_tests_run(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    r = subprocess.run(["git", "-C", str(tmp_path), "var", "GIT_AUTHOR_IDENT"], capture_output=True, text=True)
    assert r.returncode != 0 or "@" not in r.stdout.split(">")[0].split("<")[-1], r.stdout
