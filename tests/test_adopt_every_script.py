"""parity helpers / parity installed, the wider sweep roots, the friction row and the honesty UNCARDED finding."""
from pathlib import Path

from fieldkit import parity_goal
from fieldkit.desk import sweep
from fieldkit.lifecycle import friction
from fieldkit.method import honesty

H = "a" * 64


def _repo(tmp_path, linux_extra="", backlog="", darwin_lacks=False):
    tb = tmp_path / "oc" / "internal" / "toolbox"
    tb.mkdir(parents=True)
    rows = []
    for s, ext in (("windows-amd64", ".exe"), ("linux-amd64", ""), ("darwin-amd64", "")):
        for n in ("pfind", "gsize") + (("gonly",) if s == "windows-amd64" and darwin_lacks else ()):
            rows.append(f"{H}  {s}/{n}{ext}")
    rows.append(linux_extra)
    (tb / "SHA256SUMS").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (tb / "parity_test.go").write_text("var parityBacklog = map[string]bool{" + backlog + "}\n", encoding="utf-8")
    return tmp_path / "oc"


def test_helpers_equal_and_backlog_empty(tmp_path):
    r = parity_goal.helpers(_repo(tmp_path))
    assert r["ok"] and r["helpers"] == ["gsize", "pfind"] and r["problems"] == []


def test_helpers_names_what_a_system_lacks(tmp_path):
    r = parity_goal.helpers(_repo(tmp_path, darwin_lacks=True))
    assert not r["ok"] and r["missing"]["linux-amd64"] == ["gonly"] and "darwin-amd64 lacks: gonly" in r["problems"]


def test_helpers_backlog_not_empty(tmp_path):
    r = parity_goal.helpers(_repo(tmp_path, backlog='"x": true,'))
    assert not r["ok"] and "parityBacklog is not empty" in r["problems"][0]


def test_helpers_unreadable_repo_is_an_error_not_a_pass(tmp_path):
    r = parity_goal.helpers(tmp_path / "nowhere")
    assert not r["ok"] and "error" in r


def test_installed_equal_to_each_other_and_the_newest():
    r = parity_goal.installed(mine="0.1.156", peers={"vaio": "0.1.156"}, newest="0.1.156")
    assert r["ok"]


def test_installed_peer_behind_is_a_finding():
    r = parity_goal.installed(mine="0.1.156", peers={"vaio": "0.1.154"}, newest="0.1.156")
    assert not r["ok"] and "vaio has gorilla-opencode 0.1.154" in r["problems"][0]


def test_installed_both_equal_but_behind_the_release_fails():
    r = parity_goal.installed(mine="0.1.154", peers={"vaio": "0.1.154"}, newest="0.1.156")
    assert not r["ok"] and len(r["problems"]) == 2


def test_installed_without_a_peer_cannot_pass():
    r = parity_goal.installed(mine="0.1.156", peers={}, newest="0.1.156")
    assert not r["ok"] and "no peer" in r["problems"][0]


def test_installed_unknown_newest_is_an_error():
    r = parity_goal.installed(mine="1", peers={"v": "1"}, newest=None, run=lambda *a, **k: (1, "", "no gh"))
    assert r.get("error") and not r["ok"]


def test_default_repo_from_sync_repos():
    local = {"sync": {"repos": ["/x/Fieldkit", "/x/Gorilla.Opencode"]}}
    assert parity_goal.default_repo(local) == Path("/x/Gorilla.Opencode")


# ---- the sweep sees local/ and the scratch scripts, ignore carries reasons ------------------------------------------
def test_default_roots_name_local_and_scratch_scripts():
    tm = [t for t, _ in sweep.DEFAULT_ROOTS]
    assert "${FIELDKIT}/local" in tm and "${SCRATCH}/scripts" in tm
    skip = dict(sweep.DEFAULT_ROOTS)["${FIELDKIT}/local"]
    assert "tests" in skip and "kits" in skip


def test_local_root_skips_its_tests_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "DEFAULT_ROOTS", ((str(tmp_path / "local"), ("tests",)),))
    (tmp_path / "local" / "tests").mkdir(parents=True)
    (tmp_path / "local" / "tests" / "t.py").write_text("1\n")
    (tmp_path / "local" / "loose.py").write_text("1\n")
    rows = sweep.scan([tmp_path / "local"], [], {}, ignore=set())
    assert [r["name"] for r in rows] == ["loose.py"]


def test_ignore_entries_carry_reasons():
    local = {"sweep": {"ignore": ["old", {"name": "gohang", "why": "scratch probe"}]}}
    assert sweep.ignore_reasons(local) == {"old": "", "gohang": "scratch probe"}
    assert {"old", "gohang", "__pycache__"} <= sweep.ignored(local)


def test_unadopted_counts_untracked_and_no_test():
    rows = [{"status": s} for s in ("untracked", "no-test", "ok", "adopted", "adopt")]
    assert len(sweep.unadopted(rows)) == 2


# ---- friction row ---------------------------------------------------------------------------------------------------
def test_friction_row_not_ok_while_scripts_are_unadopted():
    rows = [{"status": "untracked", "name": "a.py"}, {"status": "ok", "name": "b.py"}]
    got = friction.sweep_rows(rows)
    assert len(got) == 1 and got[0]["line"].startswith("1 scripts not adopted (fieldkit sweep)")


def test_friction_row_absent_when_nothing_is_unadopted():
    assert friction.sweep_rows([{"status": "ok", "name": "b.py"}]) == []


# ---- honesty: a script the session wrote with no card ---------------------------------------------------------------
def test_uncarded_names_a_script_under_a_root(tmp_path):
    root = tmp_path / "Scripts"
    (root / "NewTool").mkdir(parents=True)
    f = root / "NewTool" / "x.py"
    f.write_text("print(1)\n")
    assert sweep.uncarded(f, [root], [], set()) == "untracked"
    tools = [{"id": "nt", "path": str(f), "test": None}]
    assert sweep.uncarded(f, [root], tools, set()) == "no-test"
    tools = [{"id": "nt", "path": str(f), "test": ["x"]}]
    assert sweep.uncarded(f, [root], tools, set()) is None
    assert sweep.uncarded(f, [root], [], {"NewTool"}) is None
    assert sweep.uncarded(tmp_path / "elsewhere.py", [root], [], set()) is None
    assert sweep.uncarded(root / "notes.md", [root], [], set()) is None


def test_honesty_reports_uncarded_script(tmp_path, monkeypatch):
    f = tmp_path / "tool.py"
    f.write_text("1\n")
    monkeypatch.setattr(honesty, "_uncarded", lambda p: "untracked")
    found = honesty.check("wrote `tool.py`\nNOT verified: - (x)", "", offline=True, base=tmp_path)
    assert [x["kind"] for x in found] == ["UNCARDED"]
    monkeypatch.setattr(honesty, "_uncarded", lambda p: None)
    assert honesty.check("wrote `tool.py`\nNOT verified: - (x)", "", offline=True, base=tmp_path) == []
