"""The shipping command and the release-page composer refuse with exit 3 while a capability is not built in."""
import argparse
import subprocess

from fieldkit.gdocs import cli as gcli
from fieldkit.integration import core, gate, selftest
from fieldkit.method import ship

CT, CAP = next(iter(core.load_rules()["fieldkit"]["capabilities"].items()))
OMIT = (CT, CAP["surfaces"][0]["id"])


def _git(root, *a):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                   capture_output=True)


def _repo(root, omit=None):
    root.mkdir()
    selftest.build(root, "fieldkit", omit)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _branch_adding_unbuilt_package(root):
    _git(root, "checkout", "-q", "-b", "feat/x")
    (root / "fieldkit/newpkg").mkdir()
    (root / "fieldkit/newpkg/__init__.py").write_text("", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "adds a package with no card")


def test_guard_passes_a_clean_repo_and_skips_a_foreign_folder(tmp_path):
    r = gate.guard(_repo(tmp_path / "ok"), "main")
    assert r["ok"] and not r["skipped"]
    other = tmp_path / "other"
    other.mkdir()
    assert gate.guard(other, "main") == {"ok": True, "skipped": True, "gaps": [], "lines": []}


def test_guard_names_the_gap_of_what_the_branch_adds(tmp_path):
    root = _repo(tmp_path / "r")
    _branch_adding_unbuilt_package(root)
    r = gate.guard(root, "main")
    assert not r["ok"] and any("newpkg" in l for l in r["lines"])


def test_guard_ignores_a_gap_the_branch_did_not_add(tmp_path):
    root = _repo(tmp_path / "r", OMIT)                              # an old gap already on main
    _git(root, "checkout", "-q", "-b", "feat/x")
    (root / "notes.txt").write_text("x", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "unrelated")
    assert gate.guard(root, "main")["ok"]
    assert not gate.guard(root)["ok"]                               # the whole repo still shows it


def _fake_sync(monkeypatch):
    monkeypatch.setattr(ship, "sync", lambda repo, base: {"ok": True, "state": "ahead", "branch": "feat/x",
                                                          "local": "a" * 40})
    monkeypatch.setattr("fieldkit.method.strikes.parked_for", lambda b: False)


def _fake_git(monkeypatch):
    calls = []
    monkeypatch.setattr(ship, "git", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    return calls


def test_ship_refuses_a_branch_that_is_not_built_in(tmp_path, monkeypatch):
    root = _repo(tmp_path / "r")
    _branch_adding_unbuilt_package(root)
    _fake_sync(monkeypatch)
    calls = _fake_git(monkeypatch)
    r = ship.push(str(root), "main", mandate=lambda *a, **k: ("allow", "", None))
    assert not r["ok"] and r["state"] == "integration-gap" and "newpkg" in r["error"]
    assert calls == []                                              # git was never started


def test_ship_exit_code_is_3_on_a_gap(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path / "r")
    _branch_adding_unbuilt_package(root)
    _fake_sync(monkeypatch)
    _fake_git(monkeypatch)
    assert ship.main(["push", "--repo", str(root)]) == 3
    assert "newpkg" in capsys.readouterr().out


def test_ship_goes_on_when_built_in(tmp_path, monkeypatch):
    root = _repo(tmp_path / "r")
    _git(root, "checkout", "-q", "-b", "feat/x")
    _fake_sync(monkeypatch)
    calls = _fake_git(monkeypatch)
    monkeypatch.setattr(ship, "pushed_report", lambda *a, **k: {"ok": True, "pushed": True})
    r = ship.push(str(root), "main", mandate=lambda *a, **k: ("allow", "", None))
    assert r["ok"] and calls


def _compose(tmp_path, repo):
    for n in ("opening", "layman", "developer"):
        (tmp_path / n).write_text(n, encoding="utf-8")
    return argparse.Namespace(action="compose", page=None, opening=str(tmp_path / "opening"),
                              layman=str(tmp_path / "layman"), developer=str(tmp_path / "developer"), extra=None,
                              out=str(tmp_path / "page.md"), repo=str(repo), since=None, json=False)


def test_release_page_compose_refuses_a_repo_with_a_gap(tmp_path, capsys):
    root = tmp_path / "r"
    root.mkdir()
    selftest.build(root, "fieldkit", OMIT)
    assert gcli.cmd_release_page(_compose(tmp_path, root)) == 3
    assert "not built in" in capsys.readouterr().out
    assert not (tmp_path / "page.md").exists()


def test_release_page_compose_goes_on_in_a_built_in_repo(tmp_path, capsys):
    root = tmp_path / "r"
    root.mkdir()
    selftest.build(root, "fieldkit")
    gcli.cmd_release_page(_compose(tmp_path, root))
    assert "not built in" not in capsys.readouterr().out            # the page's own checks may still speak
