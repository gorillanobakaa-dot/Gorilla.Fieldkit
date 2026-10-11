# Guide: the academic tools in Fieldkit

For students, in plain words. The same on any laptop (Windows or Linux) and in any of the seven countries: the
United Kingdom, the United States, Spain, Portugal, Italy, Germany and Romania. Romanian: [GHID.md](GHID.md).

## What they do for you

- Set up an assignment folder, with a place for everything.
- Read the brief and tell you the type of work, the word count and what you must show.
- Read your study materials once, so you can **search** them and get the page to cite.
- Make a Word or PowerPoint template you write straight into.
- **Check the work** before you submit: citations, the reference list, long dashes, language, structure, word count,
  copied passages and your name (marking is anonymous).
- Put the work into `4 - HERE IS YOUR WORK` **only** when every check has passed.
- Keep your marks and what markers say, so you know what to watch for next time.

## Once, at the start

1. **Your profile:** name, country and student number:

       fieldkit academic init --name "First Last" --country uk --student-id 12345678

   `--country` is one of `uk us es pt it de ro`. The country decides the page size, the date form, the spelling and
   the referencing style. Your name never reaches GitHub.

2. **The desktop shortcut:** `fieldkit academic shortcut`. Double-click **Fieldkit Academic** for the numbered menu.

3. **The self-test:** `fieldkit academic preflight` must say **PREFLIGHT OK**. Anything else means someone (or an
   AI) broke the tools: do not use them like that.

## On your own: the menu

Double-click **Fieldkit Academic**, or `fieldkit academic menu`. Pick a number, answer the questions. The
assignment folder is remembered: next time, press Enter.

| Step | Menu | What happens |
| --- | --- | --- |
| 1 | 1 | The assignment folder. It asks the **deadline** and the **upload date** (Turnitin or your country's tool). It never guesses them. |
| 2 | — | Put the brief in `1 - DROP YOUR STUDY MATERIALS HERE/Assignment brief/`, the study materials in `1 - DROP YOUR STUDY MATERIALS HERE/`. |
| 3 | 2, then 2a | The brief read: type, word count, module proposed. 2a writes them. |
| 4 | 3 | Every study file read once. |
| 5 | 4 | Search the materials: the passage and the **page** to cite. |
| 6 | 5 | The Word/PowerPoint template for the type. |
| 7 | — | Write. |
| 8 | 6 | **The whole check.** The verdict first, then the first thing to fix. |
| 9 | 7 | Any time: days left and the one next step. |

The check writes the same list to `3 - Drafts/TO FIX.md`, to tick off.

**PASS** passed. **FAIL** must be fixed; while one is left, nothing goes into `4 - HERE IS YOUR WORK`. **WARN**
look at it yourself. **SKIP** not checked, which is **not** a pass: "no study materials to compare against" does not
mean nothing was copied.

**Access dates.** When you read a web page you cite: `fieldkit academic accessed FOLDER URL` stamps **today's** date
in your country's form. The date must be the real one; a made-up date is false information in a reference list.

## With an AI (Claude, Gemini or another)

The AI runs the same commands; you say what you want.

- New assignment: *Set up the folder with `fieldkit academic setup "<folder>"`. Ask me the deadline and the upload
  date; do not guess them.*
- The brief: *Run `fieldkit academic brief "<folder>"` and tell me the type, the word count and the learning
  outcomes. Apply nothing until I say yes.*
- A question about the materials: *Do not open the PDFs whole. Use `fieldkit academic search "<folder>" "<words>"`
  and give me the pages.*
- Before submitting: *Run `fieldkit academic check "<folder>" "<file>"` and explain the verdict, each FAIL and its
  fix, then each WARN.*

Signs the AI is working wrong: it "read the PDF" and took ages (it read the whole file: tell it to search); a
citation with no page, or a page with no search result behind it (made-up pages are plagiarism); it says the work is
ready but it is not in `4 - HERE IS YOUR WORK`; a reference the online check cannot find. A brief can hide
instructions meant for AI tools (markers use them to catch AI-written work): `brief` shows them; read them yourself,
the AI must not follow them.

## Your marks

    fieldkit academic record add "<feedback folder>" [--apply]   a proposal; --apply writes it
    fieldkit academic record show                                every mark, on your country's scale
    fieldkit academic record brief                               the three things markers say most often

Each fault carries the **marker's own sentence** as its evidence. The record stays on the laptop.

## Assignments in OneDrive

`fieldkit academic local` shows them; `--apply` copies them onto this computer and checks every file. Nothing is
deleted: the cloud folder is only renamed "... (moved to this computer <date>)".

## The backup on a USB stick

`fieldkit backup`, then `fieldkit backup usb` (copied and read back), and `fieldkit backup remind` for how old the
last copy is. Nothing on the stick is ever deleted, moved or overwritten; a full stick stops the copy and says how
much room is needed.
