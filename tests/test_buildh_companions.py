# Version: 1.0.0 · updated 26-10-15-01-55
"""companions: a ported .cpp without its header (the 2026-10-15 PDMFactory failure), recovered from an archived set."""
import subprocess

from fieldkit.buildh import companions

LOG = """\
51:31.26 E {w}/dom/PDMFactory.cpp:311:24: error: unknown type name 'Purpose'
51:31.26 E   311 | PDMFactory::PDMFactory(Purpose aPurpose)
51:31.26 E {w}/dom/PDMFactory.cpp:321:18: error: out-of-line definition of 'IsBlockedVideoCodec' does not match any declaration in 'mozilla::PDMFactory'
51:31.27 E {w}/dom/PDMFactory.cpp:322:7: error: use of undeclared identifier 'mForWebCodecs'
"""

HEADER = """\
--- a/dom/PDMFactory.h
+++ b/dom/PDMFactory.h
@@ -1,3 +1,6 @@
 class PDMFactory {
-  PDMFactory();
+  enum class Purpose { Default, WebCodecs };
+  explicit PDMFactory(Purpose aPurpose = Purpose::Default);
+  bool IsBlockedVideoCodec() const;
+  const bool mForWebCodecs;
 };
"""


def _git(w, *a):
    subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)


def _tree(tmp_path):
    w = tmp_path / "tree"
    (w / "dom").mkdir(parents=True)
    (w / "dom" / "PDMFactory.h").write_text("class PDMFactory {\n  PDMFactory();\n};\n")
    _git(w, "init", "-q")
    _git(w, "add", "-A")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "pristine")
    root = tmp_path / "harness"
    (root / "patchset-155.0b4" / "16.SNAPSHOT.DELTA").mkdir(parents=True)
    (root / "patchset-155.0b4" / "16.SNAPSHOT.DELTA" / "dom_PDMFactory.h.patch").write_text(HEADER)
    (root / "patches").mkdir()
    (root / "patches" / "unrelated.patch").write_text("--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n-a\n+int unrelated;\n")
    return w, root


def test_errors_reads_mach_prefixed_clang_lines():
    e = companions.errors(LOG.format(w="/src"))
    assert set(e) == {"Purpose", "IsBlockedVideoCodec", "mForWebCodecs"}
    assert e["Purpose"] == ["/src/dom/PDMFactory.cpp:311"]


def test_sections_split_a_multi_file_patch():
    two = HEADER + "diff --git a/b.c b/b.c\n--- a/b.c\n+++ b/b.c\n@@ -1 +1 @@\n-x\n+y\n"
    s = companions.sections(two)
    assert [t for t, _ in s] == ["dom/PDMFactory.h", "b.c"]
    assert "diff --git" not in s[0][1]


def test_finds_dry_runs_and_applies_the_missing_header(tmp_path):
    w, root = _tree(tmp_path)
    r = companions.diagnose(w, root, LOG.format(w=w))
    assert r["ok"] and [c["file"] for c in r["plan"]] == ["dom/PDMFactory.h"]
    assert all(c["file"] != "x.c" for c in r["candidates"])      # a patch without the names is never a candidate
    a = companions.apply(w, r["plan"])
    assert a["ok"] and "enum class Purpose" in (w / "dom" / "PDMFactory.h").read_text()
    log = subprocess.run(["git", "-C", str(w), "log", "-1", "--format=%B"], capture_output=True, text=True).stdout
    assert "16.SNAPSHOT.DELTA" in log                            # the checkpoint names the source patch
    again = companions.diagnose(w, root, LOG.format(w=w))
    assert again["fixed"] and not again["plan"]                   # second run: already present, rebuild


def test_nothing_fits_means_a_person_and_no_change(tmp_path):
    w, root = _tree(tmp_path)
    (w / "dom" / "PDMFactory.h").write_text("something else entirely\n")
    r = companions.diagnose(w, root, LOG.format(w=w))
    assert not r["ok"] and not r["plan"]
    assert "a person writes the declaration" in companions.lines(r)[-1]


def test_a_log_without_declaration_errors_is_not_this_tool():
    r = {"errors": {}, "candidates": [], "plan": []}
    assert "does not apply" in companions.lines(r)[-1]


def test_a_failed_build_stage_names_the_known_failure_and_its_fix(tmp_path):
    from fieldkit.build import firefox_linux
    log = tmp_path / "latest_build.log"
    log.write_text(LOG.format(w="/src"))
    s = firefox_linux.failure_summary(log)
    assert s.startswith("known failure ported-half-without-declaration") and "companions" in s
    assert "no mach log" in firefox_linux.failure_summary(tmp_path / "absent.log")


def test_archive_gap_lists_what_the_archived_set_had_and_the_port_never_touched(tmp_path):
    w, root = _tree(tmp_path)
    (w / "dom" / "Other.cpp").write_text("x\n")
    _git(w, "add", "-A")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "upstream file")
    gap = companions.archive_gap(w, root / "patchset-155.0b4")
    assert [x["file"] for x in gap] == ["dom/PDMFactory.h"]                 # archived change, port never made it
    (w / "dom" / "PDMFactory.h").write_text("ported\n")
    assert companions.archive_gap(w, root / "patchset-155.0b4") == []      # once the port changes it, no gap
