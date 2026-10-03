# Gorilla writer brief (Gorilla.Documentation.IBM.Style)

You are filling one `.filled.json` for one track (layman or developer) of one
group of Fieldkit's source code. These rules come from the Gorilla Open Source
Philosophy and from the maintainer, whose requirement is, word for word:

> "We need to make sure that the layman user get proper documentation,
> exaustive attention to the detail, analogies, spoonfeed never dumbed down,
> basically everything gorilla philosophy is talking about."

`fieldkit docs render` checks most of what follows by machine and refuses the
document (exit 3, every reason listed) when a rule is broken. The rules that a
machine cannot check are still rules.

## Why this matters

Open source gave the world the recipe and forgot to teach people how to cook.
Publishing code is transparency in principle; a document that someone who has
never opened a terminal can follow is transparency in practice. The layman
track is not a dumbed-down developer track. It is the same truth, complete and
honest, in a different language. Neither track summarises the other, and no
reader should have to trust a summary they cannot check.

## Rules for both tracks

1. **British English.** Colour, behaviour, organise, licence (noun), programme
   (a plan; a computer program is a "program"), favour, centre.
2. **Facts only from the source.** Every statement about what the code does
   must be traceable to the source files in the prep, its tests, or
   `MEASUREMENTS.md`. If it is not there, write exactly "Not available in the
   source material." Never invent a plausible answer.
3. **Numbers only from `MEASUREMENTS.md` or the source.** Any other number,
   including one you computed yourself (a sum of timeouts, a count you made),
   carries the words "not measured" right next to it, for example "about 285
   seconds (not measured: the sum of the scene times in the source)". Never
   estimate silently. Speed, memory, battery and data use are "not measured"
   unless `MEASUREMENTS.md` gives them.
4. **Say plainly what is untested.** If the source says a part is untested,
   unmeasured, Linux-only or written without being run, say so in the first
   place a reader would look (Should You Run This, Purpose), not only in a
   footnote.
5. **Every command you show must exist.** Commands start with `fieldkit` and
   the subcommand, action and options must be real (the checker parses them
   against Fieldkit's own command line). If the source names a command that
   does not exist, you may quote it to say so, and the same line must say "not
   registered" or "does not exist".
6. **Code blocks start on their own line.** In a step's `action`, write the
   lead-in sentence, then a blank line (`\n\n`), then the fence
   (```` ```powershell ````). A fence directly after "Step 1:" breaks the page.
7. **Quotes are verbatim.** Text in "double quotes" that comes from the source
   must be word for word. If you paraphrase, drop the quote marks.
8. **No personal data.** No home folder paths (`C:\Users\<name>`,
   `/home/<name>`), no email addresses, no user names, no location. Write
   `<your Fieldkit folder>` or "the Fieldkit folder" instead.
9. **"The maintainer", never "the owner".** The person who runs this project
   is the maintainer. The word "owner" may appear only inside a verbatim quote
   or a `code span` copied from the source.
10. **No AI assistant names** (Claude, Gemini, ChatGPT, Opus, Sonnet, Fable,
    Luna or any other product name of a hosted assistant). Write "an AI model",
    "a coding agent" or "a model". The local model Gemma may be named where the
    source names it (the exam measures it).
11. **No vendor web addresses** unless the same address is in the source or in
    `MEASUREMENTS.md` (the checker compares them).
12. **`claim_sources` is honest.** Every conclusion or assessment gets an
    entry. Mark reasoning as `model_inference` with `evidence: null`; use
    `stated_in_input` only with an exact short phrase from the input as the
    evidence. A list with no inferences is a red flag.
13. **No marketing, no minimisers, no self-praise.** dual_track.py's banned
    list applies (powerful, robust, seamlessly, simply, just, easy, obviously,
    and the rest).

## Layman track: for someone who has never opened a terminal

Write in the second person ("you"), present tense, one idea per sentence. The
reader is clever and has never seen a command line. Spoon-feed every step;
never talk down. Translate complexity; do not delete it.

- **Should You Run This** (`should_you_run_this`): an honest yes / no / only if,
  with the reasons. Include **when not to run it**.
- **Worst Case, Honestly** (`concept.worst_case`): the most harmful outcome
  that is plausible from what the code really does, described as what you
  would experience, with a concrete example. Not catastrophised, not
  minimised.
- **What Data This Touches** (`concept.data_and_privacy`): every file, folder,
  setting, network address and program it reads, writes, sends or starts. If
  nothing leaves the machine, say so explicitly.
- **Before You Trust It** (`verification_task`): at least three numbered steps
  a non-programmer can actually carry out, each with what passing and failing
  look like. "Read the source code" is not a step.
- **The Big Picture** (`concept.big_picture`): what this does in your life, in
  two or three paragraphs.
- **Key Concepts** (`concept.key_concepts`): every technical word the document
  uses, each with a plain meaning AND a real-world comparison from everyday
  life (a post room, a guest list, a fuse box, a receipt). Every row needs its
  comparison; a row without one fails the check.
- **How It Works** (`how_it_works`): the actual chain of events, step by step,
  each with an analogy. Not a summary of the developer track.
- **Quirky Things** (`quirky_things`): everything a reader would get wrong on
  a first reading. Include one entry titled **"What this cannot do"** listing
  the limits: what it does not check, does not protect against, and where its
  judgement should not be trusted.
- **What This Means For You** (`real_world_impact`): battery, processor,
  memory, speed, privacy and internet use. "Not measured" is a correct answer.
- **The Off Switch** (`kill_switch_explained`): how to stop it or undo it, and
  what would happen without that switch.
- **How to use this** (`usage_task`): every step exactly runnable.
  - Step 1 says how to open PowerShell: "press the Windows key, type
    `PowerShell`, and press Enter. A window with a blinking cursor opens."
  - Say which folder to be in and how to get there (`cd "<your Fieldkit
    folder>"`).
  - Every command goes in its own code block, after a lead-in line and a blank
    line, followed by "press Enter".
  - `expected_result` shows what the screen really prints, copied from the
    source or the tests (real input and real output), with **Pass:** and
    **Fail:** lines.
- **If Something Goes Wrong** (`troubleshooting`): at least three symptoms the
  reader can actually see, each with the plain cause and what to do.
- **Glossary** (`glossary`): at least six terms, one sentence each, no jargon.
- **Risks**: name them where they belong (Worst Case, Quirky Things, Should You
  Run This). Never leave the reader to discover a risk by running the tool.

## Developer track: for someone who will audit, fork or change the code

Technical precision, correct terminology, programming literacy assumed but not
familiarity with this project. Explain why before how. Fill: purpose and trust
level; alternatives that were rejected (only those the source documents);
architecture, dependencies, trust boundary and attack surface; flags, API
surface, kill switches, error conditions, dead code; performance and security
(numbers only per rule 3); tasks with exact commands and expected results;
troubleshooting; technical debt with a specific next action; what breaks if
the module is removed. State known failure modes and what the code does not
yet do.

## What the checker measures (so you are not surprised)

Required sections present; word floors per section; numbered trust steps;
a comparison for every key concept; how-to steps with code blocks and how to
open PowerShell; troubleshooting and glossary entries; limit statements
(cannot, does not, never, untested, not measured); every `fieldkit` command
parses; every number is sourced or marked "not measured"; web addresses are
sourced; privacy scan, assistant names and "the owner". The floors and their
measured origin are documented in `fieldkit/gdocs/checks.py`.
