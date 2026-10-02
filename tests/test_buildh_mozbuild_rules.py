"""mozbuild refuses an empty assignment: detected by the verifier, fixed by repair and by build-run."""
from fieldkit.buildh import buildrun, firefox, mozbuild_rules as mr

BAD = 'include("/x.mozbuild")\n\nDIRS = [\n    # GORILLA excised: "pingsender"\n]\n\nEXPORTS += []\nSOURCES += ["a.cpp"]\n'
LOG = ["The error occurred while processing the following file:", "", "    C:/T/toolkit/components/telemetry/moz.build", "",
       "KeyError: 'Variable DIRS assigned an empty value.'"]


def test_detect_and_fix(tmp_path):
    assert mr.empty_assignments(BAD) == [(3, "DIRS"), (7, "EXPORTS")]
    p = tmp_path / "moz.build"
    p.write_text(BAD, encoding="utf-8")
    assert sorted(mr.fix_empty_assignments(p)) == ["DIRS", "EXPORTS"]
    text = p.read_text(encoding="utf-8")
    assert mr.empty_assignments(text) == [] and 'SOURCES += ["a.cpp"]' in text and '# GORILLA excised: "pingsender"' in text
    compile(text, "moz.build", "exec")


def test_verifier_reports_it(tmp_path):
    (tmp_path / "moz.build").write_text(BAD, encoding="utf-8")
    probs = firefox.syntax_problems(tmp_path, ["moz.build"])
    assert any("empty DIRS" in p for p in probs) and any("empty EXPORTS" in p for p in probs)


def test_build_stop_is_classified():
    name, fix = buildrun.classify(LOG)
    assert name == "mozbuild-empty-assignment" and fix is buildrun.fix_mozbuild_empty
    assert mr.from_log(LOG) == ("C:/T/toolkit/components/telemetry/moz.build", "DIRS")
