# Version: 1.0.0 · updated 26-10-15-14-00
"""Test isolation: warn, wait for the harness's own work, close only browsers, never call noise a result."""
import os

from fieldkit.build import isolation as i


def test_noise_is_named():
    s = {"browsers": [{"pid": 7, "name": "firefox", "cmdline": []}], "video_engine_pct": 1.2, "net_kbps": 900.0,
         "cpu_hogs": [{"pid": 9, "name": "python", "cpu_pct": 72.6}, {"pid": 10, "name": "x", "cpu_pct": 12.0}]}
    why = i.noisy(s)
    assert why == ["other browsers open: firefox (7)", "the video engine is already 1.2% busy",
                   "the network is moving 900.0 KB/s", "busy programs: python 72.6%"]
    assert i.noisy({"browsers": [], "video_engine_pct": 0.0, "net_kbps": 0.5, "cpu_hogs": []}) == []


def test_a_running_pre_commit_check_is_own_work(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / f"pre-commit.running.{os.getpid()}").write_text("")
    assert any("pre-commit check is running" in w for w in i.own_work([tmp_path]))
    (tmp_path / ".git" / f"pre-commit.running.{os.getpid()}").unlink()
    (tmp_path / ".git" / "pre-commit.running.999999999").write_text("")          # a stale marker: process gone
    assert not any("pre-commit" in w for w in i.own_work([tmp_path]))


def test_own_work_is_waited_for_never_killed():
    states = [["pytest running"], ["pytest running"], []]
    said = []
    left = i.wait_own(limit=100, out=said.append, sleep=lambda s: None, check=lambda: states.pop(0))
    assert left == [] and said and "waiting for the harness's own work" in said[0]


def test_warning_is_loud_and_counts_down():
    said, slept = [], []
    i.warn("a test", seconds=20, out=said.append, speak=False, sleep=slept.append)
    assert any(s.startswith("HANDS OFF:") for s in said) and "step away from the keyboard" in said[1]
    assert sum(slept) == 20


def test_only_browsers_are_ever_closed():
    assert i.BROWSERS.match("firefox") and i.BROWSERS.match("gorilla-unleash") and i.BROWSERS.match("chrome")
    for n in ("gnome-shell", "pipewire", "claude", "python3", "bash", "Xwayland"):
        assert not i.BROWSERS.match(n) and i.NEVER.match(n)


SS_A = """State Recv-Q Send-Q Local Address:Port Peer Address:PortProcess
ESTAB 0 0 10.0.0.2:5000 149.154.167.51:443 users:(("Telegram",pid=4242,fd=30))
\t bbr bytes_acked:1000 bytes_received:1000000
ESTAB 0 0 10.0.0.2:5001 1.1.1.1:443 users:(("claude",pid=77,fd=3))
\t bbr bytes_acked:500 bytes_received:500
"""
SS_B = SS_A.replace("bytes_received:1000000", "bytes_received:4072000").replace("bytes_received:500", "bytes_received:900")


def test_telegram_downloading_in_the_background_is_named():
    snaps = [SS_A, SS_B]

    class R:
        def __init__(self, t):
            self.stdout = t
    rows = i.net_by_process(3, sleep=lambda s: None, run=lambda *a, **k: R(snaps.pop(0)))
    assert rows[0] == {"pid": 4242, "name": "Telegram", "kbps": 1000.0}
    s = {"browsers": [], "heavy_apps": [{"pid": 4242, "name": "Telegram", "cmdline": []}], "video_engine_pct": 0.0,
         "net_kbps": 1000.0, "cpu_hogs": [], "net_hogs": rows}
    why = i.noisy(s)
    assert "background-heavy apps open: Telegram (4242)" in why
    assert any(w.startswith("programs moving data: Telegram 1000.0 KB/s") for w in why)
    assert not any("claude" in w for w in why)            # the AI session running the test is not noise to close


def test_heavy_apps_list():
    for n in ("Telegram", "telegram-desktop", "WhatsApp", "Discord", "steam", "qbittorrent", "dropbox", "syncthing"):
        assert i.HEAVY.match(n), n
    for n in ("gnome-shell", "firefox", "bash"):
        assert not i.HEAVY.match(n)
