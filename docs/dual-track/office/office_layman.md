# Read, make, check and clean Word, Excel, PowerPoint and PDF files on your own computer — Plain Language Guide

> Generated 2026-10-02 from `office`

---

## Should You Run This?

Yes, if you send Word, Excel, PowerPoint or PDF files and want to check them for damage and remove your name from the hidden properties before they leave your computer. Use `fieldkit office deliver` in preference to the separate commands, because only `deliver` also searches the file's contents and its folder for leftovers. Only with care, if you share or copy your Fieldkit folder: the backups in `state\office-backups` still hold every removed name until you delete them. Do not treat `SAFE TO SEND` as proof that a document is anonymous: it does not change the visible text and cannot flag a name that is not on your private words list. Do not expect it to clear a PDF that has an author, or any file with a part larger than the scanner's limit: those always end `NOT SAFE`, by design. Do not run it while the document is open in Office. Do not use `--no-backup` on your only copy of a document unless you are sure you never need the original properties again.

## Worst Case, Honestly

The most likely real harm is still a false sense of safety. Example: you run `fieldkit office deliver CV.docx`, it prints `SAFE TO SEND`, and you send it. Your name is out of the Author box, but the document text mentions your old employer, which is not on your private words list. The privacy scan looks only for secrets, home-folder paths, email addresses and the words on your list, so it does not flag it.

A second real harm: the backups in `state\office-backups` inside the Fieldkit folder keep your name, and they pile up, one folder per cleaning run, because nothing deletes them. If you copy the whole Fieldkit folder to a USB stick or give it to someone, those backups go with it.

A third: with `--no-backup` you keep no copy of the original. Fieldkit still holds the original in memory while it works and puts it back if its own check of the cleaned file fails, so a faulty rewrite does not cost you the file. But once cleaning succeeds, the original with its names is gone for good, and if the computer loses power in the middle of the swap, what is left on disk is not described in the source (Not available in the source material).

A fourth: `create --force` replaces an existing file of the same name on purpose. Without `--force`, `create` refuses, so a typing slip can no longer silently replace a document; with it, the old file is gone (only after the new one has passed its check).

## What Data This Touches

The commands read only the files you name, the folder that holds them (`deliver` lists that folder to look for leftover backups and lock files), and the settings file `fieldkit.local.json` (for your private words list). Nothing leaves your machine: the source says "Nothing is uploaded anywhere", and the checker's XML reader runs with network access turned off (`no_network=True`).

What is written, and where:

- `create` writes the new file you name. While it works, a temporary file whose name starts with `.fieldkit-create-` appears in the same folder for a moment and is removed afterwards.
- `scrub` and `deliver` rewrite your document in place. A temporary file starting with `.fieldkit-scrub-` appears beside it for a moment and is removed afterwards.
- The backup of the original goes into the Fieldkit folder, under `state\office-backups`\<date-time>\, together with `original.json`, which records the full path your document came from. That backup still contains every name that was removed, and the note contains the path, which can include your Windows user name. Fieldkit never deletes these backups (nothing in the source removes them); they stay until you delete them. With `--no-backup`, no copy is kept.
- `deliver` keeps a small record of each run in Fieldkit's own `state` folder.
- `read` prints the document's text in the terminal window, and saves it to a file only if you add `--out`.

Two optional readers, markitdown and Docling, can be chosen with `--engine`. Whether those optional readers use the internet: Not available in the source material.

## Before You Trust It

These commands change files. Try them on a copy first, so that a mistake costs you nothing, and compare what Fieldkit says with what Word or Excel shows you.

**Step 1:** In File Explorer, right-click the document you want to send, choose Copy, then right-click empty space in the same folder and choose Paste. Rename the copy to `test.docx` (or `test.xlsx`, `test.pptx`).
  - Look for: Pass: you now have the original and `test.docx` side by side. Work only on `test.docx` in the steps below.
**Step 2:** Open the copy in Word, choose File, then Info, and look at the Author and Last Modified By boxes on the right. Close Word completely.
  - Look for: Pass: you have noted which names are there. These are what Fieldkit should remove. Closing Word matters: while it is open, its `~$` lock file sits beside the document and `deliver` will say NOT SAFE.
**Step 3:** In the folder, click the address bar, type `powershell` and press Enter. In the window that opens, type `fieldkit office scrub test.docx --check` and press Enter.
  - Look for: Pass: a list under `found` with entries such as `"properties", "creator"` followed by a name, matching what you saw in step 2. Fail: an error saying `fieldkit` is not recognised means Fieldkit is not installed.
**Step 4:** Type `fieldkit office deliver test.docx` and press Enter.
  - Look for: Pass: `SAFE TO SEND` and the file name, followed by three lines for `check`, `scrub` and `privacy`. The `scrub` line says "backup kept outside the document's folder" and names the backup. If you see `NOT SAFE`, read the stage line that failed: it names the problem.
**Step 5:** Open `test.docx` in Word again and repeat step 2, then close Word.
  - Look for: Pass: Author and Last Modified By are empty, and the words on the pages are unchanged. Fail: a name is still there, or the text differs.
**Step 6:** Look in the document's folder. Then open the Fieldkit folder, then `state\office-backups`, and open the newest folder (its name is the date and time of the run).
  - Look for: Pass: the document's folder holds no `test.docx.bak` and no file starting with `.fieldkit-scrub-`. The backup folder holds `test.docx` and `original.json`; opened in Word, the backup still shows your name. This confirms the backup works and where it lives.
**Step 7:** Prove the leftover check. In File Explorer, copy `test.docx` and paste it in the same folder, then rename the copy to `test.docx.bak`. Back in PowerShell, type `fieldkit office deliver test.docx` and press Enter. Afterwards, delete `test.docx.bak`.
  - Look for: Pass: `NOT SAFE`, with the `privacy` line naming `test.docx.bak` as a "stale backup or lock file next to it". Fail: `SAFE TO SEND` while the `.bak` is there.
**Step 8:** Prove the overwrite refusal. Type `fieldkit office create report.json test.docx` and press Enter, using the `report.json` spec from "How to use this", step 5.
  - Look for: Pass: an error ending with "refusing to overwrite it (use --force to replace it)", and `test.docx` is unchanged. Fail: `test.docx` is replaced.
**Step 9:** Optional, to run the group's own tests: open the Fieldkit folder in File Explorer, click the address bar, type `powershell`, press Enter, then type `python -m pytest tests/test_office.py tests/test_deliver.py tests/test_office_security.py -q` and press Enter.
  - Look for: Pass: the last line reports `passed` with no `failed`. One test is skipped when the free `pdftotext` program is not installed; that is expected.

## The Big Picture

This part of Fieldkit works with four kinds of document: Word (`.docx`), Excel (`.xlsx`), PowerPoint (`.pptx`) and PDF (`.pdf`). You type one short `fieldkit office` command and it does one of five jobs: it reads a document out as plain text, makes a new document from a short description you write, checks that a document is not broken, finds and removes people's names in a document's hidden properties, or runs the full "is this safe to send?" routine.

Everything happens on your own computer. The source says it plainly: "Nothing is uploaded anywhere." You do not need Microsoft Office installed for any of the five jobs, because Fieldkit uses free, open helpers that read and write these formats directly.

The most useful job for most people is the last one, `fieldkit office deliver`. Before you email a report or upload a CV, it checks the file is sound, takes your name out of the hidden "Author" and "Last saved by" boxes, keeps a backup of the original inside Fieldkit's own folder (never next to your document), checks again, and then searches every part inside the file for passwords, home-folder paths and words you have marked as private. It also refuses to call a file safe while an old backup or a Word lock file lies in the same folder, or while any part of the file could not be read. For a PDF it looks at the PDF's own properties too; if they hold a name, it stops and tells you how to fix that elsewhere, because Fieldkit cannot clean PDF properties. It ends with one clear verdict: `SAFE TO SEND` or `NOT SAFE`.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `Office file as a parcel` | A `.docx`, `.xlsx` or `.pptx` file is a ZIP package: a bundle of many small text parts (the words, the styles, the properties) packed into one file. | A parcel with several labelled envelopes inside. If one envelope is torn, or the packing list names an envelope that is missing, the parcel is damaged even though the box looks fine. |
| `Hidden properties` | Boxes stored inside the file that you do not see on the page: Author, Last saved by, Company, Manager, Title, Subject, Keywords and others. | The return address on the back of an envelope. The letter can be anonymous while the envelope still carries your name. |
| `Review marks` | Comments and tracked changes. Each one stores the name of the person who made it. | Initials pencilled in the margin of a shared draft. |
| `Private words list` | A list of words you never want in a file you send, such as your real name or login name. Fieldkit reads it from the `privacy` section of a settings file called `fieldkit.local.json`, and you can add more words with `--term`. | A list of names you give to the person who checks the post before it goes out. |
| `Spec` | A short text file in which you describe the document you want: its headings, paragraphs, bullet lists, tables, sheets or slides. Fieldkit turns it into the real file. | An order form at a print shop. You fill in what you want; the shop produces the printed item. |
| `Cached value` | In Excel, the last answer a formula worked out, saved next to the formula. Excel recalculates on opening, but other programs show only the saved answer. | A sticky note with yesterday's total on a calculator. If nobody pressed equals yet, the note is blank. |
| `Text layer of a PDF` | Real letters stored inside a PDF. A scanned page is only a picture of letters and has no text layer. | A typed letter versus a photograph of a typed letter. |
| `Backup folder` | Before Fieldkit removes names from a file, it copies the original into Fieldkit's own folder, under `state\office-backups` and then a folder named after the date and time, such as `<date>-<time>`. Next to the copy it writes a small note, `original.json`, that says where the file came from. Nothing is left next to your document. | Handing the uncorrected form to the office safe before you correct the copy on your desk. The original is kept, but not in the envelope you are about to post. |
| `Temporary copy and swap` | Fieldkit never edits your file directly. It writes a new version under a temporary name that starts with `.fieldkit-create-` or `.fieldkit-scrub-` in the same folder, checks that new version, and only then puts it in place of the old one. If the check fails, the temporary file is deleted and your file stays as it was. | Assembling a new bookcase in the hallway and checking it stands straight before you carry it into the room and take the old one out. |
| `Lock file` | While Word or Excel has a document open, it puts a small hidden file beside it whose name starts with `~$`, for example `~$port.docx` next to `report.docx`. That lock file holds the name of the person editing. | A "room occupied" sign on a door with the occupant's name written on it. |
| `Fail closed` | If Fieldkit cannot read part of a file (because it is too large, damaged or compressed in a way it cannot undo), it reports that part as "not scanned" and the file is not called safe. Unread never counts as clean. | A customs officer who cannot open a sealed crate does not wave it through; the crate waits until someone can look inside. |
| `PDF properties` | A PDF has its own hidden boxes, separate from Word's: Author, Title, Subject, Keywords, Creator and Producer, plus a second copy of similar details in a block called XMP. | The label on the spine of a ring binder, which can name the person it belongs to even when every page inside is anonymous. |

## How It Works — Step by Step

### Step 1: Reading a document out as text

`fieldkit office read` opens the file and walks through it from top to bottom. In Word, it turns headings into lines that start with `#`, list items into lines that start with `-`, and each table into one block of rows separated by `|`, with the rows on consecutive lines so that a Markdown viewer draws them as one table. A `|` sign inside a cell is written as `\|` so it does not split the cell. In Excel, it writes each sheet's name and then its rows as one table, showing the saved answers of formulas, not the formulas. In PowerPoint, it writes `## Slide 1: <title>`, the bullet points, and any speaker notes as `> Notes: ...`. For a PDF, it uses the free `pdftotext` program if your computer has it, and otherwise a built-in PDF reader. Think of a clerk copying a document into a notebook: the words and table rows come across, the layout and pictures do not.

### Step 2: Making a document from your spec: the safety checks first

`fieldkit office create` reads your spec file (JSON or YAML). Before it writes anything, it makes two checks. First, the kind of document in your spec must match the ending of the file name: a spec that says `docx` with an output called `report.pdf` is refused with a message that says the file name ends `.pdf`, and nothing is written. Second, if a file with that name already exists, it is refused with "refusing to overwrite it (use --force to replace it)". Only `--force` lets it replace an existing file. This is like a print shop that checks the order form says A4 when you asked for A4 paper, and asks before printing over a sheet that already has something on it.

### Step 3: Building the document under a temporary name

It then builds the document part by part, in a temporary file in the same folder whose name starts with `.fieldkit-create-`: a heading, a paragraph, a bullet list, a numbered list, a table or a page break for Word; sheets of rows for Excel; slides with a title, bullets and notes for PowerPoint; headings, paragraphs and bullets for PDF. It leaves the Author and Last saved by boxes empty unless your spec sets `author`. The source says why: "the file should not carry the machine owner's name by accident".

### Step 4: Checking its own work before handing it over

`create` runs the full check from step 5 on the temporary file. Only a file that passes is moved to the name you asked for. If the check finds a problem, the command stops with "failed its own check and was not written", the temporary file is deleted, and any older file of the same name is left exactly as it was. This is like a baker cutting one slice from every loaf before it goes on the shelf, and throwing away a bad loaf instead of shelving it.

### Step 5: Checking a document for damage

`fieldkit office check` opens the parcel and looks at every envelope. For Word, Excel and PowerPoint it confirms the file is a readable ZIP package, that no part name appears twice or tries to point outside the package, that the packing list (`[Content_Types].xml`) and the index (`_rels/.rels`) exist, that every text part is well-formed, that every internal link points at a part that exists, and that the main part (for example `word/document.xml`) is present. It then opens the file with the matching open-source reader. For Excel it also counts formulas and lists those with no saved answer. For a PDF it checks that the file starts with `%PDF-`, that the end marker `%%EOF` is in the last 2048 bytes, that it opens, how many pages it has, and whether the first five pages contain any real text. It prints `OK` or `FAIL` for each file.

### Step 6: Finding names in the hidden properties

`fieldkit office scrub --check` only looks and reports. For Word, Excel and PowerPoint it lists Author and Last saved by whenever they hold anything, Company and Manager whenever they hold anything, Title, Subject, Keywords, Description and Category only when they contain a word from your private words list, custom properties that contain such a word, and the name on every comment and tracked change. For a PDF it now looks too: it lists Author whenever it holds anything, and Title, Subject, Keywords, Creator and Producer when they contain a private word. It also reads the PDF's XMP block and lists the creator and author there, plus title, description, rights and similar fields when they contain a private word.

### Step 7: Removing those names, carefully

`fieldkit office scrub` without `--check` works in this order. One: if it finds nothing to remove, it changes nothing and makes no backup. Two: it writes a cleaned copy beside your file under a temporary name starting with `.fieldkit-scrub-`, emptying the boxes listed in step 6 and replacing every comment and tracked-change name with the word `Author` and the initials with `A`. Three: it checks that cleaned copy, which must still pass the step 5 check and have no names left; if not, it stops with "the cleaned copy failed its check, the file was not changed". Four: it copies your original into the backup folder (`state\office-backups`\<date-time>\ in the Fieldkit folder) unless you said `--no-backup`. Five: it swaps the cleaned copy into place. Six: it checks the file now in place once more; if that fails, it writes your original back and says "the original was put back". The source says this cleaning matches what Word's own "Remove Personal Information" does, and states: "Never touched: the document's own text." A PDF is refused at this step with "PDF properties cannot be cleaned by Fieldkit". Think of a restorer who works on a photocopy, checks it, locks the original in the safe, hangs the copy, and takes the original back out if the copy on the wall turns out to be wrong.

### Step 8: The full safe-to-send routine

`fieldkit office deliver` runs three stages in order and stops at the first one that fails. Stage `check` runs step 5; a broken file is never touched. Stage `scrub` runs step 7 and then proves the result: the file must still pass the check and have no names left in its properties. For a PDF, this stage only looks: if the PDF's properties hold a name, it stops here with "PDF properties hold names" and instructions to re-export the PDF with the author and title fields blank. Stage `privacy` then searches every part inside the file for secrets such as passwords and keys, home-folder paths, email addresses and your private words. For a PDF it unpacks the compressed parts and reads the visible text as well, so a name hidden in compressed text is found. Any part it could not read (too large, damaged, or packed in a way it cannot unpack) is reported as "could not be read, so cannot be called clean". The same stage also looks in the document's folder for a "stale backup or lock file next to it": an old `.bak`, a `.wbk`, a "Backup of" copy, a Word lock file starting with `~$`, or a leftover `.fieldkit-scrub-` file. You see `SAFE TO SEND` only if all three stages pass. It works like three gates at airport security: each gate can turn you back, and an unopened bag never goes through.

### Step 9: Getting the original back

There is no `fieldkit` command to undo a cleaning. The program has a restore function inside it (the tests use it), but it is not registered as a command, so you cannot type it. To get the original back yourself, open the Fieldkit folder, then `state\office-backups`, then the folder with the date and time of the run. Open `original.json` in Notepad to see where the file came from, and copy the document from this folder back over the cleaned one. It is like fetching the original form back out of the office safe by hand: the safe keeps it, but no clerk brings it to you.

## Quirky Things Worth Knowing

### What this cannot do

It cannot remove names from the visible text: "Never touched: the document's own text." It cannot clean a PDF's properties; it can only find names there and stop. It does not flag a name, address or employer that is not on your private words list. It does not read pictures, so a name inside an image is never seen. It cannot read the old `.doc`, `.xls` or `.ppt` formats or OpenDocument files. It does not recognise text in scanned pages (there is no OCR). It cannot call a file safe if any part is larger than the privacy scanner's limit of 5,000,000 bytes (a limit written in the scanner's source): that part is reported as not scanned and the verdict is `NOT SAFE`. It has no undo command, and it never deletes its own backups. It does not stop Word from writing your name back the next time you save.

### Word puts your name back every time you save

The source explains that Word kept writing the author's name back into Last saved by on every save. So run `scrub` or `deliver` after your last edit and save. If you open the cleaned file in Word and save it again, run `deliver` again.

### The backup is no longer beside your file, but it still has your name

Earlier versions left a `.bak` copy next to the document. Now the backup goes into the Fieldkit folder, under `state\office-backups`. It is still an exact copy of the file before cleaning, with your name in it. Do not share the Fieldkit folder, and delete old backup folders yourself once you are happy with the cleaned files.

### An old `.bak` or an open document makes `deliver` say NOT SAFE

If a `.bak` from an earlier run, a `.wbk`, a "Backup of" file, or a leftover `.fieldkit-scrub-` file lies next to your document, `deliver` stops at `privacy` and names it. The same happens while the document is open in Word or Excel, because Office creates a lock file starting with `~$` beside it, and that lock file holds the editor's name. Close the document, delete or move the leftover file, and run `deliver` again. Files with a different name, such as `report-final.docx` or `other.docx.bak`, are not counted.

### `SAFE TO SEND` does not mean the text is anonymous

`scrub` never changes the words on the page. The privacy stage of `deliver` only flags secrets, home-folder paths, email addresses and words on your private words list. A name, address or employer that is not on your list stays in the text and is not reported.

### A PDF with an author stops at `scrub`

Any PDF whose Author box holds anything at all is `NOT SAFE`, because an author is a name by definition. Fieldkit cannot fix it for you. The source gives the reason: with the libraries it uses, the only way to change the properties is an update "which leaves the old values in the file". Export the PDF again from the program that made it, with the author and title fields blank, then run `deliver` again. A PDF made by `fieldkit office create` has an empty author unless your spec sets one.

### Very large files cannot be called safe

The privacy scanner reads each file or inner part only up to 5,000,000 bytes (a limit written in its source). Anything larger used to be skipped without a word; now it is reported as "too large" and `deliver` says `NOT SAFE`. A big PDF, or a Word file with a huge part inside, will therefore never get `SAFE TO SEND` from this tool. That is deliberate: a file that was not read cannot be called clean.

### A new Excel file shows blanks where formulas are

Fieldkit writes formulas but does not work out their answers. `check` reports them as `formulas_without_cached_value`, and `read` shows those cells as empty. Excel calculates them when you open the file; other programs may show blanks until someone opens and saves it in Excel.

### A `|` in a table cell reads out as `\|`

`read` writes tables in Markdown, where `|` separates cells. So a `|` that is really part of the cell's text is written with a backslash in front of it. A Markdown viewer shows it as a plain `|`; a plain text editor shows the backslash.

### Pictures, headers, footers and text boxes are not read

For Word, `read` reads only the main body's paragraphs and tables. For PowerPoint, it reads titles, shapes that hold text, and notes. Images and charts are never turned into text.

### A scanned PDF reads as empty

There is no text recognition (OCR) in this group. `check` reports `has_text: False` when the first five pages hold no real letters.

### Put file names before `--term`

`--term` takes every word after it. If you type `fieldkit office scrub --term Smith report.docx`, the file name is taken as a second word to hunt and the command complains that no file was given. Type `fieldkit office scrub report.docx --term Smith` instead. For several words, repeat it: `--term Smith --term Jones`.

### `create` will not replace a file unless you say so

If the output file already exists, `create` stops and leaves it alone. Because this particular refusal is not one of the errors Fieldkit tidies up, it appears as several lines of Python error text ending with `FileExistsError` and "refusing to overwrite it (use --force to replace it)". Nothing is wrong; choose another name, or add `--force` if you really mean to replace the file.

### The file name ending must match the type

If your spec says `type: docx` but you name the output `report.pdf`, `create` now refuses before writing anything and tells you to name the output `.docx` or change the spec's type. No wrongly named file is left behind.

### Older formats are refused

The old `.doc`, `.xls` and `.ppt` formats, and OpenDocument files, are not supported. `read` also refuses the macro-enabled `.docm` and `.pptm`, although `check` and `scrub` accept them.

## What This Means For You

### Battery, Processor & Memory

Effect on battery, processor and memory: not measured. Today's changes add work: `scrub` now checks the cleaned file twice (before and after the swap), and the privacy stage now unpacks compressed PDF parts and reads the PDF's visible text. How much extra time or memory that costs is not measured. For Excel workbooks, `check` opens the workbook twice in full, once for formulas and once for saved answers; how much memory that uses is not measured.

### Speed

How long each command takes: not measured. The full Fieldkit test suite, which includes the office tests, ran in 194.89 s with 584 passed, 1 skipped and 2 xfailed (measured 2026-10-02 on a Windows 11 laptop). That figure covers all of Fieldkit, not this group alone, and whether it already includes today's new office security tests is not available in the source material.

### Your Privacy

This group exists to protect your privacy when you send documents. It removes names from hidden properties and review marks, finds names in PDF properties, searches the inside of the file for private material, and refuses to call a file safe when part of it could not be read or a backup or lock file lies beside it. It does not remove names from the visible text and cannot clean PDF properties. Its backups, which still hold the names, are kept in `state\office-backups` inside the Fieldkit folder until you delete them. Your private words list stays in `fieldkit.local.json` on your machine.

### Your Internet

The built-in readers, writers and checks make no network connections: the source says "Nothing is uploaded anywhere", and the checker's XML reader has network access switched off. Whether the optional markitdown or Docling readers connect to the internet: Not available in the source material.

## The Off Switch

**What it is:** There is no single off switch, but there are six guards. First, `--check` on `scrub` makes it report only, with no change to the file. Second, `create` refuses to replace an existing file unless you add `--force`, and refuses a file name whose ending does not match the spec. Third, `create` and `scrub` build the new file under a temporary name and check it before it replaces anything; a failed check leaves your file as it was. Fourth, `scrub` checks the file once more after the swap and puts the original back if that check fails. Fifth, `scrub` and `deliver` keep a backup in `state\office-backups` unless you add `--no-backup`. Sixth, `deliver` stops at the first failed stage, so a broken file is never rewritten, and it refuses to say safe while anything was left unread. To get an original back, copy it out of the backup folder by hand; there is no undo command.

**Without it:** Without `--check` you could not see what would be removed before it happens. Without the overwrite refusal, a typing slip in `create` could replace a document you wanted to keep. Without the temporary copy and the double check, a faulty rewrite could leave you with a damaged document. Without the backup you would have no copy with the original properties. Without the stop-at-first-failure rule and the fail-closed privacy scan, `deliver` could rewrite a damaged file or call a file safe that it never read.

**Think of it like:** A dry run before a fire drill; a print shop that asks before printing over a used sheet; a restorer who works on a copy and keeps the original in the safe; and a security gate that turns you back, and will not wave through a bag it could not open.

## How to use this

**Before you start:**
- Fieldkit is installed. To confirm, open PowerShell and type `fieldkit host`; it should print details about your computer, not an error.
- The document is a `.docx`, `.xlsx`, `.pptx` or `.pdf` file and is closed in Word, Excel or PowerPoint (an open document leaves a `~$` lock file that makes `deliver` say NOT SAFE).
- Optional: your private words list. Ask whoever set up Fieldkit to add your real name and login name under `privacy` and `terms` in `fieldkit.local.json`, or add words each time with `--term`.

**Step 1:** Open PowerShell: press the Windows key, type `PowerShell`, and press Enter. A window with a blinking cursor opens.
  - You should see: Pass: a window with a line ending in `>` and a blinking cursor. Fail: nothing opens; try again and make sure you typed `PowerShell` in the Start menu search.
**Step 2:** Go to the folder that holds your document. Type `cd` and one space, but do not press Enter yet. Then drag the folder from File Explorer and drop it on this window: its full location appears after `cd`, with quotation marks if it has spaces. Now press Enter.

```powershell
cd 
```

  - You should see: Pass: the line before the cursor now shows that folder. Fail: "Cannot find path" means the folder name was mistyped; copy it again from File Explorer.
**Step 3:** Check that the file is not broken. Type this and press Enter (use your own file name, and put quotes round it if it contains spaces, for example `"my report.docx"`):

```powershell
fieldkit office check report.docx
```

  - You should see: Pass: a line that starts with `OK` and the file name, followed by counts such as paragraphs and tables. Fail: `FAIL` followed by lines starting with `-`; each line names a problem.
**Step 4:** Read the document as text in the window. Type this and press Enter:

```powershell
fieldkit office read report.docx
```

To save the text to a file instead, type this and press Enter:

```powershell
fieldkit office read report.docx --out report.md
```

  - You should see: Pass: the text appears with headings marked by `#`, bullet points by `-` and each table as one block of rows between `|` signs, the rows on consecutive lines. With `--out`, a short summary appears and `report.md` is in the folder (an existing `report.md` is replaced without asking). Fail: "unsupported type" means the file format is not one this group reads.
**Step 5:** Make a new Word file. Open Notepad, paste the lines below, and save as `report.json` in the same folder with "Save as type" set to "All files":

```json
{"type": "docx", "title": "Field report", "blocks": [
  {"heading": "Findings", "level": 1},
  {"paragraph": "The pump failed at 14:00."},
  {"bullets": ["check the seal", "order parts"]},
  {"table": [["Part", "Qty"], ["seal", "2"]]}]}
```

Then type this and press Enter (use a name that does not exist yet, here `new-report.docx`):

```powershell
fieldkit office create report.json new-report.docx
```

If you really want to replace an existing file of that name, add `--force` and press Enter:

```powershell
fieldkit office create report.json new-report.docx --force
```

  - You should see: Pass: a short report ending with `"problems": []` and a new `new-report.docx` in the folder. Reading it back shows `# Findings`, `The pump failed at 14:00.`, `- check the seal` and the table rows `| Part | Qty |` and `| seal | 2 |` on consecutive lines. Fail: "refusing to overwrite it" means the file already exists (nothing was changed); "spec type is 'docx' but the file name ends" means the ending does not match the spec.
**Step 6:** See which names the hidden properties hold, without changing anything. Replace `Jane Public` (a made-up name) with the name you are looking for, and press Enter:

```powershell
fieldkit office scrub report.docx --check --term "Jane Public"
```

This works for a PDF too:

```powershell
fieldkit office scrub report.pdf --check --term "Jane Public"
```

  - You should see: Pass: a list under `found`, for example the author box and any title containing the word you gave; for a PDF the entries start with `pdf properties` or `pdf xmp`. An empty list means nothing was found.
**Step 7:** Run the full safe-to-send routine after your last save. Type this and press Enter:

```powershell
fieldkit office deliver report.docx
```

  - You should see: Pass: `SAFE TO SEND` and the file name with three lines: `check` says `sound`, `scrub` says how many items were removed and "backup kept outside the document's folder" with the backup's place, and `privacy` says "nothing private inside, no stale backup beside it". Fail: `NOT SAFE` with the stage that stopped it and the reason, for example `windows-user-path` when a home-folder path is written inside the file, "stale backup or lock file next to it", or "PDF properties hold names".
**Step 8:** Only if you do not want any backup kept (for example, the original is already safe elsewhere), add `--no-backup` and press Enter:

```powershell
fieldkit office deliver report.docx --no-backup
```

  - You should see: Pass: the `scrub` line ends with "no backup kept (no_backup)" when something was removed. Nothing is added to `state\office-backups`. Fail: any `NOT SAFE` line, read as in step 7.
**Step 9:** Before you send anything, look at the document's folder in File Explorer, and later tidy the backup folder: open the Fieldkit folder, then `state\office-backups`, and delete the date-and-time folders you no longer need. To get an original back instead, copy it from its folder over the cleaned file; `original.json` in the same folder says where it came from.
  - You should see: Pass: only the cleaned `report.docx` goes out, no `.bak` lies beside it, and old backups with your name are gone from the Fieldkit folder. Fail: a backup folder you meant to delete is still there.

## If Something Goes Wrong

**`fieldkit` is not recognised as the name of a cmdlet or program.**
Fieldkit is not installed, or it was installed for a different copy of Python.
What to do: Follow the install guide (`INSTALL.md` in the Fieldkit folder), then open a new PowerShell window and type `fieldkit host`.

**`FAIL` and "not a ZIP package (corrupt, or not really an Office file)".**
The file is damaged, or it only has a `.docx` ending but is something else, such as an old `.doc` renamed.
What to do: Open it in Word and use Save As to save a fresh `.docx`, then check the new file.

**"unsupported type .doc" (or `.xls`, `.ppt`, `.odt`).**
This group handles only the newer Office formats and PDF.
What to do: Open the file in Word, Excel or PowerPoint and save it as `.docx`, `.xlsx` or `.pptx` first.

**`create` cannot find `report.json`, or the folder shows `report.json.txt`.**
Notepad added `.txt` because "Save as type" was left on "Text documents".
What to do: Save again with "Save as type" set to "All files", or rename the file so it ends in `.json`.

**Several lines of error text ending with `FileExistsError` and "refusing to overwrite it (use --force to replace it)".**
A file with the output name already exists. `create` protects it.
What to do: Choose a new output name, or add `--force` if you really want the old file replaced.

**"spec type is 'docx' but the file name ends .pdf; name the output .docx or change the spec's type".**
The `type` in your spec and the ending of the output name disagree. Nothing was written.
What to do: Make the ending match the type, for example `type: docx` with `report.docx`, and run `create` again.

**"failed its own check and was not written".**
The new file did not pass the check, so Fieldkit threw it away. Any older file of the same name is untouched.
What to do: Read the problems listed after the message; they name what is wrong. Check the spec's blocks against the examples in "How to use this".

**`NOT SAFE` with `privacy` failed and "stale backup or lock file next to it".**
A `.bak`, `.wbk`, "Backup of" file, Word lock file (`~$...`) or leftover `.fieldkit-scrub-` file lies beside your document. These can hold the names that were removed.
What to do: Close the document in Office (that removes the lock file), delete or move the named file out of the folder, and run `deliver` again.

**`NOT SAFE` with `privacy` failed and "could not be read, so cannot be called clean".**
Part of the file was too large for the scanner, damaged, or packed in a way it cannot unpack. Fieldkit will not call an unread part clean.
What to do: Make the file smaller (for example, compress or remove large pictures) or save it again from Office, then run `deliver` again. If it is a large PDF, this tool cannot clear it; check it another way.

**`NOT SAFE` with `scrub` failed and "PDF properties hold names: Author".**
The PDF's own properties carry a name. Fieldkit can find it but cannot remove it.
What to do: Export the PDF again from the program that made it, with the author and title fields blank, then run `deliver` again.

**`fieldkit: PDF properties cannot be cleaned by Fieldkit` after `scrub` on a PDF.**
`scrub` refuses PDFs; only `scrub --check` and `deliver` look at them.
What to do: Use `fieldkit office scrub report.pdf --check` to see the names, then re-export the PDF as above.

**`NOT SAFE` with `privacy` failed and a kind such as `windows-user-path` or `email`.**
Something inside the file, often in the text or a link, matches a home-folder path, an email address, a secret or a private word.
What to do: Open the document, find and remove that item, save, close it, and run `deliver` again.

**The command ends with a permission error while removing names.**
Word, Excel or PowerPoint may still have the file open, so it cannot be replaced. The source does not handle this case specially.
What to do: Close the document in Office and run the command again.

**`read` on a PDF prints nothing, and `check` shows `has_text: False`.**
The PDF is a scan: a picture of text, not text.
What to do: This group cannot read it. Use a text-recognition (OCR) program first.

**"the following arguments are required: files" when you used `--term`.**
`--term` took your file name as one of its words.
What to do: Put the file name first: `fieldkit office scrub report.docx --term Smith`.

## Why a Developer Would Do This

The developer chose to work only with free, open helpers on your own computer so that private documents never go to an online converter. Every file `create` writes is checked before it is given its real name, so a broken file shows up as an error on your screen instead of as a complaint from the person you sent it to, and an existing file is never replaced by accident. Names come from your own settings file, never from the program itself, so the same tool works for anyone without carrying one person's details. The cleaning step copies Word's own "Remove Personal Information" behaviour so that the result is what an Office user would expect. The backup moved out of your document's folder because, as the source puts it, "a .bak beside the cleaned file still holds every removed name." And the privacy check now fails closed: a part it could not read is a reason to stop, never a reason to say safe.

## Why It Matters That You Can Read This

Removing names from documents is a job you cannot check by eye: the properties are hidden, and a tool that claims to clean them could miss a box, keep a copy somewhere unexpected, or quietly send your file elsewhere. Because the source is readable, anyone can confirm the exact list of boxes that `scrub` clears (Author, Last saved by, Company, Manager, plus Title, Subject, Keywords, Description and Category when they contain a private word), that the document text is never touched, where the backup goes (`state\office-backups` in the Fieldkit folder, not beside the file), that a cleaned file is checked before and after it replaces the original, and that nothing in the group sends data anywhere. The same reading shows the limits plainly: PDF properties are found but not cleaned, the backups keep the names until you delete them, and parts larger than the scanner's limit make the verdict `NOT SAFE`. Today's fixes themselves came from reading the source: a review found the old `.bak` beside the file, the silent skip of large parts and the unchecked PDF properties, and each is now covered by a test. With a closed tool you would be trusting a vendor's word on every one of those points.

## Glossary

**Terminal / PowerShell** — A window where you type commands instead of clicking.

**`.docx`, `.xlsx`, `.pptx`** — The file endings of current Word, Excel and PowerPoint documents.

**Metadata** — Information about a file, such as its author, stored inside it but not shown on the page.

**Tracked change** — An edit in Word that is recorded with the editor's name so others can accept or reject it.

**Formula** — A cell in Excel that starts with `=` and works out its value from other cells.

**Markdown** — Plain text with simple marks, such as `#` for a heading and `-` for a bullet point.

**JSON / YAML** — Two plain-text ways of writing structured information that a program can read.

**OCR** — Text recognition that turns a picture of words into real text; this group does not include it.

**Exit code 3** — The number `fieldkit` hands back to the computer when a check, a scrub report or a delivery finds a problem, so other programs can react.

**Backup folder** — The place inside the Fieldkit folder, `state\office-backups`, where originals are kept before their names are removed.

**Lock file** — A small hidden file starting with `~$` that Office puts beside a document while it is open, holding the editor's name.

**Fail closed** — Treating anything that could not be checked as a failure, never as a pass.

**XMP** — A block of details inside a PDF, such as its creator and title, kept apart from the pages.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Nothing is uploaded by the built-in readers | 📄 stated in input | Nothing is uploaded anywhere. |
| Checker's XML reader cannot reach the network | 📄 stated in input | etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False) |
| Created files carry no author unless the spec sets one | 📄 stated in input | author/last-modified-by are written EMPTY unless the spec sets |
| create never overwrites without --force | 📄 stated in input | An existing `out` is never overwritten unless force=True. |
| create refuses a type that does not match the file name ending | 📄 stated in input | name the output .{kind} or change the spec's type |
| create writes under a temporary name and leaves nothing on a failed check | 📄 stated in input | failed its own check and was not written |
| Scrub never changes the document text | 📄 stated in input | Never touched: the document's own text. |
| Word writes the name back on save, so scrub must be last | 📄 stated in input | Word writes the name back on every save, so scrub is the LAST step. |
| Review-mark names become Author, like Word's own feature | 📄 stated in input | become "Author" - the same thing Word's own "Remove Personal Information" does |
| The backup goes to Fieldkit's state folder because a .bak beside the file leaks names | 📄 stated in input | a .bak beside the cleaned file still holds every removed name. |
| scrub checks the cleaned copy and puts the original back on failure | 📄 stated in input | on failure the original is put back. |
| PDF properties are inspected but cannot be cleaned | 📄 stated in input | PDF properties cannot be cleaned by Fieldkit |
| Changing PDF properties with the available libraries would leave the old values | 📄 stated in input | which leaves the old values in the file |
| deliver fails on a stale backup or lock file beside the document | 📄 stated in input | stale backup or lock file next to it |
| deliver fails when a part could not be read | 📄 stated in input | could not be read, so cannot be called clean |
| deliver honours no_backup | 📄 stated in input | no backup kept (no_backup) |
| read keeps table rows together | 📄 stated in input | a blank line between rows ends the table |
| A broken file is never touched by deliver | 📄 stated in input | Stages: check (not broken) -> scrub |
| PDF text check covers only the first five pages | 📄 stated in input | range(min(len(doc), 5)) |
| New Excel formulas have no saved answer | 📄 stated in input | formulas with no cached value are flagged |
| read refuses .docm and .pptm | 📄 stated in input | SUPPORTED = (".docx", ".xlsx", ".xlsm", ".pptx", ".pdf") |
| Whole test suite result and time | 📄 stated in input | 584 passed, 1 skipped, 2 xfailed in 194.89 s |
| The privacy scanner's 5,000,000-byte limit and its fail-closed reporting (read from the core privacy scanner, outside this group) | 🤖 model inference | *(none — model judgment)* |
| Backups are never deleted automatically and accumulate in the Fieldkit folder | 🤖 model inference | *(none — model judgment)* |
| There is no command to restore a backup; restore() has no CLI entry point | 🤖 model inference | *(none — model judgment)* |
| An open Office document makes deliver fail through its ~$ lock file | 🤖 model inference | *(none — model judgment)* |
| The overwrite refusal appears as a Python traceback because the CLI does not catch FileExistsError | 🤖 model inference | *(none — model judgment)* |
| SAFE TO SEND can give a false sense of safety for names not on the list | 🤖 model inference | *(none — model judgment)* |
| A file open in Office causes a permission error during scrub | 🤖 model inference | *(none — model judgment)* |
| --term placed before the file name swallows it | 🤖 model inference | *(none — model judgment)* |
| Behaviour after a power cut during the swap is not described | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*