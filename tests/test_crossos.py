"""One command, both systems (P9, core/crossos.py): no fieldkit command refuses a job for "runs on X only"."""
import json
from pathlib import Path

import pytest

from fieldkit.core import crossos
from fieldkit.core import platform_gate as gate

ROOT = Path(__file__).resolve().parent.parent
IDX = {
    "build-harness build-run": {"platforms": ["windows"], "why": "the Windows build script", "ids": ["bh-build-run"]},
    "build-harness capture": {"platforms": ["windows"], "why": "pktmon", "ids": ["bh-capture"]},
    "build-harness newthing": {"platforms": ["windows"], "why": "", "ids": ["bh-new"]},
    "win11 run copilot check": {"platforms": ["windows"], "why": "", "ids": ["w"]},
    "wifi scan": {"platforms": ["linux"], "why": "iw", "ids": ["ws"]},
    "audio": {"platforms": ["linux"], "why": "", "ids": ["a"]},
    "size find": {"platforms": ["linux", "windows"], "why": "", "ids": ["s"]},
}


def rows():
    return crossos.load()


# -- the guard: a single-system command cannot ship without an answer for the other system -------------------------
def test_every_single_system_command_has_a_row():
    missing = [r["command"] for r in crossos.table() if r["route"] == "MISSING"]
    assert not missing, f"add these to fieldkit/core/crossos.yaml (dispatch, offload or none): {missing}"


def test_every_row_has_a_known_route_and_a_reason():
    for key, row in rows().items():
        assert row.get("route") in crossos.ROUTES, key
        assert row.get("why"), f"{key}: a row says why"
        if row["route"] == "dispatch":
            assert row.get("dispatch"), key


def test_a_dispatch_target_is_a_real_command():
    idx = gate.load_index(local=False)
    from fieldkit import cli
    src = Path(cli.__file__).read_text(encoding="utf-8")
    for key, row in rows().items():
        for plat, argv in (row.get("dispatch") or {}).items():
            words = [w for w in argv if w != "{args}"]
            known = gate.matching(words, idx) is not None or f'"{words[0]}"' in src
            assert known, f"{key} dispatches on {plat} to an unknown command: {words}"


def test_the_platforms_doc_is_the_generated_table():
    doc = (ROOT / "docs" / "PLATFORMS.md").read_text(encoding="utf-8")
    assert crossos.markdown(crossos.table()) in doc, "regenerate: python -m fieldkit.core.crossos --markdown"


# -- the routes ------------------------------------------------------------------------------------------------------
def test_build_run_on_linux_dispatches_to_the_linux_build():
    """The 2026-11-11 refusal: build-run firefox-160-linux on the Debian laptop."""
    d = crossos.decide(["build-harness", "build-run", "firefox-160-linux"], IDX, rows(), here="linux")
    assert d["action"] == "dispatch" and d["argv"] == ["emerge", "run", "firefox-debian"]


def test_a_windows_job_from_linux_is_offloaded_to_the_windows_machine():
    d = crossos.decide(["build-harness", "capture", "T1"], IDX, rows(), here="linux")
    assert d["action"] == "offload" and d["device"] == "windows"
    assert d["args"] == ["build-harness", "capture", "T1"]


def test_a_dispatch_row_seen_from_the_side_it_does_not_name_offloads():
    """build-run names only a Linux equivalent; on a Mac it goes to the Windows machine, never a refusal."""
    d = crossos.decide(["build-harness", "build-run"], IDX, rows(), here="macos")
    assert d["action"] == "offload" and d["device"] == "windows"


def test_an_unwritten_build_command_still_runs_somewhere():
    d = crossos.decide(["build-harness", "newthing"], IDX, {}, here="linux")
    assert d["action"] == "offload" and d["unwritten"]


def test_what_cannot_exist_here_says_not_this_platform():
    d = crossos.decide(["win11", "run", "copilot", "check"], IDX, rows(), here="linux")
    assert d["action"] == "not-this-platform" and "Windows" in d["why"]


def test_args_pass_through_a_dispatch():
    d = crossos.decide(["audio", "--fix"], IDX, rows(), here="windows")
    assert d["action"] == "dispatch" and d["argv"] == ["laptop", "sound", "--fix"]


def test_on_peer_sends_any_command_to_the_other_machine():
    d = crossos.decide(["size", "find", "--on", "peer", "--name", "x"], IDX, rows(), here="linux")
    assert d["action"] == "offload" and d["device"] == "windows" and d["args"] == ["size", "find", "--name", "x"]


def test_a_command_for_this_system_runs_untouched():
    assert crossos.decide(["size", "find"], IDX, rows(), here="linux")["action"] == "run"
    assert crossos.decide(["wifi", "scan", "--help"], IDX, rows(), here="windows")["action"] == "run"


# -- the door (cli) ----------------------------------------------------------------------------------------------------
def test_not_this_platform_exits_0_with_json(monkeypatch, capsys):
    monkeypatch.setattr(crossos, "decide", lambda a: {"action": "not-this-platform", "args": a, "command": "wifi scan",
                                                      "platforms": ["linux"], "why": "iw", "port": False})
    assert crossos.handle(["wifi", "scan", "--json"], main=lambda a: 99) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": True, "status": "not-this-platform", "command": "wifi scan", "platforms": ["linux"],
                   "why": "iw", "port": False}


def test_dispatch_re_enters_the_command_line(monkeypatch):
    seen = []
    monkeypatch.setattr(crossos, "decide", lambda a: {"action": "dispatch", "args": a, "argv": ["emerge", "run", "x"],
                                                      "command": "build-harness build-run", "why": "w"})
    assert crossos.handle(["build-harness", "build-run"], main=lambda a: seen.append(a) or 7, say=lambda s: None) == 7
    assert seen == [["emerge", "run", "x"]]


# -- offload ---------------------------------------------------------------------------------------------------------
def _target(row_ok=True):
    return lambda dev: ({"id": "l15", "tag": "[L15-WIN]"}, "laptop" if row_ok else None, "" if row_ok else "unpaired")


@pytest.mark.parametrize("status,code", [("pass", 0), ("fail", 3)])
def test_offload_reports_the_signed_result(monkeypatch, tmp_path, status, code):
    from fieldkit.emerge import runner
    from fieldkit.core import settings
    monkeypatch.setattr(runner, "offload_target", _target())
    monkeypatch.setattr(settings, "state_root", lambda: tmp_path)
    said = []
    rc = crossos.offload(["build-harness", "capture"], "windows", say=said.append,
                         sender=lambda *a, **k: ("J1", "sent", {"digest": "d"}), waiter=lambda j, s: None,
                         result=lambda j, p, d: {"status": status, "host": "L15", "tail": "last lines"})
    assert rc == code and "last lines" in said and any("OFFLOADED" in s for s in said)


def test_offload_with_no_machine_fails_plainly(monkeypatch):
    from fieldkit.emerge import runner
    monkeypatch.setattr(runner, "offload_target", _target(False))
    said = []
    assert crossos.offload(["build-harness", "capture"], "windows", say=said.append) == 3
    assert "OFFLOAD FAILED" in said[0] and "unpaired" in said[0]


def test_offload_with_no_signed_result_is_not_a_pass(monkeypatch, tmp_path):
    from fieldkit.emerge import runner
    from fieldkit.core import settings
    monkeypatch.setattr(runner, "offload_target", _target())
    monkeypatch.setattr(settings, "state_root", lambda: tmp_path)
    assert crossos.offload(["x"], "windows", say=lambda s: None, sender=lambda *a, **k: ("J", "sent", {}),
                           waiter=lambda j, s: None, result=lambda j, p, d: None) == 3
