# Test whether a small AI model on your own computer can do five real coding jobs, with and without Fieldkit — Plain Language Guide

> Generated 2026-10-02 from `exam`

---

## Should You Run This?

Run it if you want to know whether your own small local model can do these five kinds of jobs, and whether Fieldkit's tools help it. Keep the default local address unless you mean to send the practice project elsewhere. Do not treat a good score as proof that the model will handle your real projects: it is five jobs on one invented project, marked against tools written by the same author.

## Worst Case, Honestly

The most likely harm is a wrong conclusion, not damage to your computer. Example: you see 5 of 5 with the kit and decide your model can now handle your real build failures, then it fails on your own logs. The exam has five jobs on one invented project, and the same author wrote both the jobs and the kit tools, so a good score here does not prove the model will do your real work. A second harm is time: each request to the model may wait up to 900 seconds, and each job may use up to 10 rounds, so a slow model can keep your computer busy for a long time. How long a full run takes on your machine is not measured. A third, smaller harm: if the program is killed part-way (for example you close the window), its temporary practice folder may stay behind in your temporary files folder under a name that starts `fieldkit-exam-`.

## What Data This Touches

The exam reads and writes only these things. First, it writes the invented practice project into a new temporary folder, and deletes that folder when the job ends. Second, it sends the job text, the tool results and the conversation so far to the model server address. By default that address is `http://localhost:1234/v1`, which is a program on your own computer (LM Studio's usual address), so nothing leaves your machine. Third, `fieldkit exam run` writes one result file per job and toolset into the `exam-results` folder inside the Fieldkit folder, or into the folder you name with `--out`. Each file holds the model name, pass or fail, the reason, the first 400 characters of the answer, and the name and arguments of every tool call. The tools can only read inside the practice project. A request for a file outside it gets the reply `ERROR: path is outside the project`. The practice project holds no personal data. Your own files are never shown to the model. If you change `--base` to an address on another computer or on the internet, the job text and the practice project's contents go to that address instead.

## Before You Trust It

This exam talks to a program on your computer and writes files. You can check what it will do, and that its marking works, without running any AI model at all.

**Step 1:** Open PowerShell: press the Windows key, type `PowerShell`, and press Enter. Then type `fieldkit exam --help` and press Enter.
  - Look for: Pass: you see a usage message that lists `{run,report}`, `--model`, `--toolset`, `--task`, `--base`, `--out` and `--max-rounds`, and `--base` says `OpenAI-compatible server (LM Studio)`. Fail: "not recognised" means Fieldkit is not installed or not on your path.
**Step 2:** In the same window, type `fieldkit exam report` and press Enter.
  - Look for: Pass: you see a table header starting `run`, `model`, `toolset`, `passed`, followed by any saved results. If you have never run the exam, you see only the header. This command never contacts a model. Fail: an error message instead of a table.
**Step 3:** Before a real run, check where the exam will send its messages. Type `fieldkit exam run --help` and press Enter, and read the `--base` line.
  - Look for: Pass: the default is the local address on your own computer (`localhost`). Only add `--base` yourself if you mean to send the practice project to another machine.
**Step 4:** After a run, open the `exam-results` folder inside the Fieldkit folder in File Explorer, and open one of the `.json` files with Notepad.
  - Look for: Pass: you see the job name, `passed`, the reason, the answer and the list of tool calls, all about invented files such as `src/cache.py`. Fail: any path or text from your own files would mean something is wrong; stop and report it.

## The Big Picture

The `fieldkit exam` command is a test bench. It sets five small jobs for an AI model that runs on your own computer, and it marks every answer with a fixed script. It runs each job twice: once with three basic tools (list a folder, read a file, search for text) and once with those three plus three Fieldkit tools (`find`, `triage` and `refcheck`). Think of a driving test taken twice, once in a car with no satnav and once with one, marked by the same examiner with the same checklist.

The jobs happen inside a small practice project that the code writes fresh each time, with the same 29 files and the same bytes every time. The five jobs are: find where a named function is defined; spot that a second name does not exist at all (a trap); read a 1,488-line failed build log and name the missing package and the fix; find which file holds a given block of code; and list which files a manifest names but the project lacks.

You get a pass or fail for each job, the reason, how many rounds and tool calls the model needed, how many words of input (tokens) it read, and how many seconds it took. On the author's laptop, the same small model (Gemma) got 1 of 5 jobs right with the basic tools and 5 of 5 with Fieldkit. That is the only measured result. Every other speed or size figure is not measured.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `Model` | An AI program that reads text and writes text. Here it runs on your own computer inside a program such as LM Studio. | A new trainee who can only see the project through the tools you hand them. |
| `Toolset (`raw` or `kit`)` | The set of tools the model may use. `raw` has three basic tools. `kit` has those three plus `find`, `triage` and `refcheck`. | A toolbox with only a screwdriver, a torch and a tape measure, against the same box with a stud finder added. |
| `Fixture` | The small practice project the exam writes into a temporary folder before each job. It is invented and identical every time. | A practice exam paper that is printed fresh for every candidate, word for word the same. |
| `Grader` | A short fixed script that marks one job by looking for the right file, line, package name or list in the answer. No AI marks another AI. | A multiple-choice answer key laid over the answer sheet. |
| ``ANSWER:` line` | The model must finish with a line that starts `ANSWER:`. The grader reads the text after the last such line. If the line is missing, the exam still marks the whole reply but records a format miss. | Writing your final answer in the box at the bottom of the page. |
| `Round` | One message to the model and one reply. A job stops after 10 rounds by default. | One turn in a game of twenty questions. |
| `Token` | A piece of a word. The exam counts how many tokens the model had to read, because longer input makes a small model slower. | Counting the pages a trainee had to read before answering. |

## How It Works — Step by Step

### Step 1: You start a run

You type `fieldkit exam run --model` followed by the model's name as your model server lists it. By default the exam runs all five jobs, each with both toolsets, so one model gets 10 job runs.

### Step 2: The exam writes a fresh practice project

For each job run it creates a temporary folder and writes the same 29 invented files into it. These include a small app, 15 plugin files, a 1,488-line failed build log, a Windows-style file holding a block of code, and a `MANIFEST.txt` that lists 12 files, three of which do not exist. It is like laying out the same set of props on the table before each candidate walks in.

### Step 3: The model gets the job and the tools

The model receives a short instruction ("You can only see the project through the tools"), the job question, and the list of tools it may use. For example, the trap job asks: "In this project, where is the function `rebalance_cache` defined? Give the file and line." The honest answer is that it is not defined anywhere; only a different name, `rebalance_caches`, appears in a design note.

### Step 4: The model asks for tools, the exam runs them

Each time the model asks for a tool, the exam runs it inside the practice folder and sends back the result, cut to at most 8,000 characters. `read_file` shows at most 120 lines at a time. `search_text` shows at most 40 matching lines. The exam writes down every tool call. This repeats until the model answers without asking for a tool, or until 10 rounds pass.

### Step 5: The kit tools do the thinking

With the `kit` toolset, the model can call `find`, `triage` or `refcheck`. These give a short verdict and a `NEXT:` line telling the model what to answer. For example, `find` with `apply_fragment` replies `DEFINED: `apply_fragment` is defined at src/build/kernel_tools.py:40` followed by `NEXT: answer src/build/kernel_tools.py:40.` `triage` on the build log names the known failure `unmet-build-deps` and the evidence line `dpkg-checkbuilddeps: error: Unmet build dependencies: libdw-dev:native`. `refcheck` on `MANIFEST.txt` replies `3 of 12 missing (manifest):` and lists them. Without the kit, the model has to page through the files itself.

### Step 6: A script marks the answer

The exam takes the text after the last `ANSWER:` line and checks it with that job's grader. For the build-log job, the answer passes only if it names `libdw-dev` and an `apt install` or `apt-get install` command. An answer of "Missing libdw-dev." fails, because it gives no fix. If the model ran out of rounds or the server could not be reached, the job is marked as failed with the reason "no final answer", never as a pass.

### Step 7: You see a line per job and a summary table

After each job you see `PASS` or `FAIL`, the reason, and the rounds, calls, prompt tokens and seconds. At the end you get a table with one row per run, model and toolset: tasks passed, prompt tokens, seconds, tool calls, format misses, and (for the kit) how often the model chose the tool made for that job. `fieldkit exam report` rebuilds the same table later from the saved result files without contacting any model.

## Quirky Things Worth Knowing

### A pass with the kit does not mean the model used the kit

The summary keeps two numbers apart: how many jobs passed, and how many times the model chose the tool made for that job (`chose kit tool`). A model can pass with the kit toolset by using the basic tools. Read both columns.

### The trap job rewards saying "it does not exist"

One job asks where `rebalance_cache` is defined. It is not defined anywhere. A model that names a file, such as `src/cache.py`, fails. This tests whether the model invents an answer instead of admitting it found nothing.

### The kit tools were adjusted after watching Gemma fail

The tests record three fixes dated 2026-09-29. In one, `find` listed the near-miss name `rebalance_caches` and Gemma then gave a Markdown file as the definition. In another, Gemma pasted all 12 manifest paths into `find`, got "NOT FOUND", and reported all of them as missing. The tools now handle both cases. This means the 5 of 5 result comes from tools tuned on this same test.

### Some descriptions in the code do not match the code

The practice project's own description says "About 40 files" and "a 1,500-line" log. The code writes 29 files and a 1,488-line log. The top description of the exam names only `find` and `triage` as kit tools, but the kit also has `refcheck`. These are wording slips; they do not change the marks.

### Speed figures in the code's comments are not measurements here

A comment in the code gives reading speeds for two models. Those speeds are not in the verified measurements, so treat them as not measured.

### Running without a model name does nothing

If you type `fieldkit exam run` with no `--model`, there is no model to test. You get an empty table and no error.

## What This Means For You

### Battery, Processor & Memory

The exam itself does little work: it writes small files and runs searches. The model server does the heavy work, and its processor, memory and battery use depend on your model. Not measured.

### Speed

Not measured for your machine. The only verified result is the pass count: 1 of 5 with basic tools and 5 of 5 with Fieldkit for Gemma on the author's laptop. Each request to the model may wait up to 900 seconds before the exam gives up on it.

### Your Privacy

With the default address, everything stays on your computer. The model only sees the invented practice project, never your own files. The saved result files hold the model's answers and tool calls about that invented project.

### Your Internet

None by default: the default address `http://localhost:1234/v1` is on your own computer. Network use happens only if you point `--base` at another machine. The amount of data sent in that case is not measured.

## The Off Switch

**What it is:** Press `Ctrl+C` in the window where the exam runs to stop it. Inside the exam there are three built-in limits: the round limit (`--max-rounds`, 10 by default), the 900-second wait limit per request to the model, and the folder fence that refuses any file path outside the practice project.

**Without it:** Without the round limit, a model that keeps asking for tools could loop for ever. Without the folder fence, a model could ask to read any file on your computer, such as your documents, and that text would go to the model server.

**Think of it like:** An exam hall with a clock on the wall and a locked door: the candidate gets a fixed time and can only use what is on the desk.

## How to use this

**Before you start:**
- Fieldkit installed, so that `fieldkit` works in a terminal (see verification step 1).
- LM Studio (or another program that offers an OpenAI-compatible server) running on your computer, with its local server switched on at the default address.
- A model loaded in that server that can call tools. You need the model's exact name as the server lists it, for example `google/gemma-4-e2b`.
- For the `find` tool: the `pfind` helper gathered into Fieldkit. If it is missing, run `fieldkit gather --only pfind`.
- Time: a full run can take a long time on a small laptop. How long is not measured.

**Step 1:** Open PowerShell: press the Windows key, type `PowerShell`, and press Enter.
  - You should see: A window opens with a blinking cursor.
**Step 2:** Type the command below and press Enter. If your model has a different name in LM Studio, type that name instead of `google/gemma-4-e2b`.

```powershell
fieldkit exam run --model google/gemma-4-e2b
```
  - You should see: Pass: a line appears for each job and toolset, such as `google/gemma-4-e2b | locate     | raw ...`, followed by `PASS` or `FAIL` and the reason. Fail: the reason `no final answer` on every job usually means the server is not running (see Troubleshooting).
**Step 3:** Wait until the summary table appears at the end.
  - You should see: One row for `raw` and one for `kit`, each with a `passed` value such as `1/5` or `5/5`.
**Step 4:** To test only one job, add `--task` and the job name. Example: the command below. The job names are `locate`, `trap`, `build-log`, `snippet` and `manifest`.

```powershell
fieldkit exam run --model google/gemma-4-e2b --task trap
```
  - You should see: Only that job runs, once with each toolset.
**Step 5:** To see all saved results again later, type the command below and press Enter.

```powershell
fieldkit exam report
```
  - You should see: The same summary table, built from the files in `exam-results`, with no model contacted.

## If Something Goes Wrong

**Every job shows `FAIL  no final answer` with 1 round, and the run finishes in seconds.**
The exam could not reach the model server. LM Studio is closed, its server is off, or it uses a different address.
What to do: Start LM Studio, switch on its local server, load the model, and run the command again. If the server uses another address, add `--base` with that address.

**A job shows `FAIL  no final answer` after 10 rounds.**
The model kept asking for tools and never gave a final answer within the round limit.
What to do: That is a real fail for the model. You can allow more rounds with `--max-rounds 20`, but the result is then not comparable with runs at 10.

**The `find` tool replies `ERROR: find needs pfind, which is not gathered here`.**
The `pfind` helper that `find` uses is not in your Fieldkit folder.
What to do: Run `fieldkit gather --only pfind`, then run the exam again.

**The summary shows a high `fmt miss` count.**
The model did not end its reply with a line starting `ANSWER:`.
What to do: Nothing to fix in the exam. The answer was still marked; the count shows the model did not follow the instruction.

**The table is empty after `fieldkit exam run`.**
You did not give `--model`, so no model was tested.
What to do: Add `--model` and your model's name.

## Why a Developer Would Do This

Small models running on a laptop are slow and make mistakes, and it is tempting to believe a single good demo. This exam exists so that the claim "the tools matter more than the model" can be checked by a script instead of by opinion. Fixed practice files, fixed marking scripts and saved records mean anyone can repeat the test and get comparable numbers.

## Why It Matters That You Can Read This

Because you can read the five jobs and their marking scripts, you can see exactly what "5 of 5" means: five narrow jobs on one invented project. If you could not read them, you would have to trust a headline number with no idea what was tested or how it was marked. You can also see that the same author wrote the jobs and the tools, and that the tools were adjusted after watching the model fail. The source says this openly; a closed benchmark would not have to. Anyone can rerun the same exam on their own model and get their own numbers instead of trusting these.

## Glossary

**LM Studio** — A program that runs AI models on your own computer and answers requests at a local address.

**OpenAI-compatible server** — A program that accepts requests in the same message format that many AI services use, so one client can talk to any of them.

**Localhost** — The name your computer uses for itself, so a request to it never leaves the machine.

**Manifest** — A file that lists other files which should exist.

**Build log** — The long text record a program writes while it compiles software, including the reason it stopped.

**Tool call** — A request from the model to run one named tool, such as reading a file.

**Temporary folder** — A folder Windows keeps for short-lived files that programs create and delete.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Gemma scored 1 of 5 basic, 5 of 5 with Fieldkit | 📄 stated in input | got 1 of 5 tasks right with basic tools and 5 of 5 with Fieldkit |
| No model marks another model | 📄 stated in input | no model judges another |
| Practice project is identical every run | 📄 stated in input | Same bytes every time. |
| Tools cannot read outside the practice folder | 📄 stated in input | All paths are confined to the exam folder: a model cannot read outside it. |
| Running out of rounds is never a pass | 📄 stated in input | Truncation is recorded, never scored as a pass. |
| Pass count and kit-tool use are reported separately | 📄 stated in input | so "kit passed" and "kit was used" stay apart |
| Kit tools were adjusted after Gemma failures | 📄 stated in input | find listed rebalance_caches for rebalance_cache and Gemma answered docs/DESIGN.md:3 |
| Default server is on your own computer | 📄 stated in input | DEFAULT_BASE = "http://localhost:1234/v1" |
| Fixture writes 29 files and a 1,488-line log, not the documented figures | 🤖 model inference | *(none — model judgment)* |
| A good score does not prove real-world ability | 🤖 model inference | *(none — model judgment)* |
| Nothing leaves the machine with the default address | 🤖 model inference | *(none — model judgment)* |
| A killed run may leave a temporary folder behind | 🤖 model inference | *(none — model judgment)* |
| Run time, CPU, memory and battery use | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*