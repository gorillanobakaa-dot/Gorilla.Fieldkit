# How to write documentation for someone who has never opened a terminal

This guide is for anyone, person or AI model, who has to explain a piece of
software to a reader who is clever and has no technical background. It works for
any project. It is shown by `fieldkit docs guide`.

`WRITER_BRIEF.md`, in the same folder, is the short rulebook that Fieldkit's own
checker enforces on Fieldkit's own documents. This guide is the long version: it
explains **why** each rule exists and **how** to follow it, with examples of
getting it wrong and getting it right.

---

## 1. What you are being asked to do

Open source gave the world the recipe and forgot to teach people how to cook.

Publishing code lets anyone read it. That is transparency in principle. A person
who cannot read code gets nothing from it. A document that such a person can
follow from the first line to the last, and act on, is transparency in practice.

So every piece of work gets **two** documents:

- the **developer track**, for someone who will audit, fork or change the code;
- the **layman track**, for someone who will use it and has to decide whether to
  trust it.

The layman track is **not** a shorter developer track. It is the same truth,
complete and honest, in a different language. If the developer track says a part
is untested, the layman track says so too, in words the reader understands, in
the first place they would look.

The maintainer's requirement, word for word:

> "We need to make sure that the layman user get proper documentation,
> exaustive attention to the detail, analogies, spoonfeed never dumbed down,
> basically everything gorilla philosophy is talking about."

Three phrases in that sentence do the work. Each has a section below.

- **Exhaustive attention to detail**: section 5 and section 7.
- **Analogies**: section 6.
- **Spoon-feed, never dumbed down**: section 3 and section 4.

---

## 2. Who the reader is

Picture one person and write to them. Not "users". One person.

- They are intelligent. They run a household, a shop, a clinic or a classroom.
- They have never typed a command. They do not know what a terminal is, what a
  folder path is, or what "install" does beyond pressing a button.
- English may be their second or third language.
- Their computer may be fifteen years old. Their internet may be a phone with a
  data allowance that costs real money for every megabyte.
- They cannot ask you a question. Whatever you leave out, they do not have.
- If something goes wrong they cannot fix it, and they will blame themselves.

That last line is the reason for most of the rules. A developer who hits an
error reads it and moves on. This reader stops, and concludes that they are not
clever enough for the program. Your document either prevents that or causes it.

---

## 3. "Spoon-feed": what it means

Spoon-feeding means **you do the work of getting the food to the mouth**. The
food is the same food.

In practice it means every step is small enough that it cannot be misread, and
nothing is left for the reader to work out.

**Wrong** (leaves four things to work out):

> Run the installer from PowerShell.

**Right:**

> Press the Windows key. It is the key with four small squares, at the bottom
> left of your keyboard.
>
> Type `PowerShell` and press Enter. A dark window with a blinking line opens.
> That window is called a terminal. It is a place to type instructions to the
> computer, one line at a time.
>
> Type the line below exactly as it is written, then press Enter.
>
> ```
> cd Downloads
> ```
>
> - **Pass:** the text at the start of the line now ends with `Downloads>`.
> - **Fail:** red text appears. Check the spelling and type it again.

The right version is several times longer. That is correct. The length is the
help.

---

## 4. "Never dumbed down": what it means

Dumbing down means **taking truth out** to make the text shorter or more
comfortable. It is the opposite of spoon-feeding and it is forbidden.

You translate complexity. You do not delete it.

**Dumbed down** (true things removed):

> This update makes the program safer.

**Translated** (the same truth, in plain words):

> Before this update, if a command printed one of your passwords on screen, the
> AI read it, and so the company that provides the AI received it. Now the
> program replaces the password with a label before the AI sees anything.
>
> One thing has not changed: the complete printout is still saved in a folder on
> your computer, and the password is in that copy.

The test: **could the reader, after reading only your version, make the same
decision an expert would make?** If your version would lead them to a different
decision, you removed something that mattered.

Things that must never be removed:

- what has **not** been tested;
- what the tool **cannot** do;
- the worst thing that can plausibly happen;
- every file, folder and network address it touches;
- how to stop it and how to undo it.

---

## 5. The parts of a layman document, in order

Use these headings in this order. A reader who stops early has still read the
parts that protect them.

### Should you use this?

An honest yes, no, or "only if". Give the reasons. **Say when not to.** This
comes first because it may save the reader the rest of the page.

### The worst case, honestly

The most harmful thing that is plausible from what the software really does,
described as what the reader would experience, with one concrete example.

Do not catastrophise ("could destroy your computer") and do not minimise
("extremely unlikely"). Say what happens and how they would know.

### What this touches

Every file, folder, setting, program and network address it reads, writes, sends
or starts. If nothing leaves the machine, say that sentence explicitly: "Nothing
leaves your computer."

### Before you trust it

At least three numbered checks a non-programmer can carry out, each with what
passing and failing look like. "Read the source code" is not a check this reader
can perform. "Compare this 64-character code with the one on the download page"
is.

### The big picture

Two or three paragraphs: what this does in the reader's life. Start from their
problem, not from the software's feature.

### Key ideas

Every technical word the document uses, each with a plain meaning **and** a
comparison from everyday life. See section 6.

### How it works

The real chain of events, step by step, each with its comparison. Not a summary
of the developer track: a translation of it.

### Things you would get wrong on a first reading

Everything surprising. Always include one entry titled **"What this cannot do"**.

### What it costs you

Battery, memory, speed, storage, privacy, internet data. If you did not measure
it, write "not measured". That is a correct answer. A guess is not.

### The off switch

How to stop it, how to undo it, and what would happen without that switch.

### How to use it

Numbered steps. See section 7.

### If something goes wrong

At least three symptoms the reader can **see**, each with the plain cause and
what to do. Write the symptom the way it looks on their screen, not the way it is
named in the code.

### Words used here

A glossary: one sentence per term, no jargon in the definitions.

---

## 6. Analogies: how to find one and how to test it

An analogy lets the reader use something they already understand to hold
something they do not. It is the single most useful tool you have, and the
easiest to do badly.

### How to find one

Ask: **what does this thing do, stripped of the computer?**

| The thing | What it does, stripped | An everyday match |
|---|---|---|
| a checksum | proves a copy is identical to the original | a wax seal on a letter |
| a backup taken before a change | lets you put things back | photographing a shelf before rearranging it |
| a permission prompt | makes a person decide | a courier asking for a signature |
| a log file | records what happened, in order | a receipt roll |
| an allow-list | names who may enter | a guest list at a door |
| a size limit that cuts a printout | keeps only part of something long | photocopying the first and last page of a report |
| a loop detector | notices the same action repeating | someone pressing a lift button that is already lit |

### How to test one

An analogy must pass three tests.

1. **The reader already knows it.** A post room, a fuse box, a receipt, a guest
   list. Not a "load balancer for people".
2. **It breaks where the real thing breaks.** A wax seal shows tampering and
   does not prevent it; neither does a checksum. That is a good analogy. Calling
   a checksum "a lock" fails this test, because a lock prevents and a checksum
   only reveals.
3. **You say where it stops.** Every analogy is wrong somewhere. Say where:
   "Unlike a wax seal, anyone can make a new one, so you must get the code from
   the official page and not from the same place as the file."

### One per idea

Give every technical term its own comparison. Do not stretch one comparison over
a whole document; by the third use it is doing damage.

---

## 7. How to write a step

A step is one action with one visible result.

1. **Say how to get to the starting point.** How to open the terminal. Which
   folder to be in, and the line that gets them there.
2. **One command per block, on its own line,** after a sentence that introduces
   it and a blank line. Follow it with "press Enter".
3. **Show what the screen prints.** Copy it from a real run. Never write what you
   expect it to print.
4. **Give Pass and Fail.**
   - **Pass:** what they see when it worked.
   - **Fail:** what they see when it did not, and the next thing to do.
5. **Never chain.** "Do A, then B, then C" in one step is three steps.
6. **Never say "simply", "just" or "easy".** If it goes wrong for them, those
   words tell them the fault is theirs.
7. **Every command you show must exist.** Run it before you write it down. A
   command that does not exist is worse than no command: the reader types it,
   sees an error, and stops trusting the page.

---

## 8. Honesty rules

These are not style. They are the reason the document can be trusted.

- **Facts come from the source.** Every statement about what the software does
  must be traceable to the code, its tests, or a recorded measurement. If you
  cannot find it there, write "Not available in the source material." Never fill
  a gap with something plausible.
- **Numbers come from measurement.** A number you did not measure carries the
  words "not measured" beside it.
- **Quotes are word for word.** If you paraphrase, remove the quote marks.
- **Untested is said first.** If a part was never run, or never run on the
  reader's kind of computer, say so at the top, not in a footnote.
- **Limits are stated.** Every document says what the tool cannot do.
- **No personal data.** No home folder paths with a name in them, no email
  addresses, no user names. Write "your home folder".
- **No praise.** Not "powerful", "robust", "seamless", "smart". Describe what it
  does and let the reader judge.

A document that says "this was not tested on Linux" is more useful, and more
believed, than one that says nothing.

---

## 9. Words

- **British English**: colour, behaviour, organise, licence (the noun), centre.
- **"You", present tense, active voice.** "You press Enter", not "Enter should
  be pressed".
- **One idea per sentence.** If a sentence has "and" in the middle, try two
  sentences.
- **The same word for the same thing, every time.** If you call it "the
  terminal" in step 1, it is not "the console" in step 4 and "the shell" in
  step 6. The reader will think they are three things.
- **Name the key, the button, the menu.** "Press the Windows key", not "open the
  Start menu".
- **Explain a term the first time it appears,** in the same sentence, and again
  in the glossary.

---

## 10. A worked example

**What the developer wrote:**

> `truncateOutput` discards the middle of any stdout over 30,000 bytes. v0.1.135
> spills the full content to `tool-output/` under the state directory (0600,
> newest 40 kept) and names the path in the truncation notice.

**A dumbed-down version (do not write this):**

> Long output is now handled better.

**The layman translation:**

> When a command prints a great deal of text, the program cannot pass all of it
> to the AI. Until now it kept the beginning and the end and threw away the
> middle, like photocopying the first and last page of a long report and binning
> the rest.
>
> That mattered most when building a large program. A build prints thousands of
> lines, and when it fails, the error is usually in the middle. The AI could not
> see it. The only way to see it was to run the whole build again.
>
> Now the program saves the complete printout as a file on your computer and
> tells the AI where the file is. The AI opens it and reads the part it needs.
>
> **What this costs you:** some disk space. The program keeps the 40 newest
> printouts and deletes older ones by itself.
>
> **What to know:** each saved printout holds whatever the command printed. If a
> command printed a password, the password is in that file. Only your own user
> account can open it.
>
> **Not measured:** how much data this saves you.

Look at what the translation kept: the limit, the count of 40, the cost, the
privacy consequence, and what was not measured. Nothing true was removed. It is
several times longer than the original.

---

## 11. The method: two tracks from one source

Write both tracks from the **same source material**, so neither can say
something the other does not.

`dual_track.py` (in Fieldkit's toolbox, under `dual-track-doc-generator`) does
the mechanical parts. It never contacts the internet. It has two jobs:

- **prep** reads your source and writes a file holding the rules, the material
  and the exact shape of the answer;
- **render** reads the filled answer, checks it, and writes the finished page.

The writing in between is done by a person or an AI model.

For release notes, from the project's folder:

```
python dual_track.py release prep --input SOURCE.md --output-dir Changelogs --version v1.2.3 --previous v1.2.2
```

Fill the two files it names, then:

```
python dual_track.py release render --output-dir Changelogs --version v1.2.3 --previous v1.2.2
```

The render step refuses a document that is missing a required part, uses a
banned word, or contains no concrete numbers, and prints every reason.

For Fieldkit's own code, the same steps run through Fieldkit:

```
fieldkit docs plan
```

```
fieldkit docs prep
```

```
fieldkit docs render
```

```
fieldkit docs check
```

`fieldkit docs plan` tells you which step comes next.

---

## 12. Before you publish: the checklist

Read your document as the person in section 2 and answer each line.

- [ ] Could I decide whether to use this from the first section alone?
- [ ] Is the worst case stated, with an example, without exaggeration?
- [ ] Is every file, folder and network address it touches listed?
- [ ] Can I carry out every check under "Before you trust it" without reading code?
- [ ] Does every technical word have a plain meaning and an everyday comparison?
- [ ] Does every comparison say where it stops being true?
- [ ] Does step 1 tell me how to open the terminal?
- [ ] Is every command on its own line, and did the writer run every one?
- [ ] Does every step say what passing and failing look like?
- [ ] Is there a section saying what this cannot do?
- [ ] Is everything untested or not measured marked as such, near the top?
- [ ] Is there an off switch, and a way to undo?
- [ ] Are there at least three "if something goes wrong" entries I would recognise on my screen?
- [ ] Are "simply", "just", "easy" and every word of praise gone?
- [ ] Is there any name, email address or home folder path left in it?

---

## 13. What a machine can check, and what it cannot

A checker can count sections and words, find banned words, confirm that a
command exists, and notice a number with no source. Fieldkit's does all of that.

A checker cannot tell whether an analogy is true, whether a step is small
enough, or whether the worst case is the real worst case. Those are your job. A
document can pass every machine check and still fail the reader. The checklist
in section 12 is for the part no machine does.

---

## 14. Where the document goes: on the page, never behind it

A layman document that nobody opens was never published.

On 4 October 2026 three releases went out with a complete, checked plain-language
document attached to the release, and this at the bottom of the page:

> **Full notes**
> - `v0.1.137-release-notes.layman.md`, plain English

The page itself held a summary. The reader the document was written for, someone
deciding on that page whether to download anything, was the one reader who would
never see it. A person who has never opened a terminal does not open an attached
file with `.md` on the end. The link was also written in a form that does not
open from a release page at all.

It is like printing the instructions for a medicine and then locking them in the
pharmacy's filing cabinet, with a note on the box saying where the cabinet is.

The rule:

- **The plain-language text goes on the page the reader lands on, in full.**
  Not a summary of it. Not a link to it.
- **It comes first.** The developer text follows it, also in full.
- **The page opens by answering three questions**, each under its own heading,
  before anything else:
  1. *What is this program?* Assume the reader has never heard of it.
  2. *Should you download this version?* Yes, no, or only if, with reasons, and
     when not to.
  3. *Why this matters to you.* One comparison from ordinary life, then what it
     means for them in one line.
- **Every link is a full web address.** A link such as `docs/GUIDE.md` does not
  open from a release page.

This is not left to memory. The page is built and checked by a command:

```
fieldkit release-page compose --opening OPENING.md --layman LAYMAN.md --developer DEVELOPER.md --out PAGE.md
```

It refuses to write a page that breaks the rule, and prints every reason. To
check a page that already exists:

```
fieldkit release-page check PAGE.md --layman LAYMAN.md --developer DEVELOPER.md
```

The opening is the one part you write by hand, and the part that decides whether
the reader stays. Section 6 is how to find its comparison.

---

## 15. Why any of this is done

The reasons are in one document, the Gorilla Open Source Philosophy. Read it
before you write anything for a reader:

```
fieldkit docs philosophy
```

Its shortest form: open source gave the world the recipe and forgot to teach
people how to cook. Publishing the code is not the same as being understood. The
explanation is not an extra. It is the work.
