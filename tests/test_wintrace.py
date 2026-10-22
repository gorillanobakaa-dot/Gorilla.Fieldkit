"""wintrace (kits/wintrace/wintrace.py): the incident-response verdicts, on recorded facts, plus live read-only checks.

The facts are the owner's laptop on 2026-10-22, while the "Please quit and reopen Claude" banner was on screen:
Claude's window (pid 21472), the taskbar and a 1x1 Lenovo helper window above it, the listening sockets, and
testdata/opens-kernel-file.csv, cut from a real 10 s opensnoop trace (tracerpt CSV; user names removed).
Nothing here elevates, records a trace, kills or changes anything.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1] / "kits" / "wintrace"
TOOL = KIT / "wintrace.py"
OPENS = KIT / "testdata" / "opens-kernel-file.csv"
pytestmark = pytest.mark.skipif(not TOOL.is_file(), reason="kits/wintrace missing")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="reads Windows APIs")

spec = importlib.util.spec_from_file_location("wintrace", TOOL)
wt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wt)

VOLMAP = {"\\Device\\HarddiskVolume3": "C:"}
MS = "CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"
ANTHROPIC = 'CN="Anthropic, PBC", O="Anthropic, PBC", L=San Francisco, S=California, C=US'


def run(*args):
    r = subprocess.run([sys.executable, "-I", str(TOOL), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return r.returncode, r.stdout + r.stderr


def win(z, pid, rect, exe="C:\\x.exe", **kw):
    w = {"z": z, "hwnd": 1000 + z, "pid": pid, "exe": exe, "title": "", "class": "", "rect": rect, "topmost": False,
         "layered": False, "clickthrough": False, "tool": False, "alpha": None, "cloaked": False, "minimized": False,
         "foreground": False}
    w.update(kw)
    return w


CLAUDE = win(18, 21472, [0, 0, 1920, 1032], "C:\\Program Files\\WindowsApps\\Claude\\app\\claude.exe", alpha=255)
TPOSD = win(1, 8944, [0, 0, 1, 1], "C:\\Program Files\\Lenovo\\tposd.exe", topmost=True, layered=True)
TASKBAR = win(11, 25336, [0, 1032, 1920, 1080], "C:\\Windows\\explorer.exe", topmost=True)


# ---- whodraws ------------------------------------------------------------------------------------------------------
def test_recorded_banner_screen_has_nothing_over_claude():
    targets, over, slivers = wt.judge_over([TPOSD, TASKBAR, CLAUDE], {21472})
    assert [t["pid"] for t in targets] == [21472]
    assert over == []                                        # the taskbar touches the edge, it does not overlap
    assert [w["pid"] for w, _, _ in slivers] == [8944]       # the 1 px helper is listed, not counted


def test_a_window_of_another_process_over_the_target_is_caught():
    fake = win(5, 666, [600, 400, 1300, 600], "C:\\ProgramData\\x\\updater.exe", topmost=True)
    _, over, _ = wt.judge_over([TPOSD, fake, TASKBAR, CLAUDE], {21472})
    assert [(w["pid"], pct) for w, _, pct in over] == [(666, 7.07)]


def test_an_invisible_click_catcher_still_counts_but_a_cloaked_or_minimized_one_does_not():
    catcher = win(3, 667, [0, 0, 800, 800], alpha=0, clickthrough=False, layered=True)
    cloaked = win(4, 668, [0, 0, 800, 800], cloaked=True)
    minimized = win(6, 669, [0, 0, 800, 800], minimized=True)
    _, over, _ = wt.judge_over([catcher, cloaked, minimized, CLAUDE], {21472})
    assert [w["pid"] for w, _, _ in over] == [667]


def test_windows_below_the_target_are_never_over_it():
    below = win(30, 670, [0, 0, 1920, 1032])
    _, over, _ = wt.judge_over([CLAUDE, below], {21472})
    assert over == []


def test_windows_of_the_target_itself_are_not_intruders():
    popup = win(2, 21472, [100, 100, 500, 300])
    targets, over, _ = wt.judge_over([popup, CLAUDE], {21472})
    assert over == [] and len(targets) == 2


def test_at_point_lists_the_stack_top_first():
    hits = wt.judge_at([TPOSD, TASKBAR, CLAUDE, win(40, 1, [0, 0, 1920, 1080], cloaked=True)], 960, 500)
    assert [w["pid"] for w in hits] == [21472]
    assert [w["pid"] for w in wt.judge_at([TPOSD, TASKBAR, CLAUDE], 960, 1050)] == [25336]


def test_over_takes_a_process_name():
    assert wt.resolve_targets("claude.exe", [CLAUDE, TASKBAR]) == {21472}
    assert wt.resolve_targets("21472", []) == {21472}


# ---- ss -------------------------------------------------------------------------------------------------------------
def sock(pid, lip, lport, state="LISTEN", proto="tcp"):
    return {"proto": proto, "state": state, "lip": lip, "lport": lport, "rip": "", "rport": 0, "pid": pid}


PROCS = {1784: {"name": "svchost.exe", "exe": "C:\\Windows\\System32\\svchost.exe"},
         4: {"name": "System", "exe": ""},
         666: {"name": "backdoor.exe", "exe": "C:\\ProgramData\\x\\backdoor.exe"},
         777: {"name": "protected.exe", "exe": ""}}
SIGS = {"C:\\Windows\\System32\\svchost.exe": {"status": "Valid", "org": "Microsoft Corporation"},
        "C:\\ProgramData\\x\\backdoor.exe": {"status": "NotSigned", "org": ""}}
SVCS = {1784: [{"name": "RpcEptMapper"}, {"name": "RpcSs"}]}


def test_recorded_listeners_are_ok():
    rows = [sock(1784, "0.0.0.0", 135), sock(4, "192.168.1.26", 139), sock(1784, "::", 135, proto="tcp6")]
    judged = wt.judge_sockets(rows, PROCS, SIGS, SVCS)
    assert [s["judgement"] for s in judged] == ["ok", "kernel", "ok"]
    assert judged[0]["services"] == ["RpcEptMapper", "RpcSs"]
    verdict, code = wt.sockets_verdict(judged)
    assert code == 0 and verdict.startswith("OK: 3 listener(s)")


def test_an_unsigned_listener_reachable_from_the_network_is_flagged():
    judged = wt.judge_sockets([sock(1784, "0.0.0.0", 135), sock(666, "0.0.0.0", 4444)], PROCS, SIGS, SVCS)
    verdict, code = wt.sockets_verdict(judged)
    assert code == 3 and verdict.startswith("FLAGGED: 1 listener") and "backdoor.exe (666, NotSigned)" in verdict


def test_an_unsigned_listener_on_loopback_only_is_not_flagged():
    judged = wt.judge_sockets([sock(666, "127.0.0.1", 4444), sock(666, "::1", 4444, proto="tcp6")], PROCS, SIGS, SVCS)
    assert wt.sockets_verdict(judged)[1] == 0 and not any(s["exposed"] for s in judged)


def test_an_unsigned_process_with_only_outgoing_connections_is_not_a_listener():
    row = dict(sock(666, "192.168.1.26", 50000, state="ESTABLISHED"), rip="1.2.3.4", rport=443)
    judged = wt.judge_sockets([row], PROCS, SIGS, SVCS)
    assert judged[0]["judgement"] == "" and wt.sockets_verdict(judged)[1] == 0


def test_udp_bound_to_all_interfaces_is_a_listener():
    judged = wt.judge_sockets([sock(666, "0.0.0.0", 5353, state="UNCONN", proto="udp")], PROCS, SIGS, SVCS)
    assert judged[0]["judgement"] == "FLAGGED"


def test_an_exposed_listener_whose_binary_cannot_be_read_is_unverified_never_ok():
    judged = wt.judge_sockets([sock(777, "0.0.0.0", 8080)], PROCS, SIGS, SVCS)
    verdict, code = wt.sockets_verdict(judged)
    assert code == 3 and verdict.startswith("UNVERIFIED")


def test_listen_and_pid_filters():
    rows = [sock(1784, "0.0.0.0", 135), dict(sock(666, "10.0.0.2", 5000, state="ESTABLISHED"), rip="1.1.1.1")]
    assert [s["pid"] for s in wt.judge_sockets(rows, PROCS, SIGS, SVCS, listen_only=True)] == [1784]
    assert [s["pid"] for s in wt.judge_sockets(rows, PROCS, SIGS, SVCS, pid=666)] == [666]


# ---- modules, signers, services ----------------------------------------------------------------------------------
def test_signer_org_is_read_from_real_subjects():
    assert wt.org_of(MS) == "Microsoft Corporation"
    assert wt.org_of(ANTHROPIC) == "Anthropic, PBC"
    assert wt.org_of("CN=Only Name") == "Only Name"
    assert wt.org_of("") == ""


def test_recorded_claude_modules_are_all_signed():
    paths = [f"C:\\Windows\\System32\\m{i}.dll" for i in range(155)] + ["C:\\app\\claude.exe", "C:\\app\\x.node"]
    sigs = {p: {"status": "Valid", "org": "Anthropic, PBC" if p.startswith("C:\\app") else "Microsoft Corporation"}
            for p in paths}
    rows, by_org, bad = wt.judge_modules(paths, sigs, pe=lambda p: False)
    verdict, code = wt.modules_verdict(21472, rows, by_org, bad)
    assert code == 0
    assert verdict == "OK: 157 modules in pid 21472, all validly signed (155 Microsoft Corporation, 2 Anthropic, PBC)"


def test_an_unsigned_module_is_found_even_when_named_like_data():
    paths = ["C:\\Windows\\System32\\ntdll.dll", "C:\\ProgramData\\x\\cache.dat", "C:\\Windows\\Fonts\\arial.ttf"]
    sigs = {"C:\\Windows\\System32\\ntdll.dll": {"status": "Valid", "org": "Microsoft Corporation"},
            "C:\\ProgramData\\x\\cache.dat": {"status": "NotSigned", "org": ""}}
    rows, by_org, bad = wt.judge_modules(paths, sigs, pe=lambda p: p.endswith(".dat"))
    assert [r["path"] for r in bad] == ["C:\\ProgramData\\x\\cache.dat"]
    assert len(rows) == 2                                   # the font is data and not listed without --all
    verdict, code = wt.modules_verdict(1, rows, by_org, bad)
    assert code == 3 and "cache.dat (NotSigned)" in verdict


def test_a_module_without_a_signature_answer_is_not_valid():
    rows, by_org, bad = wt.judge_modules(["C:\\a.dll"], {}, pe=lambda p: False)
    assert bad and bad[0]["status"] == "Unreadable"


def test_all_lists_data_files_without_judging_them():
    rows, by_org, bad = wt.judge_modules(["C:\\f.ttf"], {"C:\\f.ttf": {"status": "NotSupportedFileFormat", "org": ""}},
                                         pe=lambda p: False, all_files=True)
    assert len(rows) == 1 and not rows[0]["code"] and bad == []
    assert wt.modules_verdict(1, rows, by_org, bad)[1] == 3   # no code read at all is UNKNOWN, never OK


def test_svchost_group_from_the_command_line():
    assert wt.svchost_group("C:\\WINDOWS\\system32\\svchost.exe -k netsvcs -p -s Schedule") == "netsvcs"
    assert wt.svchost_group("C:\\app\\claude.exe --type=renderer") == ""


# ---- opensnoop ------------------------------------------------------------------------------------------------------
def test_real_trace_csv_parses_to_file_opens():
    with open(OPENS, encoding="utf-8", newline="") as f:
        opens = wt.parse_opens(f, VOLMAP)
    assert len(opens) == 99                                  # every Kernel-File Create row, nothing else
    assert not any(p.endswith("open.etl") for _, p in opens)  # the trace's own header row is not an open
    assert sum(1 for pid, _ in opens if pid == 0x74C) == 4
    assert ("C:\\Windows\\ServiceProfiles\\LocalService\\AppData\\Local\\Intel\\ipfsrv\\ipfsrv.public"
            in {p for _, p in opens})
    assert all(p.startswith("C:") for _, p in opens)


def test_device_paths_map_to_drive_letters():
    assert wt.to_drive("\\Device\\HarddiskVolume3\\Windows\\a.dll", VOLMAP) == "C:\\Windows\\a.dll"
    assert wt.to_drive("\\Device\\HarddiskVolume30\\a.dll", VOLMAP) == "\\Device\\HarddiskVolume30\\a.dll"


def test_opens_are_summarized_and_filtered():
    with open(OPENS, encoding="utf-8", newline="") as f:
        opens = wt.parse_opens(f, VOLMAP)
    s = wt.summarize_opens(opens, {0x74C: "WUDFHost.exe"}, match=r"ipfsrv")
    assert list(s) == [0x74C] and s[0x74C]["name"] == "WUDFHost.exe" and s[0x74C]["opens"] == 4
    assert list(wt.summarize_opens(opens, {}, pid=0x74C)) == [0x74C]
    verdict, code = wt.opens_verdict(len(opens), s, 10, "ipfsrv")
    assert code == 0 and verdict.startswith("MATCH: 4 open(s)")


def test_an_empty_trace_is_incomplete_never_quiet():
    assert wt.opens_verdict(0, {}, 10, None) == ("INCOMPLETE: the trace holds no file-open events in 10 s; it did not "
                                                 "record", 3)
    assert wt.opens_verdict(5, {}, 10, "x")[0].startswith("NO MATCH")


def test_trace_script_records_opens_only_converts_to_csv_and_always_stops():
    s = wt.trace_script("wintrace-opensnoop-1", "C:\\t\\o.etl", "C:\\t\\o.csv", 10)
    assert "-p 'Microsoft-Windows-Kernel-File' 0x80 0x4" in s
    assert "-of CSV" in s and "XML" not in s.upper().replace("NEVER", "")
    assert "finally {" in s and "stop 'wintrace-opensnoop-1' -ets" in s
    assert "query 'wintrace-opensnoop-1' -ets" in s                 # a running session of the name is never touched
    assert "BUSY" in s and s.index("BUSY") < s.index(" start ")
    assert f"-max {wt.ETW_MAX_MB}" in s
    for word in ("Remove-Item", "Stop-Process", "Set-", "del ", "Invoke-WebRequest"):
        assert word not in s


def test_work_dir_is_a_plain_folder_under_the_root(tmp_path):
    p = wt.work_dir("wintrace-opensnoop-1", tmp_path)
    assert p.parent == tmp_path and p.is_dir() and p.name.startswith("wintrace-opensnoop-1-")
    assert wt.work_dir("wintrace-opensnoop-1", tmp_path) != p


def test_find_elevate_takes_the_first_that_exists(tmp_path):
    a, b = tmp_path / "a" / "elevate.py", tmp_path / "b" / "elevate.py"
    assert wt.find_elevate([a, b]) is None
    b.parent.mkdir()
    b.write_text("# stub\n", encoding="utf-8")
    assert wt.find_elevate([a, b]) == b


def test_opensnoop_refuses_without_yes(monkeypatch, capsys):
    # in-process, with no elevate kit and no subprocess: even a broken refusal can never put a UAC prompt on screen
    monkeypatch.setattr(wt, "find_elevate", lambda candidates=None: None)
    monkeypatch.setattr(wt.subprocess, "run", lambda *a, **k: pytest.fail("opensnoop started something"))
    a = wt.argparse.Namespace(seconds=10, pid=None, match=None, top=15, keep=False, yes=False, json=False)
    assert wt.cmd_opensnoop(a) == 2
    assert "VERDICT: REFUSED: not started without --yes" in capsys.readouterr().out


def test_opensnoop_caps_seconds():
    # without --yes: a broken cap must not be able to start a trace either
    for seconds in ("0", "121"):
        code, out = run("opensnoop", "--seconds", seconds)
        assert code == 2 and "--seconds must be 1..120" in out


def test_fuser_that_cannot_read_the_list_says_unknown_never_free(monkeypatch, capsys, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")

    def denied(paths):
        raise OSError(6, "RmGetList failed (6)")
    monkeypatch.setattr(wt, "fuser", denied)
    assert wt.cmd_fuser(wt.argparse.Namespace(paths=[str(f)], json=False)) == 3
    out = capsys.readouterr().out
    assert "VERDICT: UNKNOWN" in out and "FREE" not in out


# ---- live, read-only ------------------------------------------------------------------------------------------------
@windows_only
def test_live_fuser_finds_this_test_holding_a_file(tmp_path):
    held = tmp_path / "held.txt"
    held.write_text("x", encoding="utf-8")
    with open(held, "rb"):
        code, out = run("fuser", held, "--json")
    d = json.loads(out)
    assert code == 0 and os.getpid() in [h["pid"] for h in d["holders"]], out
    assert d["verdict"].startswith("HELD")
    code, out = run("fuser", held)
    assert code == 0 and "VERDICT: FREE" in out and "NEXT:" in out


@windows_only
def test_live_modules_of_this_python_process():
    code, out = run("modules", os.getpid(), "--json")
    d = json.loads(out)
    assert code in (0, 3), out                               # a pip-installed .pyd may be unsigned: that is a finding
    pyd = [m for m in d["modules"] if Path(m["path"]).name.lower().startswith("python3")]
    assert pyd and all(m["status"] == "Valid" for m in pyd)
    assert any(Path(m["path"]).name.lower() == "ntdll.dll" and m["status"] == "Valid" for m in d["modules"])


@windows_only
def test_live_whodraws_enumerates_windows():
    windows = wt.enum_windows()
    assert isinstance(windows, list)
    assert [w["z"] for w in windows] == sorted(w["z"] for w in windows)
    code, out = run("whodraws")
    assert code in (0, 3) and "VERDICT:" in out and "NEXT:" in out


@windows_only
def test_live_doctor_and_svc():
    code, out = run("doctor")
    assert "restart manager" in out and "VERDICT:" in out
    code, out = run("svc", os.getpid())
    assert code == 0 and "VERDICT: NO SERVICES" in out
