# The teaching log: a record of every command an AI session ran, pulled out of the session's own transcript — Plain Language Guide

> Generated 2026-10-14 from `teach`

---

## Should You Run This?

Run it after any job you want the harness to learn from, and read the page before anyone else does. Do not publish the page without the privacy scan, because it copies command output as it was. Do not treat the page as an explanation of the job; it is the list of operations, and the explanation is elsewhere. Read it with the transcript open beside it the first few times, until you trust the match.

## Worst Case, Honestly

The most harmful plausible outcome is that a page written from a session that showed something private, such as a password printed by a careless command or a folder path with your name in it, is copied into a public repository because it looked like a dry technical log. The tool does not look for secrets; it copies output as it was. The cost would be that text being public until somebody notices. The second harm is smaller: a page cut at the default limit per command can hide the one line of output that mattered, and a reader who trusts the page over the transcript draws a wrong conclusion about what a command did.

## What Data This Touches

The tool reads one file: the session transcript under the hidden folder where the assistant keeps its sessions, chosen by the name or part of the name you give, or the newest one if you say newest. It writes one file: the page you name with --out. It reads nothing else, opens no network connection, starts no program and changes nothing in the session file. Be aware of what the transcript holds: every command the assistant ran and everything those commands printed, which can include folder paths with your user name, file contents, and anything a command showed on screen. All of that is copied into the page as it was. The page is therefore as private as the session was. Keep it with the project's private notes until it has been read; this project's privacy scanner is the check to run on it before it goes anywhere public.

## Before You Trust It

The tool reads one file and writes one file with your normal rights. Checking it means seeing that the page matches the transcript and that nothing else happened.

**Step 1:** Make a page from the newest session. In the Fieldkit folder, type:

```powershell
fieldkit teach newest --out teach-check.md
```

and press Enter.
  - Look for: Pass: one line saying wrote teach-check.md, a count of commands and failures, and the transcript it read. Fail: REFUSED with no transcript found; no session is recorded on this machine.
**Step 2:** Open teach-check.md in a text editor and pick any one numbered entry. Then open the transcript file named in step 1 in the same editor and search for the command text.
  - Look for: Pass: the command appears in the transcript, and the output in the page matches the text that follows it there. Fail: the command is not in the transcript, which would mean the page was not made from it.
**Step 3:** Confirm that nothing else was written. Look at the folder sorted by date.
  - Look for: Pass: teach-check.md is the only new file. Fail: any other new file.
**Step 4:** Run the tool's own tests. Type:

```powershell
python -m pytest -q tests/test_teach.py
```

and press Enter.
  - Look for: Pass: 5 passed. Fail: any failed line; do not rely on the page until the cause is known.

## The Big Picture

When an AI assistant does a job on your computer, it types commands into a terminal on your behalf, one after another, and reads what comes back. The assistant's chat window shows you its words; the commands themselves scroll past and are forgotten. This tool goes to the file where the assistant's session is recorded on your disk, picks out every terminal command in the order it was typed, attaches the text that came back and whether it failed, and writes all of it into one plain text page.

The maintainer asked for this on 2026-10-14 in these words: "every command you execute he will execute, every tool you call he will call". The idea is that a job done once by an assistant, say checking an old folder against the latest published code, leaves a complete record of the operations, and the harness, which is the collection of checked tools in this project, can later be taught those operations from the record. The page is the raw material for that teaching; it is not the teaching itself.

The assistant's own sentences are left out on purpose. What an assistant says about its work and what it actually ran are two different things, and this project already has a tool whose whole job is to distrust the first and check it against the second. This page is the second.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `transcript` | The file on your disk in which the assistant's session is recorded as it happens: every message, every command, every result. | The black box flight recorder on an aircraft: it writes everything down whether or not anyone reads it. |
| `tool call` | One action the assistant asked the computer to perform, such as running a terminal command or reading a file. Only terminal commands go into the log. | One line in a cook's order book: what was asked for, in which order. |
| `tool result` | The text the computer sent back after a tool call, and whether the call failed. | The receipt that comes back with each order, stamped paid or refused. |
| `teaching log` | The page this tool writes: one numbered entry per command, with the time, the command, its output and ok or FAILED. | A lab notebook copied out neatly from the scribbles: the same steps, in order, readable by somebody else. |
| `harness` | This project's collection of tools that each have a card, a test and a self-check, so a job is done the same way every time. | A workshop where every machine has a label, a manual and a safety inspection sticker. |
| `since` | An optional time; commands before it are left out, so a log can cover one job inside a long session. | Tearing out the pages of the notebook from the day the job started. |
| `max-chars` | How much of each command's output is kept on the page; longer output is cut and the page says how much was left out. | A photocopier set to copy only the first page of each report, with a note saying there were more pages. |

## How It Works — Step by Step

### Step 1: Find the transcript

You name a session, or say newest. The tool looks under the assistant's sessions folder for a file whose name starts with what you gave, or takes the one changed most recently. If nothing matches, it stops and says so; it never makes an empty page. It is like a librarian who either finds the exact ledger you asked for or tells you it is not on the shelf.

### Step 2: Read it line by line

The transcript is a file with one record per line. The tool reads each line, skips any it cannot understand, and looks inside each record for two kinds of entry: a tool call and a tool result. A tool call whose tool is a terminal, Bash or PowerShell, is kept with its time, its command and the short description the assistant gave. Other tools, such as reading a file, are ignored. It is like reading a diary and copying out only the lines that begin with I typed. A line it cannot read, for example the half-written last line of a session that is still running, is skipped without complaint, and the rest of the file is read as normal. Nothing is written back to the transcript at any point; it is opened for reading only.

### Step 3: Match each result to its call

Every tool call carries a short identifier, and the result that comes back later carries the same one. The tool keeps the calls it has not yet seen results for, and when a result arrives, attaches its text and its failed flag to the right call. A call with no result stays in the log with no output. It is like matching each order slip to the receipt with the same number.

### Step 4: Write the page

For each command, in order, the page gets a numbered heading with the time and the description, the command in a code box, the output in a second code box cut at the limit if long, and the word ok or FAILED. A short summary at the end counts the commands, the failures and the first words of the distinct commands, so you can see at a glance what kinds of operations the job used. It is the same layout the live wrapper script writes, so logs from either source read alike. The summary's list of first words is the quickest view of a job: a page whose first words are git, diff, grep and gh was a comparison job; one whose first words are apt, dpkg and make was an install. That list is what a person reads first when deciding which parts of the job deserve to become checked tools.

## Quirky Things Worth Knowing

### The assistant's words are deliberately absent

You will see no explanations in the page, only commands and results. That is the design: this page records what was run. The reasoning lives in the session and in the documents the session wrote.

### Only terminal commands are kept

File reads, edits and web fetches done through the assistant's other tools are not in the log. A job that mostly edited files through the editor tool leaves a short log. Use --tools to widen or narrow which tool names count.

### A command that failed is still in the log

It is marked FAILED with whatever came back. Failures are part of the record: a job that tried three things before the fourth worked teaches more than the fourth alone.

### The time is the assistant's clock

Times come from the transcript, in the form the assistant wrote them, usually universal time with a Z at the end. They may not match your wall clock.

### A long session makes a long page

Hundreds of commands produce hundreds of entries. --since cuts the beginning off; --max-chars cuts each output. The page tells you how many characters it left out of each cut output.

### A log is not proof the job was done well

The page shows every command and every result, including the ones that led nowhere. A job that ran forty commands and reached a wrong conclusion leaves a page as tidy as a job that reached a right one. The page is evidence of what happened, in the way a till roll is evidence of what was sold; judging whether it was the right thing to do is still a person's job, with the page open beside them.

### What this cannot do

It cannot say why a command was run, only that it was. It does not know which commands mattered and which were dead ends. It does not scan for secrets or names; the project's privacy scanner does that and must be run on the page before it goes anywhere public. It cannot read a session that has been deleted from disk, and it cannot reach a session from another machine. It does not run anything: the commands in the page are text. A command typed across several lines with a here-document is copied whole, so a reader must not paste the page back into a terminal. Nothing about its speed or memory use is measured.

## What This Means For You

### Battery, Processor & Memory

Not measured. The tool reads one text file once and writes one text file.

### Speed

Not measured. It runs once and exits; nothing else becomes faster or slower.

### Your Privacy

Nothing leaves your machine. The page holds whatever the session's commands printed, so treat it as private until it has been read and scanned.

### Your Internet

None. No connection is opened.

## The Off Switch

**What it is:** There is nothing running to stop. The one refusal is built in: when no transcript matches what you asked for, the tool stops with REFUSED and writes nothing. To make a smaller page, --since and --max-chars limit what goes in.

**Without it:** Without the refusal, asking for a session that does not exist would produce an empty page that looks like a job with no commands, which is a false record.

**Think of it like:** A photocopier that refuses to print when the tray is empty instead of handing you blank sheets.

## How to use this

**Before you start:**
- Fieldkit installed, so that typing fieldkit in a terminal prints its help
- at least one assistant session recorded on this computer

**Step 1:** Open a terminal. On Windows, press the Windows key, type PowerShell, and press Enter. A window with a blinking cursor opens. On Linux, open the application called Terminal.
  - You should see: A prompt with a blinking cursor.
**Step 2:** Go to the Fieldkit folder. Type:

```powershell
cd (fieldkit where)
```

and press Enter.
  - You should see: The prompt shows the Fieldkit folder.
**Step 3:** Write the page for the newest session. Type:

```powershell
fieldkit teach newest --out teaching-log.md
```

and press Enter.
  - You should see: Pass: wrote teaching-log.md: N command(s), M failed, from the transcript path. Fail: REFUSED: no transcript found.
**Step 4:** To cover only one job inside a long session, add the time it started. Type:

```powershell
fieldkit teach newest --out teaching-log.md --since 2026-10-14T14:40
```

and press Enter.
  - You should see: The same line, with a smaller count.
**Step 5:** Before the page goes anywhere shared, scan it. Type:

```powershell
fieldkit privacy scan teaching-log.md
```

and press Enter.
  - You should see: Pass: 0 finding(s). Fail: a list of lines with paths, names or secrets to reword first.
**Step 6:** Keep the page with the project it belongs to, in a folder that git ignores or in the private notes, and give it the date in its name. Open it when the job is to be done again, or when the harness is to be taught the job.
  - You should see: The page sits beside the project's other records, dated, and nothing about it has been published.

## If Something Goes Wrong

**REFUSED: no transcript found for the name you gave.**
No session file under the assistant's sessions folder starts with that text, or the sessions folder is elsewhere on this machine.
What to do: Use newest, or give the full path to the .jsonl file.

**The page has far fewer commands than the session ran.**
Most of the work went through tools other than the terminal, which are not kept, or --since cut the start off.
What to do: Drop --since, or add the other tool names with --tools.

**An entry says (no output).**
The command produced nothing, or the result never arrived in the transcript, for example because the session ended first.
What to do: Check the transcript at that time; the command is still a true record of what was run.

**The output of a command ends with more characters not shown.**
The output was longer than the limit per command, 4000 characters unless you set --max-chars.
What to do: Raise --max-chars, or read that command's result in the transcript.

**The page is written but the count of commands is zero.**
The session used no terminal commands after the --since time, or its commands went through a tool name not in the kept list.
What to do: Drop --since to see the whole session, or add the tool names with --tools.

## Why a Developer Would Do This

The developer could have asked the assistant to copy each command into a file as it worked. That costs effort on every command and misses the ones nobody remembered to copy. The transcript already holds all of them, so the developer read it instead: zero effort during the job, nothing missed, and the record cannot be softened by the session that made it. Leaving the assistant's words out was the same choice this project makes in its recorder: judge a session by what it ran, not by what it said. The one thing the developer did not do was make the page pretty: no colours, no summary of what the job achieved, no verdict. A record that starts to interpret itself stops being a record.

## Why It Matters That You Can Read This

A record of what an assistant did on your computer is only trustworthy if you can see how the record was made. This tool is short enough to read in one sitting: it reads the transcript, keeps terminal commands, matches results by identifier, and writes them out. If this were a hidden service, you would have to trust that nothing was dropped or reworded. Here you can check the page against the transcript yourself, line for line.

## Glossary

**session** — One conversation with the assistant, from start to finish, recorded as it happens.

**jsonl** — A text file with one record per line, each written in a format programs read easily.

**identifier** — A short unique label a tool call carries so its result can be matched to it.

**here-document** — A way of typing several lines of text into one terminal command; the whole block is copied into the page.

**privacy scan** — This project's check for names, paths, addresses and secrets in files that might be shared.

**FAILED** — The mark on a log entry whose command came back as an error.

**harness** — The project's collection of checked tools, each with a card, a test and a self-check.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| The maintainer asked for every command and tool call to be recorded for the harness | 📄 stated in input | every command you execute he will execute, every tool you call he will call |
| Only Bash and PowerShell calls are kept by default | 📄 stated in input | TOOLS = ("Bash", "PowerShell") |
| A missing transcript is refused and no file is written | 📄 stated in input | refuses when the transcript is not found |
| The session's words are left out on purpose | 📄 stated in input | The session's own text is left out on purpose |
| The tool opens no network connection and changes nothing in the transcript | 🤖 model inference | *(none — model judgment)* |
| Speed and memory are not measured | 🤖 model inference | *(none — model judgment)* |
| The page copies output as it was and does not look for secrets | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*