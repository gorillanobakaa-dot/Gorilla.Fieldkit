"""The temperature journal (crash 2026-10-27: no record of the temperature existed)."""
import os
import time

from fieldkit.thermal import journal


def test_a_line_per_tick_with_the_fan_controls_reading(tmp_path):
    s = {"t": 61, "max": 66, "load": 54, "fan": 7, "rpm": 3492, "mode": 5}
    assert journal.run(tmp_path, read=lambda: s, sleep=lambda _: None, ticks=3) == 3
    f = next(tmp_path.glob("journal-*.csv")).read_text().splitlines()
    assert f[0] == journal.HEADER.strip() and len(f) == 4 and f[1].endswith(",61,66,54,7,3492,5")


def test_no_status_is_written_not_skipped(tmp_path):
    journal.run(tmp_path, read=lambda: None, sleep=lambda _: None, ticks=1)
    assert next(tmp_path.glob("journal-*.csv")).read_text().splitlines()[1].endswith("no-status")


def test_only_its_own_files_older_than_seven_days_go(tmp_path):
    now = time.mktime(time.strptime("2026-10-27", "%Y-%m-%d"))
    for name in ("journal-2026-10-19.csv", "journal-2026-10-20.csv", "journal-2026-10-26.csv", "other-2026-01-01.csv"):
        (tmp_path / name).write_text("x")
    assert journal.prune(tmp_path, now=now) == ["journal-2026-10-19.csv"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["journal-2026-10-20.csv", "journal-2026-10-26.csv",
                                                          "other-2026-01-01.csv"]


def test_the_default_folder_is_the_checkouts_state_thermal():
    p = journal.folder()
    assert p.parts[-2:] == ("state", "thermal")


def test_a_stale_status_file_is_no_status(tmp_path, monkeypatch):
    """2026-10-28: after the rollback to gorilla.3 the journal copied gorilla.5's frozen status file as live."""
    from fieldkit.thermal import buildguard
    f = tmp_path / "status.txt"
    f.write_text("cpu=33;max=66;load=21", encoding="ascii")
    os.utime(f, (1, 1))
    monkeypatch.setattr(buildguard, "STATUS", f)
    d = tmp_path / "j"
    journal.run(d, sleep=lambda _: None, ticks=1)
    assert next(d.glob("journal-*.csv")).read_text(encoding="ascii").splitlines()[-1].endswith("no-status")
