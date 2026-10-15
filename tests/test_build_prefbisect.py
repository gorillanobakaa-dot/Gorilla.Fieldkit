# Version: 1.0.0 · updated 26-10-15-13-05
"""pref-bisect: halving finds the one costly pref; no cost means no suspect; defaults come from the pristine tree."""
import subprocess

from fieldkit.build import prefbisect as pb


def _fake(culprit, cost=6.0, base=17.0):
    return lambda reset: base + (0.0 if culprit in reset else cost)


def test_halving_finds_the_one_costly_pref():
    names = [f"p{i}" for i in range(12)]
    for culprit in ("p0", "p5", "p11"):
        r = pb.bisect(names, _fake(culprit), tolerance=3.0, out=lambda *a: None)
        assert r["suspect"] == culprit
        assert len(r["rounds"]) <= 2 + 4                         # 12 suspects: at most 4 halvings


def test_no_cost_in_these_prefs_means_no_suspect():
    r = pb.bisect(["a", "b", "c"], lambda reset: 22.0, tolerance=3.0, out=lambda *a: None)
    assert r["suspect"] is None and "not in these prefs" in r["why"]


def test_defaults_are_read_from_the_pristine_release(tmp_path):
    w = tmp_path / "src"
    (w / "modules" / "libpref" / "init").mkdir(parents=True)
    (w / "browser" / "app" / "profile").mkdir(parents=True)
    (w / "modules/libpref/init/StaticPrefList.yaml").write_text(
        "- name: gfx.webrender.all\n  type: bool\n  value: false\n  mirror: once\n"
        "- name: per.platform\n  type: bool\n#if defined(XP_WIN)\n  value: true\n#else\n  value: false\n#endif\n")
    (w / "modules/libpref/init/all.js").write_text('pref("widget.dmabuf.force-enabled", false);\n')
    (w / "browser/app/profile/firefox.js").write_text("")
    g = lambda *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                  check=True, capture_output=True)
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "pristine")
    (w / "modules/libpref/init/all.js").write_text('pref("widget.dmabuf.force-enabled", true);\n')   # our change
    d = pb.upstream_defaults(w)
    assert d["gfx.webrender.all"] is False and d["widget.dmabuf.force-enabled"] is False
    assert "per.platform" not in d                                 # two platform values: never guessed
    assert pb.overrides(d, ["gfx.webrender.all", "unknown.pref"]) == {"gfx.webrender.all": False}


def test_a_verdict_inside_the_wobble_is_not_confirmed():
    seq = iter([24.0, 17.5, 20.0, 21.0, 18.0, 22.0])            # shipped, reset, shipped, reset ... overlapping
    r = pb.confirm("x", lambda reset: next(seq), out=lambda *a: None)
    assert not r["confirmed"]


def test_a_real_cost_is_confirmed():
    r = pb.confirm("x", lambda reset: 17.0 + (0 if reset else 6.0), out=lambda *a: None)
    assert r["confirmed"] and r["gap"] == 6.0 and r["spread"] == 0.0
