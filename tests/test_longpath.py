"""fieldkit/core/longpath.py: the platform maximum by default, the standard direct delete, links never followed."""
import os
import sys

import pytest

from fieldkit.core import longpath

WIN = os.name == "nt"


def test_extended_form_on_windows_and_unchanged_elsewhere():
    assert longpath.extended("C:\\a\\b", nt=True) == "\\\\?\\C:\\a\\b"
    assert longpath.extended("\\\\srv\\share\\x", nt=True) == "\\\\?\\UNC\\srv\\share\\x"
    assert longpath.extended("\\\\?\\C:\\a", nt=True) == "\\\\?\\C:\\a"
    assert longpath.extended("/var/tmp/x", nt=False) == "/var/tmp/x"
    assert longpath.plain("\\\\?\\C:\\a") == "C:\\a" and longpath.plain("\\\\?\\UNC\\srv\\s") == "\\\\srv\\s"


def test_limits_are_the_platform_maximum():
    assert longpath.limits("win32") == {"path": 32767, "name": 255, "legacy_path": 260}
    assert longpath.limits("linux") == {"path": 4096, "name": 255}
    assert longpath.limits("darwin") == {"path": 1024, "name": 255}


def _deep(base, depth=12, seg=30):
    d = longpath.extended(base)
    for _ in range(depth):
        d = os.path.join(d, "d" * seg)
    os.makedirs(d)
    f = os.path.join(d, "f.txt")
    with open(f, "w") as h:
        h.write("x")
    return f


def test_a_tree_deeper_than_260_characters_is_made_and_deleted(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    f = _deep(root)
    assert len(longpath.plain(f)) > 300
    res = longpath.remove_tree(root)
    assert res["files"] == 1 and res["dirs"] == 12 and res["error_count"] == 0
    assert os.listdir(root) == []


def test_keep_top_stays_and_read_only_files_go(tmp_path):
    import stat
    (tmp_path / "keep.json").write_text("{}")
    ro = tmp_path / "sub" / "ro.txt"
    ro.parent.mkdir()
    ro.write_text("x")
    os.chmod(ro, stat.S_IREAD)
    res = longpath.remove_tree(tmp_path, keep_top=("keep.json",))
    assert res["files"] == 1 and os.listdir(tmp_path) == ["keep.json"]


def test_a_symlink_is_removed_never_followed(tmp_path):
    target = tmp_path / "precious"
    target.mkdir()
    (target / "x.txt").write_text("keep")
    d = tmp_path / "d"
    d.mkdir()
    try:
        os.symlink(target, d / "s", target_is_directory=True)
    except OSError:
        pytest.skip("this account may not make symlinks")
    res = longpath.remove_tree(d)
    assert res["links"] == 1 and (target / "x.txt").read_text() == "keep" and os.listdir(d) == []


@pytest.mark.skipif(not WIN, reason="junctions are Windows")
def test_a_junction_is_removed_never_followed_and_a_junction_top_is_refused(tmp_path):
    import _winapi
    target = tmp_path / "precious"
    target.mkdir()
    (target / "x.txt").write_text("keep")
    d = tmp_path / "d"
    d.mkdir()
    _winapi.CreateJunction(str(target), str(d / "j"))
    res = longpath.remove_tree(d)
    assert res["links"] == 1 and (target / "x.txt").read_text() == "keep"
    _winapi.CreateJunction(str(target), str(tmp_path / "jtop"))
    with pytest.raises(longpath.Refused):
        longpath.remove_tree(tmp_path / "jtop")
    assert (target / "x.txt").exists()


def test_posix_path_refuses_another_file_system(tmp_path, monkeypatch):
    if WIN:
        pytest.skip("the one-file-system check is the POSIX delete")
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "f").write_text("x")
    real = os.lstat

    class S:
        def __init__(self, st, dev):
            self._st, self.st_dev = st, dev

        def __getattr__(self, k):
            return getattr(self._st, k)
    monkeypatch.setattr(os, "lstat", lambda p, *a, **k: S(real(p), 999) if str(p).endswith("/m") else real(p))
    with pytest.raises(longpath.Refused):
        longpath.remove_tree(tmp_path)
    assert (tmp_path / "m" / "f").exists()


def test_machine_rows_on_windows_read_the_registry_and_git():
    if not WIN:
        assert [r[0] for r in longpath.machine()] == ["limits"]
        return

    class R:
        def __init__(self, out):
            self.stdout = out
    rows = dict((n, (ok, d)) for n, ok, d in longpath.machine(reg=lambda: 0, run=lambda *a, **k: R("false\n")))
    assert rows["LongPathsEnabled"][0] is False and "longpaths-enabled" in rows["LongPathsEnabled"][1]
    assert rows["git core.longpaths"][0] is False
    rows = dict((n, (ok, d)) for n, ok, d in longpath.machine(reg=lambda: 1, run=lambda *a, **k: R("true\n")))
    assert rows["LongPathsEnabled"][0] and rows["git core.longpaths"][0]


def test_probe_and_doctor(tmp_path, capsys):
    ok, n, why = longpath.probe(tmp_path)
    assert ok and n > 300, why
    assert os.listdir(tmp_path) == []
    rc = longpath.main(["--probe", str(tmp_path)])
    out = capsys.readouterr().out
    assert "probe" in out and "VERDICT" in out and rc in (0, 1)
