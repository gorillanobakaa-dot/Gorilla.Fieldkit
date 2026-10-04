"""The Firefox upgrade workflow end to end, on a miniature Firefox and patch set."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fieldkit.buildh import firefox, task, upstream

pytestmark = pytest.mark.skipif(not Path(firefox._patch_exe()).exists() and not shutil.which("patch"),
                                reason="GNU patch not installed")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


PREFS_157 = "".join(f'pref("filler.{i}", {i});\n' for i in range(30)) + \
    'pref("browser.startup.page", 1);\npref("datareporting.telemetry.enabled", true); // renamed in 157\n'
# 157 moved the telemetry pref 30 lines down AND renamed it: neither patch nor the transplant may guess
TELEMETRY_PATCH = """--- a/prefs.js
+++ b/prefs.js
@@ -1,3 +1,3 @@
 pref("browser.startup.homepage", "about:home");
-pref("toolkit.telemetry.enabled", true);
+pref("toolkit.telemetry.enabled", false);
 pref("browser.old.neighbour", 0);
"""
LOOK_PATCH = """--- a/theme.css
+++ b/theme.css
@@ -1,2 +1,2 @@
 :root {
-  --accent: blue;
+  --accent: purple;
"""
GONE_PATCH = """--- a/removed/feature.js
+++ b/removed/feature.js
@@ -1,1 +1,1 @@
-let on = true;
+let on = false;
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    up = tmp_path / "mozilla"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main")
    _git(up, "config", "user.email", "t@example.com")
    _git(up, "config", "user.name", "t")
    _w(up / "prefs.js", PREFS_157)
    _w(up / "theme.css", ":root {\n  --accent: blue;\n}\n")
    _w(up / "privacy.js", 'const tracking = "off";\n')          # the PRIVACY patch is already upstream
    _git(up, "add", ".")
    _git(up, "commit", "-q", "-m", "157")
    _git(up, "tag", "FIREFOX_157_0_RELEASE")
    harness = tmp_path / "Gorilla.firefox"
    _w(harness / "config" / "patch_policy.json", json.dumps({"patchset_root": "patchset", "groups": {
        "05.PREFS": {"status": "enabled"}, "08.Look": {"status": "enabled"},
        "09.GONE": {"status": "enabled"}, "13.PRIVACY": {"status": "enabled"}, "01.MEDIA": {"status": "disabled"}}}))
    _w(harness / "patchset" / "05.PREFS" / "telemetry.patch", TELEMETRY_PATCH)
    _w(harness / "patchset" / "08.Look" / "accent.patch", LOOK_PATCH)
    _w(harness / "patchset" / "09.GONE" / "feature.patch", GONE_PATCH)
    _w(harness / "patchset" / "13.PRIVACY" / "tracking.patch",
       '--- a/privacy.js\n+++ b/privacy.js\n@@ -1,1 +1,1 @@\n-const tracking = "on";\n+const tracking = "off";\n')
    info = upstream.latest_firefox(versions={"LATEST_FIREFOX_VERSION": "157.0"}, repo=up.as_uri())
    steps = firefox.plan(harness, vault_base=tmp_path / "vault")
    task.start("ff", "firefox-upgrade", tmp_path / "work" / "157.0", steps, meta={"pinned": info})
    task.approve("ff", "owner")
    return tmp_path


def test_parse_patch_and_patch_output():
    files = firefox.parse_patch(TELEMETRY_PATCH)
    assert files[0]["file"] == "prefs.js" and len(files[0]["hunks"]) == 1
    out = "patching file prefs.js\nHunk #1 FAILED at 1.\n1 out of 1 hunk FAILED -- saving rejects to file prefs.js.rej\n"
    assert firefox.failures_from_output(out) == {"failed": [("prefs.js", 1)], "missing": False}


def test_whole_workflow_script_does_the_easy_parts_model_gets_one_hunk(world, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)   # the test plays the owner at a terminal
    p = task.packet("ff")
    w = world / "work" / "157.0"
    assert p["step"].startswith("port-05.PREFS-telemetry")
    assert ("near line 31" in p["packet"] or "near line 32" in p["packet"]) and "toolkit.telemetry.enabled" in p["packet"]
    # a small model does the job
    prefs = w / "prefs.js"
    prefs.write_text(prefs.read_text().replace('pref("browser.startup.page", 1);\n',
                                               'pref("browser.startup.page", 1);\npref("toolkit.telemetry.enabled", false);\n'),
                     newline="\n")
    assert task.submit("ff")["ok"]
    nxt = task.packet("ff")
    assert (w / "theme.css").read_text().count("purple") == 1             # the next group: applied by the script
    # the GONE group's patch targets a file that no longer exists: resolved as OBSOLETE by default (2026-10-01)
    assert nxt["state"] == "DONE", nxt
    done = nxt
    assert next(s for s in task.load("ff")["steps"] if s["id"].startswith("owner-09.GONE"))["status"] == "obsolete"
    t = task.load("ff")
    upstreamed = next(s for s in t["steps"] if s["id"] == "apply-13.PRIVACY")["result"]["upstreamed"]
    assert upstreamed == ["13.PRIVACY/tracking.patch (the whole patch is already in this Firefox)"]
    exported = (world / "work" / "patchset-157.0" / "05.PREFS.patch").read_text()
    assert '+pref("toolkit.telemetry.enabled", false);' in exported
    from fieldkit.buildh import vault
    assert vault.verify("firefox", "157.0", base=world / "vault")["intact"]      # the vault never moved


def test_a_wrong_port_is_put_back(world):
    task.packet("ff")
    w = world / "work" / "157.0"
    (w / "prefs.js").write_text("// I rewrote the whole file, it is better now\n", newline="\n")
    r = task.submit("ff", note="Fixed and verified.")
    assert not r["ok"] and any("missing added line" in x for x in r["why"])
    assert (w / "prefs.js").read_text() == PREFS_157


def test_hunk_problems_counts_lines():
    hunk = firefox.parse_patch(TELEMETRY_PATCH)[0]["hunks"][0]
    before = PREFS_157.splitlines()
    assert firefox.hunk_problems(before, before, hunk)
    after = before + ['pref("toolkit.telemetry.enabled", false);']
    assert firefox.hunk_problems(before, after, hunk) == []
    assert firefox.already_upstream(after, hunk) and not firefox.already_upstream(before, hunk)


def test_real_case_run4_collateral_damage_is_refused():
    """Gemma's real answer from live run 4: the old check passed it; the new one names all the damage."""
    import json as _json
    case = _json.loads((Path(__file__).parent / "data" / "run4_gemma_touchmode.json").read_text(encoding="utf-8"))
    assert firefox.hunk_problems(case["before"], case["after"], case["hunk"]) == []
    why = firefox.collateral(case["before"], case["after"], case["hunk"])
    assert any('sticky_pref("browser.touchmode.auto", false);' in w for w in why)
    assert any('pref("browser.touchmode.auto", true);' in w for w in why)
    assert any("compactmode.show" in w for w in why)


def test_collateral_allows_exactly_the_hunk():
    hunk = firefox.parse_patch(TELEMETRY_PATCH)[0]["hunks"][0]
    before = ['pref("a", 1);', 'pref("toolkit.telemetry.enabled", true);', 'pref("b", 2);']
    after = ['pref("a", 1);', 'pref("toolkit.telemetry.enabled", false);', 'pref("b", 2);']
    assert firefox.collateral(before, after, hunk) == []


def test_transplant_does_the_real_run5_hunk_without_a_model():
    """The hunk Gemma failed nine times: every removed line still exists together, so no model is needed."""
    import json as _json
    case = _json.loads((Path(__file__).parent / "data" / "run4_gemma_touchmode.json").read_text(encoding="utf-8"))
    after = firefox.transplant(case["before"], case["hunk"])
    assert firefox.hunk_problems(case["before"], after, case["hunk"]) == []
    assert firefox.collateral(case["before"], after, case["hunk"]) == []
    assert 'sticky_pref("browser.touchmode.auto", false);' in [l.strip() for l in after]     # Mozilla's line survives


def test_transplant_refuses_when_it_would_have_to_guess():
    hunk = firefox.parse_patch(TELEMETRY_PATCH)[0]["hunks"][0]
    with pytest.raises(firefox.Ambiguous, match="missing"):
        firefox.transplant(['pref("datareporting.telemetry.enabled", true);'], hunk)
    twice = ['pref("toolkit.telemetry.enabled", true);'] * 2
    with pytest.raises(firefox.Ambiguous, match="2 places"):
        firefox.transplant(twice, hunk)


# ── question mode (run 6 countermeasure) ────────────────────────────────────

def test_question_packet_marks_auto_removes_and_asks_uncertain():
    import json as _json
    case = _json.loads((Path(__file__).parent / "data" / "run6_gemma_h11.json").read_text(encoding="utf-8"))
    auto, uncertain = firefox.identify_questions(case["before"], case["hunk"], 10)
    assert len(auto) == 7                     # 5 pref/comment lines + the 2 blank lines the hunk removes with them
    assert len(uncertain) == 6
    assert 10 in uncertain  # the edited Nimbus comment
    assert 17 in uncertain  # the new pref

    packet = firefox.packet_port_questions({"workdir": ""}, {}, 10000, "patch", "file", case["hunk"])
    # If the file doesn't exist, the packet says so. But if we mock the file:
    # Instead of full packet integration test, we verify identify_questions logic which is the core.


def test_apply_question_answers_correct_decisions(tmp_path):
    import json as _json
    case = _json.loads((Path(__file__).parent / "data" / "run6_gemma_h11.json").read_text(encoding="utf-8"))
    
    # Write a temp file with before content
    target = tmp_path / "firefox.js"
    target.write_text("\n".join(case["before"]) + "\n", encoding="utf-8", newline="")

    auto, uncertain = firefox.identify_questions(case["before"], case["hunk"], 10)
    
    # Decisions: KEEP the new upstream stuff, REMOVE nothing from uncertain
    decisions = [(n, "keep") for n in uncertain]
    
    count, summary, removed_texts = firefox.apply_question_answers(target, decisions, case["hunk"], auto)
    assert count == 7  # 7 auto-removes (incl. the hunk's two removed blank lines)
    assert len(removed_texts) == 0  # 0 model removes
    
    after = target.read_text(encoding="utf-8", errors="replace").splitlines()
    assert len(after) == len(case["before"]) - 7
    assert "pref(\"places.semanticHistory.multilingualEmbeddingRegions\", \"[[\\\"FR\\\",[\\\"en-*\\\",\\\"fr-*\\\"]],[\\\"*\\\",[\\\"fr-*\\\"]]]\");" in after  # KEPT


def test_collateral_with_extra_allowed_removals():
    hunk = firefox.parse_patch(TELEMETRY_PATCH)[0]["hunks"][0]
    before = ['pref("a", 1);', 'pref("toolkit.telemetry.enabled", true);', 'pref("b", 2);']
    after = ['pref("a", 1);', 'pref("toolkit.telemetry.enabled", false);']
    # Removed "b" which is not in the patch -> collateral damage
    why = firefox.collateral(before, after, hunk)
    assert len(why) == 1 and 'pref("b", 2);' in why[0]
    
    # But if "b" is in extra_removals, it's allowed
    why = firefox.collateral(before, after, hunk, extra_removals=['pref("b", 2);'])
    assert len(why) == 0


def test_real_run6_gemma_answer_is_refused():
    """Gemma's real broken answer from run 6 fails question parsing."""
    from fieldkit.buildh import answer
    import json as _json
    case = _json.loads((Path(__file__).parent / "data" / "run6_gemma_h11.json").read_text(encoding="utf-8"))
    
    with pytest.raises(answer.BadAnswer, match="no answers found"):
        # Gemma answered with DELETE / CHANGE operations, not REMOVE / KEEP
        answer.parse_questions(case["gemma_bad_answer"], {745, 746, 749, 750, 751, 752})


# ── auto-substitute (run 7 improvement) ─────────────────────────────────────

def test_auto_substitute_individual_replacement():
    """When upstream inserts a line between two removed lines, auto_substitute
    handles each one individually instead of requiring a contiguous block."""
    lines = ['pref("a", 1);', 'pref("upstream.new", 99);', 'pref("b", 2);']
    hunk = firefox.parse_patch(
        '--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n'
        '-pref("a", 1);\n+pref("a", 10);\n'
        '-pref("b", 2);\n+pref("b", 20);\n'
    )[0]["hunks"][0]
    after = firefox.auto_substitute(lines, hunk)
    assert after == ['pref("a", 10);', 'pref("upstream.new", 99);', 'pref("b", 20);']


def test_auto_substitute_individual_deletion():
    lines = ['pref("a", 1);', 'pref("upstream.new", 99);', 'pref("b", 2);']
    hunk = firefox.parse_patch(
        '--- a/f\n+++ b/f\n@@ -1,2 +0,0 @@\n'
        '-pref("a", 1);\n-pref("b", 2);\n'
    )[0]["hunks"][0]
    after = firefox.auto_substitute(lines, hunk)
    assert after == ['pref("upstream.new", 99);']


def test_auto_substitute_falls_back_to_contiguous():
    """When the lines ARE contiguous, auto_substitute works the same as transplant."""
    lines = ['pref("a", 1);', 'pref("b", 2);', 'pref("c", 3);']
    hunk = firefox.parse_patch(
        '--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n'
        '-pref("a", 1);\n-pref("b", 2);\n'
        '+pref("a", 10);\n+pref("b", 20);\n'
    )[0]["hunks"][0]
    after = firefox.auto_substitute(lines, hunk)
    assert after == ['pref("a", 10);', 'pref("b", 20);', 'pref("c", 3);']


def test_auto_substitute_refuses_ambiguous():
    lines = ['pref("a", 1);', 'pref("a", 1);']  # duplicated line
    hunk = firefox.parse_patch(
        '--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n'
        '-pref("a", 1);\n+pref("a", 10);\n'
    )[0]["hunks"][0]
    with pytest.raises(firefox.Ambiguous, match="2 places"):
        firefox.auto_substitute(lines, hunk)


# -- the answer key is never a shortcut (overnight run 2026-10-01 copied 58 old files over 157) -----

def test_the_answer_key_is_never_copied_over_the_new_source(world):
    task.packet("ff")
    harness = world / "Gorilla.firefox"
    target = world / "work" / "157.0" / "theme.css"
    target.write_text(":root {\n  --accent: red;\n  --new-thing: green;\n}\n", encoding="utf-8", newline="")
    key = harness / "src" / "theme.css"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(":root {\n  --accent: purple;\n}\n", encoding="utf-8", newline="")
    before = target.read_text(encoding="utf-8")
    t = task.load("ff")
    t["meta"]["harness_root"] = str(harness)
    hunk = firefox.parse_patch(LOOK_PATCH)[0]["hunks"][0]
    s = {"id": "x", "allowed": ["theme.css"]}
    firefox.auto_port(t, s, "08.Look/accent.patch", "theme.css", hunk)
    assert not s.get("answer_key_used")
    assert target.read_text(encoding="utf-8") != key.read_text(encoding="utf-8") or target.read_text(encoding="utf-8") == before


def test_no_flag_on_a_step_can_switch_the_port_check_off(world):
    task.packet("ff")
    target = world / "work" / "157.0" / "theme.css"
    target.write_text(":root {\n  --accent: purple;\n  --extra: thing;\n}\n", encoding="utf-8", newline="")
    t = task.load("ff")
    hunk = firefox.parse_patch(LOOK_PATCH)[0]["hunks"][0]
    plain = firefox.check_port(t, {"id": "a"}, "08.Look/accent.patch", "theme.css", hunk)
    flagged = firefox.check_port(t, {"id": "b", "answer_key_used": True}, "08.Look/accent.patch", "theme.css", hunk)
    assert plain == flagged
    assert not plain["ok"]


def test_a_deletion_is_done_when_the_file_is_gone_and_only_then(tmp_path):
    # 2026-10-04: macOS-only branding files deleted by hand could not be recorded or verified
    whole = {"header": "@@ -1,2 +0,0 @@", "lines": ["-a", "-b"]}
    part = {"header": "@@ -10,3 +9,0 @@", "lines": ["-a"]}
    assert firefox.deletes_whole_file(whole) and not firefox.deletes_whole_file(part)
    assert firefox.deletes_whole_file({"binary": True, "deleted": True})
    assert not firefox.deletes_whole_file({"binary": True, "sha256": "x"})
    t = {"workdir": str(tmp_path)}
    assert firefox.check_port(t, {"id": "d"}, "p", "gone.icns", {"binary": True, "deleted": True})["ok"]
    (tmp_path / "back.icns").write_bytes(b"x")
    assert not firefox.check_port(t, {"id": "d"}, "p", "back.icns", {"binary": True, "deleted": True})["ok"]
