"""fieldkit rules (HARNESS-PLAN R2, 2026-10-28): the one core in every agent's global rules file, every rules file
under its size limit, a project file never repeating the core; install touches only the block. Fixture homes only."""
from pathlib import Path

import pytest

from fieldkit import rules

ROOT = Path(rules.__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path):
    for p in rules.global_files(tmp_path).values():
        p.parent.mkdir(parents=True)
    return tmp_path


def test_this_repository_passes_its_own_check():
    assert all(x["ok"] for x in rules.check(ROOT, repo_only=True)), rules.check(ROOT, repo_only=True)


def test_install_puts_the_block_first_keeps_the_rest_and_backs_up(home, tmp_path):
    f = rules.global_files(home)["claude-code"]
    f.write_text("# my own rules\nkeep this line\n", encoding="utf-8")
    block = rules.core_block(ROOT)
    assert rules.check_file(f, block, "global")["problems"] == ["no core block (fieldkit rules install puts it in)"]
    r = rules.install([f], ROOT, backup_dir=tmp_path / "bk")
    text = f.read_text(encoding="utf-8")
    assert text.startswith(block) and text.endswith("# my own rules\nkeep this line\n")
    assert "inserted" in r[0]["done"] and len(list((tmp_path / "bk").iterdir())) == 1
    assert rules.check_file(f, block, "global")["ok"]
    assert rules.install([f], ROOT, backup_dir=tmp_path / "bk")[0]["done"] == "already current"


def test_install_replaces_an_old_block_in_place_and_never_creates_a_file(home, tmp_path):
    f = rules.global_files(home)["gemini"]
    f.write_text("top\n<!-- fieldkit-core-rules v0 -->\nold core\n<!-- /fieldkit-core-rules -->\nbottom\n", encoding="utf-8")
    block = rules.core_block(ROOT)
    assert "not the current" in rules.check_file(f, block, "global")["problems"][0]
    assert "updated" in rules.install([f], ROOT, backup_dir=tmp_path / "bk")[0]["done"]
    assert f.read_text(encoding="utf-8") == "top\n" + block + "\nbottom\n"
    codex = rules.global_files(home)["codex"]
    assert rules.install([codex], ROOT, backup_dir=tmp_path / "bk")[0]["done"].startswith("missing")
    assert not codex.exists() and rules.check_file(codex, block, "global")["ok"]


def test_size_and_repeats_fail(tmp_path):
    block = rules.core_block(ROOT)
    big = tmp_path / "CLAUDE.md"
    big.write_text(block + "\n" + "x" * rules.LIMIT, encoding="utf-8")
    assert "over the 8192 limit" in rules.check_file(big, block, "global")["problems"][0]
    proj = tmp_path / "AGENTS.md"
    proj.write_text("# project\n" + rules._core_lines(block)[3] + "\n", encoding="utf-8")
    assert "repeats the core (1 line(s))" in rules.check_file(proj, block, "project")["problems"][0]
    proj.write_text("# project\n" + block + "\n", encoding="utf-8")
    assert "its block" in rules.check_file(proj, block, "project")["problems"][0]
    proj.write_text("# project\nonly its own rules\n", encoding="utf-8")
    assert rules.check_file(proj, block, "project")["ok"]


def test_the_check_exits_3_on_a_failing_global_file(home, monkeypatch, capsys):
    monkeypatch.setattr(rules.Path, "home", classmethod(lambda cls: home))
    rules.global_files(home)["claude-code"].write_text("no block\n", encoding="utf-8")
    assert rules.main(["check"]) == 3 and "FAIL" in capsys.readouterr().out
    rules.install_all(home, ROOT, backup_dir=home / "bk")
    assert rules.main(["check"]) == 0
