"""build-harness actions that were unreachable or wrong, found while carding every action (2026-10-18):
a NameError-class crash on success, a declared-nowhere flag, silent exits and a word taken for a file."""
import pytest

from fieldkit import cli
from fieldkit.buildh import releasecheck, reprobuild
from fieldkit.leakgate import linux


def test_leakgate_selftest_text_output_does_not_crash_when_ready(monkeypatch, capsys):
    # run() imports `settings` locally further down, so the bare name was local everywhere in run() and the
    # success line raised UnboundLocalError before printing NEXT
    monkeypatch.setattr(linux, "selftest", lambda: {"tools": {"ip": "/usr/sbin/ip"}, "root": True, "ok": True,
                                                    "missing_required": []})
    assert cli.main(["build-harness", "leakgate-selftest"]) == 0
    assert "NEXT: " in capsys.readouterr().out


def test_repro_plan_text_output_does_not_crash_when_ready(monkeypatch, capsys):
    monkeypatch.setattr(reprobuild, "plan", lambda tid: {"ok": True, "rows": [{"ok": True, "check": "c", "evidence": "e"}],
                                                         "work2": "W2", "obj2": "O2"})
    assert cli.main(["build-harness", "repro", "--task", "t1"]) == 0
    assert "window repro t1 --run" in capsys.readouterr().out


def test_release_check_fast_is_a_declared_option(monkeypatch):
    seen = {}

    def fake(tid, say, skip_post_install):
        seen["skip"] = skip_post_install
        return [{"ok": True}]
    monkeypatch.setattr(releasecheck, "run", fake)
    assert cli.main(["build-harness", "release-check", "--task", "t1", "--fast"]) == 0
    assert seen["skip"] is True
    assert cli.main(["build-harness", "release-check", "--task", "t1"]) == 0
    assert seen["skip"] is False


@pytest.mark.parametrize("args", [["vault"], ["vault", "nonsense"]])
def test_vault_without_a_known_sub_command_is_refused_not_silent(args, capsys):
    assert cli.main(["build-harness", *args]) == 2
    assert "REFUSED: vault: say what to do" in capsys.readouterr().err


def test_vault_list_is_still_a_known_sub_command(monkeypatch):
    from fieldkit.buildh import vault
    monkeypatch.setattr(vault, "listing", lambda: [])
    assert cli.main(["build-harness", "vault", "list"]) == 0


@pytest.mark.parametrize("args", [["start"], ["start", "kernel"]])
def test_start_without_firefox_is_refused_not_a_crash(args, capsys):
    assert cli.main(["build-harness", *args]) == 2                    # `start` alone was an IndexError
    assert "start firefox" in capsys.readouterr().err


def test_start_firefox_plans_a_task_and_names_the_owner_step(monkeypatch, capsys):
    from fieldkit.buildh import cli as bh
    monkeypatch.setattr(bh, "start_firefox", lambda *a, **k: {"id": "firefox-999.0", "steps": [{"id": "vault"}],
                                                              "workdir": "W"})
    assert cli.main(["build-harness", "start", "firefox"]) == 0
    out = capsys.readouterr().out
    assert "task firefox-999.0 planned: vault" in out and "build-harness approve firefox-999.0" in out


def test_describe_gives_the_step_in_words_and_refuses_an_unknown_step(monkeypatch, capsys):
    from fieldkit.buildh import task
    t = {"id": "t1", "steps": [{"id": "s1"}, {"id": "s2"}]}
    monkeypatch.setattr(task, "load", lambda tid: t)
    monkeypatch.setattr(task, "current", lambda t, *a: t["steps"][0])
    monkeypatch.setattr(task, "describe_step", lambda t, s, **k: f"step {s['id']} in words")
    assert cli.main(["build-harness", "describe", "t1"]) == 0
    assert "step s1 in words" in capsys.readouterr().out
    assert cli.main(["build-harness", "describe", "t1", "s2"]) == 0
    assert "step s2 in words" in capsys.readouterr().out
    assert cli.main(["build-harness", "describe", "t1", "nope"]) == 2


def test_log_prints_the_last_journal_events(monkeypatch, tmp_path, capsys):
    from fieldkit.buildh import task
    monkeypatch.setattr(task, "STATE", tmp_path)
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "journal.jsonl").write_text(
        "\n".join(f'{{"t": "2026-10-18T21:{i:02d}", "task": "t1", "event": "e{i}", "step": "s", "n": {i}}}'
                  for i in range(50)) + "\n", encoding="utf-8")
    assert cli.main(["build-harness", "log", "t1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 40 and lines[0].split()[1] == "e10" and lines[-1].split()[1] == "e49"
    assert cli.main(["build-harness", "log", "never-started"]) == 0   # no journal yet: nothing to print


def test_carry_needs_a_commit_patches_and_a_reason():
    assert cli.main(["build-harness", "carry", "t1", "abc123"]) == 2
    assert cli.main(["build-harness", "carry", "t1", "abc123", "x.patch"]) == 2      # no --note


def test_whole_files_exits_3_when_a_whole_file_is_missing(monkeypatch, tmp_path, capsys):
    from fieldkit.buildh import lossaudit, task
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "patch_policy.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(task, "load", lambda tid: {"meta": {"harness_root": str(tmp_path)}, "workdir": str(tmp_path / "w")})
    seen = {}

    def fake(sets, tree, pol, missing):
        seen["sets"] = sets
        return {"missing": missing, "differs": [], "exempt": [], "same": 4}
    monkeypatch.setattr(lossaudit, "whole_files", lambda s, t, p: fake(s, t, p, [{"group": "08.Look", "path": "logo.png"}]))
    assert cli.main(["build-harness", "whole-files", "t1"]) == 3
    assert "MISSING 08.Look: logo.png" in capsys.readouterr().out and seen["sets"] == tmp_path / "patches"
    monkeypatch.setattr(lossaudit, "whole_files", lambda s, t, p: fake(s, t, p, []))
    assert cli.main(["build-harness", "whole-files", "t1"]) == 0


def test_every_build_harness_action_has_a_card():
    # 2026-10-18: 3 of 97 actions had a card, so an agent could not discover the rest through `fieldkit agent`/MCP
    import re
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[1]
    src = (root / "fieldkit" / "cli.py").read_text(encoding="utf-8")
    start = src.index('bh.add_argument("action", choices=[') + len('bh.add_argument("action", choices=[')
    actions = re.findall(r'"([a-z0-9-]+)"', src[start:src.index("]", start)])
    ids = {c["id"] for c in yaml.safe_load((root / "fieldkit" / "desk" / "tools.yaml").read_text(encoding="utf-8"))["tools"]}
    # public cards only: the answer must be the same on CI, where the owner's local/ does not exist
    assert len(actions) > 90
    missing = [a for a in actions if f"bh-{a}" not in ids and a not in ids]
    assert not missing, f"build-harness actions without a card (add bh-<action> to fieldkit/desk/tools.yaml): {missing}"
