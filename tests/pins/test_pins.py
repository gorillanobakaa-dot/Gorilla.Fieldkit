"""fieldkit pins: every break kind is refused, the clean fixture passes, a pin turns an archive from refused to fine."""
import json
import subprocess
import sys

import pytest

from fieldkit.pins import cli, core, selftest


def tree(tmp_path, kind=None):
    selftest.build(tmp_path, kind)
    return tmp_path


def test_clean_fixture_passes(tmp_path, capsys):
    assert cli.main(["check", "--repo", str(tree(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "all immutable and sha256-pinned" in out and out.rstrip().splitlines()[-1].startswith("NEXT:")


@pytest.mark.parametrize("kind", list(selftest.BREAKS))
def test_each_break_kind_exits_3_and_names_the_source(tmp_path, capsys, kind):
    assert cli.main(["check", "--repo", str(tree(tmp_path, kind)), "--json"]) == 3
    data = json.loads(capsys.readouterr().out)
    assert not data["ok"] and data["findings"] and data["next"]
    assert all(f["file"] and f["line"] for f in data["findings"])


@pytest.mark.parametrize("kind,expect", [("latest-release", "latest"), ("nightly", "nightly"), ("rolling", "rolling"),
                                         ("autobuild", "autobuild"), ("master-archive", "branch-archive"),
                                         ("main-archive", "branch-archive"), ("head-archive", "branch-archive"),
                                         ("refs-heads", "refs-heads"), ("unversioned", "unversioned"),
                                         ("unpinned", "unpinned"), ("malformed-sums", "unpinned")])
def test_finding_kind(tmp_path, kind, expect):
    f, _ = core.scan(tree(tmp_path, kind))
    assert expect in {x["kind"] for x in f}


def test_text_output_ends_with_next_and_lists_url(tmp_path, capsys):
    assert cli.main(["check", "--repo", str(tree(tmp_path, "latest-release"))]) == 3
    out = capsys.readouterr().out
    assert "releases/download/latest/ffmpeg-master-latest" in out
    assert out.rstrip().splitlines()[-1].startswith("NEXT:")


def test_pinning_the_archive_makes_it_pass(tmp_path):
    d = tree(tmp_path, "unpinned")
    assert core.scan(d)[0]
    with open(d / "third_party/PINS.md", "a", encoding="utf-8") as h:
        h.write("| tool | x | 2.0.1 | tool-2.0.1.tar.gz " + "d" * 64 + " |\n")
    assert core.scan(d)[0] == []


def test_a_versioned_archive_url_is_not_rolling():
    assert core.rolling_reason("https://github.com/o/r/releases/download/v1.2.3/tool-1.2.3.zip") is None
    assert core.rolling_reason("https://github.com/o/r/releases/download/latest/tool.zip")[0] == "latest"
    assert core.rolling_reason("https://github.com/o/r/archive/main.zip")[0] == "branch-archive"
    assert core.rolling_reason("https://example.org/dl/tool.zip")[0] == "unversioned"


def test_a_commented_url_is_not_a_source(tmp_path):
    d = tree(tmp_path)
    with open(d / "scripts/build-toolbox.sh", "a", encoding="utf-8") as h:
        h.write("# was https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/x.zip\n")
    assert core.scan(d)[0] == []


def test_missing_file_is_a_finding(tmp_path):
    d = tree(tmp_path)
    (d / "scripts/build-toolbox.ps1").unlink()
    assert any(f["kind"] == "missing-file" for f in core.scan(d)[0])


def test_not_a_folder_exits_2(tmp_path):
    assert cli.main(["check", "--repo", str(tmp_path / "nope")]) == 2


def test_self_test_passes():
    ok, lines = selftest.run()
    assert ok, lines


def test_self_test_fails_when_the_check_is_blind(monkeypatch):
    monkeypatch.setattr(core, "scan", lambda root: ([], []))
    ok, lines = selftest.run()
    assert not ok and any(l.startswith("MISSED") for l in lines)


def test_fieldkit_command_wired():
    r = subprocess.run([sys.executable, "-m", "fieldkit", "pins", "self-test"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.rstrip().splitlines()[-1].startswith("NEXT:")


def test_self_test_command_exits_3_when_a_kind_is_missed(monkeypatch, capsys):
    monkeypatch.setattr(selftest, "run", lambda: (False, ["MISSED x"]))
    assert cli.main(["self-test"]) == 3
    monkeypatch.setattr(selftest, "run", lambda: (True, ["ok x"]))
    assert cli.main(["self-test"]) == 0
