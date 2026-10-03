# The agent door: how Fieldkit lets an AI model use your tools without letting it change things on its own — Plain Language Guide

> Generated 2026-10-02 from `agent-door`

---

## Should You Run This?

Run it if you want an AI model to use Fieldkit's reviewed tools and you accept that it may apply `reversible` changes, and undo earlier runs whose files have not changed since, without asking you first. Before you connect any model, run the tests in the check list below and read the card of every tool marked `reversible` or `irreversible`. Always work on copies of important files: the undo depends on backups of the files and folders the card names, and nothing else. Do not run it if you need a model to ask before every change, because the source lets reversible changes through without your approval. Do not point a card's scope at a very large folder, because the whole folder is copied before every change and the copies are never deleted automatically.

## Worst Case, Honestly

The realistic worst case is a change that the door counts as reversible but is not fully covered by the backup. The backup copies only the files and folders named in the card's `scope` inputs. Example: a model runs a `reversible` tool through the MCP door without asking you, because reversible tools with no system effects need no approval. If that tool also changes a second file that is not in its `scope`, the backup does not hold a copy of that second file, and `undo` cannot put it back. The door does not stop a tool from doing something its card does not describe; it trusts the card that a person reviewed.

A second case: you type `fieldkit agent run TOOL` without `--mode preview`. The command applies the change straight away, because apply is the default.

A third case: a model connected through the MCP door can ask to undo any run, including one you started yourself, as long as nothing has changed the files since. You would find your file back as it was before your own change. The model cannot force an undo over later edits; that needs `--approve`, which only you can type.

A fourth case: a card whose scope names a large folder makes the desk copy that whole folder into `state\agent-backups\` before every change. That uses disk space that is never freed automatically, and copying and fingerprinting take time (not measured).

## What Data This Touches

This group itself sends nothing over the internet. The `fieldkit mcp` door talks only through the text window it runs in (standard input and output), not over a network. It writes these files inside the Fieldkit folder: one journal file per run in `state\agent-runs\`, containing the tool name, the inputs you gave, the full location of every file or folder in the card's scope, a fingerprint of each one before and after the run, the list of what changed, and the last 4,000 characters of the tool's output; backup copies of the files and whole folders in scope in `state\agent-backups\`; one log file per command in `state\agent\logs\`; and, when a model uses the MCP door, a daily "flight recorder" file in `state\recorder\` with every request and answer (each input value cut to 2,000 characters, each answer to 4,000). When undo puts a folder back, it first copies the backup next to the original under a temporary name ending in `.fieldkit-restore-` and six letters or digits, then swaps it into place. If you pass a file name or a personal name as an input, it is stored in those files. The tools the door starts are a separate matter: each tool does whatever its card says, and some other Fieldkit commands, for example `fieldkit gather` without `--offline`, can use the internet. The source does not say that any of these files are deleted automatically.

## Before You Trust It

This desk lets a program run tools on your computer. Check that the safety rules are in place on your copy before you connect any AI program to it.

**Step 1:** Open PowerShell: press the Windows key, type `PowerShell`, and press Enter. A window with a blinking cursor opens. Type `fieldkit --version` and press Enter.
  - Look for: Pass: the line `fieldkit 0.1.0` appears. Fail: a message that `fieldkit` is not recognised, which means Fieldkit is not installed on this computer.
**Step 2:** Type `fieldkit readiness` and press Enter.
  - Look for: Pass: a short summary of how many tools are verified, tested, carded or draft. Draft tools are the ones the desk refuses to run. Fail: an error message instead of the summary.
**Step 3:** Type `fieldkit cards list` and press Enter.
  - Look for: Pass: one line per tool, with its trust level, its safety class and its name, and a count of cards at the end. Look for any tool marked `irreversible` and note its name. Fail: an error message instead of the list.
**Step 4:** Type `fieldkit agent describe office-scrub` and press Enter.
  - Look for: Pass: the card shows `"safety": "reversible"`, a `scope` that lists `file`, and a `verify` section with two checks. Fail: an error saying there is no such tool.
**Step 5:** Run the project's own tests for this group. Go to the Fieldkit folder with `cd "<your Fieldkit folder>"` and press Enter. Then type `python -m pytest tests/test_agent.py tests/test_mcp.py tests/test_agent_security.py` and press Enter.
  - Look for: Pass: the last line says the tests passed and none failed. The tests check that drafts are refused, that irreversible tools need approval, that a failed check restores the file, that the MCP door has no approve argument, that inputs starting with a dash are refused, that folders are backed up and restored, that a forged run number or journal is refused with nothing changed, that checks and undo commands are stopped when they take too long, and that undo will not overwrite later edits without approval. Fail: any line with `FAILED`.
**Step 6:** Try the dash rule yourself. Type `fieldkit agent run office-scrub --input file=-x.docx --mode preview` and press Enter.
  - Look for: Pass: a line starting `REFUSED:` that says the input starts with '-', and nothing runs. Fail: the tool runs, or the error is about something else.

## The Big Picture

Fieldkit is a collection of small tools that run on your own computer. The agent door is the part that decides how a language model (a program that reads and writes text, such as the small local model Gemma) is allowed to use those tools. Think of it as the reception desk of a workshop. The model asks at the desk for a job. The desk finds the right tool, checks the request, and only then hands the work to the tool.

The group has four files. `__init__.py` gives Fieldkit its name and version number, `0.1.0`. `cli.py` is the single command you type, `fieldkit`, with one sub-command for every part of the collection, for example `fieldkit office`, `fieldkit pipeline`, `fieldkit docs` and `fieldkit agent`. `agent.py` is the reception desk itself: it finds tools, checks inputs, makes backups, runs the tool, checks the result and can undo it. `mcp.py` lets other programs that speak the Model Context Protocol (MCP) talk to that same desk.

The rule at the centre of this group is written in the source: "Approval must come from a person, not from text a model produced." A model can look up tools, run tools that only read, and run tools whose changes Fieldkit can put back. A change that cannot be undone, or that touches system settings, stops and waits for you to approve it on the command line.

On 2026-10-02 the desk was given stricter rules after a security review. It now refuses any input that starts with a dash, because a tool could mistake it for an instruction. It backs up whole folders as well as single files, and if it cannot make a backup it runs nothing. It takes a fingerprint of every file and folder before and after each change, so it can tell you exactly what changed. And before an undo puts anything back, it checks its own records and refuses to overwrite work you did after the run.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `card` | A short written description of one tool: its name, the inputs it takes, whether it changes anything, and how to check its result. Cards live in `tools.yaml`. | The label and instruction sheet tied to each machine in a workshop. |
| `trust level` | How far a card has been checked. The four levels, lowest first, are `gathered` (a draft nobody has reviewed), `carded` (a person reviewed it), `tested` (its tests passed) and `verified` (tests passed and it can preview, undo and check its own changes). | A new employee who moves from trainee to fully signed-off. |
| `safety class` | What a tool does to your files: `read-only` (looks only), `reversible` (changes things Fieldkit can put back) or `irreversible` (changes things that cannot be put back). | Reading a book, writing in pencil, or writing in pen. |
| `preview` | A dry run. The tool shows what it would do and changes nothing. | A builder's drawing before anyone picks up a hammer. |
| `approve` | Your yes for a change that cannot be undone, that touches system settings, or for an undo that would overwrite work you did later. Only you can give it, by adding `--approve` to the command you type. | Signing for a parcel: the courier cannot sign for you. |
| `scope` | The list on a card of which inputs name files or folders that the desk must back up before a change. Anything the tool touches outside that list has no backup. | The list of rooms a decorator agrees to cover with dust sheets before painting. |
| `option` | A word starting with a dash, such as `--check`, that tells a tool how to behave instead of what to work on. The desk refuses input values that start with a dash, so a file name can never be read as an option by mistake. | A note on a parcel that says "return to sender": the post room acts on it instead of delivering the parcel. |
| `fingerprint (hash)` | A long code worked out from the exact contents of a file or folder. If one letter changes, the code changes. The desk records one before and one after each change, and checks the backup copy has the same fingerprint as the original. | A wax seal on a letter: if the seal does not match, someone opened it. |
| `journal` | A record of every run, saved as a file in `state\agent-runs\` inside the Fieldkit folder. It holds the inputs, the backup list, the fingerprints and the list of what changed. | A logbook at the workshop door. |
| `undo` | Puts files and folders back from the backup Fieldkit made before a run, or runs the card's own undo command. It checks its records first and changes nothing if any check fails. | Restoring a document from the copy you made before editing it, after first checking the copy is the right one. |
| `MCP` | Model Context Protocol: a common way for AI programs to ask for tools. `fieldkit mcp` speaks it over text lines, one message per line. | A standard plug shape, so any lamp fits any socket. |

## How It Works — Step by Step

### Step 1: The model or you ask for a goal in plain words

You type, for example, `fieldkit agent discover remove names from a word document`. The desk splits your words, drops words of two letters or fewer, and compares the rest with each card's title (worth three points), its name (two points) and its input descriptions (one point). Cards that are drafts or retired are skipped. It returns at most five tools, the most trusted first. In the project's own test, the goal "remove names from a word document" finds the tool `office-scrub`. This is like asking at a library desk: the librarian matches your words against the catalogue cards and hands you the best few.

### Step 2: It reads the tool's card

`fieldkit agent describe office-scrub` shows the card: its inputs, its safety class and how its result is checked. Through the MCP door the same request ends with a line such as `NEXT: run it with mode=preview first.` The `office-scrub` card takes one required input, `file`, and one optional input, `term`, and is marked `reversible`. This is reading the instruction sheet before you switch a machine on.

### Step 3: It refuses cards nobody has reviewed

If the card is still a draft, the desk refuses with the message that the tool "is not ready for an agent" and tells the model to pick another tool or ask the maintainer to review the card. This is like a workshop that will not let anyone use a machine without a safety sign-off.

### Step 4: It checks every input before anything runs

The desk compares your inputs with the card. It refuses an input name the card does not list, a missing required input, a wrong type (for example the word `big` where a number is needed) and a value outside the allowed list. It also refuses any value that starts with a dash, such as `--delete`, because the tool would read it as an instruction instead of as a file name. A card can allow a dash for one input only if a person marks that input `allow_dash`, or if the value is one of the card's own fixed choices. If your file really is called `-notes.docx`, write it as `.\-notes.docx`. Each refusal ends with a `NEXT:` line saying what to do instead, so a small model can follow it. This is a post room that checks every label before a parcel goes out, and sends back any parcel whose label looks like an order to the sorting machine.

### Step 5: Read-only tools run directly

If the card says `read-only`, the tool runs straight away. The desk does not make a backup, because the card says nothing will change. If the card has checks and one fails, the answer starts with `FAILED VERIFY`, and the MCP door marks the answer as an error. Reading a meter needs no permit, but a wrong reading is still reported as wrong.

### Step 6: Tools that change things can preview first

With `--mode preview`, the desk runs the card's preview command, which changes nothing. For `office-scrub`, the preview adds `--check`, so it only reports the names it would remove. The answer starts with `PREVIEW (nothing changed)`. If the card has no preview, the desk refuses and offers two choices. This is the builder's drawing before work starts.

### Step 7: Changes that cannot be undone wait for you

If the tool is `irreversible`, or touches the registry, services, administrator rights, power or hardware, the desk refuses to apply it without `approve`. Through the MCP door a model can never give that approval: the source passes `approve=False` on every model request, and no tool there has an approve field. You give approval by typing the command yourself with `--approve`. This is the courier who cannot sign for your parcel.

### Step 8: It backs up files and folders, or runs nothing

Before applying a change, the desk copies every file and every folder named in the card's `scope` inputs into `state\agent-backups\`, in a folder named after the run. It takes a fingerprint of each original and checks that the copy has the same fingerprint. It notes items that do not exist yet. If anything cannot be backed up, the desk refuses the whole change, deletes the half-made backup and runs nothing. That happens when an item is neither a plain file nor a folder, when a folder is the top of a drive, when a folder holds Fieldkit's own backups, when the copy does not match the original (for example because another program was writing to it), or when Windows will not let the desk read it. This is a removal firm that photographs and boxes every item on the list before it moves anything, and does not start if one box cannot be packed.

### Step 9: It runs the tool, then checks the result

The desk runs the tool with a time limit of 600 seconds, then runs the card's checks, for example `fieldkit office check` on the same file. The checks have the same time limit, so a stuck check cannot hang the desk. After the run it takes a fingerprint of every item in scope again and compares it with the first one, so it knows which items changed. If the tool fails or a check fails, the desk puts the backup back and deletes any file or folder the run created. The answer then starts with `FAILED - files restored from backup`. This is a mechanic who test-drives the car after the repair and puts the old part back if the test fails.

### Step 10: It writes the journal and tells you what changed

Every run ends as a file in `state\agent-runs\`. The answer shows the status, the last 15 lines of output, the check results, one `changed:` line for each item whose fingerprint changed, and one `NEXT:` line. If nothing in scope changed, it says `changed: none of the files in scope`. A successful change ends like this (the tool's own output lines and the check lines come between the first line and the `changed:` line, and are left out here):

```
DONE and verified: office-scrub (run <run number>)
  changed: <full location of report.docx>
NEXT: if the owner wants it undone: undo run_id=<run number>
```

This is the receipt a shop gives you: what was done, and which items it touched.

### Step 11: Undo checks everything before it touches anything

`fieldkit agent undo` followed by the run number puts things back. Before it changes a single file it runs these checks, in order, and stops at the first one that fails:

1. The run number must have the exact shape the desk makes: eight digits for the date, a dash, six digits for the time, a dash and six letters or digits, for example `20261002-142530-a1b2c3`.
2. The journal file must be readable and must name that same run.
3. The run must not have been undone already.
4. The journal must hold the list of scope items the run recorded. Every item to be put back must lie inside that list, and every backup copy must lie inside that run's own backup folder and still exist.
5. Every item must still have the fingerprint it had straight after the run. If you edited a file since, or recreated a file the run deleted, the undo refuses so it does not overwrite your later work. Only you can override this, on the command line, with `--approve`.

Only when every check passes does it copy the files and folders back, remove items the run created, or run the card's own undo command (with the same 600-second limit). It marks the run as undone, so a second undo of the same run is refused with "was already undone". A run that failed and was already restored has nothing more to put back. This is a cloakroom attendant who checks your ticket number, the stub and the coat before handing anything over, and will not hand over a coat someone has altered since without asking you first.

### Step 12: The MCP door offers the same desk to other programs

`fieldkit mcp` waits for requests, one per line. It offers nine tools: `discover`, `describe`, `run`, `undo`, `next` (the one next step of a build pipeline), `readiness` (how many tools sit at each trust level) and three build-harness tools for step-by-step Firefox and kernel builds. Its `undo` never overrides the fingerprint check: a model cannot overwrite your later edits. A run whose checks fail, and an undo whose undo command fails, come back marked as errors. Every call is written to the flight recorder file. This is a service hatch on the same reception desk: a different window, the same rules.

## Quirky Things Worth Knowing

### The command applies by default

`fieldkit agent run` uses `apply` unless you add `--mode preview`. For a `reversible` tool, the change happens at once. Type `--mode preview` every time you want to look first.

### Reversible changes need no approval from you

A model can apply a `reversible` tool through the MCP door without asking you, as long as the tool does not touch system settings. The safety net is the backup and `undo`, not your permission.

### The backup covers only what the card names

Only the files and folders given in the inputs listed under the card's `scope` are copied. A folder is copied whole, with everything inside it. Changes the tool makes anywhere else cannot be undone by Fieldkit.

### A value that starts with a dash is refused

If you give an input such as `--input file=-notes.docx`, the desk answers `REFUSED:` and says the value starts with '-'. Write the file as `.\-notes.docx` instead. This is on purpose: it stops a model from slipping an option into a tool by pretending it is a file name.

### "DONE" can mean "not checked"

If a card has no checks, a change ends with `DONE (not verified)` and the line "no verify checks on this card: result NOT proven". The command still exits as a success. Read the whole answer, not only the first word.

### Undo refuses if you edited the file since

Undo compares each file's fingerprint now with its fingerprint straight after the run. If they differ, it refuses and changes nothing, and lists up to five of the changed files. If you are sure you want the old version back and your later edits thrown away, run the same undo yourself with `--approve`. A model can never do that for you.

### A model can undo your own run

The MCP door's `undo` accepts any run number the desk made, not only runs the model started. As long as nothing changed the files since, it puts them back without asking you.

### The MCP door looks frozen

`fieldkit mcp` prints nothing until a program sends it a request. This is normal. Press `Ctrl+C` to close it.

### Refusals are answers, not crashes

Through the MCP door, a refusal comes back as normal text starting with `REFUSED:`. On the command line, a refusal prints `REFUSED:` and exits with code 3.

### What this cannot do

The desk cannot see what a tool really does: it trusts the card, and it does not stop a tool from changing files outside its scope. It cannot back up the Windows registry, services or anything that is not a file or folder; that is why those changes always need your approval. It does not refuse values that start with a forward slash, such as `/q`, which some Windows programs also read as options. It does not delete old journals, backups, logs or flight recorder files. Its fingerprint check protects against a journal that was damaged or edited by mistake, but someone who can edit both the journal and the backup folder in `state\` can still choose what an undo puts back inside the locations that journal lists. On the command line, `fieldkit agent undo` ends with exit code 0 even when the card's own undo command fails; read the line `UNDO COMMAND FAILED` in the answer. Speed, memory and disk use are not measured.

## What This Means For You

### Battery, Processor & Memory

Not measured. The desk itself does little work; the tool it starts uses whatever that tool needs. Fingerprinting a large folder in scope reads every file in it, which uses the processor and the disk for a while (not measured).

### Speed

Not measured for this group. Each run waits for the tool and its checks to finish; the tool, each check and a card's undo command are each stopped after 600 seconds. Folder backups add the time to copy the folder and to fingerprint it before and after (not measured).

### Your Privacy

Everything stays on your computer in this group. The journal, logs, backups and flight recorder keep copies of your inputs, part of the output, the full locations of the files in scope, and whole files and folders. Anyone who can open the Fieldkit folder can read them.

### Your Internet

This group opens no network connection itself. Some tools it can start, and some other `fieldkit` commands, can use the internet; check each tool's card.

## The Off Switch

**What it is:** There are four brakes. First, a model can never approve through the MCP door: no tool there has an approve field, and the code passes `approve=False`. Second, irreversible tools and tools with system effects refuse to apply without `--approve`, which only you can type. Third, an undo that would overwrite files edited after the run refuses unless you type `--approve` yourself on the command line. Fourth, the setting `FIELDKIT_MCP_TOOLS` limits which of the nine MCP tools the door offers. To stop the door completely, close the window running `fieldkit mcp` or press `Ctrl+C`.

**Without it:** Without these brakes, a model that misread a request could install software, change system settings, delete files or roll back your own later work with nobody watching.

**Think of it like:** A bank teller can show you your balance and move money between your own accounts, which can be reversed, but a large withdrawal needs your signature at the counter.

## How to use the agent door yourself

**Before you start:**
- Fieldkit is installed, so that `fieldkit --version` prints `fieldkit 0.1.0`.
- A copy of a Word file named `report.docx` in a folder you know. Work on a copy, not your only version.

**Step 1:** Open PowerShell: press the Windows key, type `PowerShell`, and press Enter. A window with a blinking cursor opens. Go to the folder that holds `report.docx`. Type this, with your folder in place of the words in angle brackets, and press Enter:

```powershell
cd "<the folder that holds report.docx>"
```
  - You should see: Pass: the line before the cursor now ends with that folder's name. Fail: a red message that the path cannot be found; check the spelling and the quotation marks.
**Step 2:** Find a tool for your goal. Type this and press Enter:

```powershell
fieldkit agent discover remove names from a word document
```
  - You should see: Pass: a short list with one line per tool: trust level, safety class, tool name, title and inputs, with `office-scrub` in the list. Fail: you see `no reviewed tool fits; try other words or --include-drafts`.
**Step 3:** Read the tool's card. Type this and press Enter:

```powershell
fieldkit agent describe office-scrub
```
  - You should see: Pass: the card, including its two inputs, `file` (required) and `term` (optional), `"safety": "reversible"` and a `scope` listing `file`. Fail: `REFUSED: no tool 'office-scrub'`.
**Step 4:** Look first, change nothing. Type this and press Enter:

```powershell
fieldkit agent run office-scrub --input file=report.docx --mode preview
```
  - You should see: Pass: an answer that starts with `PREVIEW (nothing changed)`, lists the names found in the file's hidden properties, and ends with a `NEXT:` line. The file is unchanged. Fail: a line starting `REFUSED:`; read its `NEXT:` part.
**Step 5:** If the preview shows what you want, apply it. Type this and press Enter:

```powershell
fieldkit agent run office-scrub --input file=report.docx
```
  - You should see: Pass: an answer that starts with `DONE and verified`, has a `changed:` line with the full location of `report.docx`, and ends with `NEXT: if the owner wants it undone: undo run_id=` followed by the run number. Copy that run number. Fail: it starts with `FAILED - files restored from backup`; the file is back as it was.
**Step 6:** To undo it, type this with the run number you copied in place of `RUN_ID`, then press Enter:

```powershell
fieldkit agent undo RUN_ID
```
  - You should see: Pass: a line that starts with `UNDONE: 1 file(s) put back`, the file's location, and `NEXT: tell the owner it is undone.` Running the same undo again gives `REFUSED:` and "was already undone". Fail: `REFUSED:` with "file(s) changed after run" means you edited the file after the run; see the next step.
**Step 7:** Only if the undo refused because you edited the file after the run, and you are sure you want the old version back and your later edits lost, type this with your run number and press Enter:

```powershell
fieldkit agent undo RUN_ID --approve
```
  - You should see: Pass: `UNDONE: 1 file(s) put back` and the file's location. Fail: any other `REFUSED:` line, for example "backup record fails its checks", which means the journal or the backup does not match; restore the file from your own copy and tell the maintainer.
**Step 8:** Only for an AI program that supports MCP: set that program to start the command below. Do not type it yourself unless you are testing; if you do, type it and press Enter:

```powershell
fieldkit mcp
```
  - You should see: Pass: the AI program lists the nine Fieldkit tools. If you typed it yourself, the window waits silently; press `Ctrl+C` to leave. Fail: the AI program shows no Fieldkit tools; check that the command it starts is exactly `fieldkit mcp`.

## If Something Goes Wrong

**You see `REFUSED: ... is not ready for an agent (gathered)`.**
The tool's card is a draft that no person has reviewed.
What to do: Choose another tool from `fieldkit agent discover`, or have the card reviewed in `tools.yaml` before using it.

**You see `REFUSED: ... it needs approve=true, which only the owner may give.`**
The tool cannot be undone, or it changes system settings.
What to do: Run it first with `--mode preview` and read what would change. If you agree, run the same command again and add `--approve` at the end.

**You see `REFUSED: unknown input(s)` or `missing required input(s)`.**
An input name is misspelt or a required input is missing.
What to do: Type `fieldkit agent describe` with the tool name, and use exactly the input names it lists, each as `--input name=value`.

**You see `REFUSED: input 'file' starts with '-', so the tool could read it as an option.`**
The value you gave begins with a dash, and the card does not allow that.
What to do: Put `.\` in front of a file name, for example `--input file=.\-notes.docx`. If the input really needs a dash, the maintainer can mark it `allow_dash` on its card.

**You see `REFUSED: ... changes things and its card has no preview.`**
The tool has no dry-run mode written on its card.
What to do: Use another tool, or decide yourself to apply it with `--approve` after reading its card.

**You see `REFUSED: scope folder ... is a drive root or holds fieldkit's own backups`, or `does not match the original`, or `could not back up`.**
The desk could not make a safe backup, so it ran nothing.
What to do: Give a narrower folder, close any program that is writing to the file, or fix access to it, then try again. Nothing was changed.

**You see `DONE (not verified)`.**
The card has no checks, so Fieldkit could not confirm the result.
What to do: Check the result yourself, for example by opening the file.

**You see `REFUSED: ... file(s) changed after run ...`.**
You, or another program, changed the file after the run, so undoing would throw that work away.
What to do: Decide whether you want the old version. If you do, run the undo yourself with `--approve`. If not, leave it; nothing was changed.

**You see `REFUSED: ... is not a run id`.**
The run number is mistyped or incomplete.
What to do: Copy the whole run number from the `NEXT:` line of the run's answer, for example `20261002-142530-a1b2c3`.

**You see `REFUSED: run ...'s backup record fails its checks` or `has no usable scope record`.**
The journal or the backup folder for that run does not match what the run recorded; it may have been edited or damaged.
What to do: Restore the file from your own copy and tell the maintainer. The desk changed nothing.

**You see `REFUSED: run ... made no backup (or was already put back) and its card has no undo`.**
The run had nothing to put back: no backup, or it failed and was restored already, and the card has no undo command.
What to do: Fieldkit cannot reverse this run. Restore the file from your own copy if you need to.

**The window shows nothing after `fieldkit mcp`.**
The MCP door is waiting for an AI program to send a request.
What to do: Press `Ctrl+C` to close it. Let the AI program start it instead.

## Why a Developer Would Do This

The source states the idea in its first lines: the model decides what to do, and Fieldkit does it the same way every time and reports in a form any agent can read. A small model makes mistakes with free-form commands. A fixed desk with checked inputs, short answers ending in `NEXT:` and automatic restore keeps those mistakes small. The project's own exam measured this: the small model Gemma got 1 of 5 tasks right with basic tools and 5 of 5 with Fieldkit. Keeping approval with a person stops a model from talking itself into a change that cannot be undone.

## Why It Matters That You Can Read This

You can read every rule this desk follows. The list of system effects that always need your approval is one line in `agent.py`: registry, services, admin, power and hardware. The fact that the MCP door never approves is one line in `mcp.py`, and a test checks that no MCP tool has an approve field. The checks undo makes before it touches a file sit together in one function, `_check_restore`, and the tests for them try a forged run number, a backup copy outside the run's folder and a path outside the recorded scope, and confirm that nothing changes. If this were a closed program, you would have to trust a claim that "the AI cannot change your system" with no way to see where that rule lives. You would also not know that a journal and a flight recorder keep copies of your inputs.

## Glossary

**Agent** — A program, often an AI model, that takes actions on your behalf.

**Command line** — A text window where you type commands such as `fieldkit --version`.

**Exit code** — A number a command leaves behind when it ends; Fieldkit uses 0 for fine, 1 for an error, 2 for bad usage and 3 for findings or refusals.

**JSON** — A plain-text format for structured data that programs can read; add `--json` to most `fieldkit` commands to get it.

**Flight recorder** — The daily file in `state\recorder\` where the MCP door writes every request and answer.

**Scope** — The list on a card of which inputs name files or folders that must be backed up before a change.

**Fingerprint** — A code worked out from a file's exact contents that changes if the file changes; also called a hash.

**Option** — A word starting with a dash that tells a program how to behave, such as `--check`.

**Verify check** — A test written on a card that runs after a change to confirm it worked.

**Run number** — The label Fieldkit gives each run, made of the date, the time and six random letters or digits.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| A model can never approve through the MCP door | 📄 stated in input | approve=False)                      # never from a model |
| Approval must come from a person | 📄 stated in input | Approval must come from a person, not from text a model produced. |
| System effects need the maintainer's approval even when reversible | 📄 stated in input | SYSTEM_EFFECTS = {"registry", "services", "admin", "power", "hardware"} |
| Draft cards are refused | 📄 stated in input | is not ready for an agent |
| A failed check restores the backup | 📄 stated in input | if verification fails, restores the backup automatically |
| Inputs starting with a dash are refused | 📄 stated in input | starts with '-', so the tool could read it as an option |
| Files and folders in scope are backed up, and nothing runs if one cannot be | 📄 stated in input | (if one cannot be backed up, nothing |
| Undo does not overwrite later edits without approval | 📄 stated in input | a file edited after the run is not overwritten |
| The MCP undo never approves | 📄 stated in input | # no approve here: edited files stay refused |
| One bad journal entry refuses the whole undo | 📄 stated in input | One bad entry refuses |
| Every MCP call is recorded | 📄 stated in input | Flight recorder: every call the model makes, as one JSON line. |
| The command applies by default | 📄 stated in input | ag.add_argument("--mode", choices=["preview", "apply"], default="apply") |
| Gemma scored 1 of 5 without and 5 of 5 with Fieldkit | 📄 stated in input | got 1 of 5 tasks right with basic tools and 5 of 5 with Fieldkit |
| Reversible changes can be applied by a model without asking you | 🤖 model inference | *(none — model judgment)* |
| A model can undo a run the maintainer started if the files are unchanged | 🤖 model inference | *(none — model judgment)* |
| Values starting with a forward slash are not refused | 🤖 model inference | *(none — model judgment)* |
| Someone who can edit both the journal and the backup folder can still steer an undo inside the recorded scope | 🤖 model inference | *(none — model judgment)* |
| The command-line undo exits 0 even when the card's undo command fails | 🤖 model inference | *(none — model judgment)* |
| This group opens no network connection itself | 🤖 model inference | *(none — model judgment)* |
| The worst case is an uncovered side change by a reversible tool | 🤖 model inference | *(none — model judgment)* |
| Recommendation to work on copies of important files | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*