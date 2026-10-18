# Version: 1.0.0 · updated 26-10-17-00-10
"""2026-10-16: "one decision, one value". 05.PREFS wrote Inter while firefox.js wrote Segoe UI (D-158-12); eight more
prefs were set twice by the set. And UITour kept lazy.AIWindow after the getter went. None of these may come back."""
import subprocess

from fieldkit.buildh import compile as cg
from fieldkit.buildh import decisions, prefconflicts as pc, symbols


def test_two_added_values_under_overlapping_conditions_conflict():
    alljs = pc.definitions('pref("a", 1);\npref("font.x", "Inter");\n#ifdef XP_WIN\npref("lvl", 9);\n#else\n'
                           'pref("lvl", 4);\n#endif\n', upstream_lines=['pref("a", 1);'])
    ffjs = pc.definitions('pref("font.x", "Segoe UI");\npref("a", 2);\n')
    found = pc.conflicts({"all.js": alljs, "firefox.js": ffjs})
    names = sorted(c["name"] for c in found)
    assert names == ["font.x"]                       # a: upstream line not judged; lvl: different #if branches


def test_the_fix_goes_into_the_patch_with_correct_hunk_headers():
    patch = ("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,3 +1,8 @@\n a\n+#ifdef N\n+p(1);\n+#endif\n+keep\n+#endif\n"
             " b\n c\n@@ -10,2 +15,3 @@\n d\n+e\n f\n")
    out, n = pc.drop_added_lines(patch, [["#ifdef N", "p(1);", "#endif"]])
    assert n == 3                                    # the lone "#endif" after "keep" stays
    assert "@@ -1,3 +1,5 @@" in out and "@@ -10,2 +12,3 @@" in out and "+keep\n+#endif" in out


def test_tree_pref_all_wants_one_value_in_every_added_definition(tmp_path):
    w = tmp_path
    (w / "modules/libpref/init").mkdir(parents=True)
    (w / "browser/app/profile").mkdir(parents=True)
    (w / "modules/libpref/init/all.js").write_text('pref("f", "");\n', newline="\n")
    (w / "browser/app/profile/firefox.js").write_text("// x\n", newline="\n")
    g = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(w), *a],
                                  check=True, capture_output=True)
    g("init", "-q"); g("add", "-A"); g("commit", "-qm", "pristine")
    (w / "modules/libpref/init/all.js").write_text('pref("f", "");\npref("f", "Inter");\n', newline="\n")
    (w / "browser/app/profile/firefox.js").write_text('pref("f", "Segoe UI");\n', newline="\n")
    ok, ev = decisions.tree_pref_all(w, "f", "Segoe UI")
    assert not ok and "all.js:2" in ev
    (w / "modules/libpref/init/all.js").write_text('pref("f", "");\npref("f", "Segoe UI");\n', newline="\n")
    assert decisions.tree_pref_all(w, "f", "Segoe UI")[0]
    assert pc.scan(w) == []


def test_an_undeclared_lazy_name_counts_only_when_the_port_made_it():
    before = 'const lazy = {};\nChromeUtils.defineESModuleGetters(lazy, {\n  AIWindow: "x",\n  Foo: "y",\n});\n' \
             "lazy.AIWindow.go(); lazy.Foo.go(); lazy.Odd.go();\n"
    after = 'const lazy = {};\nChromeUtils.defineESModuleGetters(lazy, {\n  Foo: "y",\n});\n' \
            "lazy.AIWindow.go(); lazy.Foo.go(); lazy.Odd.go();\n"
    assert symbols.undeclared_lazy(after) == ["AIWindow", "Odd"]
    was = set(symbols.undeclared_lazy(before))
    assert [n for n in symbols.undeclared_lazy(after) if n not in was] == ["AIWindow"]


def test_the_gate_asks_the_owners_preflight_and_fails_closed_without_one(monkeypatch):
    from fieldkit.buildh import ownercheck
    t = {"workdir": "W", "meta": {}}
    monkeypatch.setattr(ownercheck, "_owner_root", lambda t: "R")
    monkeypatch.setattr(pc, "scan", lambda w: [])
    monkeypatch.setattr(decisions, "register_root", lambda t: None)
    text = "[-] Lazy imports resolve (BLOCKER): 1 undeclared lazy symbol use(s)\n"
    rows = cg.owner_rows(t, run_preflight=lambda root: (1, text), local={})
    assert not rows[0][1] and "Lazy imports resolve" in rows[0][2]
    monkeypatch.setattr(ownercheck, "_owner_root", lambda t: None)
    assert not cg.owner_rows(t, run_preflight=None, local={})[0][1]
    ok = cg.owner_rows(t, local={"firefox": {"owner_preflight": "none: the Linux machine checks with set-proof"}})
    assert ok[0][1]
