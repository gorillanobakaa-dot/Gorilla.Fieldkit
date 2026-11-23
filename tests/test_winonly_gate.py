"""CI gate: Windows-only programs need an OS branch and a Linux path (desk/winonly.py). Runs on both OSes."""
import subprocess
import sys
import textwrap

from fieldkit import proc as fkproc
from fieldkit.desk import winonly


def _tree(tmp_path, body):
    d = tmp_path / "fieldkit"
    d.mkdir()
    (d / "m.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def test_repo_has_no_new_windows_only_calls():
    assert winonly.problems() == []


def test_repo_windows_port_cards_do_not_grow():
    assert winonly.port_problems() == []


def test_gate_fails_on_bare_powershell(tmp_path):      # mutation: proves the gate can fail
    root = _tree(tmp_path, 'import subprocess\ndef f():\n    return subprocess.run(["powershell", "-c", "x"])\n')
    found = winonly.sites(root)
    assert list(found) == ["fieldkit/m.py::f::powershell"]
    assert any("no OS branch" in p for p in winonly.problems(found, {}))


def test_gate_passes_with_os_branch(tmp_path):
    root = _tree(tmp_path, 'import sys, subprocess\ndef f():\n    if sys.platform != "win32":\n        return ["ip", "a"]\n'
                           '    return subprocess.run(["taskkill", "/F"])\n')
    assert winonly.sites(root) == {}


def test_gate_flags_stale_baseline_and_weak_reason():
    assert any("no longer" in p for p in winonly.problems({}, {"a.py::f::cmd": "x y z w v"}))
    assert any("reason" in p for p in winonly.problems({"a.py::f::cmd": "a.py:1"}, {"a.py::f::cmd": "todo"}))


def test_gate_flags_windows_card_marked_port():
    cards = [{"id": "x", "platforms": ["windows"], "_file": "a.yaml"}]
    assert any("not in the baseline" in p for p in winonly.port_problems({"x": {"status": "PORT"}}, cards, []))


def test_kill_tree_stops_a_process_on_this_os():      # the Linux twin of the taskkill path; runs on both OSes
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    assert fkproc.kill_tree(p.pid) == 0
    assert p.wait(timeout=20) is not None


def test_kill_tree_of_a_missing_pid_is_quiet():
    assert fkproc.kill_tree(2 ** 22 + 12345) in (0, 128)
