"""fieldkit repair, process/worktree/binary/ini tools (I-19, part 2), and the wiring of all 11 (cli, cards, skill)."""
import json
import struct
import subprocess

import yaml

from fieldkit import repair
from fieldkit.repair import evidence_macho_extract as mo
from fieldkit.repair import pid_guarded_stop as pg
from fieldkit.repair import pwsh_parent_chains as pc
from fieldkit.repair import tpfan_config_verify as tp
from fieldkit.repair import worktree_classify_recycle as wc


# --- pid-guarded-stop ----------------------------------------------------------------------------------------------
TABLE = {10: {"pid": 10, "name": "Node.EXE", "started": "2026-11-09 01:00:00"}}


def test_pid_guard_refuses_reused_pid_and_wrong_name():
    assert pg.verify_pid_target(TABLE, 10, "node", "2026-11-09 01:00:00")[0] == "STOP"
    assert pg.verify_pid_target(TABLE, 10, "node", "2026-11-09 02:00:00")[0] == "REFUSED"
    assert pg.verify_pid_target(TABLE, 10, "python")[0] == "REFUSED"
    assert pg.verify_pid_target(TABLE, 11, "node")[0] == "SKIP"


def test_pid_guard_cli(tmp_path):
    f = tmp_path / "t.json"
    f.write_text(json.dumps(list(TABLE.values())), encoding="utf-8")
    base = ["--proc-json", str(f), "--pid", "10", "--name", "node"]
    assert pg.main(base) == 0
    assert pg.main(base + ["--started", "2000-01-01 00:00:00"]) == 3
    assert pg.main(base + ["--stop"]) == 1                      # --stop needs --started, and a live table


def test_pid_guard_stop_needs_started(capsys):
    assert pg.main(["--pid", "1", "--name", "x", "--stop"]) == 1


def test_pid_guard_stops_a_real_process_only_when_it_matches():
    import sys
    import time
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        tab = pg.live_table(p.pid)
        started = tab[p.pid]["started"]
        assert pg.main(["--pid", str(p.pid), "--name", "python", "--started", "1999-01-01 00:00:00", "--stop"]) == 3
        assert p.poll() is None                                  # refused: still running
        assert pg.main(["--pid", str(p.pid), "--name", tab[p.pid]["name"], "--started", started, "--stop"]) == 0
        for _ in range(50):
            if p.poll() is not None:
                break
            time.sleep(0.1)
        assert p.poll() is not None
    finally:
        if p.poll() is None:
            p.kill()


# --- pwsh-parent-chains --------------------------------------------------------------------------------------------
PROCS = [{"pid": 1, "ppid": 0, "name": "explorer.exe"}, {"pid": 2, "ppid": 1, "name": "node.exe"},
         {"pid": 3, "ppid": 2, "name": "pwsh.exe"}, {"pid": 4, "ppid": 3, "name": "conhost.exe"},
         {"pid": 5, "ppid": 2, "name": "powershell.exe"}, {"pid": 6, "ppid": 6, "name": "pwsh.exe"},
         {"pid": 7, "ppid": 8, "name": "pwsh.exe"}, {"pid": 8, "ppid": 7, "name": "cmd.exe"}]


def test_parent_chains_owners_and_loops():
    chains = pc.build_chains(PROCS)
    assert [("conhost.exe", 4), ("pwsh.exe", 3), ("node.exe", 2), ("explorer.exe", 1)] in chains
    assert pc.owners(chains)["node.exe(2)"] == 3                  # pwsh 3, conhost 4 (via pwsh), powershell 5
    assert [("pwsh.exe", 6)] in chains                            # self-parent stops
    assert len([c for c in chains if c[0][1] == 7][0]) == 2       # 7 <- 8 <- 7 stops at the repeat


def test_parent_chains_cli(tmp_path, capsys):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(PROCS), encoding="utf-8")
    assert pc.main(["--proc-json", str(f), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["owners"][0]["owner"] == "node.exe(2)"
    assert pc.main(["--proc-json", str(tmp_path / "none.json")]) == 1


# --- worktree-classify-recycle -------------------------------------------------------------------------------------
def test_worktree_verdicts():
    prs = [{"headRefName": "feat/a", "headRefOid": "aaa", "state": "MERGED"}, {"headRefName": "feat/b", "headRefOid": "bbb", "state": "OPEN"}]
    row = lambda br, head, anc=False, dirty=(): wc.classify("p", "refs/heads/" + br, head, list(dirty), anc, 1, 2.0, prs)
    assert row("feat/a", "aaa")["verdict"] == "merged"                  # squash-merged: not an ancestor, the PR says so
    assert row("x", "ccc", anc=True)["verdict"] == "merged"
    assert row("feat/b", "bbb")["verdict"] == "open-pr"
    assert row("feat/z", "zzz")["verdict"] == "unmerged"
    assert row("feat/a", "aaa", dirty=["M f"])["verdict"] == "merged+dirty"


def test_worktree_cli_lists_only_clean_merged(tmp_path, capsys):
    fx = {"prs": [{"headRefName": "a", "headRefOid": "h1", "state": "MERGED"}],
          "worktrees": [{"path": "/w/1", "branch": "refs/heads/a", "head": "h1"},
                        {"path": "/w/2", "branch": "refs/heads/a", "head": "h1", "dirty_lines": ["M x"]},
                        {"path": "/w/3", "branch": "refs/heads/q", "head": "h3"}]}
    f = tmp_path / "w.json"
    f.write_text(json.dumps(fx), encoding="utf-8")
    assert wc.main(["--fixture-json", str(f), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert [r["path"] for r in d["worktrees"] if r["recyclable"]] == ["/w/1"]
    assert "fieldkit size recycle" in d["next"]


def test_worktree_porcelain_parse():
    t = "worktree /a\nHEAD abc\nbranch refs/heads/main\n\nworktree /b\nHEAD def\ndetached\n"
    assert [w["worktree"] for w in wc.parse_worktree_porcelain(t)] == ["/a", "/b"]


def test_worktree_live_in_a_real_repo(tmp_path):
    g = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True, check=True)
    g("init", "-q", "-b", "main")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i")
    wts, prs, notes = wc.live(tmp_path, run=lambda cmd, **k: (subprocess.CompletedProcess(cmd, 1, "", "") if cmd[0] == "gh"
                                                              else subprocess.run(cmd, **k)))
    assert len(wts) == 1 and wts[0]["is_ancestor_of_main"] and prs == [] and notes


# --- evidence-macho-extract ----------------------------------------------------------------------------------------
def _thin(minos=(10, 13, 0), cmd=0x24, dep="/usr/lib/libSystem.B.dylib"):
    v = (minos[0] << 16) | (minos[1] << 8) | minos[2]
    lc_ver = struct.pack("<IIII", cmd, 16, v, v)
    name = dep.encode() + b"\0"
    dsize = (24 + len(name) + 7) // 8 * 8
    lc_dep = struct.pack("<IIIIII", 0xC, dsize, 24, 0, 0, 0) + name.ljust(dsize - 24, b"\0")
    cmds = lc_ver + lc_dep
    hdr = struct.pack("<IiiIII", 0xFEEDFACF, 0x01000007, 3, 2, 2, len(cmds)) + struct.pack("<II", 0, 0)
    return hdr + cmds


def test_macho_thin_version_and_deps(tmp_path):
    f = tmp_path / "ok"
    f.write_bytes(_thin())
    s = mo.parse_macho(f.read_bytes())[0]
    assert s["min_os"][0]["version"] == "10.13.0" and s["deps"] == ["/usr/lib/libSystem.B.dylib"]
    assert mo.main([str(f)]) == 0
    g = tmp_path / "new"
    g.write_bytes(_thin((11, 0, 0)))
    assert mo.main([str(g)]) == 3 and mo.main([str(g), "--max-minos", "11.0"]) == 0


def test_macho_universal_and_garbage(tmp_path):
    a, b = _thin((10, 9, 0)), _thin((12, 0, 0))
    table = 8 + 2 * 20
    fat = (struct.pack(">II", 0xCAFEBABE, 2) + struct.pack(">iiIII", 7, 3, table, len(a), 0)
           + struct.pack(">iiIII", 7, 3, table + len(a), len(b), 0) + a + b)
    assert mo.newest_macos(mo.parse_macho(fat)) == (12, 0, 0)
    f = tmp_path / "fat"
    f.write_bytes(fat)
    assert mo.main([str(f)]) == 3                                          # one slice asks for 12.0
    junk = tmp_path / "j"
    junk.write_bytes(b"hello world")
    assert mo.main([str(junk)]) == 3
    trunc = tmp_path / "t"
    trunc.write_bytes(_thin()[:40])
    assert mo.main([str(trunc)]) == 3
    assert mo.main([str(tmp_path / "missing")]) == 1


# --- tpfan-config-verify -------------------------------------------------------------------------------------------
INI = b"[Main]\r\nActive=1\r\nFoo=2\r\n"


def test_tpfan_switch_and_guards(tmp_path):
    f = tmp_path / "TPFanControl.ini"
    f.write_bytes(INI)
    assert tp.main([str(f), "--set-active", "4"]) == 3 and f.read_bytes() == INI
    assert tp.main([str(f), "--set-active", "4", "--require-current", "2", "--apply"]) == 3 and f.read_bytes() == INI
    assert tp.main([str(f), "--set-active", "4", "--require-current", "1", "--apply"]) == 0
    assert f.read_bytes() == b"[Main]\r\nActive=4\r\nFoo=2\r\n"
    two = tmp_path / "two.ini"
    two.write_bytes(b"Active=1\nActive=2\n")
    assert tp.main([str(two), "--set-active", "4", "--apply"]) == 3


def test_tpfan_exe_pin(tmp_path):
    import hashlib
    ini, exe = tmp_path / "a.ini", tmp_path / "TPFanControl.exe"
    ini.write_bytes(INI)
    exe.write_bytes(b"binary")
    good = hashlib.sha256(b"binary").hexdigest()
    assert tp.main([str(ini), "--exe", str(exe), "--sha256", good]) == 0
    assert tp.main([str(ini), "--exe", str(exe), "--sha256", "0" * 64, "--set-active", "4", "--apply"]) == 3
    assert ini.read_bytes() == INI                                          # a wrong pin blocks the edit
    assert tp.main([str(ini), "--exe", str(exe), "--sha256", "abc"]) == 1


# --- wiring: cli, cards, skill -------------------------------------------------------------------------------------
def test_every_action_has_a_module_a_card_and_a_skill_line():
    import importlib
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    cards = {t["id"]: t for t in yaml.safe_load((root / "fieldkit/desk/tools.yaml").read_text(encoding="utf-8"))["tools"]}
    skill = (root / "skills/fieldkit-repair/SKILL.md").read_text(encoding="utf-8")
    for name, mod in repair.ACTIONS.items():
        assert callable(importlib.import_module("fieldkit.repair." + mod).main), name
        assert cards[name]["entry"] == ["python", "-m", "fieldkit", "repair", name], name
        assert f"fieldkit repair {name}" in skill, name


def test_cli_dispatches_to_the_module(tmp_path):
    from fieldkit import cli
    f = tmp_path / "a.cpp"
    f.write_text("int a(){\n", encoding="utf-8")
    assert cli.main(["repair", "brace-parity-check", str(f)]) == 3
    f.write_text("int a(){}\n", encoding="utf-8")
    assert cli.main(["repair", "brace-parity-check", str(f)]) == 0
