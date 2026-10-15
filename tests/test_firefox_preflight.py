"""The Linux preflight: checks ported from the Windows harness, each proved on a small fake tree."""
import os
from pathlib import Path

import pytest

from fieldkit.build import firefox_preflight as p
from fieldkit.build.firefox_linux import MACH_ENV_DROP, mach_env


TUNED = ('export RUSTFLAGS="-C opt-level=3 -C target-cpu=native"\n'
         'ac_add_options --enable-optimize="-O3 -march=native -mtune=native"\n'
         "ac_add_options --with-ccache=sccache\nac_add_options --enable-lto=thin\n"
         "ac_add_options --enable-linker=lld-21\nmk_add_options MOZ_MAKE_FLAGS=\"-j6\"\n")



@pytest.fixture(autouse=True)
def _no_machine_tasks(monkeypatch):
    # source-is-the-port reads this machine's harness tasks; the fixture trees here belong to no task
    from fieldkit.build import firefox_linux
    monkeypatch.setattr(firefox_linux, "task_work_trees", lambda version, state=None: [])

def _tree(tmp_path, branding=True, fonts=()):
    root, src = tmp_path / "harness", tmp_path / "src"
    root.mkdir()
    (src / "browser" / "config").mkdir(parents=True)
    (src / "browser" / "config" / "version.txt").write_text("158.0\n")
    (root / "mozconfig").write_text("ac_add_options --with-branding=browser/branding/gorilla  # brand\n" + TUNED)
    (root / "recreate.sh").write_text("git clone --branch FIREFOX_158_0_RELEASE ...  # stable 158.0\n")
    (root / "intents" / "port-158.0").mkdir(parents=True)
    (root / "intents" / "port-158.0" / "STATE.md").write_text("state\n")
    for rel in ("scripts/build_deb.sh", "scripts/fonts-microsoft.sh",
                "deb_template/usr/share/icons/hicolor/1024x1024/apps/gorilla-unleashed.png",
                "deb_template/usr/share/applications/gorilla-unleashed.desktop"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n")
    if branding:
        (src / "browser" / "branding" / "gorilla").mkdir(parents=True)
        (src / "browser" / "branding" / "gorilla" / "a.png").write_bytes(b"x")
    (src / "browser" / "fonts").mkdir(parents=True)
    (src / "browser" / "fonts" / "moz.build").write_text(
        "FINAL_TARGET_FILES.fonts += [" + ", ".join(f'"{f}"' for f in ("Twemoji.ttf",) + tuple(fonts)) + "]\n")
    (src / "browser" / "fonts" / "Twemoji.ttf").write_bytes(b"f")
    return p.Ctx(root=root, src=src, env={}, which=lambda t: "/usr/bin/" + t, free_gb=lambda _: 100.0,
                 temps=lambda: [("acpitz", 40.0), ("coretemp", 55.0)])


def _row(res, cid):
    return next(r for r in res["rows"] if r["id"] == cid)


def test_a_healthy_tree_passes(tmp_path):
    res = p.run(_tree(tmp_path))
    assert res["ok"] and not res["warnings"], p.lines(res)


def test_missing_branding_and_font_are_blockers(tmp_path):
    res = p.run(_tree(tmp_path, branding=False, fonts=("consola.ttf",)))
    assert set(res["blockers"]) == {"branding", "bundled-files"}
    assert "consola.ttf" in _row(res, "bundled-files")["detail"]
    assert "REFUSED: branding, bundled-files" in p.lines(res)[-1]


def test_objdir_defaults_to_mach_and_honours_moz_objdir(tmp_path):
    ctx = _tree(tmp_path)
    assert p.objdir(ctx) == ctx.src / "obj-x86_64-pc-linux-gnu"
    (ctx.root / "mozconfig").write_text("mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-ff\n")
    assert p.objdir(ctx) == ctx.src / "obj-ff"


def test_clobber_compares_mtimes_not_contents(tmp_path):
    ctx = _tree(tmp_path)
    od = p.objdir(ctx)
    od.mkdir()
    (ctx.src / "CLOBBER").write_text("bug 123: a moz.build change\n")
    (od / "CLOBBER").write_text("")                      # a zero-byte marker, never a copy
    os.utime(od / "CLOBBER", (2_000_000_000, 2_000_000_000))
    assert _row(p.run(ctx), "clobber")["ok"]             # different contents, current marker: fine
    os.utime(ctx.src / "CLOBBER", (2_000_000_100, 2_000_000_100))
    assert not _row(p.run(ctx), "clobber")["ok"]


def test_stale_objdir_beside_the_real_one_warns(tmp_path):
    ctx = _tree(tmp_path)
    p.objdir(ctx).mkdir()
    old = ctx.src / "obj-old"
    old.mkdir()
    (old / "config.status").write_text("")
    r = _row(p.run(ctx), "stale-objdir")
    assert not r["ok"] and r["severity"] == p.WARN and "obj-old (configured)" in r["detail"]


def test_tools_disk_and_temperature(tmp_path):
    ctx = _tree(tmp_path)
    ctx.which = lambda t: None if t == "cargo" else "/usr/bin/" + t
    ctx.free_gb = lambda _: 5.0
    ctx.temps = lambda: [("acpitz", 40.0)]               # a board zone is not a CPU sensor
    res = p.run(ctx)
    assert {"base-tools", "disk"} <= set(res["blockers"]) and "temp-source" in res["warnings"]
    assert _row(res, "base-tools")["detail"] == "missing: cargo"


def test_agent_markers_warn_and_the_build_strips_all_four(tmp_path):
    ctx = _tree(tmp_path)
    ctx.env = {"OPENCODE": "1"}
    assert "agent-env" in p.run(ctx)["warnings"]
    env = mach_env({m: "1" for m in p.AGENT_MARKERS} | {"PATH": "/usr/bin"})
    assert set(MACH_ENV_DROP) == set(p.AGENT_MARKERS) and env == {"PATH": "/usr/bin"}


def test_a_crashing_check_fails_and_does_not_stop_the_run(tmp_path):
    boom = p.Check("boom", "raises", p.BLOCKER, lambda ctx: 1 / 0, "fix", "test")
    res = p.run(_tree(tmp_path), checks=[boom] + p.CHECKS)
    assert not _row(res, "boom")["ok"] and "ZeroDivisionError" in _row(res, "boom")["detail"]
    assert len(res["rows"]) == len(p.CHECKS) + 1


def test_every_check_names_its_incident_and_its_origin():
    for c in p.CHECKS:
        assert c.why and c.remedy and c.origin and c.severity in (p.BLOCKER, p.WARN), c.cid
    assert len({c.cid for c in p.CHECKS}) == len(p.CHECKS)


def test_hwmon_reader(tmp_path):
    h = tmp_path / "hwmon0"
    h.mkdir()
    (h / "name").write_text("coretemp\n")
    (h / "temp1_input").write_text("56000\n")
    (h / "temp2_input").write_text("garbage\n")
    assert p.hwmon_temps(tmp_path) == [("coretemp", 56.0)]


def test_a_lost_tuning_line_blocks_the_build(tmp_path):
    ctx = _tree(tmp_path)
    m = ctx.root / "mozconfig"
    m.write_text(m.read_text().replace("ac_add_options --with-ccache=sccache\n", "# ac_add_options --with-ccache=sccache\n"))
    r = _row(p.run(ctx), "vaio-tuning")
    assert not r["ok"] and "sccache compile cache" in r["detail"]


def test_recreate_building_a_nightly_is_flagged(tmp_path):
    ctx = _tree(tmp_path)
    (ctx.root / "recreate.sh").write_text('say "Baseline: Firefox 154.0a1 nightly"\n')
    r = _row(p.run(ctx), "recreate-current")
    assert not r["ok"] and "Nightly" in r["detail"] and "158.0" in r["detail"]


def test_a_missing_packaging_piece_blocks(tmp_path):
    ctx = _tree(tmp_path)
    (ctx.root / "scripts" / "fonts-microsoft.sh").unlink()
    assert "deb-packaging" in p.run(ctx)["blockers"]


def test_the_vaio_sensors_tool_is_read_first_and_warns_on_throttling(tmp_path):
    """2026-10-15: the owner's sensors.gorilla knows the Sony profile, the fan daemon and throttling."""
    ctx = _tree(tmp_path)
    ctx.vaio = {"tool": "x", "package_c": 77.0, "fan_pct": 58, "profile": "performance", "daemon": "active",
                "package_w": 30.0, "throttle_events": 0}
    r = _row(p.run(ctx), "temp-source")
    assert r["ok"] and "profile performance" in r["detail"]
    ctx.vaio = dict(ctx.vaio, throttle_events=3, daemon="inactive")
    r = _row(p.run(ctx), "temp-source")
    assert not r["ok"] and "throttled 3" in r["detail"] and "daemon is inactive" in r["detail"]
