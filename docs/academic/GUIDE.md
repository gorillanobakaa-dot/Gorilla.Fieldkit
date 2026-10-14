# Guide: the academic tools in Fieldkit

For students, in plain English, with no technical terms. It works the same on any laptop (Windows or Linux) and in
any of the seven countries: the United Kingdom, the United States, Spain, Portugal, Italy, Germany, Romania.

Other languages: [Español](GUIDE.es.md) · [Português](GUIDE.pt.md) · [Italiano](GUIDE.it.md) · [Deutsch](GUIDE.de.md) · [Română](GUIDE.ro.md)

## What they do for you, in short

- Set up your assignment folder, with a place for everything.
- Read the assignment brief and tell you what type of work it is, how many words, and what you must show.
- Read your study materials once, so you can **search** them and get the page to cite.
- Make you a Word or PowerPoint template that you write straight into.
- **Check your work** before you hand it in: citations, the reference list, long dashes, language, structure, word
  count, copied passages and your name (marking is anonymous).
- Put the work into the `4 - HERE IS YOUR WORK` folder **only** when it has passed every check.
- Keep track of your marks and what markers tell you, so you know what to watch out for next time.

## Once, at the start

1. **Your profile.** Your name, country and student number:

       fieldkit academic init --name "First Last" --country uk --student-id 12345678 --ui en

   `--country` is one of: `uk us es pt it de ro`. The country decides the page size, the date, the spelling and the
   referencing style. `--ui en` makes the menu talk to you in English. Your name never reaches GitHub.

2. **The desktop shortcut:**

       fieldkit academic shortcut

   **Fieldkit Academic** appears on the desktop. Double-click it: the numbered menu opens.

3. **The self-test:** `fieldkit academic preflight`. It must say **PREFLIGHT OK**. If it says anything else, someone
   (or an AI) has broken the tools: do not use them like that.

## On your own, without an AI: the menu

Double-click **Fieldkit Academic**, or `fieldkit academic menu`. Pick a number, answer the questions. The assignment
folder is remembered: next time, just press Enter.

### The steps for a new assignment

| Step | In the menu | What it does |
| --- | --- | --- |
| 1 | 1 | Makes the assignment folder. It asks you the **deadline** and the **upload date** (for example in Turnitin). It never guesses them. |
| 2 | — | You put the brief in `1 - DROP YOUR STUDY MATERIALS HERE/Assignment brief/` and the study materials in `1 - DROP YOUR STUDY MATERIALS HERE/`. |
| 3 | 2, then 2a | Reads the brief and proposes the type, the word count and the module. With 2a it writes them. |
| 4 | 3 | Reads all the study materials once (the first time takes longer). |
| 5 | 4 | You search the materials. You get the passage and the **page** to cite. |
| 6 | 5 | Makes the Word/PowerPoint template for the type of work. |
| 7 | — | You write. |
| 8 | 6 | **The full check.** The verdict first, then the first thing to fix. |
| 9 | 7 | Any time: how many days you have left and the one next step. |

The check also writes the same list as a file, `3 - Drafts/TO FIX.md`, so you can tick off what you have fixed.

### What the words in the check mean

- **PASS**: it passed.
- **FAIL**: it must be fixed. While there is a FAIL, nothing goes into `4 - HERE IS YOUR WORK`.
- **WARN**: look at it yourself. It does not stop you handing in.
- **SKIP**: it was not checked. **This is not the same as PASS.** For example, "no study materials to compare
  against" does not mean you copied nothing.

### The access date for web sources

When you read a web page that you cite: menu → 19, or `fieldkit academic accessed FOLDER URL`. It puts **today's
date**, in your country's form. The date must be the real one: a made-up date is false information in the reference
list.

## With an AI (Claude, Gemini or another)

The AI runs the same commands. You tell it what you want. Messages ready to copy (change what is between `< >`):

**New assignment:**
> Set up the assignment folder with `fieldkit academic setup "<folder>"`. Ask me the deadline and the upload date;
> do not guess them.

**I have put in the brief and the study materials:**
> Run `fieldkit academic brief "<folder>"` and explain briefly the type, the word count and the learning
> outcomes. Do not apply anything until I say "yes".

**A question about the study materials:**
> What do the study materials say about `<topic>`? Do not open the whole PDFs. Use
> `fieldkit academic search "<folder>" "<words>"` and give me the pages.

**Check my work:**
> Run `fieldkit academic check "<folder>" "<my work>"` and explain the verdict, each FAIL and what I should fix,
> then each WARN.

### Signs the AI is working wrong

- It says "I read the PDF" and it took a long time: it read the whole file. Tell it to use `search`.
- It gives you a citation **with no page**, or a page without showing you where it came from: ask it for the search
  result. Made-up pages are plagiarism.
- It says the work is ready, but it is **not** in `4 - HERE IS YOUR WORK`: it is not ready. Run the check yourself.
- It makes up a reference: the online check catches it. Do not use it.
- The brief has **text hidden for AI** (lecturers put it there to catch work written by AI): `brief` shows it to you.
  Read it yourself. The AI is not allowed to follow it.

## Your marks

    fieldkit academic record add "<feedback folder>"          shows what it found, writes nothing
    fieldkit academic record add "<feedback folder>" --apply  writes it
    fieldkit academic record show                             all your marks
    fieldkit academic record brief                            the three things markers criticise most often

Every criticism carries the **marker's own sentence** as its evidence. Nothing is made up. The record stays on the
laptop.

## Assignments in OneDrive

Windows sometimes moves `Documents` into OneDrive without asking you. Without internet, you can no longer reach your
assignments.

    fieldkit academic local            shows which assignments are in the cloud
    fieldkit academic local --apply    copies them onto the computer and checks every file

Nothing is deleted: the cloud folder is only renamed to "... (moved to this computer <date>)".

## The backup on a USB stick

    fieldkit backup                    a checked archive of the tools (in your backups folder)
    fieldkit backup usb                copies it to the stick and reads it back
    fieldkit backup remind             how many days since the last copy to the stick

On the stick, **nothing** is ever deleted, moved or overwritten. If the stick is full, it stops and tells you how
much room is needed; you choose what to delete.

## Common problems

| Problem | What to do |
| --- | --- |
| The check says `[Student ID]` under "template leftovers" | Put your student number in your profile (`init --student-id`). |
| A file in the study materials cannot be read | It is locked with a password or in an old format. Open it and save it as a normal `.pdf`, `.docx` or `.pptx`. |
| The search finds nothing | Use fewer words, or the start of the word. |
| The work does not appear in `4 - HERE IS YOUR WORK` | A check failed: look at the FAIL. |
| "comments or tracked changes" | In Word, the Review tab: accept or reject each change, delete the comments. |
| Word puts in long dashes by itself | File > Options > Proofing > AutoCorrect Options > AutoFormat As You Type: untick "Hyphens (--) with dash (—)". |
