# Version: 1.0.0 · updated 26-10-15-03-45
"""2026-10-15, Linux 158: the package gate refused FONT-001 after an 87-minute build; three lessons, three tests."""
import subprocess

from fieldkit.build import firefox_linux, firefox_preflight
from fieldkit.core import pipeline

GATE = 'REQUIRED_FONTS = ["segoeui.ttf", "consola.ttf",\n                  "YuGothR.ttc"]\n'


class Ctx:
    def __init__(self, root, src):
        self.root, self.src = root, src


def _harness(tmp_path, listed, present):
    root, src = tmp_path / "harness", tmp_path / "src"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "release_gate.py").write_text(GATE)
    fonts = src / "browser" / "fonts"
    fonts.mkdir(parents=True)
    (fonts / "moz.build").write_text("FINAL_TARGET_FILES.fonts += [" + ", ".join(f'"{f}"' for f in listed) + "]\n")
    for f in present:
        (fonts / f).write_bytes(b"font")
    return Ctx(root, src)


def test_fonts_list_comes_from_the_gate(tmp_path):
    c = _harness(tmp_path, [], [])
    assert firefox_preflight.required_fonts(c.root) == ["segoeui.ttf", "consola.ttf", "YuGothR.ttc"]


def test_the_158_state_is_refused_before_the_build(tmp_path):
    c = _harness(tmp_path, ["TwemojiMozilla.ttf"], ["TwemojiMozilla.ttf"])   # what the 157 set left on Linux
    ok, detail = firefox_preflight._c_package_fonts(c)
    assert not ok and "not in browser/fonts: segoeui.ttf" in detail and "not listed" in detail


def test_present_but_unlisted_is_still_refused(tmp_path):
    c = _harness(tmp_path, ["TwemojiMozilla.ttf"], ["segoeui.ttf", "consola.ttf", "YuGothR.ttc"])
    ok, detail = firefox_preflight._c_package_fonts(c)
    assert not ok and detail.startswith("not listed in browser/fonts/moz.build")


def test_fixed_tree_passes(tmp_path):
    names = ["segoeui.ttf", "consola.ttf", "YuGothR.ttc"]
    assert firefox_preflight._c_package_fonts(_harness(tmp_path, names, names))[0]


def test_package_failure_names_the_gate_line(tmp_path):
    (tmp_path / "harness" / "scripts").mkdir(parents=True)
    (tmp_path / "harness" / "scripts" / "build_deb.sh").write_text("exit 1\n")
    class C:
        vars = {"harness": str(tmp_path / "harness"), "src": str(tmp_path / "src"), "release": str(tmp_path / "out")}
    out = "  FAIL  FONT-001   missing from the build: segoeui.ttf\n1 gate(s) FAILED.\n"
    r = firefox_linux.stage_package(C, run=lambda *a, **k: (1, out))
    assert "FONT-001" in r["detail"]


def test_a_changed_source_tree_is_not_up_to_date(tmp_path):
    w = tmp_path / "tree"
    w.mkdir()
    (w / "moz.build").write_text("a\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(w), *args], check=True, capture_output=True)
    stage = {"id": "build", "run": {"cmd": ["true"]}, "source_tree": str(w)}
    before = pipeline.Pipeline.fingerprint(stage)
    assert pipeline.Pipeline.fingerprint(stage) == before                  # stable while nothing changes
    (w / "moz.build").write_text("a\nfonts\n")
    assert pipeline.Pipeline.fingerprint(stage) != before                  # an uncommitted edit counts
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "y"],
                   check=True, capture_output=True)
    assert pipeline.Pipeline.fingerprint(stage) != before                  # and so does a commit
    plain = {"id": "x", "run": {"cmd": ["true"]}}
    assert pipeline.Pipeline.fingerprint(plain) == pipeline.Pipeline.fingerprint(dict(plain))
