"""The numbered menu: every academic command without remembering one, in the student's interface language.

    fieldkit academic menu                 the menu, in the profile's ui language (en es pt it de ro)
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

LANGS = ("en", "es", "pt", "it", "de", "ro")

ITEMS = [
    # key, the command (FOLDER / FILE / WORDS / ... are asked); the label is LABELS[ui][key]
    ("1", ["setup", "FOLDER", "--deadline", "DEADLINE", "--upload", "UPLOAD"]),
    ("2", ["brief", "FOLDER"]),
    ("2a", ["brief", "FOLDER", "--apply"]),
    ("3", ["extract", "FOLDER"]),
    ("4", ["search", "FOLDER", "WORDS"]),
    ("5", ["template", "TYPE", "FOLDER"]),
    ("6", ["check", "FOLDER", "FILE"]),
    ("7", ["next", "FOLDER"]),
    ("8", ["refs", "FILE"]),
    ("9", ["dashes", "FILE"]),
    ("10", ["language", "FILE"]),
    ("11", ["words", "FILE"]),
    ("12", ["finish", "FILE"]),
    ("13", ["record", "show"]),
    ("14", ["record", "brief"]),
    ("15", ["local"]),
    ("16", ["preflight"]),
    ("17", ["types"]),
    ("18", ["BACKUP"]),
    ("19", ["accessed", "FOLDER", "URL"]),
]
# One block per interface language, every key in every block (a test holds them equal). Romanian is written without
# diacritics on purpose: older Windows consoles show them as question marks.
LABELS = {
    "en": {"1": "Set up an assignment folder", "2": "Read the brief and propose the set-up",
           "2a": "Accept the brief's proposal", "3": "Read the study materials", "4": "Search the study materials",
           "5": "A Word/PowerPoint template for this type", "6": "The whole check before submitting",
           "7": "What do I do next?", "8": "Citations and the reference list", "9": "Long dashes",
           "10": "Language and spelling", "11": "Word count", "12": "Finish a file written in Word/PowerPoint",
           "13": "My marks and what markers say", "14": "Before I write: what to watch for",
           "15": "Assignments in OneDrive: copy them here", "16": "Self-test of these tools",
           "17": "The document types", "18": "Back up Fieldkit, then copy it to the USB stick",
           "19": "A web source I read today: stamp its date"},
    "es": {"1": "Preparar la carpeta de un trabajo", "2": "Leer el enunciado y proponer la configuración",
           "2a": "Aceptar la propuesta del enunciado", "3": "Leer los materiales del curso",
           "4": "Buscar en los materiales del curso", "5": "Una plantilla Word/PowerPoint para este tipo",
           "6": "La revisión completa antes de entregar", "7": "¿Qué hago ahora?", "8": "Citas y bibliografía",
           "9": "Rayas largas", "10": "Idioma y ortografía", "11": "Número de palabras",
           "12": "Terminar un archivo escrito en Word/PowerPoint", "13": "Mis notas y lo que dicen los correctores",
           "14": "Antes de escribir: a qué prestar atención", "15": "Trabajos en OneDrive: copiarlos aquí",
           "16": "Autoprueba de estas herramientas", "17": "Los tipos de trabajo",
           "18": "Copia de seguridad de Fieldkit y copia al USB",
           "19": "Una fuente web leída hoy: poner la fecha de consulta"},
    "pt": {"1": "Preparar a pasta de um trabalho", "2": "Ler o enunciado e propor a configuração",
           "2a": "Aceitar a proposta do enunciado", "3": "Ler os materiais do curso",
           "4": "Pesquisar nos materiais do curso", "5": "Um modelo Word/PowerPoint para este tipo",
           "6": "A verificação completa antes de entregar", "7": "O que faço agora?",
           "8": "Citações e bibliografia", "9": "Travessões", "10": "Língua e ortografia",
           "11": "Número de palavras", "12": "Concluir um ficheiro escrito em Word/PowerPoint",
           "13": "As minhas notas e o que dizem os avaliadores", "14": "Antes de escrever: a que estar atento",
           "15": "Trabalhos no OneDrive: copiá-los para aqui", "16": "Autoteste destas ferramentas",
           "17": "Os tipos de trabalho", "18": "Cópia de segurança do Fieldkit e cópia para a pen USB",
           "19": "Uma fonte web lida hoje: registar a data de acesso"},
    "it": {"1": "Preparare la cartella di un elaborato", "2": "Leggere la consegna e proporre le impostazioni",
           "2a": "Accettare la proposta della consegna", "3": "Leggere i materiali del corso",
           "4": "Cercare nei materiali del corso", "5": "Un modello Word/PowerPoint per questo tipo",
           "6": "Il controllo completo prima della consegna", "7": "Che cosa faccio adesso?",
           "8": "Citazioni e bibliografia", "9": "Trattini lunghi", "10": "Lingua e ortografia",
           "11": "Numero di parole", "12": "Completare un file scritto in Word/PowerPoint",
           "13": "I miei voti e cosa dicono i correttori", "14": "Prima di scrivere: a cosa fare attenzione",
           "15": "Elaborati in OneDrive: copiarli qui", "16": "Autotest di questi strumenti",
           "17": "I tipi di elaborato", "18": "Backup di Fieldkit, poi copia sulla chiavetta USB",
           "19": "Una fonte web letta oggi: annotare la data di consultazione"},
    "de": {"1": "Einen Ordner für eine Arbeit anlegen", "2": "Die Aufgabenstellung lesen und Einstellungen vorschlagen",
           "2a": "Den Vorschlag aus der Aufgabenstellung übernehmen", "3": "Die Kursmaterialien lesen",
           "4": "In den Kursmaterialien suchen", "5": "Eine Word-/PowerPoint-Vorlage für diesen Typ",
           "6": "Die vollständige Prüfung vor der Abgabe", "7": "Was mache ich als Nächstes?",
           "8": "Zitate und Literaturverzeichnis", "9": "Lange Striche", "10": "Sprache und Rechtschreibung",
           "11": "Wortzahl", "12": "Eine in Word/PowerPoint geschriebene Datei abschließen",
           "13": "Meine Noten und was die Korrektoren sagen", "14": "Vor dem Schreiben: worauf ich achten muss",
           "15": "Arbeiten in OneDrive: hierher kopieren", "16": "Selbsttest dieser Werkzeuge",
           "17": "Die Arbeitstypen", "18": "Fieldkit sichern, dann auf den USB-Stick kopieren",
           "19": "Eine heute gelesene Webquelle: Abrufdatum eintragen"},
    "ro": {"1": "Pregatesc folderul unei teme", "2": "Citesc cerinta temei si propun setarile",
           "2a": "Accept propunerea din cerinta temei", "3": "Citesc cursurile", "4": "Caut in cursuri",
           "5": "Un sablon Word/PowerPoint pentru tipul temei", "6": "Verificarea completa inainte de predare",
           "7": "Ce fac acum?", "8": "Citarile si bibliografia", "9": "Liniutele lungi", "10": "Limba si ortografia",
           "11": "Numarul de cuvinte", "12": "Finalizez un fisier scris in Word/PowerPoint",
           "13": "Notele mele si ce spun corectorii", "14": "Inainte sa scriu: la ce sa fiu atent",
           "15": "Teme in OneDrive: le copiez pe calculator", "16": "Autotestul uneltelor", "17": "Tipurile de lucrari",
           "18": "Backup, apoi copia pe stick-ul USB", "19": "O sursa web citita azi: pun data accesarii"},
}
ASK = {"en": {"pick": "Pick a number (0 to leave)", "FOLDER": "The assignment folder", "FILE": "The file (drag it here)",
              "DEADLINE": "The deadline (as the brief writes it)", "UPLOAD": "The date it must be uploaded by",
              "URL": "The web address",
              "WORDS": "What to look for (words, or \"an exact phrase\")", "TYPE": "The document type",
              "enter": "Press Enter for the menu", "title": "FIELDKIT ACADEMIC", "bad": "Pick a number from the list."},
       "es": {"pick": "Elige un número (0 para salir)", "FOLDER": "La carpeta del trabajo",
              "FILE": "El archivo (arrástralo aquí)", "DEADLINE": "La fecha límite (como la escribe el enunciado)",
              "UPLOAD": "La fecha hasta la que hay que subirlo", "URL": "La dirección web",
              "WORDS": "Qué buscar (palabras, o \"una frase exacta\")", "TYPE": "El tipo de trabajo",
              "enter": "Pulsa Intro para el menú", "title": "FIELDKIT ACADEMIC - MENÚ",
              "bad": "Elige un número de la lista."},
       "pt": {"pick": "Escolhe um número (0 para sair)", "FOLDER": "A pasta do trabalho",
              "FILE": "O ficheiro (arrasta-o para aqui)", "DEADLINE": "O prazo (como está escrito no enunciado)",
              "UPLOAD": "A data até à qual tem de ser carregado", "URL": "O endereço web",
              "WORDS": "O que procurar (palavras, ou \"uma frase exata\")", "TYPE": "O tipo de trabalho",
              "enter": "Prime Enter para o menu", "title": "FIELDKIT ACADEMIC - MENU",
              "bad": "Escolhe um número da lista."},
       "it": {"pick": "Scegli un numero (0 per uscire)", "FOLDER": "La cartella dell'elaborato",
              "FILE": "Il file (trascinalo qui)", "DEADLINE": "La scadenza (come scritta nella consegna)",
              "UPLOAD": "La data entro cui caricarlo", "URL": "L'indirizzo web",
              "WORDS": "Cosa cercare (parole, o \"una frase esatta\")", "TYPE": "Il tipo di elaborato",
              "enter": "Premi Invio per il menu", "title": "FIELDKIT ACADEMIC - MENU",
              "bad": "Scegli un numero dall'elenco."},
       "de": {"pick": "Wähle eine Nummer (0 zum Beenden)", "FOLDER": "Der Ordner der Arbeit",
              "FILE": "Die Datei (hierher ziehen)", "DEADLINE": "Der Abgabetermin (wie in der Aufgabenstellung)",
              "UPLOAD": "Das Datum, bis zu dem hochgeladen werden muss", "URL": "Die Webadresse",
              "WORDS": "Wonach suchen (Wörter, oder \"ein genauer Ausdruck\")", "TYPE": "Der Typ der Arbeit",
              "enter": "Enter drücken für das Menü", "title": "FIELDKIT ACADEMIC - MENÜ",
              "bad": "Wähle eine Nummer aus der Liste."},
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
    return [answers.get(w, w) for w in item[1]]


def run(read=input, out=print, call=None, ui=None):
    from .cli import main as academic
    call = call or (lambda argv: academic(argv))
    p = prof.load() or {}
    lang = ui or p.get("ui") or "en"
    lang = lang if lang in LANGS else "en"
    t, labels = ASK[lang], LABELS[lang]
    while True:
        out("\n" + "=" * 60 + f"\n  {t['title']}\n" + "=" * 60)
        for key, _ in ITEMS:
            out(f"  {key:>3}. {labels[key]}")
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
        if item[1] == ["BACKUP"]:
            from ..cli import main as fieldkit
            fieldkit(["backup"])
            fieldkit(["backup", "usb"])
            read(f"\n{t['enter']}")
            continue
        answers = {}
        for need in [w for w in item[1] if w in ("FOLDER", "FILE", "WORDS", "TYPE", "DEADLINE", "UPLOAD", "URL")]:
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
