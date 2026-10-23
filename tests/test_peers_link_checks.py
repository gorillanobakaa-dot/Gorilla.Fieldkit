"""fieldkit peers link check: the Windows lessons of 2026-10-22 as prechecks. Fakes for the peer, arp and git."""
import subprocess

from fieldkit.peers import link_checks as c


def out(text, code=0):
    return lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=text, stderr="")


def test_clock_skew_against_the_peer():
    now = iter([1000.0, 1000.2])
    r = c.clock(call_fn=lambda req: {"ok": True, "time": 1000.1 + 30}, now=lambda: next(now))
    assert r["verdict"] == "OK" and "+30.0 s" in r["detail"]
    now = iter([1000.0, 1000.2])
    r = c.clock(call_fn=lambda req: {"ok": True, "time": 1000.1 - 61}, now=lambda: next(now))
    assert r["verdict"] == "PROBLEM" and "-61.0 s" in r["detail"] and "w32tm" in r["fix"]
    assert c.clock(call_fn=lambda req: {"ok": True}, now=lambda: 0.0)["verdict"] == "PROBLEM"
    r = c.clock(call_fn=lambda req: {"ok": False, "error": "refused"}, now=lambda: 0.0)
    assert r["verdict"] == "PROBLEM" and "refused" in r["detail"]


def test_firewall_rule_and_its_one_click_fix():
    assert c.firewall("win32", lambda: True)["verdict"] == "OK"
    r = c.firewall("win32", lambda: False)
    assert r["verdict"] == "PROBLEM" and "setup --first" in r["fix"] and "elevate" in r["fix"]
    assert c.firewall("linux", lambda: 1 / 0)["verdict"] == "OK"


ARP_STALE = "Interface: 10.77.0.1 --- 0x11\n  10.77.0.2             00-00-00-00-00-00     invalid\n"
ARP_GOOD = "Interface: 10.77.0.1 --- 0x11\n  10.77.0.2             3c-07-71-a2-9f-d0     dynamic\n"


def test_stale_all_zero_neighbor_after_a_cable_move():
    r = c.neighbor("10.77.0.2", "win32", out(ARP_STALE))
    assert r["verdict"] == "PROBLEM" and "arp -d 10.77.0.2" in r["fix"]
    assert c.neighbor("10.77.0.2", "win32", out(ARP_GOOD))["verdict"] == "OK"
    assert c.neighbor("10.77.0.9", "win32", out(ARP_STALE))["verdict"] == "OK"     # another host's entry


def test_put_says_where_files_land(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    r = c.put()
    assert r["verdict"] == "OK" and str(tmp_path / "received") in r["detail"] and "not the Desktop" in r["detail"]
    (tmp_path / "received").rmdir()
    (tmp_path / "received").write_text("a file where the folder should be")
    assert c.put()["verdict"] == "PROBLEM"


def test_shell_lessons():
    r = c.shell(r"cd C:\Tools\Fieldkit && python -m fieldkit", "bash")
    assert r["verdict"] == "PROBLEM" and "Git Bash eats" in r["detail"]
    assert c.shell('cd "C:/Tools/Fieldkit" && python -m fieldkit', "bash")["verdict"] == "OK"
    assert c.shell(r"cd 'C:\Tools\x'", "bash")["verdict"] == "OK"                      # quoted: kept
    r = c.shell("python - <<EOF\nprint('a\\nb')\nEOF", "bash")
    assert r["verdict"] == "PROBLEM" and "heredoc" in r["detail"]
    r = c.shell("cd $HOME; git pull && fieldkit sync", "powershell")
    assert r["verdict"] == "PROBLEM" and "refuses &&" in r["detail"]
    assert c.shell("export X=1; cd ~/x", "powershell")["verdict"] == "PROBLEM"
    assert c.shell("cd $HOME; fieldkit sync", "powershell")["verdict"] == "OK"
    assert c.shell("x", "cmd")["verdict"] == "PROBLEM"


def test_crlf_on_written_files(tmp_path):
    lf, crlf, ps1 = tmp_path / "a.sh", tmp_path / "b.py", tmp_path / "c.ps1"
    lf.write_bytes(b"#!/bin/sh\necho\n")
    crlf.write_bytes(b"x = 1\r\n")
    ps1.write_bytes(b"Write-Host x\r\n")
    assert c.crlf([lf, ps1])["verdict"] == "OK"
    r = c.crlf([lf, crlf, ps1])
    assert r["verdict"] == "PROBLEM" and "b.py" in r["detail"] and "c.ps1" not in r["detail"]
    blob = tmp_path / "d.bin"
    blob.write_bytes(b"\0\r\n")
    assert c.crlf([blob])["verdict"] == "OK"


def test_commit_runs_the_fast_checks_first(tmp_path):
    (tmp_path / "x.py").write_bytes(b"a\r\n")
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[0] == "git":
            return out("x.py\n")()
        return out("1 finding(s) in 1 file(s)\n", 3)()
    rows = c.commit(run, cwd=str(tmp_path))
    assert [r["verdict"] for r in rows] == ["PROBLEM", "PROBLEM"] and "1 finding" in rows[1]["detail"]
    assert any("privacy" in a for a in calls)
    rows = c.commit(lambda argv, **kw: out("")() if argv[0] == "git" else out("0 finding(s)\n")(), cwd=str(tmp_path))
    assert [r["verdict"] for r in rows] == ["OK", "OK"]


def test_write_size(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("x\n" * c.WRITE_LINES)
    assert c.write(f)["verdict"] == "OK"
    f.write_text("x\n" * (c.WRITE_LINES + 1))
    assert c.write(f)["verdict"] == "PROBLEM"
    f.write_text("x" * (c.WRITE_BYTES + 1))
    assert c.write(f)["verdict"] == "PROBLEM"
    assert c.write(tmp_path / "none")["verdict"] == "PROBLEM"


def test_every_symptom_finds_its_own_entry():
    for s in c.SYMPTOMS:
        r = c.symptom(s["example"])
        assert r["verdict"] == "OK" and s["id"] in r["detail"], s["id"]
    assert c.symptom("the moon is purple")["verdict"] == "PROBLEM"
    assert "1f48c1c" in c.symptom("fieldkit publish refused: no upstream")["detail"]


def test_main_exit_codes(tmp_path, capsys):
    f = tmp_path / "ok.sh"
    f.write_bytes(b"echo\n")
    assert c.main(["crlf", str(f)]) == 0 and "VERDICT: OK" in capsys.readouterr().out
    assert c.main(["shell", "--shell", "powershell", "a && b"]) == 3
    assert "NEXT: rewrite as said" in capsys.readouterr().out
    assert c.main(["shell", "a"]) == 2 and c.main(["write"]) == 2
    assert c.main(["symptom", "--json", "exit", "127"]) == 0
