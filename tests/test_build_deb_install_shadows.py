# Version: 1.0.0 · updated 26-10-15-16-38
"""install stage: personal icons that shadow the package's, and a GNOME Shell older than the menu entry
(2026-10-15: the app grid said "Gorilla Unleashed 155" and showed a different icon from the dock)."""
import os
import time

from fieldkit.build import deb_install as d

PNG = lambda size: b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + size.to_bytes(4, "big") + size.to_bytes(4, "big")


def test_personal_icon_files_are_named_with_the_wrong_size_ones_marked(tmp_path):
    local, home_icons = tmp_path / "local-icons", tmp_path / "dot-icons"
    (local / "hicolor" / "256x256" / "apps").mkdir(parents=True)
    (local / "hicolor" / "64x64" / "apps").mkdir(parents=True)
    (local / "hicolor" / "256x256" / "apps" / "gorilla-unleashed.png").write_bytes(PNG(2048))
    (local / "hicolor" / "64x64" / "apps" / "gorilla-unleashed.png").write_bytes(PNG(64))
    (local / "hicolor" / "64x64" / "apps" / "other-app.png").write_bytes(PNG(64))
    home_icons.mkdir()
    (home_icons / "gorilla-unleashed.png").write_bytes(PNG(256))
    out = d.icon_shadows("gorilla-unleashed", dirs=(local, home_icons, tmp_path / "absent"))
    assert len(out) == 3 and not any("other-app" in p for p in out)
    assert any("a 2048 px picture in 256x256" in p for p in out)
    assert all("Agents.Work.Trash" in p for p in out)


def test_no_personal_icons_no_problem(tmp_path):
    assert d.icon_shadows("gorilla-unleashed", dirs=(tmp_path,)) == []


def test_a_shell_older_than_the_menu_entry_gets_a_note_not_a_failure(tmp_path):
    entry = tmp_path / "gorilla-unleashed.desktop"
    entry.write_text("[Desktop Entry]\n")
    now = time.time()
    os.utime(entry, (now, now))
    note = d.stale_shell_note([entry, tmp_path / "missing"], started=now - 86400)
    assert len(note) == 1 and "log out and back in" in note[0] and str(entry) in note[0]
    assert d.stale_shell_note([entry], started=now + 10) == []
    assert d.stale_shell_note([entry], started=None) == []


def test_shell_start_is_read_from_proc(tmp_path):
    (tmp_path / "stat").write_text("cpu 1 2 3\nbtime 1000\n")
    p = tmp_path / "4242"
    p.mkdir()
    (p / "comm").write_text("gnome-shell\n")
    fields = ["S"] + ["0"] * 18 + [str(500 * os.sysconf("SC_CLK_TCK"))]   # field 22 (starttime), after the ")"
    (p / "stat").write_text("4242 (gnome-shell) " + " ".join(fields) + " 0 0\n")
    (tmp_path / "self").mkdir()
    assert d.shell_started(proc=tmp_path) == 1500
