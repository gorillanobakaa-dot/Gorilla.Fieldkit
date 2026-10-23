# Version: 1.0.0 · updated 26-10-23-19-55
"""release-shots: each SHOT line of the probe becomes one full-screen capture; a failed capture or a skipped surface
makes the run NOT OK (2026-10-23, Linux 158.0-2)."""
from types import SimpleNamespace

from fieldkit.buildh import releaseshots


def test_lines_are_read():
    assert releaseshots.parse("SHOT|about|Help > About reads x") == ("shot", "about", "Help > About reads x")
    assert releaseshots.parse("SHOT|skip|menu|did not open") == ("skip", "menu", "did not open")
    assert releaseshots.parse("SHOT|end") == ("end",) and releaseshots.parse("STAMP|x|y") is None


def test_each_shot_line_is_one_capture_and_a_skip_is_not_ok(tmp_path):
    def fake_probe(inst, js, on_line=None, **k):
        assert js == "release-shots" and k["headless"] is False
        for l in ["SHOT|about|the About window", "SHOT|skip|menu|did not open", "SHOT|end"]:
            on_line(l)

    def runner(cmd, **k):
        open(cmd[-1], "wb").write(b"png")
        return SimpleNamespace(returncode=0, stderr="")
    r = releaseshots.run("/inst", tmp_path, "158.0-2-", say=lambda m: None, capture=["shot"], runner=runner,
                         probe_run=fake_probe)
    assert [s["name"] for s in r["shots"]] == ["about"] and (tmp_path / "158.0-2-about.png").is_file()
    assert r["skipped"] == [("menu", "did not open")] and not r["ok"]


def test_no_capture_tool_is_said_not_guessed():
    assert releaseshots.capture_cmd(which=lambda n: None) is None
