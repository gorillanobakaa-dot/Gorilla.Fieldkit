"""fieldkit academic check / record / local / preflight / menu / shortcut: the parts of the academic harness that were
still missing (2026-10-11), each shown to fire on a bad input and stay quiet on a good one.

The student is invented (Sam Example); sources, authors and modules are invented too. Nothing touches the network.
"""
import datetime as dt
import hashlib
import json
import os

import pytest

from fieldkit.academic import assignment, check as ck, local, menu, preflight, record, shortcut
from fieldkit.academic import cli as acli
from fieldkit.academic import profile as prof
from fieldkit.core import settings

TODAY = dt.date(2026, 10, 10)
SOURCE = ("Person-centred care means that the needs of the individual guide every decision a nurse makes about "
          "treatment, and families are partners in planning that care from the first day of admission onwards.")
CLEAN = """# Care Planning in Practice

## Introduction

Care planning works best when the nurse listens first (Okafor, 2021). This essay looks at why that matters for
patients and families, and what it asks of a ward team in practice.

## Discussion

A plan written with the patient tends to be followed (Okafor, 2021). Families notice when they are heard, and the
ward runs more calmly as a result (Lindqvist and Moreau, 2019).

## Conclusion

Listening is a clinical skill, not a courtesy.

## References

Lindqvist, A. and Moreau, B. (2019) *Families on the ward*. Leeds: Example Press.

Okafor, C. (2021) *Planning care together*. London: Example Books.
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_ACADEMIC_DIR", str(tmp_path / "academic"))
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    prof.init(name="Sam Example", country="uk", student_id="99990000")
    return tmp_path


def _assignment(home, draft=CLEAN, words=None, name="Exam_1"):
    folder = home / "work" / name
    r = assignment.setup(str(folder), deadline="28 October 2026", upload="27 October 2026", words=words, today=TODAY)
    assert r["ok"], r
    mats = folder / "1 - DROP YOUR STUDY MATERIALS HERE"
    (mats / "lecture.txt").write_text(SOURCE * 3, encoding="utf-8")
    p = folder / "3 - Drafts" / "essay.md"
    p.write_text(draft, encoding="utf-8")
    return folder, p


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _gates(r):
    return {g["name"]: g["status"] for g in r["gates"]}


# -- check -----------------------------------------------------------------------------------------------------------
def test_a_clean_draft_passes_and_is_delivered_into_the_output_folder(home):
    folder, draft = _assignment(home)
    r = ck.run(str(folder), str(draft), offline=True)
    g = _gates(r)
    assert r["ok"], r["gates"]
    assert g["references"] == "PASS" and g["long dashes"] == "PASS" and g["copied text"] == "PASS"
    assert g["anonymous marking"] == "PASS" and g["identifiers online"] == "SKIP"
    assert r["delivered"] and os.path.isfile(r["delivered"]) and "4 - HERE IS YOUR WORK" in r["delivered"]
    assert os.path.isfile(r["todo"])
    said = " ".join(assignment.next_step(str(folder), today=TODAY)["say"])
    assert "The last check" in said and "READY" in said


def test_a_bad_draft_is_not_delivered_says_what_to_fix_first_and_leaves_the_draft_alone(home):
    bad = CLEAN.replace("listens first", "listens first — always") \
               .replace("(Okafor, 2021). This essay", "(Okafor, 2021; Brandt, 2018). This essay") \
               .replace("# Care Planning in Practice", "# [Title]") + "\nWritten by Sam Example.\n"
    folder, draft = _assignment(home, bad)
    before = _sha(draft)
    r = ck.run(str(folder), str(draft), offline=True)
    g = _gates(r)
    assert not r["ok"] and r["verdict"] == "NOT READY"
    assert g["template leftovers"] == "FAIL" and g["references"] == "FAIL" and g["long dashes"] == "FAIL"
    assert g["anonymous marking"] == "FAIL" and g["delivered"] == "SKIP"
    assert r["first_fix"]["gate"] == "template leftovers" and "[ ]" in r["first_fix"]["fix"]
    out = folder / "4 - HERE IS YOUR WORK, Sam"
    assert not out.exists() or not any(p.suffix == ".docx" for p in out.iterdir())
    assert _sha(draft) == before
    todo = open(r["todo"], encoding="utf-8").read()
    assert "NOT READY" in todo and "- [ ] FAIL long dashes" in todo
    said = " ".join(assignment.next_step(str(folder), today=TODAY)["say"])
    assert "NOT READY: fix 'template leftovers' first" in said


def test_copied_text_from_a_study_source_fails(home):
    copied = CLEAN.replace("Listening is a clinical skill, not a courtesy.",
                           "Person-centred care means that the needs of the individual guide every decision a nurse "
                           "makes about treatment.")
    folder, draft = _assignment(home, copied)
    g = _gates(ck.run(str(folder), str(draft), offline=True))
    assert g["copied text"] == "FAIL"


def test_the_word_target_is_held_and_no_sources_is_never_a_pass(home):
    folder, draft = _assignment(home, words=2500)
    os.remove(folder / "1 - DROP YOUR STUDY MATERIALS HERE" / "lecture.txt")
    g = _gates(ck.run(str(folder), str(draft), offline=True))
    assert g["word count"] == "FAIL" and g["copied text"] == "SKIP"


def test_a_word_file_is_checked_and_a_finished_copy_delivered_the_original_unchanged(home):
    from fieldkit.academic import build as b
    folder, draft = _assignment(home)
    docx = b.build(str(draft), "docx", str(folder / "3 - Drafts"))["file"]
    before = _sha(docx)
    r = ck.run(str(folder), docx, offline=True)
    assert r["ok"], r["gates"]
    assert r["delivered"].endswith(".docx") and r["delivered"] != docx and _sha(docx) == before
    assert os.path.isfile(os.path.join(folder, "3 - Drafts", "essay (as checked).md"))


def test_a_cloud_folder_stops_the_check_when_the_profile_keeps_work_local(home):
    p = json.loads(prof.path().read_text())
    p["support"]["refuse_cloud"] = True
    prof.path().write_text(json.dumps(p))
    folder = home / "OneDrive" / "Exam_9"
    folder.mkdir(parents=True)
    (folder / "d.md").write_text(CLEAN)
    r = ck.run(str(folder), str(folder / "d.md"), offline=True)
    assert not r["ok"] and _gates(r) == {"saved on this computer": "FAIL"}
    assert sorted(os.listdir(folder)) == ["d.md"]                       # nothing written there


# -- record ----------------------------------------------------------------------------------------------------------
FEEDBACK = """Module feedback
Total mark: 58
Knowledge and understanding: 14/20
Use of sources: 11/20
Similarity score was 8%
The discussion is largely descriptive rather than analytical, and several points read more like descriptions.
Referencing is inconsistent between the text and the list, creating duplication for the same source.
"Care should be planned with the family from the first day of admission" is a strong opening line.
"""


def test_a_feedback_sheet_is_read_into_a_proposal_and_written_only_on_apply(home, tmp_path):
    sheets = tmp_path / "fb"
    sheets.mkdir()
    (sheets / "NURS4005 feedback.txt").write_text(FEEDBACK)
    r = record.add(str(sheets))
    e = r["entries"][0]
    assert e["module"] == "NURS4005" and e["mark"] == 58 and e["band"] == "Lower second (2:2)" and e["passed"]
    assert e["similarity_percent"] == 8 and e["criteria"]["Use of sources"] == [11, 20]
    keys = {f["fault"] for f in e["faults"]}
    assert {"descriptive_not_critical", "referencing_inconsistent"} <= keys
    assert all(f["evidence"] for f in e["faults"])                      # the marker's own sentence, every time
    assert not os.path.exists(os.path.join(str(prof.base()), record.RECORD_JSON))
    record.add(str(sheets), apply=True)
    shown = record.show()
    assert shown["entries"][0]["module"] == "NURS4005" and shown["pass_mark"] == 40
    assert os.path.isfile(os.path.join(str(prof.base()), record.RECORD_MD))
    top = record.brief()["watch"]
    assert top and top[0]["marker_said"]


def test_grade_bands_follow_the_country_germany_lower_is_better():
    g = prof.locale("de")["grades"]
    assert record.band(1.3, g) == "sehr gut" and record.band(2.7, g) == "befriedigend"
    assert record.passed(4.0, g) and not record.passed(4.3, g)
    it = prof.locale("it")["grades"]
    assert record.band(30, it) == "Ottimo" and not record.passed(17, it)


def test_work_is_linked_to_its_module_by_code_and_by_the_markers_quote(home, tmp_path):
    record.set_mark("NURS4005", 58, title="Care planning in nursing practice")
    record.set_mark("NURS4010", 64, title="Research methods for evidence")
    work = tmp_path / "work"
    work.mkdir()
    (work / "essay final.txt").write_text("x")
    from fieldkit.academic import record as rec
    fb = tmp_path / "fb"
    fb.mkdir()
    (fb / "NURS4005.txt").write_text(FEEDBACK)
    texts = {"coded.pdf": "NURS4010 assignment on sampling", "quoted.pdf":
             "Care should be planned with the family from the first day of admission, as the essay argues."}
    orig = rec.read_text
    try:
        rec.read_text = lambda p: texts.get(os.path.basename(p)) or orig(p)
        for n in texts:
            (work / n).write_bytes(b"%PDF")
        r = rec.link([str(work)], feedback=str(fb))
    finally:
        rec.read_text = orig
    got = {os.path.basename(x["file"]): (x["module"], x["confidence"]) for x in r["files"]}
    assert got["coded.pdf"] == ("NURS4010", "sure") and got["quoted.pdf"] == ("NURS4005", "sure")


# -- local -----------------------------------------------------------------------------------------------------------
def test_assignments_in_onedrive_are_copied_proven_and_the_original_renamed_never_deleted(home, monkeypatch):
    h = home / "home"
    cloud = h / "OneDrive" / "Documents" / "Exam_2"
    (cloud / "3 - Drafts").mkdir(parents=True)
    (cloud / "EXAM_CONFIG.md").write_text("cfg")
    (cloud / "3 - Drafts" / "essay.md").write_text("my only draft")
    monkeypatch.setattr(local, "HOME", str(h))
    monkeypatch.setattr(local, "documents_folder", lambda: str(h / "OneDrive" / "Documents"))
    dest = home / "Academic work"
    r = local.run(do=False, dest_root=str(dest))
    assert r["status"] == "found" and not dest.exists()
    r = local.run(do=True, dest_root=str(dest), today=TODAY)
    assert r["ok"] and (dest / "Exam_2" / "3 - Drafts" / "essay.md").read_text() == "my only draft"
    moved = h / "OneDrive" / "Documents" / f"Exam_2 (moved to this computer {TODAY.isoformat()})"
    assert (moved / "3 - Drafts" / "essay.md").read_text() == "my only draft"   # renamed, not deleted
    # a destination that exists is never written over
    (h / "OneDrive" / "Documents" / "Exam_2").mkdir()
    (h / "OneDrive" / "Documents" / "Exam_2" / "EXAM_CONFIG.md").write_text("another")
    r = local.run(do=True, dest_root=str(dest), today=TODAY)
    assert r["skipped"] and (dest / "Exam_2" / "EXAM_CONFIG.md").read_text() == "cfg"


# -- preflight, menu, shortcut, the command line ------------------------------------------------------------------
def test_preflight_passes_for_the_student_and_names_a_broken_rule(home, monkeypatch):
    assert preflight.run()["ok"]
    from fieldkit.academic import dash_check
    monkeypatch.setattr(dash_check, "find_dashes", lambda text: ([], []))      # an "improvement" that drops a check
    r = preflight.run()
    assert not r["ok"] and [x["rule"] for x in r["rules"] if not x["ok"]] == ["a long dash is found"]


def test_the_menu_runs_the_command_an_ai_would_run_and_remembers_the_folder(home):
    calls, answers = [], iter(["8", "/tmp/essay.md", "", "7", "", "", "0"])
    menu.run(read=lambda q: next(answers), out=lambda *a: None, call=calls.append, ui="en")
    assert calls == [["refs", "/tmp/essay.md"]]                  # 7 with no folder given and none known: nothing run
    calls, answers = [], iter(["7", "'/tmp/Exam 1'", "", "7", "", "", "0"])
    menu.run(read=lambda q: next(answers), out=lambda *a: None, call=calls.append, ui="ro")
    assert calls == [["next", "/tmp/Exam 1"], ["next", "/tmp/Exam 1"]]           # the second time, remembered


def test_the_shortcut_is_made_checked_and_only_ours_removed(tmp_path):
    made = shortcut.make(home=tmp_path, platform="linux")
    assert made["ok"] and len(made["made"]) == 2
    assert shortcut.check(home=tmp_path, platform="linux")["ok"]
    other = tmp_path / "Desktop" / "fieldkit-academic.desktop"
    other.write_text("[Desktop Entry]\nExec=someone-elses-program\n")
    r = shortcut.remove(home=tmp_path, platform="linux")
    assert len(r["removed"]) == 1 and r["kept_not_ours"] == [str(other)] and other.is_file()


def test_the_new_commands_answer_on_the_command_line(home, capsys):
    folder, draft = _assignment(home)
    assert acli.main(["check", str(folder), str(draft), "--offline"]) == 0
    assert "READY" in capsys.readouterr().out
    assert acli.main(["brief", str(folder)]) == 2                     # no brief in the folder: it says where it goes
    assert "Assignment brief" in capsys.readouterr().out
    assert acli.main(["record", "show"]) == 0
    capsys.readouterr()
    assert acli.main(["preflight", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "PREFLIGHT OK"


def test_a_built_page_carries_no_module_placeholder_when_no_module_is_known(home):
    from fieldkit.academic import build as b
    from fieldkit.academic.build_word import page_furniture
    folder, draft = _assignment(home)
    header, numbered = page_furniture(b.build(str(draft), "docx", str(home / "out"))["file"])
    assert "99990000" in header and "[Module Code]" not in header and numbered
