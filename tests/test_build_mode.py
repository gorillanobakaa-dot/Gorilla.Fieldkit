# Version: 1.0.0 · updated 26-10-15-14-50
"""The build-cost decision: compiled sources need a full build; JS, CSS, FTL and prefs need `mach build faster`."""
import subprocess

from fieldkit.build import firefox_linux as fl


def _tree(tmp_path):
    w = tmp_path / "src"
    (w / "obj-x86_64-pc-linux-gnu" / "dist" / "bin").mkdir(parents=True)
    (w / "browser" / "themes").mkdir(parents=True)
    (w / "dom").mkdir()
    (w / "browser" / "themes" / "a.css").write_text("a{}\n")
    (w / "dom" / "b.cpp").write_text("int b;\n")
    (w / ".gitignore").write_text("obj-*/\n")
    g = lambda *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                  check=True, capture_output=True, text=True)
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "built")
    head = g("rev-parse", "HEAD").stdout.strip()
    return w, head


def test_no_record_means_full(tmp_path):
    w, _ = _tree(tmp_path)
    assert fl.build_mode(w)[0] == "full"


def test_css_only_change_is_faster_and_cpp_is_full(tmp_path):
    w, head = _tree(tmp_path)
    (w / "obj-x86_64-pc-linux-gnu" / fl.STAMP).write_text(head + "\n")
    assert fl.build_mode(w)[0] == "nothing"
    (w / "browser" / "themes" / "a.css").write_text("a{color:pink}\n")            # uncommitted counts
    mode, why = fl.build_mode(w)
    assert mode == "faster" and "a.css" in why
    (w / "dom" / "b.cpp").write_text("int b = 1;\n")
    mode, why = fl.build_mode(w)
    assert mode == "full" and "b.cpp" in why


def test_compiled_patterns():
    for f in ("dom/x.cpp", "x/y.rs", "a/moz.build", "modules/libpref/init/StaticPrefList.yaml", "dom/webidl/X.webidl",
              "browser/locales/jar.mn", "Cargo.lock"):
        assert fl.COMPILED.search(f), f
    for f in ("browser/themes/shared/x.css", "browser/base/content/browser.js", "browser/locales/en-US/x.ftl",
              "browser/app/profile/firefox.js", "browser/modules/X.sys.mjs"):
        assert not fl.COMPILED.search(f), f
