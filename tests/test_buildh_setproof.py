# Version: 1.0.0 · updated 26-10-16-19-20
"""2026-10-16, the first Windows port of 158: what must never again become a hand job or slip through.
- a patch that only repairs code a group excluded on this platform brings in (21.PORT.FIXES.158/008 over 01.MEDIA)
- the D-157-42 security lists, data refreshed at every build (22.EGRESS.LOCKDOWN.157/098)
- every platform's selection proven on pristine (set-proof), and the pre-push hook running it
- binary-patch exemptions per platform; a decision whose checks read platform-only files must declare it
- lists-refresh before every build, fail closed when offline and a list is about to switch off"""
import json
import subprocess

import pytest

from fieldkit.buildh import binpatch, buildrun, decisions, setproof
from fieldkit.core import privacy


def test_refreshed_list_sections_are_cut_out_of_a_patch():
    text = ("# header\n\n"
            "diff --git a/security/manager/ssl/nsSTSPreloadList.inc b/security/manager/ssl/nsSTSPreloadList.inc\n"
            "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
            "diff --git a/browser/branding/x.js b/browser/branding/x.js\n--- a/y\n+++ b/y\n@@ -1 +1 @@\n-c\n+d\n")
    body, removed = setproof.strip_refreshed(text)
    assert removed == ["security/manager/ssl/nsSTSPreloadList.inc"]
    assert "nsSTSPreloadList" not in body and "browser/branding/x.js" in body and setproof.has_hunks(body)
    only, removed = setproof.strip_refreshed(text.split("diff --git a/browser")[0])
    assert removed and not setproof.has_hunks(only)


def test_a_hunk_whose_context_an_excluded_patch_adds_is_a_dependency():
    plus = {"if (StaticPrefs::media_gorilla_hardware_only_mode() &&": "01.MEDIA/dom.patch",
            "(FindInReadable(\"av01\"_ns, aType.OriginalString(),": "01.MEDIA/dom.patch"}
    hunk = ["  if (StaticPrefs::media_gorilla_hardware_only_mode() &&",
            "-      (FindInReadable(\"av01\"_ns, aType.OriginalString(),",
            "+      (CaseInsensitiveFindInReadable(\"av01\"_ns, aType.OriginalString()) ||"]
    assert setproof.dependency(hunk, plus) == "01.MEDIA/dom.patch"
    assert setproof.dependency([" int main() {", "-  return 1;", "+  return 0;"], plus) is None


def test_excluded_here_reads_platforms_and_exclude_on():
    raw = {"01.MEDIA": {"platforms": ["linux"]}, "21.FIX": {"exclude_on": {"windows": ["008-x.patch"]}}}
    ex = setproof.excluded_here(raw, "windows")
    assert "01.MEDIA/*" in ex and "21.FIX/008-x.patch" in ex
    assert setproof.excluded_here(raw, "linux") == {}


def _git(d, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(d), *a], check=True,
                   capture_output=True)


@pytest.fixture
def world(tmp_path):
    pristine = tmp_path / "pristine"
    (pristine / "dom").mkdir(parents=True)
    (pristine / "dom" / "Traits.cpp").write_text("int f() {\n  return 1;\n}\n", newline="\n")
    (pristine / "app.txt").write_text("hello\n", newline="\n")
    _git(pristine, "init", "-q")
    _git(pristine, "add", "-A")
    _git(pristine, "commit", "-qm", "pristine")
    ps = tmp_path / "set"
    for g in ("01.MEDIA", "21.FIX"):
        (ps / "patches" / g).mkdir(parents=True)
    (ps / "patches" / "01.MEDIA" / "001-media.patch").write_text(
        "diff --git a/dom/Traits.cpp b/dom/Traits.cpp\n--- a/dom/Traits.cpp\n+++ b/dom/Traits.cpp\n"
        "@@ -1,3 +1,5 @@\n int f() {\n+  if (gorilla_hardware_only_mode_enabled() &&\n"
        "+      FindInReadable(\"av01\"_ns, aType.OriginalString())) return 2;\n   return 1;\n }\n",
        newline="\n")
    (ps / "patches" / "21.FIX" / "008-fix.patch").write_text(
        "diff --git a/dom/Traits.cpp b/dom/Traits.cpp\n--- a/dom/Traits.cpp\n+++ b/dom/Traits.cpp\n"
        "@@ -1,5 +1,5 @@\n int f() {\n   if (gorilla_hardware_only_mode_enabled() &&\n"
        "-      FindInReadable(\"av01\"_ns, aType.OriginalString())) return 2;\n"
        "+      CaseInsensitiveFindInReadable(\"av01\"_ns, aType.OriginalString())) return 2;\n   return 1;\n }\n",
        newline="\n")
    (ps / "patches" / "21.FIX" / "009-app.patch").write_text(
        "diff --git a/app.txt b/app.txt\n--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-hello\n+hello gorilla\n",
        newline="\n")
    (ps / "config").mkdir()
    pol = {"patchset_root": "patches", "groups": {
        "01.MEDIA": {"status": "enabled", "platforms": ["linux"]},
        "21.FIX": {"status": "enabled"}}}
    (ps / "config" / "patch_policy.json").write_text(json.dumps(pol))
    return ps, pristine


def test_set_proof_names_an_undeclared_dependency_on_one_platform_only(world):
    ps, pristine = world
    lin = setproof.prove(ps, pristine, "linux", say=lambda m: None)
    win = setproof.prove(ps, pristine, "windows", say=lambda m: None)
    assert lin["ok"], lin
    assert not win["ok"]
    assert win["failures"][0]["patch"] == "21.FIX/008-fix.patch"
    assert win["failures"][0]["depends_on"] == "01.MEDIA/001-media.patch"
    assert any("EXCLUDED DEPENDENCY" in l for l in setproof.lines(win))


def test_once_declared_in_exclude_on_both_platforms_prove(world):
    ps, pristine = world
    pol = json.loads((ps / "config" / "patch_policy.json").read_text())
    pol["groups"]["21.FIX"]["exclude_on"] = {"windows": ["008-fix.patch"]}
    (ps / "config" / "patch_policy.json").write_text(json.dumps(pol))
    assert setproof.prove(ps, pristine, "windows", say=lambda m: None)["ok"]
    assert setproof.prove(ps, pristine, "linux", say=lambda m: None)["ok"]


def test_binary_exemptions_are_per_platform():
    pol = {"binary_exempt": {"a.bmp": "Linux does without it"},
           "binary_exempt_on": {"windows": {"b.ico": "decided for Windows"}}}
    assert binpatch.exemptions(pol, "linux") == {"a.bmp": "Linux does without it"}
    assert binpatch.exemptions(pol, "windows") == {"b.ico": "decided for Windows"}


def test_a_decision_reading_platform_only_files_must_declare_its_platforms(world):
    ps, _ = world
    pol = json.loads((ps / "config" / "patch_policy.json").read_text())
    pol["groups"]["21.FIX"]["exclude_on"] = {"windows": ["008-fix.patch"]}   # as the real policy now says
    (ps / "config" / "patch_policy.json").write_text(json.dumps(pol))
    e = [{"id": "D-X", "status": "enforced", "verify": [{"tree_contains": {"path": "dom/Traits.cpp", "text": "x"}}]},
         {"id": "D-Y", "status": "enforced", "verify": [{"tree_contains": {"path": "app.txt", "text": "x"}}]}]
    probs = decisions.platform_problems(ps, e)
    assert len(probs) == 1 and probs[0].startswith("D-X") and "['linux']" in probs[0]
    e[0]["platforms"] = ["linux"]
    assert decisions.platform_problems(ps, e) == []


def test_lists_are_refreshed_before_the_build_and_recorded():
    t = {"workdir": "W"}
    recorded = []
    fresh = lambda w, write: {"rows": [{"ok": True}], "written": ["security/ct/CTKnownLogs.h"], "source": "main abc"}
    r = buildrun.refresh_lists("t", t, say=lambda m: None, refresh=fresh, record=lambda f, n: recorded.append(f))
    assert r["ok"] and recorded == [["security/ct/CTKnownLogs.h"]]


def test_offline_the_build_goes_on_only_if_no_list_switches_off_soon():
    t = {"workdir": "W"}

    def offline(w, write):
        raise OSError("no network")
    ok = buildrun.refresh_lists("t", t, say=lambda m: None, refresh=offline, judge_rows=lambda w: [{"ok": True}])
    assert ok["ok"]
    bad = buildrun.refresh_lists("t", t, say=lambda m: None, refresh=offline,
                                 judge_rows=lambda w: [{"ok": False, "evidence": "CT switches off in 20 days"}])
    assert not bad["ok"] and "20 days" in bad["why"]


def test_the_pre_push_hook_proves_a_patch_set_on_every_platform(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    text = privacy.install_hook(tmp_path, "/opt/fk").read_text()
    assert "build-harness set-proof patchset=. platform=all" in text
    assert "config/patch_policy.json" in text


def test_the_binary_gate_refuses_when_no_patch_set_is_found(monkeypatch):
    from fieldkit.buildh import ownercheck
    monkeypatch.setattr(ownercheck, "patchset_owner", lambda t: None)
    r = buildrun.binary_gate({"workdir": "W"})
    assert not r["ok"] and "patch_policy.json" in r["why"]


def test_paths_only_one_system_builds_are_classified():
    assert setproof.platform_of("widget/gtk/nsWindow.cpp") == "linux"
    assert setproof.platform_of("widget/windows/nsWindow.cpp") == "windows"
    assert setproof.platform_of("browser/branding/gorilla/branding.nsi") == "windows"
    assert setproof.platform_of("browser/branding/gorilla/msix/Assets/StoreLogo.scale-200.png") == "windows"
    assert setproof.platform_of("browser/branding/gorilla/firefox.icns") == "mac"
    assert setproof.platform_of("browser/app/profile/firefox.js") is None
    assert setproof.foreign_only({"widget/gtk/a.cpp"}, "windows") == "linux"
    assert setproof.foreign_only({"widget/gtk/a.cpp", "dom/b.cpp"}, "windows") is None      # one shared file: judged
    assert setproof.foreign_only({"widget/cocoa/a.mm"}, "windows") is None                    # macOS is not built
    assert setproof.foreign_only({"widget/windows/a.cpp"}, "windows") is None


def test_an_undeclared_other_platform_patch_fails_the_proof_until_rebase_set_declares_it(world):
    ps, pristine = world
    pol = json.loads((ps / "config" / "patch_policy.json").read_text())
    pol["groups"]["21.FIX"]["exclude_on"] = {"windows": ["008-fix.patch"]}
    (ps / "patches" / "21.FIX" / "010-gtk.patch").write_text(
        "diff --git a/widget/gtk/w.cpp b/widget/gtk/w.cpp\nnew file mode 100644\n--- /dev/null\n+++ b/widget/gtk/w.cpp\n"
        "@@ -0,0 +1 @@\n+int gtk = 1;\n", newline="\n")
    (ps / "config" / "patch_policy.json").write_text(json.dumps(pol))
    win = setproof.prove(ps, pristine, "windows", say=lambda m: None)
    assert not win["ok"] and win["failures"][0]["foreign"] == "linux"
    assert any("OTHER PLATFORM 21.FIX/010-gtk.patch" in l for l in setproof.lines(win))
    assert setproof.declare_foreign(pol["groups"]["21.FIX"], ps / "patches" / "21.FIX") == {"windows": ["010-gtk.patch"]}
    assert pol["groups"]["21.FIX"]["exclude_on"]["windows"] == ["008-fix.patch", "010-gtk.patch"]
    (ps / "config" / "patch_policy.json").write_text(json.dumps(pol))
    assert setproof.prove(ps, pristine, "windows", say=lambda m: None)["ok"]
    assert setproof.prove(ps, pristine, "linux", say=lambda m: None)["ok"]

def test_the_pre_push_hook_guards_the_owners_decision_record(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    text = privacy.install_hook(tmp_path, "/opt/fk").read_text()
    assert "decisions/gorilla_decisions.json" in text and 'GORILLA_OWNER_APPROVED" != "1"' in text
