"""fieldkit academic: every check is shown to fire on a bad input and to stay quiet on a good one, in the seven
countries' forms.

The student is invented (Sam Example); sources and authors are invented too. Nothing here touches the network.
"""
import datetime as dt
import json
import os

import pytest

from fieldkit.academic import assignment, dates, language, styles
from fieldkit.academic import cli as acli
from fieldkit.academic import profile as prof
from fieldkit.academic import reference_auditor as ra
from fieldkit.core import settings

TODAY = dt.date(2026, 10, 10)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_ACADEMIC_DIR", str(tmp_path / "academic"))
    monkeypatch.setattr(settings, "ROOT", tmp_path)          # the private word list goes here, not the real one
    return tmp_path


def student(country="uk", **kw):
    return prof.init(name="Sam Example", country=country, student_id="99990000", **kw)


# -- data files ----------------------------------------------------------------

def test_seven_countries_with_their_own_defaults():
    assert prof.countries() == ["de", "es", "it", "pt", "ro", "uk", "us"]
    names = set(styles.names())
    langs = language.languages()
    for c in prof.countries():
        loc = prof.locale(c)
        assert loc["default_style"] in loc["styles"] and set(loc["styles"]) <= names, c
        assert loc["work_language"] in langs, c
        assert "{day}" in loc["date_format"] and "{month}" in loc["date_format"]
    assert prof.locale("us")["page"] == "Letter" and prof.locale("uk")["page"] == "A4"
    assert prof.locale("uk")["default_style"] == "harvard-ctr"
    assert all(prof.locale(c)["default_style"] != "harvard-ctr" for c in ("es", "pt", "ro"))
    for code, d in langs.items():
        assert len(d["months"]) == 12, code


def test_unknown_country_and_style_refused(home):
    with pytest.raises(prof.BadProfile):
        prof.locale("xx")
    with pytest.raises(prof.BadProfile):
        student(style="nonsense")


# -- profile -------------------------------------------------------------------

def test_init_asks_before_it_writes(home):
    r = prof.init()
    assert r["status"] == "ask" and len(r["questions"]) == 2
    assert not prof.path().exists()
    r = prof.init(ui="ro", name="Sam Example")
    assert r["status"] == "ask" and "țară" in r["questions"][0]


def test_init_takes_country_defaults_and_hides_the_name(home):
    r = student("de")
    assert r["status"] == "created" and r["style"] == "apa7" and r["work_language"] == "de"
    p = prof.load()
    assert p["page"] == "A4" and p["date_format"] == "{day}. {month} {year}" and p["support"]["short_messages"]
    terms = json.loads((home / "fieldkit.local.json").read_text(encoding="utf-8"))["privacy"]["terms"]
    assert "Sam Example" in terms and "99990000" in terms
    assert student("de")["status"] == "exists"


# -- dates ---------------------------------------------------------------------

@pytest.mark.parametrize("text", ["28 September 2026", "28th Sept 2026", "September 28, 2026", "2026-09-28",
                                  "28 de septiembre de 2026", "28 de setembro de 2026", "28 settembre 2026",
                                  "28. September 2026", "28 septembrie 2026", "28/09/2026"])
def test_dates_read_in_every_form(text):
    assert dates.parse(text) == dt.date(2026, 9, 28)


def test_dates_never_guess():
    assert dates.parse("31/02/2026") is None
    assert dates.parse("soon") is None
    assert dates.parse("09/10/2026", "us") == dt.date(2026, 9, 10)
    assert dates.parse("09/10/2026", "uk") == dt.date(2026, 10, 9)


def test_dates_written_the_country_way():
    d = dt.date(2026, 9, 28)
    assert dates.fmt(d, prof.locale("us")["date_format"]) == "September 28, 2026"
    assert dates.fmt(d, prof.locale("es")["date_format"], "es") == "28 de septiembre de 2026"
    assert dates.fmt(d, prof.locale("de")["date_format"], "de") == "28. September 2026"


def test_deadline_found_in_a_brief_in_each_language():
    from fieldkit.academic.extract_submission_date import extract_date_from_text
    for text in ("Submission deadline: 28th September 2026", "Fecha límite de entrega: 28 de septiembre de 2026",
                 "Abgabetermin: 28. September 2026", "Termenul limită de predare: 28 septembrie 2026",
                 "Scadenza: 28/09/2026", "Prazo de entrega: 28 de setembro de 2026"):
        assert extract_date_from_text(text)[0], text
    assert extract_date_from_text("Week 4 starts in 2026")[0] is None


# -- the assignment: dates asked, then counted down --------------------------------

def test_setup_asks_both_dates_and_creates_nothing(home):
    student("uk")
    folder = home / "work" / "Essay_1"
    r = assignment.setup(str(folder))
    assert r["status"] == "ask" and len(r["questions"]) == 2 and "Turnitin" in r["questions"][1]
    assert not folder.exists()
    r = assignment.setup(str(folder), deadline="next Friday", upload="28 September 2026")
    assert r["status"] == "ask" and "next Friday" in r["questions"][0]
    assert not folder.exists()


def test_setup_then_next_counts_down_to_the_earlier_date(home):
    student("uk")
    folder = home / "work" / "Essay_1"
    r = assignment.setup(str(folder), deadline="28 October 2026", upload="2026-10-21", doc_type="reflective-essay")
    assert r["ok"] and r["deadline"] == "28 October 2026" and r["upload"] == "21 October 2026"
    cfg = (folder / "EXAM_CONFIG.md").read_text(encoding="utf-8")
    assert "**Upload:** 21 October 2026" in cfg and "Harvard (Cite Them Right)" in cfg and "Turnitin" in cfg
    n = assignment.next_step(str(folder), today=TODAY)
    assert n["first"] == "upload" and n["days_left"] == 11 and n["step"]
    assert n["say"][0] == "Upload to Turnitin: 21 October 2026. 11 day(s) left."
    late = assignment.next_step(str(folder), today=dt.date(2026, 10, 22))
    assert "ago" in late["say"][0]


def test_agreed_adjustments_reminded_only_in_the_last_week(home):
    student("uk")
    p = json.loads(prof.path().read_text(encoding="utf-8"))
    p["support"]["agreed"] = ["attach the dyslexia sticker"]
    prof.path().write_text(json.dumps(p), encoding="utf-8")
    folder = home / "w"
    assignment.setup(str(folder), deadline="2026-10-28", upload="2026-10-28")
    assert not any("sticker" in s for s in assignment.next_step(str(folder), today=TODAY)["say"])
    assert any("sticker" in s for s in assignment.next_step(str(folder), today=dt.date(2026, 10, 25))["say"])


def test_next_without_dates_says_so(home):
    student("uk")
    assert assignment.next_step(str(home / "nowhere"))["status"] == "no-dates"


def test_accessed_keeps_the_first_date_and_follows_style_and_language(home):
    student("es")
    folder = home / "w"
    assignment.setup(str(folder), deadline="2026-11-02", upload="2026-11-02")
    r = assignment.accessed(str(folder), "https://example.org/a", today=TODAY)
    assert r["new"] and r["line"] == "Recuperado el 10 de octubre de 2026, de https://example.org/a"
    again = assignment.accessed(str(folder), "https://example.org/a", today=dt.date(2026, 10, 20))
    assert not again["new"] and again["accessed"] == "2026-10-10"


# -- references, in any of the languages ---------------------------------------------

GOOD = """## Introduction

Care matters (Smith, 2020). Ortega y Núñez (2019) disagree; others agree (Müller und Weber, 2021).
Undated guidance exists (Health Board, n.d.).

## References

Health Board (n.d.) Guidance. Available at: https://example.org.
Müller, A. und Weber, B. (2021) Pflege. Berlin: Verlag.
Ortega, L. y Núñez, R. (2019) Cuidados. Madrid: Editorial.
Smith, J. (2020) Care. London: Publisher.
"""


def test_references_match_across_languages():
    r = ra.audit_text(GOOD)
    assert r["orphans"] == [] and r["unused"] == [] and r["alphabetical"], r
    assert r["issues"] == 0


def test_references_fire_on_missing_unused_and_order():
    bad = GOOD.replace("(Smith, 2020)", "(Smith, 2020; Jones, 2018)").replace(
        "Health Board (n.d.) Guidance. Available at: https://example.org.\n", "") + "Adams, C. (2017) Old.\n"
    r = ra.audit_text(bad)
    assert any("jones" in o.lower() for o in r["orphans"])
    assert any("health board" in o.lower() for o in r["orphans"])
    assert any("adams" in u.lower() for u in r["unused"])
    assert not r["alphabetical"] and r["issues"] >= 3


def test_no_date_citation_is_found():
    keys = [c["key"] for c in ra.extract_inline_citations("As shown (Health Board, n.d.) and (Ruiz, s.f.).")]
    assert any("n.d." in k for k in keys) and any("s.f." in k or "n.d." in k for k in keys if "ruiz" in k)


# -- styles ------------------------------------------------------------------------

ENTRIES = {"harvard-ctr": "Smith, J. (2020) Care. London: Publisher.",
           "apa7": "Smith, J. (2020). Care. Publisher.",
           "iso690": "SMITH, John, 2020. Care. London: Publisher."}


@pytest.mark.parametrize("style", sorted(ENTRIES))
def test_each_style_accepts_its_own_entries_and_flags_the_others(style):
    for other, entry in ENTRIES.items():
        r = styles.check(f"Text (Smith, 2020).\n\n## References\n\n{entry}\n", style)
        assert r["entries"][0]["ok"] == (other == style), (style, other)


def test_style_citation_join():
    assert styles.check("A (Smith & Jones, 2020).\n\n## References\n\n", "harvard-ctr")["warnings"] == 1
    assert styles.check("A (Smith and Jones, 2020).\n\n## References\n\n", "apa7")["warnings"] == 1
    assert styles.check("A (Smith & Jones, 2020).\n\n## References\n\n", "apa7")["warnings"] == 0


# -- language ----------------------------------------------------------------------

SENTENCES = {"en": "The results show that the care which they have received has been better than expected.",
             "es": "Los resultados muestran que el cuidado que han recibido es mejor para los pacientes.",
             "pt": "Os resultados mostram que o cuidado que receberam foi melhor para os doentes também.",
             "it": "I risultati mostrano che la cura che hanno ricevuto è stata migliore per gli anziani.",
             "de": "Die Ergebnisse zeigen, dass die Pflege, die sie erhalten haben, besser ist als erwartet.",
             "ro": "Rezultatele arată că îngrijirea pe care au primit-o este mai bună decât se aștepta."}


@pytest.mark.parametrize("lang", sorted(SENTENCES))
def test_language_detected(lang):
    assert language.detect(SENTENCES[lang]) == lang


def test_language_check_fails_on_another_language_only_in_the_body():
    ok = SENTENCES["es"] + "\n\n## Referencias\n\n" + "Smith, J. (2020) The care and the cost. London: Press.\n"
    assert language.check(ok, "es")["ok"]
    bad = SENTENCES["es"] + " " + SENTENCES["ro"]
    r = language.check(bad, "es")
    assert not r["ok"] and r["other"][0]["language"] == "ro"
    quoted = SENTENCES["es"] + ' Como dice el autor, "' + SENTENCES["en"] + '"'
    assert language.check(quoted, "es")["ok"]


def test_spelling_variant_both_ways():
    gb = language.check("We analyze the behavior of the centre.", "en", "en-GB")["spelling"]
    assert {v["word"] for v in gb} == {"analyze", "behavior"}
    us = language.check("We analyse the behaviour of the center.", "en", "en-US")["spelling"]
    assert {v["word"] for v in us} == {"analyse", "behaviour"}


# -- the other checks ---------------------------------------------------------------

def test_word_count_body_only_any_language(tmp_path):
    f = tmp_path / "w.md"
    f.write_text("# Title\n\nSam\n\n## Introducere\n\n" + " ".join(["știință"] * 100)
                 + "\n\n## Bibliografie\n\n" + "x " * 300 + "\n\n## Anexa A\n\n" + "y " * 300, encoding="utf-8")
    assert acli.main(["words", str(f), "--target", "101", "--json"]) == 0
    assert acli.main(["words", str(f), "--target", "500"]) == 3


def test_plagiarism_catches_a_copied_run_with_accents(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "s.txt").write_text("La atención primaria depende de las condiciones en las que las personas nacen, "
                               "crecen, viven, trabajan y envejecen según el informe.", encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text("## Cuerpo\n\nLas condiciones en las que las personas nacen crecen viven trabajan y envejecen "
                   "importan mucho.\n", encoding="utf-8")
    good = tmp_path / "good.md"
    good.write_text("## Cuerpo\n\nEl bienestar refleja el entorno del nacimiento, el empleo y la edad.\n",
                    encoding="utf-8")
    from fieldkit.academic.plagiarism_precheck import run_precheck
    assert max(m["length"] for m in run_precheck(str(bad), str(src))) >= 10
    assert run_precheck(str(good), str(src)) == []


def test_dashes_count_only_where_the_country_counts_them(home, tmp_path):
    f = tmp_path / "d.md"
    f.write_text("## Body\n\nCare — as shown — matters.\n", encoding="utf-8")
    student("uk")
    assert acli.main(["dashes", str(f)]) == 3
    prof.path().unlink()
    student("us")
    assert acli.main(["dashes", str(f)]) == 0


# -- the CLI -------------------------------------------------------------------------

def test_cli_exit_codes(home, tmp_path, capsys):
    assert acli.main(["init"]) == 2
    assert "QUESTION:" in capsys.readouterr().out
    assert acli.main(["init", "--name", "Sam Example", "--country", "pt"]) == 0
    f = tmp_path / "r.md"
    f.write_text(GOOD, encoding="utf-8")
    assert acli.main(["refs", str(f)]) == 0
    f.write_text(GOOD.replace("(Smith, 2020)", "(Jones, 2018)"), encoding="utf-8")
    assert acli.main(["refs", str(f)]) == 3
    assert acli.main(["refs", str(tmp_path / "missing.md")]) == 2
    assert acli.main(["setup", str(tmp_path / "e")]) == 2
    assert acli.main(["countries", "--json"]) == 0
    assert acli.main(["types"]) == 0


def test_fieldkit_dispatches_academic(home, capsys):
    from fieldkit import cli
    assert cli.main(["academic", "countries"]) == 0
    assert "Deutschland" in capsys.readouterr().out


def test_no_personal_or_institution_traces_in_the_module():
    root = os.path.dirname(prof.__file__)
    banned = ("oxford brookes", "obu ", "c:/users/")      # personal names: the pre-commit privacy scan
    for dirpath, _, files in os.walk(root):
        for name in files:
            if name.endswith((".py", ".yaml")):
                text = open(os.path.join(dirpath, name), encoding="utf-8").read().lower()
                assert not [b for b in banned if b in text], name
