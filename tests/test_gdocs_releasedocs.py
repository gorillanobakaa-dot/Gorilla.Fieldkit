"""docs release: a release's documents against the material they were written from (2026-10-03: the 157 notes, story
and both tracks were checked by a throwaway script with the corpus typed in)."""
import json

import pytest

from fieldkit import cli
from fieldkit.gdocs import releasedocs as RD

PLAN = "The port took 52 compile attempts over 44 build runs.\nSee https://example.org/plan for the plan.\n"
AUDIT = "header line: 1,488 claims audited\n" + "filler\n" * 70 + "tail line: 9,999 is past the head\n"


@pytest.fixture
def rel(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "PLAN.md").write_text(PLAN, encoding="utf-8")
    (tmp_path / "src" / "AUDIT.md").write_text(AUDIT, encoding="utf-8")
    return tmp_path


def test_numbers_and_addresses_must_come_from_the_sources(rel):
    good = rel / "NOTES.md"
    good.write_text("We built it 52 times (52 attempts, 1,488 claims). Plan: https://example.org/plan\n", encoding="utf-8")
    bad = rel / "STORY.md"
    bad.write_text("It took 61 builds, and 9,999 claims; read https://example.org/elsewhere. "
                   "The CPU peak was 93 C (not measured on this laptop).\n", encoding="utf-8")
    r = RD.check(sources=[rel / "src" / "PLAN.md"], source_heads=[rel / "src" / "AUDIT.md"], plain=[good, bad], terms=[])
    assert not r["ok"] and r["sources"] == 2 and r["problems"] == []
    assert r["documents"][str(good)]["findings"] == []
    f = r["documents"][str(bad)]["findings"]
    assert any("number 61 " in x for x in f) and any("9,999" in x for x in f)       # past the audit's head: unsourced
    assert any("https://example.org/elsewhere" in x for x in f)
    assert not any(" 93 " in x for x in f)                                          # said to be not measured


def test_a_missing_source_or_document_is_a_problem_never_skipped(rel):
    r = RD.check(sources=[rel / "src" / "PLAN.md", rel / "src" / "GONE.md"], plain=[rel / "NOPE.md"], terms=[])
    assert not r["ok"] and len(r["problems"]) == 2 and all("cannot read" in p for p in r["problems"])
    assert "no source material" in RD.check(plain=[rel / "src" / "PLAN.md"], terms=[])["problems"][0]


def test_banned_words_and_the_track_checks_apply(rel):
    doc = rel / "NOTES.md"
    doc.write_text("As the owner said, 52 attempts.\n", encoding="utf-8")
    lay = rel / "LAYMAN.md"
    lay.write_text("# Too thin\n\n52 attempts.\n", encoding="utf-8")
    r = RD.check(sources=[rel / "src" / "PLAN.md"], plain=[doc], layman=[lay], terms=[])
    assert any("banned word 'the owner'" in x for x in r["documents"][str(doc)]["findings"])
    lf = r["documents"][str(lay)]["findings"]
    assert r["documents"][str(lay)]["track"] == "layman" and lf and all(x.startswith("layman") for x in lf)


def test_the_manifest_resolves_paths_from_its_folder_and_refuses_unknown_keys(rel, capsys):
    (rel / "release").mkdir()
    (rel / "release" / "NOTES.md").write_text("52 attempts over 44 build runs.\n", encoding="utf-8")
    m = rel / "release" / "release-docs.yaml"
    m.write_text("sources: [../src/PLAN.md]\nsource_heads: [../src/AUDIT.md]\nhead_lines: 5\nplain: [NOTES.md]\n",
                 encoding="utf-8")
    spec = RD.load_manifest(m)
    assert spec["sources"] == [rel / "release" / "../src/PLAN.md"] and spec["head_lines"] == 5
    assert cli.main(["docs", "release", "--manifest", str(m)]) == 0
    assert "RELEASE DOCS OK" in capsys.readouterr().out
    (rel / "release" / "NOTES.md").write_text("53 attempts.\n", encoding="utf-8")
    assert cli.main(["docs", "release", "--manifest", str(m), "--json"]) == 3
    out = json.loads(capsys.readouterr().out)
    assert not out["ok"] and any("53" in x for d in out["documents"].values() for x in d["findings"])
    m.write_text("sorces: [x]\n", encoding="utf-8")
    assert cli.main(["docs", "release", "--manifest", str(m)]) == 2


def test_the_paths_can_be_given_on_the_command_line(rel, capsys):
    doc = rel / "NOTES.md"
    doc.write_text("52 attempts.\n", encoding="utf-8")
    assert cli.main(["docs", "release", str(doc), "--source", str(rel / "src" / "PLAN.md")]) == 0
    assert cli.main(["docs", "release", str(doc)]) == 3                                  # no sources: fail closed
