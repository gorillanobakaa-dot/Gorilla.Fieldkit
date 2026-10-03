# Carrying the Gorilla changes onto a new Firefox release, one checked step at a time — Plain Language Guide

> Generated 2026-10-02 from `port-engine`

---

## Should You Run This?

Run it only if you maintain a Gorilla Firefox patch set and are prepared to read every parked change and every brief before building. Do not run `drive` while you are editing the working copy. Do not build or install the result until `vault verify` says `INTACT`, `deferred` lists nothing you have not decided, and the final checks pass. The measured 157 port shows the method can reproduce a built tree exactly, but no source material states that a ported change is functionally right; the harness itself lists that as something it cannot determine. Kernel porting is not available.

## Worst Case, Honestly

The most harmful plausible outcome is a wrong change that passes every check and is then built and installed. The source names a real case: a half-removed block of code in `DesktopActorRegistry.sys.mjs` built without error and then broke the installed browser, with no address bar and no extensions. The harness now checks that changed script files parse before any build, but a change that parses and is still wrong can get through.

A second real case is a privacy cut that is lost in the port. Then the browser quietly contacts Mozilla again. The measurements show that Gorilla 155.0.1 asked 8 Mozilla hosts for things on its own, which an earlier check had missed. This group does not catch that; the leak tests in a later stage do.

A third harm is lost work. When a model's attempt fails, the harness resets the whole working copy to the last checkpoint. If you were editing a file in the working copy at the same time and had not recorded it, your edit is wiped.

## What Data This Touches

The port engine works on Firefox source code, not on your personal browsing data. It reads and writes these things on your computer: the vault folder, the working copy (by default in a `Build.Work` folder next to the vault), the task records in Fieldkit's `state/build-harness/` folder (`task.json`, the `journal.jsonl` log, `drive.log`), the decision log `state/build-harness/decisions.jsonl`, and, when you run `export-hand`, new patch files inside your Gorilla patch set. It also writes a separate settings profile for Gorilla OpenCode (the `worker-config` folder) built from your own Gorilla OpenCode settings; the source states that secrets are not copied into it.

These things leave your computer: a request to Mozilla's version list (`product-details.mozilla.org`) to learn the latest stable version; a `git ls-remote` and a `git clone` to Mozilla's Firefox repository on GitHub (`github.com/mozilla-firefox/firefox`); and, for `vault measure`, a size-only request to `archive.mozilla.org` that downloads nothing. The kernel path contacts `kernel.org`. The job text a model reads goes to the model endpoints listed in your Gorilla OpenCode settings; the source describes a local model (Gemma on LM Studio). If your settings point at a remote service, the job text, which is Firefox source code, goes there. No personal data is sent by this group.

When `export-hand` writes a patch for the public patch set, it rewrites the recorded reason into a technical note: it strips dates and build narration and replaces personal wording about the maintainer with the word Gorilla.

## Before You Trust It

This group edits Firefox source and runs a model against it. Before you build or install anything it produced, check that the untouched copy is intact, that you read the plan, and that nothing was parked without your knowledge. Each step below uses only commands the harness provides. To open a terminal: in File Explorer, open your Fieldkit folder, click the address bar, type `powershell` and press Enter.

**Step 1:** Check the untouched copy: run `fieldkit build-harness vault verify firefox`.
  - Look for: Pass: the output starts with `INTACT`. Fail: `DAMAGED` followed by a list of problems, such as a changed file. Do not use the vault until it is fetched again.
**Step 2:** Read the current task before approving it: run `fieldkit build-harness status`.
  - Look for: Pass: you see the task name, `approved` set to false before you approve, and the step counts. Fail: `no build job has been started`, which means you have not run `start` yet.
**Step 3:** Read the recent history: run `fieldkit build-harness log`.
  - Look for: Pass: up to 40 recent events, each with a time, an event name such as `submit`, `revert` or `auto-done`, and the step. Look for `revert` and `too-large` events: they show what failed or was parked.
**Step 4:** List every parked change: run `fieldkit build-harness deferred firefox-157.0` (use your own task name).
  - Look for: Pass: a line such as `0 step(s) are parked`, or a list you have read one by one. Fail: entries you did not expect. Read each with `fieldkit build-harness deferred firefox-157.0 STEP` before building.
**Step 5:** Run the harness's own tests for this group: `python -m pytest tests -k buildh`.
  - Look for: Pass: the last line reports passed tests and no failures. Fail: any line with `failed`. The measured full suite on 2026-10-02 was 584 passed, 1 skipped, 2 xfailed.
**Step 6:** Ask the recorder for incidents: run `fieldkit build-harness report`.
  - Look for: Pass: no finding at the `incident` level, and the report is saved as `recorder-report.json`. Fail: any `INCIDENT` line; read it before going on.
**Step 7:** After `fieldkit build-harness export-hand`, read each line that starts with `[port fix]` or `[privacy cut]` and compare it with what the edit really did.
  - Look for: Pass: each line ends in `(recorded)` and the label matches the edit: a privacy cut stops a connection, a port fix makes the new Firefox build or work. Fail: a line ends in `keyword guess, nothing recorded: review`, or a label is wrong. Do not publish the patch set until every such line is checked.

## The Big Picture

Gorilla Firefox is Firefox with a set of changes on top: privacy cuts, new wording, new icons, settings that are locked. Each change is stored as a `.patch` file in the Gorilla patch set. When Mozilla releases a new Firefox, the old changes no longer fit exactly, because Mozilla moved, renamed or rewrote some of the lines they touch. The port engine is the part of Fieldkit that carries every change across to the new release and proves each one landed.

It works like a careful removal firm. First it fetches an untouched copy of the new Firefox and locks it away (the vault). Then it makes a working copy, applies every patch it can, and turns each change that does not fit into one small job. It tries each job itself first. Only the jobs it cannot do go to a small local model (Gemma), and the model never edits a file: it answers short questions in text, and the harness makes the edit and checks it. Anything that needs a human judgement, such as a change whose target Mozilla deleted, is parked for a person with a written explanation, never guessed.

You drive all of this with the `fieldkit build-harness` command. Every step is saved on disk, so you can stop and carry on later. In the Firefox 157.0 port measured on 2026-10-02, Firefox 157.0 was built from Mozilla's release tag `FIREFOX_157_0_RELEASE`, and the person-made fixes from that port were exported as 20 patches (3 port fixes, 17 privacy cuts) that, replayed in order, reproduce the built tree exactly (identical git tree hash).

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `patch` | A small text file that says which lines to remove from a Firefox file and which lines to add. | A tailor's alteration note: take in two centimetres here, add a pocket there. |
| `hunk` | One block of changes inside a patch. A patch can hold many hunks, and each hunk can fit or fail on its own. | One alteration on the tailor's note, such as the pocket. |
| `vault` | An untouched, read-only copy of the new Firefox source, exactly as Mozilla published it. Nobody works in it. | The master key kept in a safe: you cut copies from it, you never use it on the door. |
| `working copy` | A fresh copy made from the vault. All the changes happen here. | A photocopy of a contract that you mark up, while the original stays in the safe. |
| `upstream` | Mozilla, the source of Firefox. The harness asks Mozilla's official version list which release is the latest stable one. | The manufacturer whose product you are modifying. |
| `task and step` | A task is the whole port of one Firefox version (for example `firefox-157.0`). It is cut into steps, and each step is small enough for a small model or a script. | A house move cut into one box per job, each box ticked off a list. |
| `packet` | The one piece of text a model sees for a job: the change, the part of the file it applies to, and how to answer. | A single work order handed to a temp worker, with nothing else on the desk. |
| `checkpoint` | A saved point in the working copy after each step passes, so a later mistake can be undone back to it. | A save point in a video game. |
| `deferred step` | A change the harness parked because the thing it changes is gone from the new Firefox. A person decides what happens to it. | A parcel the courier will not leave because the address no longer exists. |
| `hand edit` | A change a person made in the working copy. The `record` command writes it down as a step, together with its kind (privacy cut or port fix), and `export-hand` turns it into a patch for the next release. | Writing a repair into the car's service book, so the next mechanic knows about it. |
| `Fluent file (`.ftl`)` | A Firefox wording file. Each piece of text has a name (an id) such as `open-in-tab`, and the text the browser shows. | A phrase book where each phrase has a label. |
| `preference file (`firefox.js`, `all.js`)` | Files that set Firefox's default settings, one `pref("name", value)` line per setting. | The factory settings sheet for an appliance. |
| `patch kind (privacy cut or port fix)` | The label you give a hand edit when you record it: `kind=privacy` for a change that stops Firefox contacting someone, `kind=port` for a repair that makes the new Firefox build or work. The label decides which folder of the patch set the edit is exported into. | Writing 'urgent' or 'routine' on a letter yourself before it goes to the post room, instead of letting the post room guess from words it happens to see on the envelope. |

## How It Works — Step by Step

### Step 1: Finding the right Firefox (upstream)

The harness never lets a model choose the source. It reads Mozilla's official version list and takes the latest stable version, for example 157.0. It turns that into the release tag name, `FIREFOX_157_0_RELEASE`, and asks Mozilla's GitHub repository which commit that tag points to. It refuses anything that is not a plain stable number: Nightly, beta and ESR versions are rejected. It never builds from `main`, because `main` is Nightly. If Mozilla lists a version but the tag does not exist yet, it stops and says so. Think of it as checking the manufacturer's official catalogue, not a rumour.

### Step 2: Locking away an untouched copy (vault)

Before anything is changed, the harness downloads the release tag once (a shallow `git clone` of one commit) into the vault. It checks that the commit it got is the commit the tag should be, records the commit, the time and the source in `vault.json`, and marks every file read-only. Later, `vault verify` confirms nothing has changed: the commit must still match and `git status` must show no changed or added file. You can run `vault measure` first: it asks Mozilla's archive for the size only and downloads nothing. For the Linux kernel the vault stores the kernel.org archive and checks it against kernel.org's published checksums.

### Step 3: Making a fresh working copy

The harness copies the vault into a new folder, the working copy. It refuses to copy into a folder that already has files in it, refuses to put the working copy inside the vault, and refuses to copy from a vault that fails its own check. The vault stays clean, so you can always start again from it.

### Step 4: Writing the plan and waiting for your approval

`fieldkit build-harness start firefox` writes a plan of steps and stops. Nothing runs until a person types `fieldkit build-harness approve` with the task name. There is no way for a model to approve a plan. The harness stores a fingerprint of the plan when you approve it. Each step is one of three kinds: a script step that the harness runs itself, a model step that is one small job, or a person step that waits for you.

### Step 5: Applying each patch group

The Gorilla patch set is split into groups listed in `config/patch_policy.json`. For each enabled group, the harness applies every patch with GNU `patch` and no fuzz (`--fuzz=0`), so a patch either fits exactly or is treated as not fitting. It never guesses by line number. Whole new files (icons, branding) are copied in, but a new file that Firefox itself now also ships at the same path is never overwritten: that becomes a question for a person. Files the fork deletes are deleted, and files Mozilla already removed are reported, not treated as errors. Every hunk that does not fit becomes its own small step. A hunk whose result is already in the new Firefox is noted and skipped.

### Step 6: The harness tries each job itself first

For each hunk that did not fit, the harness makes its own attempts before any model is asked. In order: wording files are carried over by message name, key files by key, settings files by setting name; then it checks whether the change is already there; then it tries to find the same block of lines elsewhere in the file and move the change there; then it matches single lines; then it removes a whole bracketed block; then it looks for lines Mozilla renamed; then it merges line by line. Every attempt is judged by the same check a model's answer would face. A miss is put back. Only what no script can do goes to a model.

### Step 7: Wording files: carried over by message, not by line (fluent)

Firefox's wording lives in `.ftl` files where each text has a name. A real example from the source: the Gorilla build re-indented `browser.ftl`, so one hunk was long but held only a few real wording changes such as "Open in New Gorilla Tab". Mozilla had also renamed those messages, for example `open-in-tab` became `open-in-tab2`. Matching by line could not see this, and a model given line edits deleted the wrong lines three times. So the harness reads the file's grammar instead: it works out which messages the hunk changes, adds or removes, ignores pure whitespace changes, and writes each changed message in the file's own style. If a message's name is gone, it looks for a renamed twin. It moves the Gorilla wording onto the new name only under strict rules: there must be exactly one candidate, and that candidate must still hold the old text or contain it exactly once. Otherwise the step goes to a person.

### Step 8: Key files and settings files: carried over by name (keyed, prefs)

Files such as `.properties`, `.dtd` and `.ini` hold `key = value` lines. Settings files (`firefox.js`, `all.js`, `mobile.js`, `firefox-branding.js`) hold `pref("name", value)` lines. The source notes that Mozilla often keeps the setting's name but moves the line or wraps it in a new `#ifdef` block. So a value Gorilla sets is written onto the setting of that name wherever it now lives. If the name no longer exists, the harness lists likely renamed candidates and parks the step for a person. If a setting is defined more than once in the file, or the hunk names it twice (for example once for Nightly and once for release), the harness does not guess which copy is meant. The check afterwards also confirms that no other setting in the file changed.

### Step 9: When the whole file has moved (relocate)

Sometimes a patch targets a file that no longer exists. Before asking a person, the harness looks for where the file went. Two real cases from the source: the code of `tabbrowser.js` moved into `Tabbrowser.sys.mjs`, and `activity-stream.css` stopped being stored in the source because Firefox now generates it at build time from `activity-stream.scss`. For a moved file, the harness searches the new source for the patch's distinctive lines and accepts a new home only when one file holds at least two of them and at least half of them, and no other file comes close. For a generated file, it appends the Gorilla additions to the end of the `.scss` source instead, but only when the patch adds lines and removes none. Anything else goes to a person.

### Step 10: Cutting the job so a small model can do it (task)

A small model cannot hold the whole Firefox port in mind, so it never sees it. The harness hands out one job at a time as a packet: the change, the part of the target file near it (at most 160 lines), and the exact form of answer it wants. The packet is held to 40% of the model's context window, counting four characters per token on purpose so it never overflows. If a packet would still be over 24,000 characters, the step is parked for a person straight away and no attempt is counted against it. The model is never allowed to name its task: in an early live run Gemma invented a task and never saw its real job.

### Step 11: The model answers in text; the harness makes the edit (answer, worker)

By default the model gets no tools at all. In live runs the model either made no edit and wrote a report of edits it never made, or, asked to rewrite a block, dropped and duplicated lines. So the model now only answers. In the usual form, the harness removes the lines it is sure about and asks about the uncertain ones, one per line, for example `745 REMOVE` or `749 KEEP`. The other form is short line operations such as `DELETE 744`, `CHANGE 750: pref("browser.x", false);` or `INSERT AFTER 760: ...`, applied from the bottom up so each number keeps its meaning. A sloppy answer (a missing line, a line answered twice, a number outside the file) is refused, not guessed at. Hunks that could only be done with line operations are parked for a person, because the model failed every one of them in earlier live runs. The model runs in a separate, minimal Gorilla OpenCode profile with no shell, no web and no sub-agents, and with longer time limits suited to a slow local model.

### Step 12: The harness checks the file, then saves or puts back (task)

After each answer, the harness checks the file itself; the model's words are never trusted. The check confirms that only the allowed file changed, that the lines the hunk removes are gone, that the lines it adds are present in the right place, and that nothing else changed. A pass saves a checkpoint, which is a git commit signed `build-harness`. A fail puts every change back to the last checkpoint and counts an attempt. After three failed attempts the step is blocked and waits for a person. Every event goes into a log in which each line carries a fingerprint of the line before, so an edited, deleted or reordered line is detected by the audit.

### Step 13: What is handed to a person instead of guessed (deferred, decision)

Some changes cannot be carried over because Mozilla removed what they change. These are parked as deferred steps, and the build gate stays closed until every one is decided. For each, `fieldkit build-harness deferred` writes a brief in plain words: what the change was, whether it only changes a comment, whether the setting it touches still exists anywhere, what the new source says at that spot, and what it cannot know (whether you still want the change). It recommends 'drop' when the change is only a comment or its setting exists nowhere, and 'hold' otherwise. Doing nothing is always safe. To drop a change you must be at a real terminal, have seen the brief at least 30 seconds earlier, and type an exact sentence such as `DROP THIS CHANGE: browser.ftl h30`. When there is nothing left at all to attach a change to, the harness marks it obsolete by default and lists it for review. A similar brief (`brief`) covers an unexpected edit to one of the maintainer's own files, such as the build settings file `mozconfig.win64`: 'hold' is the default, and putting the old version back saves the change first.

### Step 14: A person's own edits are written down (handedit)

Sometimes a person must fix something no patch asked for. A real example from the source: Firefox 157 wired new speech-recognition code into files unconditionally, while the Gorilla build switches speech off, so the build stopped. The fix touched several files that no patch covered. `fieldkit build-harness record` turns such an edit into done steps, one per block of change, and saves a checkpoint. You say what kind of edit it is by adding `kind=privacy` or `kind=port` to the command; the kind is written into the task's log next to your reason. Only those two words are accepted: any other kind, or two `kind=` words in one command, is refused before anything is recorded. `record` also refuses if any other file changed too, so nothing is recorded blind. If one block removes lines and another adds the same lines back elsewhere (a move), they are joined into one, so the move is not misread as a deletion. These recorded steps are judged later by the same final re-check as every other step. Think of a garage that writes every repair into the car's service book, and ticks a box saying whether it was a safety recall or ordinary wear.

### Step 15: Final checks on the whole tree

At the end, the harness confirms there are no leftover `.rej` or `.orig` files from `patch` (only untracked ones are removed, because Firefox itself ships many tracked `.orig` files), no conflict markers, no file of the original source deleted without a patch asking for it, and that changed `moz.build`, Python, JSON and JavaScript files still parse. It then re-checks that every ported change still stands, judged the same way it was judged when it passed.

### Step 16: Your edits become patches for the next release (export)

Each group's result is written out as a new patch. `fieldkit build-harness export-hand` also writes each recorded hand edit as a numbered patch into the patch set: port fixes into a `21.PORT.FIXES.<version>` group and privacy cuts into a `22.EGRESS.LOCKDOWN.<version>` group, each with a `README.md`. Which group a patch goes into is decided in this order. First: if the patch changes a file that an earlier privacy cut in the same export already changed, it stays a privacy cut, even if you recorded it as `kind=port`, because the port-fix group is applied before the privacy group and this patch was made on top of the cut; the screen says so with the words `recorded port, KEPT privacy`. Second: the kind you recorded with `record`. Third, and only when no kind was recorded (for example an edit recorded before this rule existed): a guess from words in your reason such as telemetry, egress, geolocation or push, and that patch is marked `keyword guess, nothing recorded: review`. Every patch is printed as one line with its kind and how the kind was decided, and the run ends with a reminder to review the kind of every patch before publishing, counting how many were guesses. The next port then applies the patches like any other Gorilla patch. Measured: the 157 port's hand work became 20 patches that, replayed in order, reproduce the built tree exactly. It works like a sorting office that reads the label you wrote on each parcel, only guesses when there is no label, and prints a list showing which parcels were guessed so you can check them before the van leaves.

### Step 17: Taking the truth from a build that really runs (snapshot)

The curated patch set and the tree that was actually built can drift apart. `fieldkit build-harness snapshot --version 155.0.1` compares the live build tree with the untouched vault copy of the same version and writes a complete patch set from the difference: one patch per changed file, new files byte for byte, binary files as replacements, and a list of deleted files. With `--prove`, it applies that set to a fresh copy and compares every touched file with the live tree; any difference means 'do not port from this snapshot'. It also lists which curated hunks are not in the build at all.

## Quirky Things Worth Knowing

### A failed attempt wipes every unsaved change in the working copy, including yours

To put a failed attempt back, the harness resets the working copy to the last checkpoint and deletes untracked files. That is the whole working copy, not only the one file. Do not edit files in the working copy while `drive` is running. If you edit by hand, run `record` straight away so the edit becomes a checkpoint.

### One job at a time, on purpose

The source records an overnight run that ran several jobs in parallel: git locks collided and the jobs crashed. The working copy is one git repository and one local model serves one request at a time, so `drive` now runs exactly one job at a time. It is slower, and it is the only safe order.

### The record is checked against the files before each run

Before `drive` hands out any job, it compares what the task record says is done with what the files really hold. A step the record calls done but the files do not support goes back to pending. The files decide, not the record.

### Some decisions refuse to work through an assistant

Skipping a step, dropping a deferred change and reverting a file all check that they run at a real terminal with a person typing. An assistant running shell commands does not have one, so these commands refuse. Check steps can never be skipped at all. The source says why: an overnight supervising agent once skipped many steps, including the failing final checks.

### 'Measure' does not download

An earlier version measured the size by partly cloning the source, which quietly downloaded all of it. `vault measure firefox` now asks the archive server for the size only.

### An old message name is not a change

If the Gorilla tree carries an older name for a message with the same text, the harness treats it as drift, not as a Gorilla change, and ports nothing. It never adds the old name back.

### 'Obsolete' is a default, not a silent skip

When Mozilla removed the very thing a change touches and there is nothing to attach it to, the step is marked obsolete without asking. It still appears in `fieldkit build-harness deferred` for review, labelled as resolved by default.

### The privacy or repair label is what you recorded, and a guess is flagged

Earlier versions chose between `21.PORT.FIXES` and `22.EGRESS.LOCKDOWN` only by searching your recorded reason for words such as Sync, push, metrics or translations, so a repair that happened to mention Sync could be filed as a privacy cut with nothing on the screen to show it. That is fixed: the kind you give with `kind=privacy` or `kind=port` now decides. Two things can still surprise you. First, a hand edit recorded without a kind still falls back to the word search; the export marks every such patch `keyword guess, nothing recorded: review` and counts them at the end. Second, a patch you recorded as `kind=port` lands in the privacy group when it changes a file an earlier privacy cut changed; the line says `recorded port, KEPT privacy` and gives the reason. Read every line the export prints before you publish the patch set.

### Only Firefox can be started, not the kernel

The vault fetches and checks kernel sources, but `fieldkit build-harness start kernel` refuses: the source says the kernel workflow comes next. Kernel porting is not available in this group.

### The help text lists fewer options than exist

The command's own help line shows `unblock TASK STEP retry|skip`, but the code also accepts `hand`, which reopens a step for a person to port without the harness or a model trying again.

### What this cannot do

It cannot tell you that a ported change is functionally right: it checks that each change landed and that changed scripts parse, not that the browser behaves as intended. It does not catch a privacy cut that was lost or a new Mozilla connection; the leak tests in a later stage do that. It cannot check that the kind you record is true: if you record a privacy cut as `kind=port`, the export believes you, unless the file was already cut earlier. It does not port the Linux kernel. Its check that a person is typing (a real terminal) is not a password: it only tells a person's window apart from an agent's shell. It does not back up edits you make in the working copy while `drive` runs; a failed attempt wipes them.

## What This Means For You

### Battery, Processor & Memory

Not measured for this group. The local model does the heaviest work: the source notes it reads prompts slowly on a processor, which is why the worker profile allows 20 minutes for the first reply and 10 minutes of silence. Each job has a default time limit of 45 minutes. The Firefox compile itself is in a separate group.

### Speed

Not measured. The source states the design trades speed for safety: one job at a time, a full check after each answer, and a reset after each failure.

### Your Privacy

Your browsing data is not touched. The job text goes to whichever model endpoint your Gorilla OpenCode settings name; with a local model it stays on your computer. Exported patch notes are cleaned of dates and personal wording before they go into the patch set.

### Your Internet

One download of the Firefox source per new version (size not measured here; `vault measure firefox` tells you before you download), plus small requests to Mozilla's version list, GitHub and Mozilla's archive. The model runs locally in the described setup and needs no internet.

## The Off Switch

**What it is:** Several stops. Nothing runs until a person types `fieldkit build-harness approve`. `drive` stops by itself after two jobs in a row crash, and stops if its preflight checks fail. Pressing Ctrl+C stops `drive` or `watch`. Every parked, blocked or deferred step keeps the build gate closed. Letting the model edit files directly (`--tools`) is refused unless you set `FIELDKIT_ALLOW_MODEL_TOOLS=1` yourself. For every decision brief, doing nothing is the safe default.

**Without it:** A model or an agent could start a port nobody read, keep retrying on a broken machine, write anywhere your user account can write, or drop a change by answering 'yes'.

**Think of it like:** A building site where work starts only when the site manager signs the plan, any worker can pull the stop cord, and a locked gate stays shut until every open question has a signed answer.

## How to carry the Gorilla changes onto a new Firefox

**Before you start:**
- Fieldkit installed, so that typing `fieldkit` in PowerShell works.
- Git for Windows, which also provides the GNU `patch` program the harness uses.
- Your Gorilla Firefox folder (the one holding `config/patch_policy.json` and the patch set) named as `firefox.root` in Fieldkit's `fieldkit.local.json` settings file.
- For model jobs: Gorilla OpenCode installed as `gorilla-opencode`, with a local model such as Gemma running (the source describes LM Studio).
- Optional: Node.js, so changed JavaScript files are syntax-checked. Without it the final check says they were not checked.
- Free disk space for the Firefox source: not measured here; step 2 tells you the download size.

**Step 1:** Open PowerShell (press the Windows key, type `PowerShell`, press Enter) and ask which Firefox is the latest stable release:

```
fieldkit build-harness latest firefox
```
  - You should see: A short result naming the version (for example 157.0), its release tag (`FIREFOX_157_0_RELEASE`), the commit, and the reason it was chosen.
**Step 2:** See how big the download is, without downloading:

```
fieldkit build-harness vault measure firefox
```
  - You should see: One line such as `Firefox 157.0: N MB to download (...)`. Nothing is downloaded.
**Step 3:** Fetch the untouched copy into the vault, then check it:

```
fieldkit build-harness vault fetch firefox
fieldkit build-harness vault verify firefox
```
  - You should see: The fetch reports `fetched` (or `already in the vault`). The verify line starts with `INTACT`.
**Step 4:** Start the port:

```
fieldkit build-harness start firefox
```
  - You should see: A line `task firefox-157.0 planned: resolve, vault, workcopy, plan-groups`, the path of the working copy, and a NEXT line telling the maintainer to approve. Nothing has been changed yet.
**Step 5:** Read the plan, then approve it (use the task name from step 4):

```
fieldkit build-harness approve firefox-157.0
```
  - You should see: `approved firefox-157.0`.
**Step 6:** Let the harness work through the steps, sending only the jobs it cannot do to the local model:

```
fieldkit build-harness drive
```
  - You should see: Timed lines such as `job 1: port-... (attempt 1)` and `harness check: PASSED`. The run ends with `DONE`, or with a list of steps waiting for a person. Both are normal. If preflight fails, it says `PREFLIGHT failed - no job started` and lists why.
**Step 7:** See where things stand:

```
fieldkit build-harness status
```
  - You should see: Counts of steps by state, such as done, blocked, deferred and obsolete, and the current step.
**Step 8:** Read each parked change and decide. List them, then read one (replace STEP with a name from the list):

```
fieldkit build-harness deferred firefox-157.0
fieldkit build-harness deferred firefox-157.0 STEP
```
  - You should see: A plain-words brief that starts `ONE OF YOUR CHANGES COULD NOT BE MOVED INTO THE NEW FIREFOX - NOTHING HAS BEEN HURT.` and ends with what to type if dropping is recommended. Doing nothing is the safe answer.
**Step 9:** Only if you decide to drop it: wait at least 30 seconds after reading the brief, then type the exact line the brief shows, for example:

```
fieldkit build-harness deferred firefox-157.0 browser.ftl-h30 --do "DROP THIS CHANGE: browser.ftl h30"
```
  - You should see: `recorded: browser.ftl h30 dropped on purpose.` with a fingerprint. Fail: `wait N more seconds`, or `type exactly: ...` if the sentence differs.
**Step 10:** To port a blocked step yourself: reopen it for a person, edit the file in the working copy, then submit it as a hand port with a note:

```
fieldkit build-harness unblock firefox-157.0 STEP hand
fieldkit build-harness submit --hand --note "what you changed and why"
```
  - You should see: `submit` reports `ok: true` and a checkpoint. Fail: the reasons, and your change is put back. Note that `submit` acts on the first open step, so check `status` first if more than one step is open.
**Step 11:** To record an edit no patch asked for (for example a build fix), make the edit, then record it by file path inside the working copy. Put `kind=port` for a repair, or `kind=privacy` for a change that stops Firefox contacting someone, before the file path. Type this line and press Enter:

```powershell
fieldkit build-harness record firefox-157.0 kind=port path/to/the/file --note "why this edit is needed"
```
  - You should see: Pass: `recorded: hand-hand-...-h1` and further step names. Fail: `REFUSED: kind must be one of privacy, port, not ...` (you typed another kind; use one of those two words), `REFUSED: one kind= per record` (you typed `kind=` twice), `other files changed too` (record or undo those first) or `no diff in` (the file has no change).
**Step 12:** When the port is finished, write your recorded edits into the patch set for the next release. Type this line and press Enter:

```powershell
fieldkit build-harness export-hand
```
  - You should see: One line per patch, such as `  [port fix] 001-<name>.patch (recorded)` or `  [privacy cut] 002-<name>.patch (recorded)`, then one count per group (for the 157 port, `21.PORT.FIXES.157: 3 patch(es)` and `22.EGRESS.LOCKDOWN.157: 17 patch(es)`; your counts depend on your recorded edits), then `REVIEW the kind of every patch above before publishing`, then a reminder to register new groups in `config/patch_policy.json`. Pass: every patch line ends in `(recorded)`, or in a `KEPT privacy` reason you agree with. Fail: the review line adds `N were guessed from keywords (record them with kind=privacy or kind=port)`; check each line marked `keyword guess` before you publish anything.

## If Something Goes Wrong

**`REFUSED: no build job has been started`**
No task exists yet, so the harness has no current task.
What to do: Run `fieldkit build-harness start firefox` first.

**`the plan is not approved`**
A person has not approved the plan.
What to do: Read the plan with `fieldkit build-harness status`, then run `fieldkit build-harness approve firefox-157.0` (your task name).

**`... is not a stable Firefox version (Nightly, beta and ESR are refused)`**
You pinned a version such as `158.0b1` or an ESR version with `--pin`.
What to do: Pin a stable version such as `157.0`, or leave `--pin` out to take the latest stable release.

**`Mozilla lists ... as the latest stable release, but ... has no tag ... yet`**
Mozilla announced the version before the release tag reached the GitHub repository.
What to do: Wait and run `fieldkit build-harness latest firefox` again later.

**`DAMAGED` after `vault verify`**
A file in the vault was changed or added, or the commit differs from the recorded one.
What to do: Do not use it. Move the damaged version folder out of the vault and run `fieldkit build-harness vault fetch firefox` again.

**`... is not empty; restore makes a FRESH copy`**
The target folder for the working copy already has files.
What to do: Move or remove that folder, or give another folder with `--workdir`.

**A step is `blocked` after three attempts**
The model's answers failed the check three times, or a packet was too large, or the change could only be done with line edits.
What to do: Port it yourself (usage step 10), or run `fieldkit build-harness unblock firefox-157.0 STEP retry` for a fresh set of attempts.

**`STOPPED: two jobs in a row crashed`**
Something on the computer is wrong, not the model's answer: for example the model server is down or git is locked.
What to do: Read the two crash lines above it, fix the cause, then run `fieldkit build-harness drive` again. The source offers `fieldkit build-harness preflight --model` to check the model server.

**`--tools lets the model write anywhere on this computer`**
You asked for tool mode, which is for experiments only.
What to do: Leave out `--tools`. The default answer mode is the supported one.

**A refusal ending `at a real terminal; an agent's shell is not one`**
The command was run through an assistant or a script, not typed by a person.
What to do: Type the command yourself in your own PowerShell window.

**Your own edit in the working copy disappeared**
A failed attempt reset the working copy to the last checkpoint.
What to do: Make the edit again while `drive` is stopped, then run `record` straight away.

**`REFUSED: kind must be one of privacy, port, not ...`**
You gave `record` a kind other than `privacy` or `port`, for example `kind=security`.
What to do: Run the same `record` command again with `kind=privacy` or `kind=port`. Nothing was recorded.

**`REFUSED: one kind= per record`**
The `record` command held two `kind=` words.
What to do: Keep one. If the files hold both a privacy cut and a repair, record them as two separate `record` commands, one per kind.

**An `export-hand` line ends in `keyword guess, nothing recorded: review`**
That hand edit was recorded without a kind, so the export guessed from words in its reason.
What to do: Check by eye whether the patch is a privacy cut or a port fix before you publish. The source offers no command that adds a kind to an edit already recorded; record future edits with `kind=`.

**`RESTORE REFUSED:` followed by `NEXT: pass --install-dir <the Gorilla install folder>`**
You asked `install --restore` to put a backup back, but did not say where, and the computer's list of installed programs names no Gorilla install, or more than one. The harness will not guess.
What to do: Run the command again and add `--install-dir` with the folder of the Gorilla install you mean.

## Why a Developer Would Do This

A browser fork lives or dies by how it survives each new upstream release. Doing it by hand means hundreds of small judgements made while tired, and a small model doing it alone was shown in live runs to invent work and report edits it never made. This design keeps the model's part as small as a yes-or-no question, makes the harness do and check every edit, and hands every real judgement to a person with the evidence written out. The person's own fixes are then written into the patch set, so the next release starts from them instead of from memory.

## Why It Matters That You Can Read This

This group decides what goes into a browser you may use every day, and its whole purpose is to keep privacy cuts in place. If you could not read it, you would be trusting that a small model's edits were checked, that a missing change was really handed to a person and not dropped, and that nothing quietly put back a Mozilla connection. Because the source is open, every rule above can be checked: the `--fuzz=0` setting, the three-attempt limit, the 30-second pause before a drop, the hash-chained log, and the refusal to run decisions through an assistant. The comments also record what went wrong in live runs, so you can see why each rule exists.

## Glossary

**GNU `patch`** — The program that applies a patch file to source files.

**fuzz** — How far `patch` may wander from the expected lines; the harness sets it to zero so nothing is placed by guesswork.

**release tag** — Mozilla's label for the exact source of a published release, such as `FIREFOX_157_0_RELEASE`.

**Nightly** — Mozilla's daily test version of Firefox, which the harness never uses.

**read-only** — A file setting that stops programs from changing the file.

**git commit** — A saved snapshot of a set of files that can be returned to later.

**journal** — The task's event log file, `journal.jsonl`, where each line is chained to the one before.

**build gate** — The check that refuses to start a build while any step is blocked or deferred.

**Gemma** — The small local language model the source used for the model jobs.

**Gorilla OpenCode** — The agent program that runs the model for each job.

**drift** — A difference that comes from Mozilla's own changes and not from a Gorilla change.

**excision creep** — New Mozilla code that reaches into a part Gorilla switched off or removed.

**patch kind** — The label `privacy` or `port` you give a hand edit, which decides the patch-set folder it is exported into.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Firefox source comes from the stable release tag, never main | 📄 stated in input | built from its release tag, never from main (Nightly) |
| Nightly, beta and ESR are refused | 📄 stated in input | is not a stable Firefox version (Nightly, beta and ESR are refused) |
| Vault files are set read-only and verified by commit and git status | 📄 stated in input | HEAD must be the recorded commit and `git status` must show nothing |
| Measuring does not download the source | 📄 stated in input | read from Mozilla's archive with a HEAD request: no download |
| Patches are applied with no fuzz | 📄 stated in input | a guess by line number is never a port |
| The harness tries its own tiers before a model is asked | 📄 stated in input | the model only gets what a script cannot do |
| Fluent files are ported by message, not by line | 📄 stated in input | Fluent files are ported by MESSAGE, never by line |
| Model gets no tools in the default answer mode | 📄 stated in input | Answer mode (the default) gives the model no tools at all |
| Three failed attempts block a step for a person | 📄 stated in input | after max_attempts the step is BLOCKED |
| A failed attempt resets the whole working copy | 🤖 model inference | *(none — model judgment)* |
| Editing the working copy during drive risks losing the edit | 🤖 model inference | *(none — model judgment)* |
| Jobs run one at a time because parallel runs collided | 📄 stated in input | git locks collided |
| Dropping a change needs a real terminal, a 30-second wait and a typed sentence | 📄 stated in input | the explanation shown 30 s earlier, and a typed sentence |
| Hand edits become patches for the next port | 📄 stated in input | so the next port applies it like any other Gorilla patch |
| The recorded kind decides the privacy or repair group; keywords are only a flagged fallback | 📄 stated in input | The recorded kind wins; keywords only when none was recorded. |
| Exported 157 hand work reproduces the built tree exactly | 📄 stated in input | replayed in order they reproduce the built tree exactly (identical git tree hash) |
| Kernel porting is not available | 📄 stated in input | the kernel workflow comes next |
| Functional correctness of a port is not proven by this group | 🤖 model inference | *(none — model judgment)* |
| Job text goes to the model endpoint named in Gorilla OpenCode settings | 🤖 model inference | *(none — model judgment)* |
| A wrong change that parses can still pass and be built | 🤖 model inference | *(none — model judgment)* |
| A later patch on a file an earlier privacy cut changed stays a privacy cut | 📄 stated in input | recorded port, KEPT privacy: an earlier privacy cut of the same file must apply first |
| Restore refuses to guess the install folder | 📄 stated in input | not guessing which |
| The export cannot check that a recorded kind is true | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*