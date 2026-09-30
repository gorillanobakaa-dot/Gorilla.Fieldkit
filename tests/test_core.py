"""Core: host facts, settings expansion, process runner, privacy scan, inventory."""
import json
import os
import sys
from pathlib import Path

import pytest

from fieldkit.core import privacy, settings
from fieldkit.core.host import find_tool, host, platform_ok
from fieldkit.core.proc import AGENT_ENV, Runner


def test_host_is_consistent():
    h = host()
    assert h["system"] in ("Windows", "Linux", "Darwin")
    assert h["is_windows"] != h["is_linux"] or h["system"] == "Darwin"
    json.dumps(h)                                    # JSON-safe


def test_platform_ok():
    assert platform_ok(None) and platform_ok([])
    here = "windows" if host()["is_windows"] else "linux"
    other = "linux" if here == "windows" else "windows"
    assert platform_ok([here]) and not platform_ok([other])


def test_find_tool_finds_python_and_not_nonsense():
    assert find_tool(Path(sys.executable).stem) or find_tool("python3") or find_tool("python")
    assert find_tool("definitely-not-a-real-tool-xyz") is None


def test_settings_expand_vars(tmp_path):
    local = {"kernel": {"workdir": "/tmp/kb"}}
    os.environ["FIELDKIT_TEST_VAR"] = "abc"
    got = settings.expand({"a": "${HOME}/x", "b": ["${ENV:FIELDKIT_TEST_VAR}"], "c": "${LOCAL:kernel.workdir}"},
                          local=local)
    assert got["a"] == str(Path.home()) + "/x"
    assert got["b"] == ["abc"] and got["c"] == "/tmp/kb"


def test_settings_strict_refuses_unknown():
    with pytest.raises(settings.SettingsError):
        settings.expand("${LOCAL:nope.nothing}", local={})
    assert settings.expand("${LOCAL:nope}", local={}, strict=False) == "${LOCAL:nope}"


def test_settings_reads_three_formats(tmp_path):
    (tmp_path / "a.json").write_text('{"x": 1}')
    (tmp_path / "a.yaml").write_text("x: 1\n")
    (tmp_path / "a.toml").write_text("x = 1\n")
    for ext in ("json", "yaml", "toml"):
        assert settings.read_file(tmp_path / f"a.{ext}") == {"x": 1}


def test_runner_logs_and_strips_agent_env(tmp_path):
    os.environ["CLAUDECODE"] = "1"
    r = Runner(tmp_path).run([sys.executable, "-c", "import os,sys; print('CLAUDECODE' in os.environ); sys.exit(3)"])
    assert r.returncode == 3 and not r.ok
    assert r.stdout.strip() == "False"               # stripped for the child
    assert Path(r.log).is_file() and "rc=3" in Path(r.log).read_text()
    assert "CLAUDECODE" in AGENT_ENV


def test_runner_refuses_to_stop_foreign_pid(tmp_path):
    with pytest.raises(PermissionError):
        Runner(tmp_path).stop(os.getpid())            # we did not start ourselves


def test_runner_timeout(tmp_path):
    r = Runner(tmp_path).run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    assert r.returncode == 124 and "timed out" in r.stderr


def test_runner_missing_program(tmp_path):
    r = Runner(tmp_path).run(["no-such-program-xyz"])
    assert r.returncode == 127


# -- privacy -------------------------------------------------------------------
FAKE_TOKEN = "ghp_" + "A1b2C3d4" * 5                 # built at runtime, not a real token


def test_privacy_finds_secrets_paths_emails_terms():
    text = "\n".join([f"url = https://{FAKE_TOKEN}@github.com/x/y.git",  # privacy-scan: allow
                      r"log at C:\Users\someone\Documents\x.txt",  # privacy-scan: allow
                      "home /home/someone/.bashrc",  # privacy-scan: allow
                      "mail person@realdomain.co.uk",  # privacy-scan: allow
                      "by Jane Q Public"])
    text = text.replace("  # privacy-scan: allow", "")
    kinds = {f["kind"] for f in privacy.scan_text(text, terms=["Jane Q Public"])}
    assert {"github-token", "url-credentials", "windows-user-path", "linux-home-path", "email", "private-term"} <= kinds


def test_privacy_masks_and_allows_placeholders():
    hits = privacy.scan_text(f"token {FAKE_TOKEN}")
    assert FAKE_TOKEN not in json.dumps(hits)        # never echoed whole
    assert privacy.scan_text("contact noreply@example.com, C:\\Users\\Public\\x") == []
    assert privacy.scan_text(r"docs say C:\Users\<name>\Documents") == []     # a placeholder, not a person
    assert privacy.scan_text(r"see /home/you/.config, C:\Users\me\x and C:\Users\..") == []   # doc placeholders
    assert privacy.scan_text(r"(C:\\Users\\.., /home/you) and C:\Users\me;") == []      # trailing punctuation
    assert privacy.scan_text("C:" + "\\Users\\" + "gorilla9" + "\\x,")                # a name still counts
    assert privacy.scan_text("/home/" + "gorilla9/.bashrc")                       # a real-looking name still counts


def test_privacy_allow_marker_only_silences_its_own_line():
    text = f"fixture {FAKE_TOKEN}  # privacy-scan: allow\nreal {FAKE_TOKEN}"
    hits = privacy.scan_text(text)
    assert {h["line"] for h in hits} == {2}


def test_privacy_git_mode_respects_gitignore(tmp_path):
    import subprocess
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ValueError):
        privacy.scan_path(plain, terms=[], git_only=True)          # not a repo: refuse, do not guess
    tmp_path = tmp_path / "repo"
    tmp_path.mkdir()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("secret.local.json\n")
    (tmp_path / "secret.local.json").write_text(FAKE_TOKEN)
    (tmp_path / "public.txt").write_text("fine")
    assert privacy.scan_path(tmp_path, terms=[], git_only=True) == {}
    (tmp_path / "public.txt").write_text(FAKE_TOKEN)
    assert list(privacy.scan_path(tmp_path, terms=[], git_only=True)) == [str(tmp_path / "public.txt")]


def test_privacy_term_in_parent_folder_is_not_a_finding(tmp_path):
    d = tmp_path / "jdoe-projects"
    d.mkdir()
    (d / "notes.txt").write_text("fine")
    assert privacy.scan_path(d, terms=["jdoe"]) == {}               # location is not published
    (d / "jdoe-cv.txt").write_text("fine")
    assert list(privacy.scan_path(d, terms=["jdoe"])) == [str(d / "jdoe-cv.txt")]


def test_privacy_scan_path_skips_git(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(f"url = https://{FAKE_TOKEN}@github.com/a/b")  # privacy-scan: allow
    (tmp_path / "clean.txt").write_text("nothing here")
    assert privacy.scan_path(tmp_path, terms=[]) == {}
    (tmp_path / "leak.txt").write_text(FAKE_TOKEN)
    assert list(privacy.scan_path(tmp_path, terms=[])) == [str(tmp_path / "leak.txt")]


def test_inventory_classifies(tmp_path):
    sys.path.insert(0, str(settings.ROOT))
    import inventory
    c = inventory.classify(["a/tests/test_x.py", "b/run.py", "c/conf.yaml", "d/SKILL.md", "e/x_test.go"])
    assert len(c["tests"]) == 2 and c["code"] == ["b/run.py"] and c["config"] == ["c/conf.yaml"]
    assert c["skills"] == ["d/SKILL.md"]


def test_privacy_looks_inside_zips_and_office_files(tmp_path):
    import zipfile
    z = tmp_path / "handover.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Fieldkit/clean.py", "print('ok')\n" * 50)
        zf.writestr("Fieldkit/leak.cfg", "token = " + FAKE_TOKEN + "\n")
    rep = privacy.scan_path(tmp_path, terms=[])
    assert list(rep) == [f"{z}!Fieldkit/leak.cfg"]
    assert rep[f"{z}!Fieldkit/leak.cfg"][0]["kind"] == "github-token"


def test_privacy_scan_of_a_missing_path_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        privacy.scan_path(tmp_path / "not-there.docx")
