"""The agent finder and the fixed link probes, with faked PATH, processes and folders (owner, 2026-10-31)."""
import fnmatch

import pytest

from fieldkit.peers import agentfind, link, link_probe


class R:
    def __init__(self, out):
        self.returncode, self.stdout, self.stderr = 0, out, ""


def runner(table):
    """A fake `run`: the version line by program path; records every argv."""
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return R(table.get(argv[0], ""))
    run.calls = calls
    return run


def fs(files):
    return (lambda pat: [f for f in files if fnmatch.fnmatchcase(f.replace("\\", "/"), pat.replace("\\", "/"))],
            lambda f: f in files)


def test_process_with_no_path_entry_yields_its_path():
    run = runner({"/app/cc/claude": "2.1.302 (Claude Code)"})
    glob, isfile = fs({"/app/cc/claude"})
    r = agentfind.find_one("claude", which=lambda e: None, procs=[(5071, "claude", "/app/cc/claude")], run=run,
                           glob=glob, isfile=isfile, osname="linux", home="/h", env={})
    assert r["installed"] and r["path"] == "/app/cc/claude" and r["how"] == "process"
    assert r["running"] == [5071] and "Claude Code" in r["version"]
    assert run.calls == [["/app/cc/claude", "--version"]]          # the only thing ever executed


def test_desktop_app_own_exe_is_not_claude_code():
    run = runner({"C:/WindowsApps/app/claude.exe": "Claude 4.4"})
    glob, isfile = fs({"C:/WindowsApps/app/claude.exe"})
    r = agentfind.find_one("claude", which=lambda e: None, procs=[(1, "claude.exe", "C:/WindowsApps/app/claude.exe")],
                           run=run, glob=glob, isfile=isfile, osname="win32", home="C:/u", env={})
    assert not r["installed"] and r["running"] == [1]


@pytest.mark.parametrize("osname,home,env,path", [
    ("linux", "/srv/ag", {}, "/srv/ag/.config/Claude/claude-code/2.1.302/claude"),
    ("darwin", "/Users/user1", {}, "/Users/user1/Library/Application Support/Claude/claude-code/2.1.302/claude"),
    ("win32", "D:/ag", {"LOCALAPPDATA": "D:/ag/AppData/Local", "APPDATA": "D:/ag/AppData/Roaming"},
     "D:/ag/AppData/Roaming/Claude/claude-code/2.1.302/x64/claude.exe"),
])
def test_known_places_per_os(osname, home, env, path):
    glob, isfile = fs({path})
    run = runner({path: "2.1.302 (Claude Code)"})
    r = agentfind.find_one("claude", which=lambda e: None, procs=[], run=run, glob=glob, isfile=isfile,
                           osname=osname, home=home, env=env)
    assert r["path"] == path and r["how"] == "known place"


def test_path_wins_and_newest_known_version_wins():
    run = runner({"/usr/bin/codex": "codex 1"})
    glob, isfile = fs({"/usr/bin/codex"})
    r = agentfind.find_one("codex", which=lambda e: "/usr/bin/codex", procs=[], run=run, glob=glob, isfile=isfile,
                           osname="linux", home="/h", env={})
    assert r["how"] == "PATH"
    a, b = "/h/.nvm/versions/node/v9/bin/codex", "/h/.nvm/versions/node/v10/bin/codex"
    glob, isfile = fs({a, b})
    r = agentfind.find_one("codex", which=lambda e: None, procs=[], run=runner({a: "x", b: "y"}), glob=glob,
                           isfile=isfile, osname="linux", home="/h", env={})
    assert r["path"] == b


def test_not_found():
    glob, isfile = fs(set())
    r = agentfind.find_one("gemini", which=lambda e: None, procs=[], run=runner({}), glob=glob, isfile=isfile,
                           osname="linux", home="/h", env={})
    assert not r["installed"] and r["path"] == ""


def test_link_agents_uses_the_finder_and_lines_show_how():
    glob, isfile = fs({"/app/claude"})
    rows = link.agents(which=lambda e: None, procs=[(7, "claude", "/app/claude")],
                       run=runner({"/app/claude": "2.1.302 (Claude Code)"}), local_sessions=[], glob=glob,
                       isfile=isfile, osname="linux", home="/h", env={})
    text = "\n".join(link.agent_lines(rows))
    assert "/app/claude" in text and "[process]" in text and "running 7" in text


def test_unknown_probe_is_refused_and_nothing_runs():
    for bad in ("sh", "rm -rf /", "", None, "AGENTS"):
        r = link_probe.run(bad)
        assert not r["ok"] and "fixed set" in r["error"]


def test_where_refuses_a_shell_word():
    for bad in ("a;b", "../x", "a b", "$(x)", ""):
        assert not link_probe.run("where", bad)["ok"]
    r = link_probe.run("where", "tool", which=lambda e: "/bin/tool", glob=lambda p: [], isfile=lambda f: True,
                       osname="linux", home="/h", env={})
    assert r["ok"] and "/bin/tool" in r["text"]


def test_probe_result_is_printed(capsys):
    run = runner({"/bin/git": "git version 2"})
    r = link_probe.run("versions", which=lambda e: "/bin/" + e if e == "git" else None, run=run)
    assert r["ok"] and r["version"] == link_probe.VERSION
    out = link_probe.render(r)
    assert out.startswith("PROBE versions v1") and "git version 2" in out and "fieldkit" in out
    r = link_probe.run("temps", temp_fn=lambda: 51.5)
    assert "51.5" in link_probe.render(r)
    assert link_probe.run("sessions", sessions_fn=lambda: [{"name": "s1"}])["text"] == "s1"
    assert link_probe.run("disk")["ok"]


def test_listener_answers_probe_verb_and_refuses_unknown(monkeypatch):
    class V:
        def check(self, req):
            return None
    ok = link.handle({"verb": "probe", "name": "temps"}, V())
    assert ok["ok"] or "failed" in ok["error"]
    bad = link.handle({"verb": "probe", "name": "bash"}, V())
    assert not bad["ok"] and "fixed set" in bad["error"]


def test_cli_probe_local_prints(capsys):
    assert link.main(["probe", "disk"]) == 0
    assert capsys.readouterr().out.startswith("PROBE disk")
    assert link.main(["probe", "nonsense"]) == 3
