"""fieldkit prepare (P9): every tool found and set up BEFORE a job starts; the sbin defect of `doctor --for leakgate`."""
import hashlib
import json
import os
import types

import pytest

from fieldkit.core import host, prepare

TBL = {"tools": {
    "git": {"bins": ["git"], "pkg": {"debian": "git"}},
    "nft": {"bins": ["nft"], "runs_as": "root", "pkg": {"debian": "nftables"}},
    "dnsmasq": {"bins": ["dnsmasq"], "runs_as": "root", "pkg": {"debian": "dnsmasq-base"}},
    "mitmdump": {"bins": ["mitmdump"], "runs_as": "root", "pkg": {"debian": "mitmproxy"}, "not_in": ["debian"], "pip": "mitmproxy==1",
                 "pip_root": "sudo PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install mitmproxy==1",
                 "fallback": "skip TLS"},
    "tiny": {"bins": ["tiny"], "pip": "tinypkg==1"},
    "blob": {"bins": ["blob"], "download": {"url": "https://x/blob", "sha256": "", "dest": "~/b"}},
},
    "jobs": {"core": {"tools": ["git"], "disk_gb": 1},
             "leakgate": {"match": ["leak gate"], "tools": ["nft", "dnsmasq", "mitmdump"], "privileges": ["root"],
                          "disk_gb": 5, "network": ["github.com"]},
             "small": {"tools": ["tiny"]},
             "build": {"recipes": ["firefox-debian"], "tools": []}}}
MACH = {"disk_gb": 100, "ram_gb": 16, "root": True}


def _which(have):
    return lambda b, runs_as="user": f"/usr/sbin/{b}" if b in have else None


# ---- the defect: a tool in /usr/sbin that the user's PATH lacks ------------------------------------------------
def test_find_tool_looks_in_the_sbin_folders_when_path_lacks_them(tmp_path, monkeypatch):
    sbin = tmp_path / "sbin"
    sbin.mkdir()
    exe = sbin / "nftfake"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(host, "SBIN_DIRS", [str(sbin)])
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    if host.host()["is_windows"]:
        pytest.skip("sbin is a Linux notion")
    assert host.find_tool("nftfake") == str(exe)
    assert host.find_tool("nftfake", runs_as="root") == str(exe)


def test_a_root_tool_is_not_found_in_the_users_local_bin(tmp_path, monkeypatch):
    if host.host()["is_windows"]:
        pytest.skip("sudo's secure_path is a Linux notion")
    monkeypatch.setattr(host, "SBIN_DIRS", [str(tmp_path / "sbin")])
    mine = tmp_path / "home" / ".local" / "bin"
    mine.mkdir(parents=True)
    (mine / "mitmfake").write_text("#!/bin/sh\n")
    (mine / "mitmfake").chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(mine))
    assert host.find_tool("mitmfake") is not None
    assert host.find_tool("mitmfake", runs_as="root") is None      # root's secure_path does not hold ~/.local/bin
    assert str(mine) not in host.tool_dirs("root")


# ---- what the job needs ----------------------------------------------------------------------------------------
def test_a_job_a_recipe_and_a_brief_resolve_to_jobs():
    assert prepare.resolve("leakgate", TBL) == (["leakgate"], "job")
    assert prepare.resolve("firefox-debian", TBL) == (["build"], "recipe")
    assert prepare.resolve("run the Leak Gate on the build", TBL) == (["leakgate"], "brief")
    assert prepare.resolve("write a poem", TBL) == ([], "brief")


def test_a_brief_file_is_read(tmp_path):
    f = tmp_path / "b.md"
    f.write_text("the leak gate must run")
    assert prepare.resolve(str(f), TBL)[0] == ["leakgate"]


def test_missing_tools_are_sorted_into_root_user_and_ready(tmp_path):
    r = prepare.check("leakgate", TBL, which=_which({"git", "nft"}), mach=MACH, fam="debian")
    by = {x["tool"]: x for x in r["rows"]}
    assert by["nft"]["ok"] and by["git"]["ok"]
    assert by["dnsmasq"]["step"] == "root" and by["dnsmasq"]["cmd"] == "apt-get install -y dnsmasq-base"
    # mitmproxy is NOT in Debian: the fallback source (pipx into /usr/local/bin) is the root step
    assert by["mitmdump"]["step"] == "root" and "PIPX_HOME=/opt/pipx" in by["mitmdump"]["cmd"]
    assert not r["ok"] and r["network"] == ["github.com"]


def test_a_user_level_tool_needs_no_root():
    r = prepare.check("small", TBL, which=_which({"git"}), mach=MACH, fam="debian")
    row = [x for x in r["rows"] if x["tool"] == "tiny"][0]
    assert row["step"] == "user" and r["root_cmds"] == []


def test_disk_and_root_are_checked():
    r = prepare.check("leakgate", TBL, which=_which({"git", "nft", "dnsmasq", "mitmdump"}), fam="debian",
                      mach={"disk_gb": 2, "ram_gb": 16, "root": False})
    assert any("disk" in b for b in r["blockers"])
    assert any(x["tool"] == "root" and not x["ok"] for x in r["rows"]) and not r["ok"]


# ---- set up, one ask --------------------------------------------------------------------------------------------
def test_prepare_batches_every_root_step_into_one_script_and_one_ask(tmp_path):
    r = prepare.prepare("leakgate", tbl=TBL, which=_which({"git"}), mach=MACH, fam="debian", root=tmp_path)
    assert r["status"] == "NOT-READY" and not r["ready"]
    script = open(r["script"]).read()
    assert "sudo apt-get install -y dnsmasq-base nftables" in script          # one apt call, sorted
    assert "pipx install mitmproxy==1" in script and script.count("sudo apt-get") == 1
    assert r["ask"].startswith("ONE ask for 3 step") and "sh " in r["ask"]


def test_a_user_step_is_done_by_prepare_itself_and_rechecked():
    have = {"git"}
    calls = []

    def run(argv):
        calls.append(argv)
        have.add("tiny")
        return types.SimpleNamespace(returncode=0, stderr="")

    r = prepare.prepare("small", tbl=TBL, which=lambda b, runs_as="user": b if b in have else None, run=run,
                        mach=MACH, fam="debian")
    assert calls and "tinypkg==1" in calls[0][-1]
    assert r["status"] == "READY" and any("installed" in d for d in r["done"])


def test_a_failed_user_step_leaves_the_job_not_ready():
    run = lambda argv: types.SimpleNamespace(returncode=1, stderr="boom")
    r = prepare.prepare("small", tbl=TBL, which=_which({"git"}), run=run, mach=MACH, fam="debian")
    assert not r["ready"] and any("FAILED" in d for d in r["done"])


def test_the_fallback_counts_only_when_every_missing_item_has_one(tmp_path):
    allhave = {"git", "nft", "dnsmasq"}
    r = prepare.prepare("leakgate", tbl=TBL, which=_which(allhave), mach=MACH, fam="debian", use_fallback=True,
                        root=tmp_path)
    assert r["status"] == "READY-FALLBACK" and r["ready"]
    r = prepare.prepare("leakgate", tbl=TBL, which=_which({"git"}), mach=MACH, fam="debian", use_fallback=True,
                        root=tmp_path)
    assert r["status"] == "NOT-READY"                     # nft has no fallback: the owner must provide it


def test_a_download_without_or_with_a_wrong_sha256_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ok, line = prepare.install_user("blob", TBL["tools"]["blob"])
    assert not ok and "no sha256" in line
    spec = {"bins": ["blob"], "download": {"url": "u", "sha256": "0" * 64, "dest": str(tmp_path / "bin" / "blob")}}
    fetch = lambda u, p: open(p, "wb").write(b"data")
    ok, line = prepare.install_user("blob", spec, fetch=fetch)
    assert not ok and not (tmp_path / "bin" / "blob").exists()
    spec["download"]["sha256"] = hashlib.sha256(b"data").hexdigest()
    ok, line = prepare.install_user("blob", spec, fetch=fetch)
    assert ok and (tmp_path / "bin" / "blob").read_bytes() == b"data"


# ---- wiring and the journal -------------------------------------------------------------------------------------
def test_the_gate_stops_a_job_that_is_not_ready_and_lets_a_ready_one_go(monkeypatch, capsys):
    monkeypatch.setattr(prepare, "prepare", lambda t: {"ready": False, "kind": "job", "target": t, "jobs": [],
                         "family": "debian", "rows": [], "blockers": ["disk"], "network": [], "status": "NOT-READY",
                         "next": "x"})
    out = []
    assert prepare.gate("x", out=out.append) is False and "NOT READY" in out[0]
    monkeypatch.setattr(prepare, "prepare", lambda t: {"ready": True})
    assert prepare.gate("x", out=out.append) is True


def test_a_broken_table_is_said_and_does_not_block(monkeypatch):
    def boom(t):
        raise ValueError("bad yaml")
    monkeypatch.setattr(prepare, "prepare", boom)
    out = []
    assert prepare.gate("x", out=out.append) is True and "skipped" in out[0]


def test_every_job_start_calls_the_gate():
    import inspect
    from fieldkit import cli, emerge
    from fieldkit import lifecycle
    for fn in (emerge.main, lifecycle.cmd_task, cli.runbook_main, cli.cmd_goal):
        assert "prepare" in inspect.getsource(fn), fn.__name__


def test_a_mid_job_missing_tool_is_journaled(tmp_path):
    row = prepare.defect("nft", "leakgate", root=tmp_path)
    assert row["known"] is True
    line = (tmp_path / "state" / "prepare" / "defects.jsonl").read_text().strip()
    assert json.loads(line)["tool"] == "nft"


def test_the_real_table_is_well_formed():
    t = prepare.table()
    for jid, j in t["jobs"].items():
        for tid in j["tools"]:
            assert tid in t["tools"], (jid, tid)
    for tid, tool in t["tools"].items():
        assert tool["bins"], tid
        assert tool.get("pkg") or tool.get("pip") or tool.get("download") or tool.get("fallback"), tid
        if tool.get("pip") and tool.get("runs_as") == "root":
            assert tool.get("pip_root"), tid
    # the Debian 13 case from the owner: mitmproxy is not in apt, dnsmasq is the binary package
    assert "debian" in t["tools"]["mitmdump"]["not_in"] and t["tools"]["dnsmasq"]["pkg"]["debian"] == "dnsmasq-base"
