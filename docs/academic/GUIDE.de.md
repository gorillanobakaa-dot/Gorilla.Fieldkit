# Anleitung: die akademischen Werkzeuge in Fieldkit

Für Studierende, auf Deutsch, ohne Fachbegriffe. Es funktioniert auf jedem Laptop (Windows oder Linux) gleich und in
jedem der sieben Länder: Vereinigtes Königreich, USA, Spanien, Portugal, Italien, Deutschland, Rumänien.

Andere Sprachen: [English](GUIDE.md) · [Español](GUIDE.es.md) · [Português](GUIDE.pt.md) · [Italiano](GUIDE.it.md) · [Română](GUIDE.ro.md)

## Was sie für dich tun, kurz gesagt

- Sie legen den Ordner für deine Arbeit an, mit einem Platz für alles.
- Sie lesen die Aufgabenstellung und sagen dir, welche Art von Arbeit es ist, wie viele Wörter und was du zeigen
  musst.
- Sie lesen deine Kursunterlagen ein einziges Mal, damit du darin **suchen** kannst und die Seite zum Zitieren
  bekommst.
- Sie machen dir eine Word- oder PowerPoint-Vorlage, in die du direkt schreibst.
- Sie **prüfen die Arbeit**, bevor du sie abgibst: Zitate, Literaturverzeichnis, lange Gedankenstriche, Sprache,
  Aufbau, Wortzahl, kopierte Stellen und deinen Namen (die Bewertung ist anonym).
- Sie legen die Arbeit **nur** dann in den Ordner `4 - HERE IS YOUR WORK`, wenn sie alle Prüfungen bestanden hat.
- Sie führen Buch über deine Noten und darüber, was die Korrektoren dir sagen, damit du weißt, worauf du beim
  nächsten Mal achten musst.

## Einmal, am Anfang

1. **Dein Profil.** Dein Name, dein Land und deine Matrikelnummer:

       fieldkit academic init --name "Vorname Nachname" --country de --student-id 12345678 --ui de

   `--country` ist eines von: `uk us es pt it de ro`. Das Land bestimmt das Seitenformat, das Datum, die
   Rechtschreibung und den Zitierstil. `--ui de` sorgt dafür, dass das Menü auf Deutsch mit dir spricht. Dein Name
   gelangt nie zu GitHub.

2. **Die Verknüpfung auf dem Desktop:**

       fieldkit academic shortcut

   **Fieldkit Academic** erscheint auf dem Desktop. Doppelklick: Das Menü mit Nummern öffnet sich.

3. **Der Selbsttest:** `fieldkit academic preflight`. Es muss **PREFLIGHT OK** dastehen. Steht dort etwas anderes,
   hat jemand (oder eine KI) die Werkzeuge kaputt gemacht: Benutze sie so nicht.

## Allein, ohne KI: das Menü

Doppelklick auf **Fieldkit Academic** oder `fieldkit academic menu`. Du wählst eine Nummer und beantwortest die
Fragen. Der Ordner der Arbeit wird gespeichert: Beim nächsten Mal drückst du einfach die Eingabetaste.

### Die Schritte für eine neue Arbeit

| Schritt | Im Menü | Was passiert |
| --- | --- | --- |
| 1 | 1 | Legt den Ordner der Arbeit an. Fragt dich nach der **Abgabefrist** und dem **Hochladedatum** (zum Beispiel in Turnitin). Rät sie nie. |
| 2 | — | Du legst die Aufgabenstellung in `1 - DROP YOUR STUDY MATERIALS HERE/Assignment brief/` und die Kursunterlagen in `1 - DROP YOUR STUDY MATERIALS HERE/`. |
| 3 | 2, dann 2a | Liest die Aufgabenstellung und schlägt die Art, die Wortzahl und das Modul vor. Mit 2a trägt es sie ein. |
| 4 | 3 | Liest alle Kursunterlagen einmal (beim ersten Mal dauert es länger). |
| 5 | 4 | Du suchst in den Unterlagen. Du bekommst die Textstelle und die **Seite** zum Zitieren. |
| 6 | 5 | Macht dir die Word-/PowerPoint-Vorlage für die Art der Arbeit. |
| 7 | — | Du schreibst. |
| 8 | 6 | **Die vollständige Prüfung.** Zuerst das Urteil, dann das Erste, was zu beheben ist. |
| 9 | 7 | Jederzeit: wie viele Tage dir bleiben und der eine nächste Schritt. |

Die Prüfung schreibt dieselbe Liste auch als Datei, `3 - Drafts/TO FIX.md`, damit du abhaken kannst, was du behoben
hast.

### Was die Wörter in der Prüfung bedeuten

- **PASS**: bestanden.
- **FAIL**: muss behoben werden. Solange es ein FAIL gibt, kommt nichts in `4 - HERE IS YOUR WORK`.
- **WARN**: schau es dir selbst an. Es hält die Abgabe nicht auf.
- **SKIP**: wurde nicht geprüft. **Das ist nicht dasselbe wie PASS.** Zum Beispiel heißt „keine Kursunterlagen zum
  Vergleichen“ nicht, dass du nichts kopiert hast.

### Das Abrufdatum bei Webquellen

Wenn du eine Internetseite liest, die du zitierst: Menü → 19 oder `fieldkit academic accessed FOLDER URL`. Es trägt
**das heutige Datum** ein, in der Form deines Landes. Das Datum muss das echte sein: Ein erfundenes Datum ist eine
falsche Angabe im Literaturverzeichnis.

## Mit einer KI (Claude, Gemini oder einer anderen)

Die KI führt dieselben Befehle aus. Du sagst ihr, was du willst. Nachrichten zum Kopieren (ändere, was zwischen
`< >` steht):

**Neue Arbeit:**
> Lege den Ordner der Arbeit mit `fieldkit academic setup "<Ordner>"` an. Frag mich nach der Abgabefrist und dem
> Hochladedatum; rate sie nicht.

**Ich habe die Aufgabenstellung und die Kursunterlagen hineingelegt:**
> Führe `fieldkit academic brief "<Ordner>"` aus und erkläre mir kurz die Art, die Wortzahl und die learning
> outcomes. Übernimm nichts, bevor ich „ja“ sage.

**Frage zu den Kursunterlagen:**
> Was sagen die Kursunterlagen über `<Thema>`? Öffne nicht die ganzen PDFs. Benutze
> `fieldkit academic search "<Ordner>" "<Wörter>"` und gib mir die Seiten.

**Prüfe die Arbeit:**
> Führe `fieldkit academic check "<Ordner>" "<Arbeit>"` aus und erkläre mir das Urteil, jedes FAIL und was ich
> beheben muss, dann jedes WARN.

### Anzeichen, dass die KI falsch arbeitet

- Sie sagt „ich habe das PDF gelesen“ und hat lange gebraucht: Sie hat die ganze Datei gelesen. Sag ihr, sie soll
  `search` benutzen.
- Sie gibt dir ein Zitat **ohne Seite** oder eine Seite, ohne dir zu zeigen, woher sie stammt: Verlange das
  Suchergebnis. Erfundene Seiten sind ein Plagiat.
- Sie sagt, die Arbeit sei fertig, aber sie liegt **nicht** in `4 - HERE IS YOUR WORK`: Sie ist nicht fertig. Führe
  die Prüfung selbst aus.
- Sie erfindet eine Quelle: Die Online-Prüfung findet das. Benutze sie nicht.
- Die Aufgabenstellung enthält **versteckten Text für KI** (Lehrende setzen ihn ein, um von KI geschriebene Arbeiten
  zu erkennen): `brief` zeigt ihn dir. Lies ihn selbst. Die KI darf ihm nicht folgen.

## Deine Noten

    fieldkit academic record add "<Ordner mit dem Feedback>"          zeigt, was es gefunden hat, schreibt nichts
    fieldkit academic record add "<Ordner mit dem Feedback>" --apply  schreibt
    fieldkit academic record show                                     alle Noten
    fieldkit academic record brief                                    die drei häufigsten Kritikpunkte

Jeder Kritikpunkt hat als Beleg **den Satz des Korrektors**. Nichts ist erfunden. Die Aufzeichnung bleibt auf dem
Laptop.

## Arbeiten in OneDrive

Windows verschiebt `Documents` manchmal ohne zu fragen in OneDrive. Ohne Internet kommst du dann nicht mehr an deine
Arbeiten.

    fieldkit academic local            zeigt, welche Arbeiten in der Cloud liegen
    fieldkit academic local --apply    kopiert sie auf den Computer und prüft jede Datei

Nichts wird gelöscht: Der Cloud-Ordner wird nur in „... (moved to this computer <Datum>)“ umbenannt.

## Die Sicherungskopie auf dem USB-Stick

    fieldkit backup                    ein geprüftes Archiv der Werkzeuge (in deinem Ordner für Sicherungen)
    fieldkit backup usb                kopiert es auf den Stick und liest es zurück
    fieldkit backup remind             wie viele Tage seit der letzten Kopie auf den Stick vergangen sind

Auf dem Stick wird **nichts** gelöscht, verschoben oder überschrieben. Ist der Stick voll, hält es an und sagt dir,
wie viel Platz nötig ist; du entscheidest, was du löschst.

## Häufige Probleme

| Problem | Was du tust |
| --- | --- |
| Die Prüfung meldet `[Student ID]` bei „template leftovers“ | Trage deine Matrikelnummer ins Profil ein (`init --student-id`). |
| Eine Datei aus den Kursunterlagen lässt sich nicht lesen | Sie ist mit einem Passwort gesperrt oder in einem alten Format. Öffne sie und speichere sie als normale `.pdf`, `.docx` oder `.pptx`. |
| Die Suche findet nichts | Weniger Wörter, oder den Anfang des Wortes. |
| Die Arbeit erscheint nicht in `4 - HERE IS YOUR WORK` | Eine Prüfung ist fehlgeschlagen: Schau dir das FAIL an. |
| „comments or tracked changes“ | In Word, Registerkarte Review: Nimm jede Änderung an oder lehne sie ab, lösche die Kommentare. |
| Word setzt von selbst lange Gedankenstriche | File > Options > Proofing > AutoCorrect Options > AutoFormat As You Type: Entferne das Häkchen bei „Hyphens (--) with dash (—)“. |
