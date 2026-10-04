# How Fieldkit makes sure every part of it is explained twice: once for you, once for programmers — Plain Language Guide

> Generated 2026-10-04 from `documentation`

---

## Should You Run This?

Run it if you change Fieldkit's code or its documentation, or before a release, because it tells you which explanations are out of date and refuses pages that break the rules. `fieldkit docs check` and `fieldkit docs plan` only read files and are safe to run at any time.

Do not run prep or render while another person or program is editing the same group's pages, because both overwrite files in `docs/dual-track/<group>` and `docs/_staging/<group>`. Do not treat a `PASS` as proof that a page is true: it proves the page has the required shape and sourced facts, nothing more. It has not been tested on Linux in this form.

## Worst Case, Honestly

The most harmful realistic outcome is a page that passes every check and is still wrong. The checks count and compare; they cannot tell whether a sentence is true. A writer can describe a feature that does not behave as described, and as long as every section is long enough, every number is sourced and every command exists, the page passes. You would then follow published instructions that do not match the program. That is why every page ends with a table that marks which claims were copied from the code and which were the writer's own judgement.

A second, smaller harm is lost work on your own disk. Each run deletes and remakes `docs/_staging/<group>`, so anything you saved in that folder is gone. Producing the pages again overwrites the rendered pages, so a correction typed straight into a page is lost; corrections belong in the filled form. A failed render also overwrites the page with the failing version; `git` shows the change and the receipt is not updated.

## What Data This Touches

This part of Fieldkit stays on your computer. It reads Fieldkit's own code files (everything under the `fieldkit` and `tests` folders), the group list `docs/groups.yaml`, the measured facts in `docs/dual-track/MEASUREMENTS.md`, and your private settings file `fieldkit.local.json`, from which it takes your list of private words (your name, your email) so it can search the pages for them. It also reads the environment setting `FIELDKIT_DUAL_TRACK` if you set one.

It writes inside the Fieldkit folder only: copies of the code into `docs/_staging/<group>` (that folder is deleted and made again each time), the work orders, the rendered pages, the receipts (`STATE.json`) and the index page `docs/dual-track/README.md`. The pipeline keeps its progress in `state/gorilla-documentation-ibm-style`.

It starts one other program: `dual_track.py`, the page generator, with your own Python. That program runs a few read-only `git` commands to describe the project, so a work order contains the project's public web address and the subjects of recent saved changes. Fieldkit itself makes no network connection, and the generator's own description says it makes none in prep or render. Nothing is uploaded anywhere. If you hand a work order to an online AI service, that is your choice, and the work order then leaves your computer with the code inside it.

## Before You Trust It

Before you rely on the pages this tool accepts, check that the checker really refuses bad pages and that it runs the same way on your computer. You need a PowerShell window open in the Fieldkit folder for every step: press the Windows key, type `PowerShell`, press Enter, then go to the Fieldkit folder with `cd` followed by the folder's location in quotes.

**Step 1:** Run the checker's own tests. Type this and press Enter:

```powershell
python -m pytest tests/test_gdocs.py -q
```
  - Look for: **Pass:** the last line says `passed` and does not say `failed`. These tests feed the checker an invented command, a thin page, an unsourced number, a home folder and a fence in the wrong place, and confirm each one is refused.
  - **Fail:** any `failed` or `error`. Do not trust the checker until that is fixed.
**Step 2:** Check every published page. Type this and press Enter:

```powershell
fieldkit docs check
```
  - Look for: **Pass:** the first line is `coverage: OK`, every group has a `PASS` line, and the last line counts the groups that pass. Lines starting with `~` are stale warnings, not failures.
  - **Fail:** a `FAIL` line followed by the reasons, or `coverage: FAIL` with the names of code files that belong to no group.
**Step 3:** See which explanations are out of date. Type this and press Enter:

```powershell
fieldkit docs plan
```
  - Look for: **Pass:** a line per group with `fresh`, `stale` or `never`, the files that changed for a stale group, and a final `NEXT:` line telling you what to do.
  - **Fail:** an error instead of the table. A `stale` group is not a failure of the tool; it tells you that page may describe older code.
**Step 4:** Read the rule book the writers receive. Type this and press Enter:

```powershell
notepad fieldkit\gdocs\WRITER_BRIEF.md
```
  - Look for: **Pass:** Notepad opens a page that starts with "Gorilla writer brief" and quotes the maintainer's requirement. You can compare its rules with any published page.
  - **Fail:** Notepad says the file does not exist: your copy of Fieldkit is incomplete.
**Step 5:** Open any receipt, for example the one for the exam group, in Notepad:

```powershell
notepad docs\dual-track\exam\STATE.json
```
  - Look for: **Pass:** you see `gorilla_check` with `PASS`, a `rendered` date and a list of code files with long fingerprints.
  - **Fail:** `gorilla_check` shows `FAIL`, or the file is missing: that group's pages were never accepted by this tool.

## The Big Picture

Fieldkit is a collection of small tools. Its code is split into groups, such as the office tools, the build tools and the privacy checkpoint for the browser. Each group gets two written explanations: a plain-language one for someone who has never opened a terminal (this kind of page), and a technical one for someone who wants to check or change the code. Neither is a short version of the other. They are the same truth in two languages.

This part of Fieldkit is the machinery that produces those two explanations and refuses to accept them when they are not good enough. It works like a publishing house with a strict editor. First it hands a writer a work order: the code, the facts that were measured, and a rule book called the writer brief. The writer can be an AI model or a person. The writer fills in a long form. Fieldkit then turns the form into a readable page and has the page inspected against a checklist before it is accepted.

The inspection is done by rules, not by opinion. It counts sections and words, it checks that every everyday comparison is there, that every command shown to you exists, that every number came from a real measurement or the code itself, and that nothing personal (a home folder, an email address, a real name) slipped into a page that will be published. If any rule fails, the page is refused and every reason is listed. It also keeps a receipt for every group, so it can tell you when the code has changed since the explanation was written.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `Group` | A set of Fieldkit's code files that are explained together, such as all the files about Office documents. The list of groups lives in `docs/groups.yaml`. | One chapter of a car manual: the brakes chapter covers every brake part, and no part appears in two chapters. |
| `Layman track and developer track` | The two explanations every group gets: one for you, one for programmers. Both must be complete. | A medicine leaflet for the patient and the detailed data sheet for the pharmacist, printed from the same facts. |
| `Prep file (`.prep.json`)` | The work order for the writer: the code of the group, the measured facts, the form to fill in and the writer brief. | A job sheet a garage hands a mechanic, with the car's history clipped to it. |
| `Filled form (`.filled.json`)` | The writer's answers, one for each track, saved as a structured file that a program can read. | A completed tax return: every box filled in, ready for the office to process. |
| `Render` | Turning the filled form into the readable page you are reading now, then inspecting it. | A printer turning a typed manuscript into a finished book, followed by the proofreader's check. |
| `Gorilla checks` | The fixed list of rules every page must pass: sections present and long enough, a comparison for every concept, real commands, sourced numbers, no personal data. | A driving test examiner with a printed checklist: the same items, in the same order, for every candidate. |
| `Stale` | A group whose code changed after its explanation was last produced. The explanation may now be wrong. | A shop price label that was printed before the price went up. |
| ``STATE.json`` | A receipt kept beside each group's pages. It records a fingerprint of every code file at the moment the pages were produced, the date, and the scores. | The stamped service record in a car's logbook that shows what was checked and when. |
| ``MEASUREMENTS.md`` | The one file that lists facts somebody really measured, with the date. Any other number in a page must come from the code or carry the words "not measured". | The calibrated scales at a market: if a weight was not measured there, the trader may not call it measured. |
| `Writer brief` | The rule book every writer receives, built from the Gorilla philosophy and the maintainer's own requirements. It is pasted into every work order. | A newspaper's style guide that every journalist gets on day one. |
| `Pipeline and `fieldkit next`` | The same steps written as a fixed sequence, and a command that tells you the one next thing to do. | A recipe card with numbered steps, and a friend who reads out only the next line. |

## How It Works — Step by Step

### Step 1: Read the map of groups

Fieldkit opens `docs/groups.yaml`, which lists every group, its title and which code files belong to it. It then lists every code file in the `fieldkit` folder and checks that each one belongs to exactly one group. Files named `__init__.py` and `__main__.py` are allowed to be left out, because they are mostly signposts. A file that belongs to no group is an orphan, and the check fails with the list of orphans. Think of a library checking that every book on the shelves appears in the catalogue exactly once.

### Step 2: Decide which groups are out of date

For each group, Fieldkit takes a fingerprint of every file (a SHA-256 hash, a long code that changes completely if one letter of the file changes) and compares it with the fingerprints saved in that group's `STATE.json`. Equal fingerprints mean fresh. Different ones mean stale. No receipt at all means never produced. Line-ending differences between Windows and Linux are ignored, so the same code counts as the same on both. This is like comparing the seal number on a delivered parcel with the number on the receipt.

### Step 3: Prepare the work order (prep)

For each stale group, Fieldkit copies the group's files into `docs/_staging/<group>`, flattening folders into the file name (`build/kernel.py` becomes `build__kernel.py`), because the page generator skips any folder named `build`. It then runs the generator's prep step with `MEASUREMENTS.md` as the list of verified facts. The generator writes two work orders, one per track. Fieldkit then edits each one: it corrects the follow-up command (the generator suggests a `--validate` option that its own render step does not accept), writes the answer path relative to the Fieldkit folder so no home folder appears, and pastes the whole writer brief at the end of the instructions. It also stamps the work order with the fingerprints and the brief's own fingerprint, so a second prep with nothing changed does nothing.

### Step 4: The writer fills in the form

This is the only step Fieldkit cannot do for you. An AI model or a person reads the work order and writes one structured answer file per track. `fieldkit docs fill` inspects those files without changing them: does each exist, was it written after its work order, is it valid, does it contain every required part, and does it avoid the banned marketing words. Until every file is right, the pipeline stops here and names the exact files that are missing, like a form office that hands back an application with each empty box circled.

### Step 5: Produce the pages (render)

Fieldkit copies the code into the staging folder again and runs the generator's render step. Before it starts, it refuses answers that were written for older code: if the work order no longer matches the code, or an answer file is older than its work order, render stops with that reason and nothing is marked as finished. The generator turns each answer file into a page, checks it with its own rules and gives it a quality score out of 100. A score below its threshold, or a missing section, makes the generator report failure, and Fieldkit records that as a reason.

### Step 6: Inspect every page (the Gorilla checks)

Fieldkit then reads each finished page and applies its own checklist. For your track: every required section is present and longer than its minimum word count, there are numbered steps for checking the tool before you trust it, every key concept has an everyday comparison, the how-to section has its commands in separate boxes and tells you how to open PowerShell, there are troubleshooting entries and a glossary, and limits are stated. For both tracks: every `fieldkit` command shown must exist in the real program, every number must appear in `MEASUREMENTS.md` or in Fieldkit's code or tests or stand within 80 characters of the words "not measured", every web address must appear in the code or the measurements, and the privacy scan must find no home folder, email address or private word. A short list of hosted AI assistant product names and the phrase `the owner` are refused, except inside quotations copied word for word from the code. The minimum word counts were measured on the ten pages that existed before this checker, and each sits about one fifth below the thinnest page accepted then.

### Step 7: Keep the receipt or refuse

If the generator and every check passed, Fieldkit writes the group's `STATE.json` with the fingerprints, the date, the scores and the check result. The group is now fresh. If anything failed, it prints every reason, ends with exit code 3, and does not record the new fingerprints, so the group stays stale until it is fixed. A check that cannot pass never quietly passes.

### Step 8: Check everything and publish the index

`fieldkit docs check` repeats the coverage check and the page checks on every group without producing anything. This is what an automated check before a release calls. A stale group is reported as a warning, or as a failure with `--strict`. `fieldkit docs index` then writes `docs/dual-track/README.md`, a table with links to both pages of every group, the scores, the last render date, the check result and whether the group is stale.

## Quirky Things Worth Knowing

### What this cannot do

It cannot judge whether a sentence is true, only whether the page has the required shape and sourced facts. It does not write the pages; a model or a person does. It cannot tell whether an everyday comparison is a good one, only that it exists and has at least four words. Its number rule accepts a number that appears anywhere in Fieldkit's code or tests, so an invented number that happens to exist somewhere else passes. Its command rule checks that the command, its action and its options exist; it does not check that the values you type after them make sense. It has not been tested on Linux in this form.

### A change to the code makes the explanation stale at once

Change one letter in one file of a group and that group is stale, even if the change does not matter to the explanation. The checker cannot tell which changes matter, so it treats every change as a reason to look again.

### Re-preparing a group makes its old answers out of date

When a work order is made again, any answer file written before it is reported as older than its work order and must be rewritten or at least saved again after checking. This is on purpose: the code in the work order may have changed.

### Ten pages were adopted, not produced by this tool

The pages that existed before this tool were written by hand-run steps. Their receipts record the code they were written from, marked as adopted. Where the code has changed since, those groups show as stale until somebody refreshes them.

### A wrong command may be quoted, if the page says it is wrong

Sometimes the code itself names a command that does not exist. A page may quote it to warn you, but the same line must say "not registered" or "does not exist".

### A longer guide for writers comes with the tool

The checks in this tool can count sections and find banned words. They cannot tell whether a comparison is true or whether a step is small enough to follow. So the tool also carries a long written guide, in the file `LAYMAN_GUIDE.md`, that explains why each rule exists and shows wrong and right examples. It is like the difference between a marking sheet and a teacher: the marking sheet says what is missing, the teacher says how to do it. To read it, type `fieldkit docs guide` and press Enter. That command prints the guide and changes nothing on your computer. The guide is written for any project, not only for Fieldkit. Whether writers who read it produce better pages is not measured.

## What This Means For You

### Battery, Processor & Memory

Not measured. The checks read text files and are light work; the page generator is a separate Python program that runs once per prep or render.

### Speed

Not measured. Each call to the page generator is stopped if it runs longer than 600 seconds.

### Your Privacy

It protects your privacy instead of using it: it searches published pages for your home folder, email addresses and the private words in your own settings file, and refuses a page that contains them. The work orders contain the project's code and recent change subjects and stay on your computer unless you send them somewhere.

### Your Internet

None from Fieldkit. Not measured for the page generator, whose description says prep and render make no network call.

## The Off Switch

**What it is:** There are three. Pressing `Ctrl+C` stops any command. Nothing is published by this tool: pages only become public when you save them to the project's history with `git` and send them. And a failed check stops the line: the receipt is not updated and the command ends with exit code 3.

**Without it:** Without the refusal on failure, a page missing its safety sections or carrying an invented number would be marked as finished, and nobody would see the gap until a reader was misled by it.

**Think of it like:** A factory line where any inspector can pull the red cord: the line stops, the faulty item is labelled with the reason, and nothing leaves the building until it is fixed.

## How to use this

**Before you start:**
- Fieldkit installed on Windows, so that `fieldkit --version` prints a version.
- The page generator `dual_track.py` on this computer, in `Documents\Scripts\DualTrackAgent`, or gathered with `fieldkit gather --only dual-track-doc-generator`.
- A model or a person ready to fill in the forms. Fieldkit does not do that step.
- About ten minutes for the commands; the writing itself takes longer, and how long is not measured.

**Step 1:** Open PowerShell: press the Windows key, type `PowerShell`, and press Enter. Then go to the Fieldkit folder. Type this, with your own folder's location between the quotes, and press Enter:

```powershell
cd "<your Fieldkit folder>"
```
  - You should see: A window with a blinking cursor, and a prompt line that ends with the Fieldkit folder's name and `>`.
**Step 2:** Ask what to do next. Type this and press Enter:

```powershell
fieldkit next gorilla-documentation-ibm-style
```
  - You should see: **Pass:** one instruction, such as `DO: fieldkit pipeline run gorilla-documentation-ibm-style --only plan`, with a `why:` line under it. If it says `BLOCKED`, it names the exact files a writer must fill.
  - **Fail:** `no pipeline` means your Fieldkit is older than this tool.
**Step 3:** See every group and its state. Type this and press Enter:

```powershell
fieldkit docs plan
```
  - You should see: **Pass:** a list of the steps, then one line per group with `fresh`, `stale` or `never`, then `coverage:` and a `NEXT:` line.
**Step 4:** Prepare the work orders for one group, here the group that documents this tool. Type this and press Enter:

```powershell
fieldkit docs prep documentation
```
  - You should see: **Pass:** `documentation prepared` (or `current` if nothing changed), followed by two `fill:` lines naming the files the writer must write.
  - **Fail:** `failed` with the generator's own message, or `no group` if the name is mistyped.
**Step 5:** Give each work order (`docs\dual-track\documentation\documentation_layman.prep.json` and the developer one) to the writer. When the writer has saved the answers, check them. Type this and press Enter:

```powershell
fieldkit docs fill documentation
```
  - You should see: **Pass:** two lines starting with `OK`.
  - **Fail:** a line starting with `WRITE` and the file name, with the reason under it, such as `not written yet`.
**Step 6:** Produce and inspect the pages. Type this and press Enter:

```powershell
fieldkit docs render documentation
```
  - You should see: **Pass:** a line starting with `PASS` and the two scores.
  - **Fail:** a line starting with `FAIL` and every reason under it. Ask the writer to fix the answer file, never the page, then repeat this step.
**Step 7:** Check everything and refresh the index page. Type these two commands, pressing Enter after each:

```powershell
fieldkit docs check
fieldkit docs index
```
  - You should see: **Pass:** `coverage: OK`, a `PASS` line for every group, then `wrote docs/dual-track/README.md`.

## If Something Goes Wrong

**`coverage: FAIL orphans=[...]` with a code file name**
Someone added a new code file and did not say which group it belongs to.
What to do: Add the file to the right group's `sources` list in `docs/groups.yaml` with Notepad. That group then shows as stale and needs prep, fill and render.

**`dual_track.py not found` when you run prep or render**
Fieldkit cannot find the page generator in any of the places it looks.
What to do: Gather it with `fieldkit gather --only dual-track-doc-generator`, or tell Fieldkit where it is with the setting `FIELDKIT_DUAL_TRACK` or `"dual_track"` in `fieldkit.local.json`.

**`FAIL` with `number ... is in neither MEASUREMENTS.md nor the group's source`**
The writer used a number that nobody measured and the code does not contain.
What to do: Ask the writer either to cite the measurement or to put "not measured" next to the number, then render again.

**`FAIL` with `command does not parse`**
The page shows a `fieldkit` command, action or option that does not exist.
What to do: Ask the writer to correct it; `fieldkit --help` lists the real commands.

**`older than its prep file` in the fill check**
The work order was made again after the answer was written, usually because the code changed.
What to do: Ask the writer to check the answer against the new work order and save it again.

**`no group` followed by a list of names**
The group name was mistyped.
What to do: Use a name exactly as `fieldkit docs plan` prints it.

## Why a Developer Would Do This

The Gorilla philosophy says documentation is the product, not an extra, and that a person who cannot read code still deserves a complete and honest explanation. Rules that live only in somebody's head get forgotten, so before this tool every writer had to be told the same rules by hand. Writing the rules down in a brief, and checking them by machine, means every page gets the same standard. The maintainer chose to make failure loud: a page that misses a rule is refused with every reason, because a quiet pass on a weak page would teach readers to trust something they should not.

## Why It Matters That You Can Read This

Documentation is where most people meet a project, and documentation is usually the least checked part of it. Here the rules a page must meet are written in code you can read (`fieldkit/gdocs/checks.py`) and in a plain rule book (`fieldkit/gdocs/WRITER_BRIEF.md`). You can see the exact minimum word counts, why each was chosen, which words are refused and how a number is judged. If a page passes, you can repeat the check yourself with one command and get the same answer. If you could not read these rules, you would have to trust that someone had looked at the pages carefully. Because you can, you can hold the pages to the standard they claim.

## Glossary

**Terminal / PowerShell** — A window where you type commands instead of clicking; on Windows it is called PowerShell.

**Command** — A line of text you type and confirm with Enter, such as `fieldkit docs plan`.

**Exit code** — A number a program leaves behind when it ends: 0 means fine, 3 means it found problems.

**Fingerprint (SHA-256 hash)** — A long code computed from a file's content that changes completely when the file changes.

**JSON** — A plain-text format for structured answers that both people and programs can read.

**Markdown** — A plain-text format for pages, where `##` marks a heading and backticks mark commands.

**Staging folder** — A temporary folder where Fieldkit puts copies of the code for the page generator; it is never published.

**Repository** — The project folder whose history `git` keeps, and from which public copies are made.

**Release gate** — A check that must pass before a new version is published.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| A page that fails any check ends with exit code 3 and keeps the group stale | 📄 stated in input | a failed render never records fresh hashes |
| The writer brief is appended to every prep file's instructions | 📄 stated in input | give every writer the same rules: the Gorilla writer brief, appended to the instructions |
| The generator's suggested --validate option is not accepted by render | 📄 stated in input | dual_track writes `--validate`, which render does not accept |
| Folders are flattened because the generator skips folders named build | 📄 stated in input | dual_track.py skips any folder named build |
| Each generator call stops after 600 seconds | 📄 stated in input | timeout=600 |
| Numbers in code blocks and fieldkit commands are treated as your input, not claims | 📄 stated in input | Commands are example input the reader types |
| Line-ending differences do not make a group stale | 📄 stated in input | so a Windows checkout with other line-ending |
| A stale group is a warning unless --strict is given | 📄 stated in input | check: a stale group is a failure (release gate) |
| The word floors sit about one fifth below the thinnest page found | 📄 stated in input | Each floor sits about 20 per |
| A page can pass every check and still be wrong | 🤖 model inference | *(none — model judgment)* |
| An invented number that exists elsewhere in the code passes the number rule | 🤖 model inference | *(none — model judgment)* |
| Running prep or render alongside another editor can overwrite their work | 🤖 model inference | *(none — model judgment)* |
| Not tested on Linux in this form | 🤖 model inference | *(none — model judgment)* |
| `fieldkit docs guide` prints a guide and changes nothing | 📄 stated in input | changes nothing and needs no group, so it works in any folder |
| The guide's effect on the quality of pages is unknown | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*