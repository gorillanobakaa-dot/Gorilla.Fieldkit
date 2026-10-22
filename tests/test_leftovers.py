# Version: 1.0.0 · updated 26-10-22-15-50
"""fieldkit leftovers: every kind found on a fake desktop built in a temp folder, every false alarm of 2026-10-22
(a mask, a TryExec self-hide, a KDE-only launcher, GNOME Classic) left alone, and a clean that can be undone."""
import json
import os

import sys

import pytest

from fieldkit.leftovers import leftovers as L

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux-only tool: Linux desktops: XDG autostart, dpkg, systemd user units")


class FakeDpkg:
    def __init__(self, owners, states, removed=()):
        self.ok, self.owners, self.states, self._removed = True, owners, states, list(removed)

    def owner(self, path):
        return self.owners.get(str(path))

    def state(self, pkg):
        return self.states.get(pkg)

    def removed(self):
        return self._removed


def desktop(path, **keys):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Desktop Entry]\nType=Application\n" + "".join(f"{k}={v}\n" for k, v in keys.items()))
    return path


@pytest.fixture
def box(tmp_path):
    """A desktop with one of everything. Returns (dirs, dpkg)."""
    d = {k: tmp_path / k for k in ("xdg", "auto", "apps", "sysapps", "units", "xs", "ws", "bin")}
    for p in d.values():
        p.mkdir()
    tool = d["bin"] / "black-gorilla-theme"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    (d["bin"] / "gtk-high-contrast-theme").symlink_to("black-gorilla-theme")
    wine = d["bin"] / "wine"
    wine.write_text("#!/bin/sh\n")
    wine.chmod(0o755)
    xdu = d["bin"] / "xdg-user-dirs-update"
    xdu.write_text("#!/bin/sh\n")
    xdu.chmod(0o755)
    # the theme: current launcher, and the removed package's leftover (its config file, state rc)
    desktop(d["xdg"] / "black-gorilla-theme.desktop", Exec=f"{tool} --apply-once")
    old = desktop(d["xdg"] / "gtk-high-contrast-theme.desktop", Exec=f"{d['bin']}/gtk-high-contrast-theme --apply-once")
    # Debian's own duplicate: desktop-base ships a KDE copy of xdg-user-dirs.desktop with no OnlyShowIn
    desktop(d["xdg"] / "xdg-user-dirs.desktop", Exec=str(xdu))
    desktop(d["xdg"] / "xdg-user-dirs-kde.desktop", Exec=str(xdu))
    desktop(d["xdg"] / "spice-vdagent.desktop", Exec="/bin/true", Name="Spice")      # renamed below
    (d["xdg"] / "spice-vdagent.desktop").write_text("[Desktop Entry]\nType=Application\nExec=spice-vdagent\n")
    desktop(d["xdg"] / "kde-only.desktop", Exec="/no/such/kded", OnlyShowIn="KDE;")   # never starts on GNOME
    desktop(d["xdg"] / "gsd-power.desktop", Exec="/bin/true", OnlyShowIn="GNOME;")    # the session's own
    desktop(d["xdg"] / "tracker-optional.desktop", Exec="/no/such/miner", TryExec="/no/such/miner")   # self-hidden
    desktop(d["auto"] / "discord.desktop", Exec="/opt/discord/Discord --start-minimized")   # removed app
    desktop(d["apps"] / "opencode.desktop", Exec="/opt/old-nvm/versions/node/v24/bin/opencode", Name="OpenCode")
    desktop(d["apps"] / "wine" / "Programs" / "Notepad++.desktop", Name="Notepad++",
            Exec=f'env WINEPREFIX="{tmp_path}/gone-prefix" wine C:\\\\npp.exe')
    desktop(d["apps"] / "fine.desktop", Exec="/bin/true %U", Name="Fine")
    desktop(d["sysapps"] / "vim.desktop", Exec="vim %F", TryExec="no-such-vim-binary")    # owned: self-hide
    desktop(d["sysapps"] / "testdisk.desktop", Exec="/bin/true", Name="TestDisk")       # unowned, program exists
    (d["units"] / "filter-chain.service").symlink_to("/dev/null")                        # a MASK
    (d["units"] / "old-sync.service").write_text("[Service]\nExecStart=/opt/gone/sync --daemon\n")
    (d["units"] / "default.target.wants").mkdir()
    (d["units"] / "default.target.wants" / "removed.service").symlink_to(str(d["units"] / "removed.service"))
    desktop(d["xs"] / "gnome.desktop", Exec="/bin/true", DesktopNames="GNOME")
    desktop(d["xs"] / "gnome-classic-xorg.desktop", Exec="/bin/true", DesktopNames="GNOME-Classic;GNOME;")
    desktop(d["ws"] / "old-xfce.desktop", Exec="/no/startxfce4", Name="Xfce")               # unowned, dead
    dpkg = FakeDpkg(
        owners={str(old): "gtk-high-contrast-theme", str(d["xdg"] / "black-gorilla-theme.desktop"): "black-gorilla-theme",
                str(tool): "black-gorilla-theme", str(xdu): "xdg-user-dirs",
                str(d["xdg"] / "xdg-user-dirs.desktop"): "xdg-user-dirs",
                str(d["xdg"] / "xdg-user-dirs-kde.desktop"): "desktop-base", str(d["sysapps"] / "vim.desktop"): "vim-runtime",
                str(d["xs"] / "gnome.desktop"): "gnome-session",
                str(d["xs"] / "gnome-classic-xorg.desktop"): "gnome-classic-xsession"},
        states={"gtk-high-contrast-theme": "rc"},
        removed=[{"package": "gtk-high-contrast-theme", "conffiles": [str(old)]}])
    return d, dpkg


def run_scan(d, dpkg, virt="none"):
    return L.scan(dpkg=dpkg, sys_autostart=d["xdg"], user_autostart=d["auto"], user_apps=d["apps"],
                  sys_apps=(d["sysapps"],), user_units=d["units"], session_dirs=(d["xs"], d["ws"]),
                  desktops={"gnome"}, virt=virt)


def test_every_kind_is_found(box, monkeypatch):
    d, dpkg = box
    monkeypatch.setenv("PATH", f"{d['bin']}:/usr/bin:/bin")
    (d["bin"] / "spice-vdagent").write_text("#!/bin/sh\n")
    (d["bin"] / "spice-vdagent").chmod(0o755)
    findings, info = run_scan(d, dpkg)
    ids = {f["id"] for f in findings}
    assert ids == {
        "removed-package:gtk-high-contrast-theme",
        "autostart-duplicate:xdg-user-dirs-kde.desktop",
        "vm-guest-agent:spice-vdagent.desktop",
        "autostart-dead:discord.desktop",
        "menu-dead:opencode.desktop",
        "menu-dead:wine/Programs/Notepad++.desktop",
        "unit-dead:old-sync.service",
        "unit-dead:default.target.wants/removed.service",
        "session-dead:old-xfce.desktop",
    }
    rp = next(f for f in findings if f["kind"] == "removed-package")
    assert "start at login (gtk-high-contrast-theme.desktop)" in rp["why"] and rp["root"]
    assert "Wine prefix" in next(f for f in findings if "Notepad" in f["id"])["why"]
    assert next(f for f in findings if f["kind"] == "session-dead")["named_only"]
    assert any("kde-only.desktop" in i and "never starts" in i for i in info)
    assert any("testdisk.desktop" in i for i in info)
    assert not any("another desktop" in i for i in info)        # GNOME Classic declares GNOME: same desktop


def test_false_alarms_of_2026_10_22_stay_quiet(box, monkeypatch):
    d, dpkg = box
    monkeypatch.setenv("PATH", f"{d['bin']}:/usr/bin:/bin")
    ids = {f["id"] for f in run_scan(d, dpkg)[0]}
    for quiet in ("filter-chain", "vim.desktop", "tracker-optional", "kde-only", "fine.desktop", "gsd-power", "black-gorilla-theme.desktop",
                  "xdg-user-dirs.desktop", "testdisk", "gnome"):
        assert not any(quiet in i for i in ids), quiet


def test_vm_agent_inside_a_vm_is_wanted(box, monkeypatch):
    d, dpkg = box
    monkeypatch.setenv("PATH", f"{d['bin']}:/usr/bin:/bin")
    (d["bin"] / "spice-vdagent").write_text("#!/bin/sh\n")
    (d["bin"] / "spice-vdagent").chmod(0o755)
    assert not any(f["kind"] == "vm-guest-agent" for f in run_scan(d, dpkg, virt="kvm")[0])


def test_duplicate_keeps_the_programs_own_package(box, monkeypatch):
    d, dpkg = box
    monkeypatch.setenv("PATH", f"{d['bin']}:/usr/bin:/bin")
    dup = next(f for f in run_scan(d, dpkg)[0] if f["kind"] == "autostart-duplicate")
    assert dup["keeps"] == "xdg-user-dirs.desktop" and dup["action"] == "hide"


def test_clean_then_restore_round_trip(box, tmp_path, monkeypatch):
    d, dpkg = box
    monkeypatch.setenv("PATH", f"{d['bin']}:/usr/bin:/bin")
    findings = [f for f in run_scan(d, dpkg)[0] if not f.get("named_only")]
    calls = []
    run = tmp_path / "run"
    run.mkdir()
    def sudo(argv):                     # a purge removes the package's config files, as dpkg does
        calls.append(argv)
        if argv[:2] == ["dpkg", "--purge"]:
            for c in dpkg.removed()[0]["conffiles"]:
                os.unlink(c)
        return 0
    manifest, fails = L.apply(findings, run, user_autostart=d["auto"], sudo=sudo)
    assert fails == []
    assert ["dpkg", "--purge", "gtk-high-contrast-theme"] in calls
    tar = next(c for c in calls if c[0] == "sh" and "tar -C / -czf" in c[2])
    assert "umask 077" in tar[2] and tar[-1] == str(run / "etc-before-purge.tar.gz")
    assert calls.index(tar) < \
        calls.index(["dpkg", "--purge", "gtk-high-contrast-theme"])                  # /etc saved BEFORE the purge
    hidden = d["auto"] / "xdg-user-dirs-kde.desktop"
    assert "Hidden=true" in hidden.read_text() and L.MARK in hidden.read_text()
    assert (d["xdg"] / "xdg-user-dirs-kde.desktop").exists()         # the package's own file is never edited
    assert not (d["apps"] / "opencode.desktop").exists()
    assert (run / "files" / str(d["apps"] / "opencode.desktop").lstrip("/")).exists()
    assert not (d["units"] / "default.target.wants" / "removed.service").is_symlink()
    # a second scan does not report what the tool itself wrote
    left = {f["id"] for f in L.scan(dpkg=FakeDpkg(dpkg.owners, {}, []), sys_autostart=d["xdg"], user_autostart=d["auto"],
                                     user_apps=d["apps"], sys_apps=(d["sysapps"],), user_units=d["units"],
                                     session_dirs=(d["xs"], d["ws"]), desktops={"gnome"}, virt="none")[0]}
    assert not left & {"autostart-duplicate:xdg-user-dirs-kde.desktop", "menu-dead:opencode.desktop",
                       "vm-guest-agent:spice-vdagent.desktop", "autostart-dead:discord.desktop"}
    (run / "manifest.json").write_text(json.dumps({"actions": manifest}))
    steps, fails = L.restore(run, apply_it=True, sudo=lambda argv: 0)
    assert fails == []
    assert (d["apps"] / "opencode.desktop").exists()
    assert (d["auto"] / "discord.desktop").exists()
    assert not hidden.exists() and (run / "restored-overrides" / "xdg-user-dirs-kde.desktop").exists()
    assert os.readlink(d["units"] / "default.target.wants" / "removed.service") == str(d["units"] / "removed.service")
    assert (d["xdg"] / "gtk-high-contrast-theme.desktop").exists()      # a purged config file comes back


def test_login_safe_catches_a_launcher_the_session_needs(box, monkeypatch):
    d, dpkg = box
    monkeypatch.setenv("PATH", f"{d['bin']}:/usr/bin:/bin")
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    before = {n for n, (p, e, s) in L.effective_autostart(d["xdg"], d["auto"]).items() if e and L.starts_here(e, {"gnome"})}
    assert "gsd-power.desktop" in before
    L.hide("gsd-power.desktop", d["auto"])                               # a mistake the check must catch
    problems = L.login_safe(before, {"gnome"}, d["xdg"], d["auto"], (d["xs"],))
    assert any("gsd-power.desktop" in p for p in problems)


@pytest.mark.parametrize("line,prog,dead", [
    ("/bin/true %U", "/bin/true", False),
    ("env FOO=1 BAR=2 /bin/true --x", "/bin/true", False),
    ('sh -c "/no/such/thing --flag"', "/no/such/thing", True),
    ("/no/such/app", "/no/such/app", True),
])
def test_exec_program(line, prog, dead):
    p, real, _ = L.exec_program(line)
    assert p == prog and (real is None) == dead


def test_mask_is_not_a_dead_unit(tmp_path):
    (tmp_path / "x.service").symlink_to("/dev/null")
    f, _ = L.scan(dpkg=FakeDpkg({}, {}), sys_autostart=tmp_path / "n", user_autostart=tmp_path / "n",
                  user_apps=tmp_path / "n", sys_apps=(), user_units=tmp_path, session_dirs=(), desktops={"gnome"},
                  virt="none")
    assert f == []


def test_no_purge_when_etc_cannot_be_saved(box, tmp_path, monkeypatch):
    d, dpkg = box
    monkeypatch.setenv("PATH", f"{d['bin']}:/usr/bin:/bin")
    rp = [f for f in run_scan(d, dpkg)[0] if f["kind"] == "removed-package"]
    calls = []
    manifest, fails = L.apply(rp, tmp_path, user_autostart=d["auto"],
                              sudo=lambda argv: calls.append(argv) or (1 if "tar" in " ".join(argv) else 0))
    assert fails and not any(c[:2] == ["dpkg", "--purge"] for c in calls)
