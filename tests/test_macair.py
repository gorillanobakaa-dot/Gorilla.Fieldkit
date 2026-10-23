# Version: 1.0.0 · updated 26-10-22-19-55
"""fieldkit macair: the Mac decisions are read from the source, the 11-inch budget is the measured arithmetic, the
published series can never carry a Microsoft font, and the verify page is found on a colour-managed screen."""
import subprocess

import sys

import pytest

from fieldkit.macair import macair as M

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux-only tool: the Mac browser is cross-built on Linux")

GOOD = {
    "browser/app/profile/firefox.js": 'pref("font.name.sans-serif.x-western", "Segoe UI");\n'
                                      'pref("gorilla.ui.font-family", "Segoe UI");\npref("gorilla.mac.thin-text", true);\n'
                                      'pref("fission.autostart",                           false);\n'
                                      'pref("browser.startup.homepage",            "about:blank");\n'
                                      'pref("browser.newtabpage.enabled",          false);\n'
                                      'pref("dom.ipc.processPrelaunch.enabled", false);\n',
    "modules/libpref/init/all.js": 'pref("font.name.monospace.x-western", "");\npref("font.name.monospace.x-western", "Consolas");\n',
    "widget/cocoa/nsLookAndFeel.mm": 'Preferences::GetCString("gorilla.ui.font-family", family)',
    "layout/style/res/ua.css": '@media -moz-pref("gorilla.mac.thin-text") {\n  :root {\n    -moz-osx-font-smoothing: grayscale;\n  }\n}\n',
    "browser/fonts/moz.build": 'if CONFIG["MOZ_WIDGET_TOOLKIT"] in ("windows", "gtk", "cocoa"):\n' +
                               "".join(f'    "{f}",\n' for f in M.FONTS),
    "browser/installer/package-manifest.in": "#if defined(XP_WIN) || defined(MOZ_WIDGET_GTK) || defined(XP_MACOSX)\n@RESPATH@/fonts/*\n#endif\n",
    "browser/base/content/aboutDialog.css": "#aboutDialogContainer {\n  min-height: 610px !important;\n}\n\n"
                                            "#leftBox {\n  width: 400px !important;\n  height: 400px !important;\n}\n",
    "toolkit/themes/shared/in-content/common-shared.css": "@media (max-height: 819px) {\n  #content {\n    background-size: min(650px, calc(100vh - 170px)) !important;\n  }\n}\n",
}
MOZCONFIG = """export CFLAGS="-march=sandybridge -mtune=sandybridge"
ac_add_options --enable-bundled-fonts
ac_add_options --without-onnx-runtime
ac_add_options --without-wasm-sandboxed-libraries
ac_add_options --target=x86_64-apple-darwin
"""


def tree(tmp_path, **changes):
    root = tmp_path / "tree"
    for rel, text in {**GOOD, **changes}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    mc = tmp_path / "mozconfig.air"
    mc.write_text(changes.pop("_mozconfig", MOZCONFIG) if "_mozconfig" in changes else MOZCONFIG)
    return root, mc


def lost(rows):
    return [what for ok, dec, what in rows if not ok]


def test_a_complete_tree_is_ready(tmp_path):
    root, mc = tree(tmp_path)
    assert lost(M.preflight(root, mc)) == []


@pytest.mark.parametrize("rel,text,expect", [
    ("browser/base/content/aboutDialog.css", "#aboutDialogContainer {\n  min-height: 800px !important;\n}\n"
     "#leftBox {\n  width: 500px !important;\n  height: 500px !important;\n}\n", "min-height 800"),
    ("modules/libpref/init/all.js", 'pref("font.name.monospace.x-western", "JetBrains Mono");\n', "monospace"),
    ("modules/libpref/init/all.js", 'pref("font.name.monospace.x-western", "Consolas");\n'
     'pref("font.name.monospace.x-western", "JetBrains Mono");\n', "also set to"),       # right value + a stale second one
    ("layout/style/res/ua.css", "", "greyscale"),
    ("toolkit/themes/shared/in-content/common-shared.css", "@media (-moz-platform: macos) {\n}\n", "page height"),
    ("browser/fonts/moz.build", 'if CONFIG["MOZ_WIDGET_TOOLKIT"] in ("windows", "gtk"):\n', "cocoa"),
])
def test_each_lost_decision_is_named(tmp_path, rel, text, expect):
    root, mc = tree(tmp_path, **{rel: text})
    assert any(expect in w for w in lost(M.preflight(root, mc)))


def test_native_cpu_in_the_mozconfig_is_refused(tmp_path):
    root, mc = tree(tmp_path)
    mc.write_text(MOZCONFIG + 'export CFLAGS="-march=native"\n')
    assert any("never -march=native" in w for w in lost(M.preflight(root, mc)))


def test_the_budget_is_the_measured_arithmetic():
    b = M.budget()
    assert b["content_max"] == 768 - 22 - 35 - 22 - 20 == 669
    assert b["logo_max"] == 669 - (100 + 40 + 20 + 50) == 459
    assert M.budget({"height": 900})["logo_max"] == 591                       # a taller screen gets a bigger logo


def test_export_refuses_a_series_that_carries_a_font(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (repo / "a.txt").write_text("x")
    g("add", "-A")
    g("commit", "-qm", "base")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (repo / "a.txt").write_text("y")
    g("commit", "-qam", "change")

    def run(argv, **kw):                       # format-patch with the font exclusion removed: a series WITH a font
        if "format-patch" in argv:
            (repo / "segoeui.ttf").write_bytes(b"\0font")
            g("add", "-A")
            g("commit", "-qm", "font")
            argv = [a for a in argv if not a.startswith(":(exclude)")]
            argv[argv.index(f"{M.BASE}..HEAD")] = f"{base}..HEAD"
        return subprocess.run(argv, **kw)
    with pytest.raises(SystemExit, match="Microsoft font"):
        M.export_patches(repo, tmp_path / "out.tar.xz", tmp_path / "macos", run=run)


def test_export_writes_tarball_and_checksums(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (repo / "a.txt").write_text("x")
    g("add", "-A")
    g("commit", "-qm", "base")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (repo / "a.txt").write_text("y")
    g("commit", "-qam", "change")
    monkeypatch.setattr(M, "BASE", base)
    out, n = M.export_patches(repo, tmp_path / "p.tar.xz", tmp_path / "macos")
    assert n == 1 and out.exists()
    assert (tmp_path / "macos" / "patches-mac.sha256").read_text().startswith(M.sha256(out))
    assert "0001-change.patch" in (tmp_path / "macos" / "PATCHES.sha256").read_text()


def screen(page):
    """A 1366x768 screen: black browser chrome on top, the test page below in `page` colour, one line of text."""
    im = Image.new("RGB", (1366, 768), (0, 0, 0))
    ImageDraw.Draw(im).rectangle([0, 96, 1365, 767], fill=page)
    ImageDraw.Draw(im).text((30, 130), "This is a list of about Gorilla pages", fill=(251, 251, 254))
    return im


def test_the_page_is_found_on_a_colour_managed_grey_and_not_on_black():
    b = M.locate(screen((44, 43, 41)))                     # #2a2a2a after colour management: close, not exact
    assert b and b[1] == 96 and b[3] <= 140
    assert M.locate(screen((0, 0, 0))) is None             # the browser's own black chrome is never the page
    assert M.locate(screen((3, 50, 89))) is None           # a coloured page is refused: LCD counts would be wrong


def test_medians_skip_missing_reads():
    assert M._median([3, None, 1, 2]) == 2 and M._median([None]) is None


def test_heat_report_puts_runs_side_by_side(tmp_path):
    import json
    p = tmp_path / "heat-runs.jsonl"
    for label, cpu in (("h264-shipped", 38.5), ("vp9-test", 121.0)):
        p.open("a").write(json.dumps({"label": label, "when": "26-10-22-21-00-00", "cpu_median": cpu,
                                      "mem_mb_median": 1500, "rpm_median": 2000, "temp_median": 55.0,
                                      "temp_max": 61.0, "prefs": []}) + "\n")
    out = M.heat_report(p)
    assert out[0].startswith("label") and "h264-shipped" in out[1] and "121.0" in out[2]


def _app_zip(path, inner_files):
    """A built Mac app as a .zip whose omni.ja holds `inner_files`."""
    import io
    import zipfile
    omni = io.BytesIO()
    with zipfile.ZipFile(omni, "w") as o:
        for f in inner_files:
            o.writestr(f, "x")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Gorilla Unleashed.app/Contents/Resources/omni.ja", omni.getvalue())
        z.writestr("Gorilla Unleashed.app/Contents/MacOS/firefox", "bin")
    return path


REG = """decisions:
  - id: D-158-08
    title: no AI
    app_absent:
      - 'chrome/toolkit/content/global/ml/*'
      - 'localization/*/preview/aiWindow.ftl'
  - id: D-157-12
    title: no app_absent here
"""


def test_app_check_names_every_removed_file_by_its_decision(tmp_path):
    reg = tmp_path / "reg.yaml"
    reg.write_text(REG)
    z = _app_zip(tmp_path / "t4.zip", ["chrome/toolkit/content/global/ml/backends/OnnxPipeline.mjs",
                                        "localization/en-US/preview/aiWindow.ftl", "chrome/toolkit/content/global/ok.js"])
    found = M.app_check(z, reg)
    assert {d for d, _ in found} == {"D-158-08"} and len(found) == 2
    assert all(p.startswith("Resources/omni.ja!") for _, p in found)
    assert M.app_check(_app_zip(tmp_path / "t5.zip", ["chrome/toolkit/content/global/ok.js"]), reg) == []


def test_install_refuses_a_dirty_app_before_touching_the_mac(tmp_path, monkeypatch):
    reg = tmp_path / "reg.yaml"
    reg.write_text(REG)
    monkeypatch.setattr(M, "REGISTER", reg)
    monkeypatch.setattr(M, "mac_target", lambda name: (_ for _ in ()).throw(AssertionError("must not reach the Mac")))
    rc, out = M.install(_app_zip(tmp_path / "t4.zip", ["chrome/toolkit/content/global/ml/x.mjs"]))
    assert rc == 3 and out.startswith("REFUSED") and "D-158-08" in out


def test_fan_control_summary_says_off_only_when_every_sample_saw_macos():
    off = [{"fan_control": "macOS (automatic)"}] * 3
    assert M.fan_control_summary(off) == "off"
    mixed = off + [{"fan_control": "Macs Fan Control"}]
    assert M.fan_control_summary(mixed) == "Macs Fan Control, macOS (automatic)"
    assert M.fan_control_summary([{"fan_control": ""}]) == "unknown"
    assert M.fan_control_summary(off + [{"fan_control": ""}]) == "off"         # a dropped read is not "on"


@pytest.mark.skipif(not M.SENSORS.exists(), reason="sensors-gorilla not gathered: fieldkit gather --only sensors-gorilla")
def test_heat_uses_the_sensors_gorilla_reader_in_this_repo():
    assert M.SENSORS.name == "sensors.gorilla" and M.SENSORS.exists()
    src = M.SENSORS.read_text()
    assert "def mac_reading" in src and "--json" in src


def test_scp_upload_reverses_the_ends():
    from fieldkit.mac import macssh
    mac = {"host_key_alias": "h", "key": "~/k", "user": "u"}
    assert macssh.scp_argv(mac, "1.2.3.4", "/tmp/r", "/l")[-2:] == ["u@1.2.3.4:/tmp/r", "/l"]
    assert macssh.scp_argv(mac, "1.2.3.4", "/tmp/r", "/l", upload=True)[-2:] == ["/l", "u@1.2.3.4:/tmp/r"]


def test_heat_puts_prefs_js_back_so_test_prefs_do_not_stay_in_the_profile(tmp_path, monkeypatch):
    # The browser copies user.js prefs into prefs.js on start; restoring only user.js left every Stage D test pref
    # set in the owner's profile (2026-10-23).
    sent = []

    class FakeMac:
        @staticmethod
        def ssh_argv(mac, address, cmd):
            return ["ssh", cmd]

        @staticmethod
        def scp_argv(*a, **k):
            return ["scp"]

        @staticmethod
        def _run(argv, timeout=None):
            sent.append(argv[-1])
            if "lsof" in argv[-1]:
                return 0, "/Users/u/Library/Application Support/Firefox/Profiles/x.default\n"  # a space in the path
            return 0, ""

    sensors = tmp_path / "sensors.gorilla"
    sensors.write_text("")
    monkeypatch.setattr(M, "SENSORS", sensors)
    monkeypatch.setattr(M, "mac_target", lambda name: (FakeMac, {}, "1.2.3.4"))
    monkeypatch.setattr("time.sleep", lambda s: None)
    M.heat("t", "blank", minutes=0, prefs=[("a.b", "false")], warmup=0, out_dir=tmp_path)
    setup = next(c for c in sent if "user.js.heat-" in c and "printf" in c)
    restore = next(c for c in sent if c.startswith("osascript") and "mv " in c)
    lookup = next(c for c in sent if "lsof" in c)
    assert "[^ ]*/Profiles" not in lookup          # the default profile is under "Application Support"
    assert "prefs.js.heat-" in setup and "cp -p" in setup
    assert "prefs.js.heat-" in restore and restore.index("pgrep") < restore.index("prefs.js")
    sent.clear()
    M.heat("t", "blank", minutes=0, prefs=[], warmup=0, out_dir=tmp_path)
    assert not any("prefs.js" in c for c in sent)          # no test prefs: the profile is not touched
