# The sitrep: a progress report you can read in pieces, checked against written rules before you see it — Plain Language Guide

> Generated 2026-10-14 from `sitrep`

---

## Should You Run This?

Run it. It reads one file you name and three of its own, writes nothing, connects to nothing and needs no special rights. Do not rely on it to judge whether a report is true or whether an ask is fair: it checks shape, not substance. Do not use it as the only reader of a report written by a program on your behalf; read the one line yourself. If you or your reader prefer the request at the end, use --order classic from the start instead of fighting the default.

## Worst Case, Honestly

The most harmful plausible outcome is a report that passes every rule and is still wrong, because the tool checks the shape of the words and not their truth. If a program writing the report puts "The backup is complete." on the one line when the backup failed, the check passes, the report prints, and you act on a false sentence: for example, you wipe a disk you believed was backed up. The tool cannot see that. A second, smaller harm: the rules are opinions written in a file, and if a rule is wrong for a particular reader (say, a reader who prefers the asks last) the default layout works against them until they add --order classic.

## What Data This Touches

The tool reads exactly three of its own files, headings.yaml, rules.yaml and example.md, which sit inside the Fieldkit folder in the sitrep subfolder, and the one report file you name on the command line. It reads them as plain text. It writes nothing to disk: no log, no cache, no copy of your report, no settings file. It prints to the screen only. It opens no network connection, starts no other program, reads no environment variable and asks for no password or special right. It does not read your other files, your clipboard or your browser. If the report file you give it holds private information, such as a person's name or a folder path, that information is printed back to your screen and nowhere else, and it is your report that holds it, not the tool. The two YAML files hold only heading words in six languages and the rules with their sources; the example file holds a made-up report about a scripts folder.

## Before You Trust It

This tool runs with your normal user rights and touches no system settings, but the point of checking is to see with your own eyes that it does only what this document says: reads a file, prints, and nothing else.

**Step 1:** Run the built-in self-test. In the Fieldkit folder, type:

```powershell
fieldkit sitrep doctor
```

and press Enter.
  - Look for: Pass: a line listing six languages (en, es, pt, it, de, ro) and the line OK: sitrep can run here. Fail: any line naming a missing file or a heading with no language; the tool is not installed correctly.
**Step 2:** Print the example report and read it. Type:

```powershell
fieldkit sitrep example
```

and press Enter.
  - Look for: Pass: seven headings starting with # One line and # What I need from you, with short bullets and numbered steps under them. Fail: an error message, or a file that is not a report.
**Step 3:** Prove the check can say no. Open a new text file, paste only the two words Status update under a heading # One line, save it as bad.md in the Fieldkit folder, then type:

```powershell
fieldkit sitrep check bad.md
```

and press Enter.
  - Look for: Pass: several lines beginning FAIL, including one about the missing full stop and six about missing sections, and a final NEXT line. Fail: the line OK: the report passes, which would mean the checker is not checking.
**Step 4:** Confirm that nothing was written to disk. Look at the folder where you ran the commands, sorted by date.
  - Look for: Pass: the only new file is bad.md, which you made yourself. Fail: any new file or folder you did not create.

## The Big Picture

When somebody has been working for you for an hour, or a whole day, you want to know four things: what did you get, what do you need from me, what happened, and what is still open. The sitrep is a fixed shape for that report, with seven headings in a fixed order. The person or the program writing the report fills in a plain text file under those headings. The tool then reads the file, checks it against rules written in another plain file, and either prints the report in the same layout every time or prints the list of rules it broke.

The shape was built for a reader who may be interrupted at any moment, and for readers with dyslexia or autism who find long paragraphs, figures of speech and hidden requests hard work. So the result comes first, in one sentence. The things being asked of you come second, not last. Everything after that is short bullets or numbered steps, with short sentences, no idioms, no abbreviations and no italics. The headings exist in English, Spanish, Portuguese, Italian, German and Romanian.

Nothing in this tool writes the words for you. It is a checker and a formatter, like a spelling checker that also lays the page out. That is deliberate: a program that reads a file says the same thing every time, costs nothing to run, and can be corrected once for everybody.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `sitrep` | Short for situation report: a report that says where things stand right now. | The note a night nurse leaves for the day nurse: what happened, what to watch, what needs doing. |
| `the one line` | The first heading. One sentence, 25 words or fewer, ending in a full stop, that gives the result. | The headline on a newspaper front page: if you read nothing else, you still know what happened. |
| `the asks` | The heading called What I need from you: numbered requests, three at most, each one a single sentence. | The three forms the receptionist hands you to sign, not the whole filing cabinet. |
| `check` | The command that reads your report file and lists every rule it breaks, with the word FAIL or WARN in front. | The ticket inspector who looks at your ticket before you board, and tells you exactly what is wrong with it. |
| `render` | The command that prints the finished report in the fixed layout, after the check passes. | The printer at the counter that prints every receipt in the same layout, whoever served you. |
| `rules file` | A plain text file, rules.yaml, that holds the numbers and the banned phrases the check uses, with a note beside each saying where it came from. | The house rules pinned to the noticeboard, with the reason written next to each rule. |
| `idiom` | A figure of speech whose words do not mean what they say, such as circle back or at the end of the day. | A wink instead of a sentence: some readers see the wink, some only see an eye closing. |
| `ask-first order` | The default order of the headings, which puts What I need from you second, straight after the one line. | A letter that tells you what to do in the first paragraph, not on page three. |
| `classic order` | The military order of the same seven headings, with the asks at the end, chosen with the option --order classic. | The same letter with the request moved to the end, for people who prefer the story first. |
| `doctor` | The command that checks the tool itself can run on this computer: its files are present, every heading has all six languages, and its own example passes. | The engineer testing the fire alarm before anyone relies on it. |

## How It Works — Step by Step

### Step 1: You get the skeleton

Typing the template command prints the seven headings, each followed by one line of advice on what goes under it. The headings come out in the language you ask for; the advice stays in English. Nothing is saved; you save it yourself into a file. It is like being handed a blank form with the field names printed and a hint under each.

### Step 2: The file is read heading by heading

When you run check or render, the tool reads your file one line at a time. A line starting with one or more # signs is a heading. The tool looks the heading up in its six-language table and, if it matches one of the seven, it collects every non-empty line beneath it as an item, stripping a leading dash, star or number. A heading it does not recognise is ignored, and the tool says so with a WARN line. The lines under an unknown heading are dropped. It is like a clerk sorting post into seven labelled trays and setting aside, with a note, any envelope addressed to a tray that does not exist.

### Step 3: Every rule is applied

The tool loads rules.yaml and goes through the items. It counts the words in each sentence and compares the count with the limit. It looks for each banned idiom, each banned abbreviation and each banned mark (the long dash and the underline tag). It looks for text wrapped in single stars or underscores, which is how italics are written. It counts the asks and the steps. It checks the one line is one sentence, within the word limit, and ends with a full stop. Each broken rule becomes one line of text beginning FAIL; each softer concern becomes a line beginning WARN. It is a proofreader with a printed checklist, ticking or crossing each box.

### Step 4: A failing report is not printed

If there is at least one FAIL line, render prints the FAIL and WARN lines and nothing else, and the command ends with exit code 3, which other programs read as "not done". A report with only WARN lines is printed, and the warnings are printed after it. The rule the source states is that a report which fails the check is not rendered; the problems are. It is like a form being handed back across the counter with the missing fields circled, instead of being filed with gaps.

### Step 5: The layout is fixed

On a pass, the tool prints each heading in the language you chose, underlined with dashes, a blank line, then the items. What happened and What I need from you are numbered; the other sections get a dash. Every line is wrapped at 80 characters, with continuation lines indented under the first word of the item, so the left edge is straight. With --md the same text comes out as markdown with ## headings and no wrapping, for pasting into a chat or a web page. An empty section prints the single word Nothing. It is a form printed by a machine that always puts the boxes in the same place.

### Step 6: The doctor checks the tool, not the report

The doctor command checks that the three data files exist, that each of the seven headings has a word in all six languages, and that the built-in example passes its own check. It prints OK or the list of what is missing. It does not look at any report of yours. It is the self-test a smoke alarm runs when you press the button.

## Quirky Things Worth Knowing

### The asks come second, not last

Most status reports end with what they want from you. This one puts it straight after the one line. The source gives the reason: a reader who stops after the first screen must still know what is being asked. If you want the older order, add --order classic to template or render. The check is the same either way.

### Bold is allowed, italics are not

Text between double stars passes. Text between single stars or underscores fails with the word italics. The rules file quotes the dyslexia style guide: avoid using, or at least overusing, italics and underlines. So the tool allows one kind of emphasis and refuses two.

### A question outside the asks is only a warning

If a bullet under What is left ends with a question mark, the tool warns that a question reads as a request. It does not fail the report. The idea is that a literal reader may take any question as something they must answer, so questions belong under What I need from you.

### Capitals are warned about unless expanded somewhere

A word of four or more capital letters, such as UEFI, draws a WARN unless the same word appears next to an opening bracket somewhere in the file, which is the tool's rough way of seeing that you expanded it once. Two or three capitals, such as PDF or USB, are left alone. The check is approximate: it does not read the expansion, it only looks for the bracket.

### An empty asks section is fine; an empty anything else is not

Every other section must have at least one item, or the check fails with the advice to write the word Nothing. if that is the truth. The asks section may be empty, and then the render prints Nothing. under it, because having nothing to ask is a real and common state.

### Headings are matched by their words, not their position

You can write the seven headings in any order, in any of the six languages, and the tool sorts them into its own order. Only the text of the heading matters. A heading with a spelling mistake is treated as unknown, with a WARN, and the lines under it vanish from the report. The WARN line is the only sign.

### The word limits are a choice, written down

The 25-word sentence limit, the limit of three asks and the seven-step warning are not laws. The rules file marks each number as stated in a source or as this tool's own inference, and the module's own notes say the inferred ones are the ones to argue with. Change the number in rules.yaml and the check changes with it.

### What this cannot do

It cannot tell whether a sentence is true. It cannot tell whether an ask is reasonable. It does not read the expansion of a capitalised word, only whether a bracket follows it. Its idiom list is short and English-only; a figure of speech not on the list passes, and a Romanian or Spanish idiom always passes. It counts words by splitting on spaces, so a long web address is one word and a hyphenated pair is one word. It splits sentences at a full stop, question mark or exclamation mark followed by a space and a capital letter or digit, so a sentence beginning with a lower-case word after a full stop is not seen as a new sentence, and an abbreviation such as Dr. followed by a capital is seen as one. It does not check spelling or grammar. It does not measure how long a reader takes; nothing in this tool is measured. It has no sound, no colours and no fonts: the terminal decides those.

## What This Means For You

### Battery, Processor & Memory

Not measured. The tool reads three small files and one report file, applies text rules and prints. No measurement of processor time or memory exists in the source material.

### Speed

Not measured. Nothing on your machine becomes faster or slower; the tool runs, prints and exits.

### Your Privacy

Nothing leaves your machine. The tool opens no network connection and writes no file. Whatever is in your report file is printed to your screen and nowhere else. The rules file and the headings file contain no personal data.

### Your Internet

None. No connection is opened, no data allowance is used.

## The Off Switch

**What it is:** There is no background process to stop: the tool runs once and exits. The nearest thing to an off switch is the check itself, which refuses to render a report that breaks a rule, and exits with code 3. To switch a rule off, edit or delete its line in rules.yaml inside the sitrep folder; to turn the ask-first layout off, add --order classic.

**Without it:** Without the refusal, a report with a 60-word sentence, three figures of speech and five asks would print in the tidy layout and look finished, and the reader it was written for would be the one to discover it was not.

**Think of it like:** A form-checking clerk at a counter who hands a form back with the errors circled instead of filing it, and whose checklist is pinned on the wall where anyone can rewrite it.

## How to use this

**Before you start:**
- Fieldkit installed, so that typing fieldkit in a terminal prints its help
- a text editor you can save plain text with, such as Notepad

**Step 1:** Open a terminal. On Windows, press the Windows key, type PowerShell, and press Enter. A window with a blinking cursor opens. On Linux, open the application called Terminal.
  - You should see: A window with a prompt and a blinking cursor.
**Step 2:** Go to the Fieldkit folder. Type:

```powershell
cd (fieldkit where)
```

and press Enter.
  - You should see: The prompt now shows the Fieldkit folder. Pass: no error. Fail: a message that fieldkit is not recognised; Fieldkit is not installed.
**Step 3:** Make the skeleton. Type:

```powershell
fieldkit sitrep template > sitrep.md
```

and press Enter. For Romanian headings, add --lang ro before the > sign.
  - You should see: Nothing is printed; a file called sitrep.md now exists in the folder with seven headings and a hint under each.
**Step 4:** Open sitrep.md in your text editor, replace each hint with your own lines, and save. Keep the headings as they are.
  - You should see: A saved file with your words under the seven headings.
**Step 5:** Check it. Type:

```powershell
fieldkit sitrep check sitrep.md
```

and press Enter.
  - You should see: Pass: the line OK: the report passes. Fail: lines beginning FAIL, each naming the heading and the rule, then a NEXT line telling you to fix them and run the check again. Fix every FAIL line; WARN lines are advice.
**Step 6:** Print it. Type:

```powershell
fieldkit sitrep render sitrep.md
```

and press Enter. For a copy you can paste into a chat window, add --md at the end.
  - You should see: Pass: the report, each heading underlined with dashes, the one line first and What I need from you second. Fail: the same FAIL lines as the check, and no report.

## If Something Goes Wrong

**The check says FAIL missing section for a heading you are sure you wrote.**
The heading text does not match one of the seven exactly, in any of the six languages; a spelling slip or a different word is enough. The tool also printed a WARN line naming the heading it did not recognise.
What to do: Copy the heading from fieldkit sitrep template and paste it over yours.

**The one line fails with does not end with a full stop even though it looks finished.**
The last character is a space, a bracket or a quotation mark after the full stop, or the line is a title with no punctuation.
What to do: End the line with the full stop as the last character and save the file again.

**Render prints the FAIL lines and no report.**
That is the tool working: a report that breaks a rule is never rendered.
What to do: Fix each FAIL line, run the check until it says OK, then render again.

**The tool fails on an idiom you did not think was one.**
The phrase is on the list in rules.yaml, which was written for readers who take words literally.
What to do: Say the literal thing instead, or, if the phrase is a real term of your trade, remove it from the idioms list in rules.yaml and note why.

**Headings come out in English though you asked for another language.**
The language code was not one of en, es, pt, it, de, ro, in which case render refuses with unknown language; or the option was typed after the > sign when making the template, so it went into the file name.
What to do: Use one of the six codes and put --lang before any > sign.

## Why a Developer Would Do This

The maintainer's first version of this report was the military situation report, written by hand. The developer's choices here come from reading what guides for dyslexic and autistic readers say: short sentences, no figures of speech, no abbreviations, bold only, bullets over paragraphs, and the must-do parts marked plainly. The developer turned those into numbers in a file instead of into code, so the numbers can be argued with by anyone, and wrote the asks-second order into the default because a report that may be read in pieces must survive being stopped after its first screen. Keeping the content out of the tool, so that no model writes the words, is the same choice Fieldkit makes elsewhere: a file says the same thing every time and costs nothing to run.

## Why It Matters That You Can Read This

The rules that decide what a good report is are written in a plain file you can open, with the reason and the source next to each number, and the code that applies them is short enough to read in one sitting. If this were a hidden service, you would be trusting somebody else's idea of what a reader with dyslexia or autism needs, with no way to see the idea, disagree with it, or fix a rule that is wrong for the person in front of you. Here you can read the rule, read where it came from, and change it.

## Glossary

**heading** — A line in the report file that begins with one or more # signs and names a section.

**item** — One bullet or one numbered line under a heading.

**FAIL** — A rule the report breaks; the report is not printed until it is fixed.

**WARN** — A concern the tool mentions but does not stop for.

**exit code** — The number a command leaves behind when it finishes; 0 means done, 3 here means the report did not pass.

**markdown** — Plain text with a few symbols, such as # for a heading and ** for bold, that chat windows and web pages turn into formatting.

**template** — The empty skeleton of the report, with the seven headings and a hint under each.

**YAML** — A plain text layout for settings and lists that people can read and programs can load.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| The asks come second so a reader who stops after the first screen still knows what is asked | 📄 stated in input | A reader who stops after the first screen still knows what is being asked |
| The 25-word sentence limit comes from GOV.UK content design, not from the dyslexia guide | 📄 stated in input | the dyslexia guide gives no number |
| The tool writes no file and opens no network connection | 🤖 model inference | *(none — model judgment)* |
| A capitalised word is treated as expanded only when a bracket follows it somewhere in the file | 🤖 model inference | *(none — model judgment)* |
| A report that fails the check is not rendered; the problems are | 📄 stated in input | a report that fails the check is not rendered, the problems are |
| Processor, memory and speed effects are not measured | 🤖 model inference | *(none — model judgment)* |
| The idiom list is short and English-only, so idioms in other languages pass | 🤖 model inference | *(none — model judgment)* |
| The 8-second attention span figure is a myth and the tool does not assume a reader cannot focus | 📄 stated in input | This tool does not assume a reader cannot focus |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*