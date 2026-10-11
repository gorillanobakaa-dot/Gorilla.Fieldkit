"""The numbered menu: every academic command without remembering one, in the student's interface language.

    fieldkit academic menu                 the menu (English, or Romanian when the profile's ui is ro)
    fieldkit academic shortcut [--remove]  a "Fieldkit Academic" shortcut that opens it (desktop and Start menu)

For a person with no AI to hand: double-click the shortcut, pick a number, answer the questions. Each item runs the
same command an AI would run, so the output is exactly that command's own. The last assignment folder is remembered
(local/academic/last.json), so after a week away the first question already has its answer.

From the academic harness this generalises (harness.py menu and Harness.bat, 2026-10-10).
"""
import json
import os
import shlex
import sys

from . import profile as prof

ITEMS = [
    # key, English, Romanian, the command (FOLDER / FILE / WORDS are asked)
    ("1", "Set up an assignment folder", "Pregatesc folderul unei teme",
     ["setup", "FOLDER", "--deadline", "DEADLINE", "--upload", "UPLOAD"]),
    ("2", "Read the brief and propose the set-up", "Citesc cerinta temei si propun setarile", ["brief", "FOLDER"]),
    ("2a", "Accept the brief's proposal", "Accept propunerea din cerinta temei", ["brief", "FOLDER", "--apply"]),
    ("3", "Read the study materials", "Citesc cursurile", ["extract", "FOLDER"]),
    ("4", "Search the study materials", "Caut in cursuri", ["search", "FOLDER", "WORDS"]),
    ("5", "A Word/PowerPoint template for this type", "Un sablon Word/PowerPoint pentru tipul temei",
     ["template", "TYPE", "FOLDER"]),
    ("6", "The whole check before submitting", "Verificarea completa inainte de predare", ["check", "FOLDER", "FILE"]),
    ("7", "What do I do next?", "Ce fac acum?", ["next", "FOLDER"]),
    ("8", "Citations and the reference list", "Citarile si bibliografia", ["refs", "FILE"]),
    ("9", "Long dashes", "Liniutele lungi", ["dashes", "FILE"]),
    ("10", "Language and spelling", "Limba si ortografia", ["language", "FILE"]),
    ("11", "Word count", "Numarul de cuvinte", ["words", "FILE"]),
    ("12", "Finish a file written in Word/PowerPoint", "Finalizez un fisier scris in Word/PowerPoint", ["finish", "FILE"]),
    ("13", "My marks and what markers say", "Notele mele si ce spun corectorii", ["record", "show"]),
    ("14", "Before I write: what to watch for", "Inainte sa scriu: la ce sa fiu atent", ["record", "brief"]),
    ("15", "Assignments in OneDrive: copy them here", "Teme in OneDrive: le copiez pe calculator", ["local"]),
    ("16", "Self-test of these tools", "Autotestul uneltelor", ["preflight"]),
    ("17", "The document types", "Tipurile de lucrari", ["types"]),
    ("18", "Back up Fieldkit, then copy it to the USB stick", "Backup, apoi copia pe stick-ul USB", ["BACKUP"]),
    ("19", "A web source I read today: stamp its date", "O sursa web citita azi: pun data accesarii",
     ["accessed", "FOLDER", "URL"]),
]
ASK = {"en": {"pick": "Pick a number (0 to leave)", "FOLDER": "The assignment folder", "FILE": "The file (drag it here)",
              "DEADLINE": "The deadline (as the brief writes it)", "UPLOAD": "The date it must be uploaded by",
              "URL": "The web address",
              "WORDS": "What to look for (words, or \"an exact phrase\")", "TYPE": "The document type",
              "enter": "Press Enter for the menu", "title": "FIELDKIT ACADEMIC", "bad": "Pick a number from the list."},
       "ro": {"pick": "Alege un numar (0 ca sa iesi)", "FOLDER": "Folderul temei", "FILE": "Fisierul (trage-l aici)",
              "DEADLINE": "Termenul de predare (cum scrie in cerinta)", "UPLOAD": "Data pana la care se incarca",
              "URL": "Adresa paginii web",
              "WORDS": "Ce cauti (cuvinte, sau \"expresie exacta\")", "TYPE": "Tipul lucrarii",
              "enter": "Apasa Enter pentru meniu", "title": "FIELDKIT ACADEMIC - MENIU",
              "bad": "Alege un numar din lista."}}


def _last_file():
    return os.path.join(str(prof.base()), "last.json")


def _remember(folder):
    try:
        os.makedirs(os.path.dirname(_last_file()), exist_ok=True)
        with open(_last_file(), "w", encoding="utf-8") as f:
            json.dump({"folder": folder}, f)
    except OSError:
        pass


def _last():
    try:
        with open(_last_file(), encoding="utf-8") as f:
            return json.load(f).get("folder")
    except (OSError, ValueError):
        return None


def _clean(answer):
    """A path dragged into a console arrives quoted, sometimes with & '...' around it (PowerShell)."""
    a = answer.strip()
    if a.startswith("& "):
        a = a[2:].strip()
    return a.strip("'\"")


def argv_for(item, answers):
    """The command an item runs, with the asked values in place."""
    return [answers.get(w, w) for w in item[3]]


def run(read=input, out=print, call=None, ui=None):
    from .cli import main as academic
    call = call or (lambda argv: academic(argv))
    p = prof.load() or {}
    lang = ui or ("ro" if p.get("ui") == "ro" else "en")
    t = ASK[lang]
    while True:
        out("\n" + "=" * 60 + f"\n  {t['title']}\n" + "=" * 60)
        for key, en, ro, _ in ITEMS:
            out(f"  {key:>3}. {ro if lang == 'ro' else en}")
        try:
            choice = read(f"\n{t['pick']}: ").strip()
        except EOFError:                       # no person at a keyboard (an agent, a pipe): the menu is for people
            out("the menu needs a person at the keyboard; an agent runs the commands themselves")
            return 2
        if choice in ("0", "", "q"):
            return 0
        item = next((i for i in ITEMS if i[0] == choice), None)
        if item is None:
            out(t["bad"])
            continue
        if item[3] == ["BACKUP"]:
            from ..cli import main as fieldkit
            fieldkit(["backup"])
            fieldkit(["backup", "usb"])
            read(f"\n{t['enter']}")
            continue
        answers = {}
        for need in [w for w in item[3] if w in ("FOLDER", "FILE", "WORDS", "TYPE", "DEADLINE", "UPLOAD", "URL")]:
            default = _last() if need == "FOLDER" else None
            a = _clean(read(f"{t[need]}" + (f" [{default}]" if default else "") + ": ")) or default or ""
            if not a:
                break
            answers[need] = a
            if need == "FOLDER":
                _remember(a)
        else:
            out("$ fieldkit academic " + " ".join(shlex.quote(x) for x in argv_for(item, answers)))
            try:
                call(argv_for(item, answers))
            except SystemExit:
                pass
        read(f"\n{t['enter']}")
