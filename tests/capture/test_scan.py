"""fieldkit capture scan: root table, classification, incremental transcripts, no secrets. Each can fail (mutations.yaml)."""
import subprocess
import sys

from fieldkit.capture import roots, scan, selftest_scan as ss, store as st


def run_scan(tmp_path, monkeypatch, home):
    env = {"FIELDKIT_SCAN_HOME": home, "FIELDKIT_SCAN_DOCUMENTS": home / "Documents", "FIELDKIT_SCAN_LOCAL": home / "fkl",
           "FIELDKIT_SCRATCH_ROOT": home / "scratch", "APPDATA": home / "ad", "LOCALAPPDATA": home / "ad", "TMP": home / "tmp", "TEMP": home / "tmp", "TMPDIR": home / "tmp"}
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("tempfile.tempdir", str(home / "tmp"))
    return scan.scan(st.Store(tmp_path / "cap"))


def test_labels_cover_every_kind():
    L = scan.labels_for
    assert "command sequence" in L("command", "cd x && ls")
    assert "command sequence" not in L("command", "ls")
    assert "config change" in L("command", "git config --global a b")
    assert "debugging procedure" in L("command", "journalctl -u x")
    assert "workaround" in L("command", "pip install x --force-reinstall")
    assert "test" in L("command", "python -m pytest -q")
    assert "build step" in L("command", "cargo build --release")
    assert "workflow" in L("command", "git a\ngit b\ncp x y\npython -m build\necho z")
    assert "program" in L("script", "package main", "x.go", "go")
    assert "test" in L("script", "def test_a(): pass", "t.py", "python")


def test_table_per_system():
    lin = {r["path"].replace("\\", "/") for r in roots.roots("linux", home="/h", fieldkit="/fk")}
    assert {"/tmp", "/h/.config/opencode", "/h/.local/share/opencode", "/h/.gemini", "/h/.claude/projects"} <= lin
    win = {r["name"] for r in roots.roots("win32", home="C:/h", fieldkit="C:/fk")}
    assert "system-temp" in win and "system-temp-tmp" not in win


def test_secret_names_are_never_read():
    for n in ("oauth_creds.json", "id_rsa", ".env", "token.py", "auth.json", "server.pem", "credentials_x.py"):
        assert roots.is_secret_name(n)
    assert not roots.is_secret_name("build.py")


def test_scan_is_idempotent_and_redacts(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Documents" / "Scripts").mkdir(parents=True)
    (home / "Documents" / "Scripts" / "one.py").write_text("TOKEN = 'ghp_" + "A" * 30 + "'\nprint(1)\n")
    (home / "Documents" / "Scripts" / "credentials_x.py").write_text("print('hidden')")
    s1 = run_scan(tmp_path, monkeypatch, home)
    s2 = run_scan(tmp_path, monkeypatch, home)
    assert s1["new"] == 1 and s2["new"] == 0 and s2["repeat"] == 0
    journal = (tmp_path / "cap" / "journal.jsonl").read_text()
    assert "ghp_" + "A" * 30 not in journal and "hidden" not in journal


def test_transcript_resumes_and_waits_for_a_half_line(tmp_path, monkeypatch):
    home = tmp_path / "home"
    tr = home / ".claude" / "projects" / "p" / "s.jsonl"
    tr.parent.mkdir(parents=True)
    tr.write_text(ss.claude_session("sid", "ask", [("a", "Bash", {"command": "echo one"}, False, "ok")]))
    run_scan(tmp_path, monkeypatch, home)
    more = ss.claude_session("sid", "ask2", [("b", "Bash", {"command": "echo two"}, False, "ok")]).splitlines(True)
    with open(tr, "a") as fh:
        fh.write(more[0] + more[1][:20])                          # the tool call is half written
    run_scan(tmp_path, monkeypatch, home)
    cmds = lambda: {i["command"] for i in st.Store(tmp_path / "cap").state()["items"].values()}
    assert "echo one" in cmds() and "echo two" not in cmds()
    with open(tr, "a") as fh:
        fh.write(more[1][20:] + more[2])
    s = run_scan(tmp_path, monkeypatch, home)
    assert "echo two" in cmds() and s["new"] == 1
    assert run_scan(tmp_path, monkeypatch, home)["new"] == 0


def test_lost_scan_state_does_not_double_count(tmp_path, monkeypatch):
    home = tmp_path / "home"
    tr = home / ".claude" / "projects" / "p" / "s.jsonl"
    tr.parent.mkdir(parents=True)
    tr.write_text(ss.claude_session("sid", "ask", [("a", "Bash", {"command": "echo one"}, False, "ok")]))
    run_scan(tmp_path, monkeypatch, home)
    (tmp_path / "cap" / "scan-state.json").unlink()
    s = run_scan(tmp_path, monkeypatch, home)
    assert s["new"] == 0 and s["repeat"] == 0 and s["seen"] == 1


def test_self_tests_pass_through_the_cli():
    for flag in ("--roots", "--kinds"):
        r = subprocess.run([sys.executable, "-m", "fieldkit", "capture", "self-test", flag], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout[-800:]
