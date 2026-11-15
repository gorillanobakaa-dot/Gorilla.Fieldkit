## What this is

Fieldkit v0.9.3: `fieldkit review` now shows you only what is yours to decide, and every item says in plain words what it is, what happens if you approve it, and what happens if you leave it.

## Should you download it?

Yes, if you use `fieldkit review`. Installing is unchanged (the install script, or `fieldkit update`).

## Why this matters

On 2026-11-10 the review asked you about 13 items, and every one of them said "(no plain explanation was written for this item)". Nobody can make an informed decision about something they cannot read. Two ideas that were already built were also offered as new work.

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->

### Only your decisions reach you

Since 1 November only a decision that is truly yours (a hard line) is meant to reach you; everything else goes to Zero's list. Thirteen items filed before that rule (failed builds, paused jobs, a finished report, refused pushes) were never moved, so the review kept showing them. They are now moved to Zero's list, and the review shows only your items.

### Every item explains itself

Each item now says what it is, what happens if you approve it, and what happens if you leave it. When whoever filed it wrote no explanation, the review builds one from the item's own title, reason and command, and it says plainly that approving runs nothing by itself.

### Built ideas say they are built

The ideas you gave today (one record of mistakes, and Zero as commander) were already built as plan rows P23 and P24. The review used to say "it becomes a row in the plan", and approving would have asked for a duplicate row. Now it says the work is already merged, and approving simply closes the idea. Idea numbers were reused over the months, so an idea is matched to its plan row by number and date, never by number alone.

### The plan sees every finished row

A finished piece of work whose title started with its row number ("P24: ...") was not counted as finished. It is now.

### The plan tells the truth about what is left

Eleven finished rows still said "planned". They now say done, with the change that finished each. One of them, P5 (everyday jobs on Windows and Linux with any model), is only partly done: it passed all 13 jobs on Linux with a cloud model, but the run with a local model is still missing. The plan now shows P5 as the open row instead of hiding it.

---

<!-- tools:begin (generated from fieldkit/desk/tools.yaml) -->
# Every tool in this release (450)

Each line is one tool: the command you type, what it does, and the systems it runs on. This list is made from the tool cards, so nothing the release carries is left off it.

| command | what it does | runs on |
|---|---|---|
| `fieldkit academic accessed` | Stamp a web source with today's date (once), in the profile's style and language | windows, linux |
| `fieldkit academic brief` | Read the assignment brief: type, word count, module, learning outcomes, AI rules and hidden AI instructions | windows, linux |
| `fieldkit academic build` | A finished Word document, slide deck or poster from the draft, in the country's layout | windows, linux |
| `fieldkit academic check` | Every pre-submission check in order, one verdict and the first fix; the work delivered only when it passes | windows, linux |
| `fieldkit academic countries` | The seven countries a student can study in, and what each sets (style, page, plagiarism tool) | windows, linux |
| `fieldkit academic dashes` | Long dashes in the work, where the country counts them against the student | windows, linux |
| `fieldkit academic date` | The submission date the brief or a deadline note states (a suggestion: setup still asks the dates) | windows, linux |
| `fieldkit academic extract` | Read every study file in the assignment folder into the searchable library | windows, linux |
| `fieldkit academic finish` | A Word file written by hand: Student ID header, page numbers, page size, language, no author name | windows, linux |
| `fieldkit academic init` | Start a student's profile (asks the name and country when they are missing) | windows, linux |
| `fieldkit academic language` | Sentences in the wrong language, and British/American spelling | windows, linux |
| `fieldkit academic local` | Assignments in OneDrive/Dropbox/Google Drive copied onto this computer, proven, the cloud copy renamed (never deleted) | windows, linux |
| `fieldkit academic menu` | The numbered menu for a person at the keyboard (English or Romanian); an agent runs the commands instead | windows, linux |
| `fieldkit academic next` | Days left to the deadline or upload, and the student's one next step | windows, linux |
| `fieldkit academic plagiarism` | Runs of words copied from the study sources, quoted or not, before the upload | windows, linux |
| `fieldkit academic portfolio` | A portfolio draft (.md) as a finished Word document in the student's layout | windows, linux |
| `fieldkit academic preflight` | The academic tools proven by building real files and measuring them: PREFLIGHT OK or which rule broke | windows, linux |
| `fieldkit academic record` | The student's marks and what markers keep saying (each fault with the marker's own sentence) | windows, linux |
| `fieldkit academic refs` | Citations against the reference list: missing, unused, duplicates, order, quotations without a page | windows, linux |
| `fieldkit academic search` | Find words or a quoted phrase in the study materials, with the page or slide to cite | windows, linux |
| `fieldkit academic setup` | Set up an assignment folder: asks the deadline and the upload date, then writes the checklist, skeleton and template | windows, linux |
| `fieldkit academic shortcut` | A Fieldkit Academic shortcut on the desktop and in the Start menu that opens the menu (only its own removed) | windows, linux |
| `fieldkit academic structure` | The sections a document type asks for, present or missing (headings in any of six languages) | windows, linux |
| `fieldkit academic style` | Reference entries and citations in the shape the referencing style asks for | windows, linux |
| `fieldkit academic template` | A Word/PowerPoint template of a document type, headings in the work language | windows, linux |
| `fieldkit academic types` | The 32 academic document types, or one type's sections and word shares | windows, linux |
| `fieldkit academic words` | Assessed word count (no title, references or appendices) against a target +/-10% | windows, linux |
| `fieldkit accept` | Everyday-jobs acceptance suite for Gorilla OpenCode: see, delete, move a file, tail, find, grep, unzip, JSON, download, image convert, OCR, probe a video, run through gorilla-opencode -p with any model; the judge reads the disk, a false claim is a MISS, a missing program is SKIP; pass/miss per model and OS | windows, linux, darwin |
| `fieldkit admit` | One machine-wide gate for heavy jobs (the full suite, mutate, builds, go test): at most N at once on this computer, the rest queue and never ask anyone; a hot CPU is offered one slot fewer | windows, linux |
| `fieldkit app` | Find and start any program by name on Windows, Linux and macOS - every match with path, version, how found (PATH, running process, App Paths, Uninstall keys, Start Menu shortcuts, Store apps / .desktop files, flatpak, snap / mdfind, application bundles) and whether it runs now; start it detached the way the OS does (CreateProcess/ShellExecute, gtk-launch/gio, open -a), the new PID verified after 3 s; never elevated, ambiguous names listed and never guessed | windows, linux, darwin |
| `fieldkit autopilot` | Unattended agent runs (Claude Code, Antigravity) with no approval prompts, held by hard lines and no credential | windows, linux |
| `fieldkit build-harness about-pages` | Every about: page of a copy of the installed build opened, read and judged; compared with the last other build | windows, linux |
| `fieldkit build-harness about-registry` | Every about: page the source can register (tree and pristine), what the newest build registered, the owner's verdict | windows, linux |
| `fieldkit build-harness accept` | The owner accepts waiting adapted ports in one go (applied, judged by meaning, checkpointed) | windows, linux |
| `fieldkit build-harness api-drift` | Upstream API changes (build/api-drift/firefox.yaml) found in the ported C/C++ files; --apply fixes them | windows, linux |
| `fieldkit build-harness approve` | The owner approves the planned steps of a task (nothing runs before this) | windows, linux |
| `fieldkit build-harness archive-gap` | Files the last archived (Linux-built) patch set changed that this port never touched | windows, linux |
| `fieldkit build-harness audit` | Audit the supervisor from the evidence - vault intact, owner settings untouched, journal hash chain, no answer keys | windows, linux |
| `fieldkit build-harness awake` | Keep the machine from idle-sleeping until a given process exits | windows, linux |
| `fieldkit build-harness binary-patches` | Every git binary patch of the set (pictures, icons) in the tree by hash; --apply restores the missing ones | windows, linux |
| `fieldkit build-harness brief` | One brief in full (brief show ID), or an owner-file-edit brief for a repo file (brief REPO PATH [--do SENTENCE]) | windows, linux |
| `fieldkit build-harness briefs` | Every open decision of a task as a full brief (decisions, deferred steps, owner edits, dispositions, claims) | windows, linux |
| `fieldkit build-harness build-gate` | Before the compile - every step done, final checks passed, no hand edits/skips/answer keys; records the tree | windows, linux |
| `fieldkit build-harness build-run` | The build loop on Windows - gate, owner's preflight, owner's build stage under watch, known stops fixed, package | windows |
| `fieldkit build-harness build-verify` | After the compile - tree unchanged since the gate, fresh real artifacts, pinned version, BuildID, about pages | windows, linux |
| `fieldkit build-harness calibrate` | Every check passes the known-good package and fails a copy broken the way it guards against | windows, linux |
| `fieldkit build-harness capture` | Network forensics of the built package on Windows - decrypted requests (mitm), DNS names, packets (admin) | windows |
| `fieldkit build-harness carry` | Record that a hand step is already carried by named patches of the set (export-hand then skips it) | windows, linux |
| `fieldkit build-harness check-change` | The check after every source change - recorded, journal chain, UI rules, decisions, export-hand, replay | windows, linux |
| `fieldkit build-harness claims` | What the public patch set claims, held against the tree and the installed build (writes the audit report) | windows, linux |
| `fieldkit build-harness clip-check` | Is dialog text cut in the installed build? repeated, against stock Firefox of the same version | linux |
| `fieldkit build-harness companions` | A compile error naming a missing declaration -> its other half from the archived patch sets (dry-run with git) | windows, linux |
| `fieldkit build-harness compare` | Hold a task's result against a person-made result folder (the answer key), file by file | windows, linux |
| `fieldkit build-harness creep` | Excision creep - upstream files with new references to symbols Gorilla excised | windows, linux |
| `fieldkit build-harness decide` | The maintainer records the answer to a brief, in their own words (real terminal only) | windows, linux |
| `fieldkit build-harness decisions` | The maintainer's product decision register checked against the tree and the installed build | windows, linux |
| `fieldkit build-harness decode-test` | Does the installed browser decode video on the GPU's video engine (D-158-06)? measured, not assumed | linux |
| `fieldkit build-harness deferred` | Steps parked because what they change is gone upstream - list, explain one, or (owner) drop it | windows, linux |
| `fieldkit build-harness describe` | One step of a task in full words (the current step by default), for messages and window titles | windows, linux |
| `fieldkit build-harness drive` | Drive a task with a local model - one fresh Gorilla OpenCode run per job, until done or blocked | windows, linux |
| `fieldkit build-harness drop` | Drop whole patches from a running port as their author said, files back to pristine, kept patches re-applied | windows, linux |
| `fieldkit build-harness export-hand` | Hand-edit checkpoints of a task become patches in the owner's patch set (21.PORT.FIXES / 22.EGRESS.LOCKDOWN) | windows, linux |
| `fieldkit build-harness fluent-check` | Fluent (.ftl) strings of the ported tree checked - syntax, orphans, moved and missing messages | windows, linux |
| `fieldkit build-harness follow` | A window log's milestones as they are written; exits with the run's own exit code (124 at the timeout) | windows, linux |
| `fieldkit build-harness herald` | The spoken herald - say one sentence, or speak up while a run goes on (battery warnings, run finished) | windows, linux |
| `fieldkit build-harness hidden-pages-doc` | The hidden about: pages explained page by page (English public, Romanian private), rendered and checked | windows, linux |
| `fieldkit build-harness images` | Every picture packaged in the installed build - count, bytes, exact duplicates | windows, linux |
| `fieldkit build-harness install` | Install a verified build - backup of install and profiles, install, verify, startup caches cleared, icon cache rebuilt | windows, linux |
| `fieldkit build-harness latest` | The latest STABLE Firefox or kernel release and exactly where its source comes from | windows, linux |
| `fieldkit build-harness leakgate` | Run the leak gate on the task's built package - quiet machine, mitm, DNS and packet sensors, judged by policy | windows, linux |
| `fieldkit build-harness leakgate-approve` | The owner approves proposed allowlist entries (by id, default every *proposed*) - real terminal only | windows, linux |
| `fieldkit build-harness leakgate-baseline` | The owner saves the leak-gate baseline from a release PASS (or the first one), with decisions enforced and claims clean | windows, linux |
| `fieldkit build-harness leakgate-context` | Each host in the install's omni.ja with path:line and context, as disposition evidence | windows, linux |
| `fieldkit build-harness leakgate-dispositions` | The owner approves the newest proposed dispositions (blanket for approvable ones; KEY names an owner-decision one) | windows, linux |
| `fieldkit build-harness leakgate-proposal` | Reviewers' result rows -> proposed-dispositions-<label>.json (approval null) plus the owner's review document | windows, linux |
| `fieldkit build-harness leakgate-propose` | Propose allowlist entries from the newest gate run's events (nothing is approved) | windows, linux |
| `fieldkit build-harness leakgate-rejudge` | The same gate evidence judged again by the current judge and allowlist; updates the owner's result if it is that run | windows, linux |
| `fieldkit build-harness leakgate-scope` | What changed since the last passed gate, and whether any of it can reach the network (report only) | windows, linux |
| `fieldkit build-harness leakgate-selftest` | Can the leak gate run on this Linux machine (root, ip, nft, ...)? Changes nothing; needs no task | linux |
| `fieldkit build-harness leakgate-status` | A (running) leak gate - runs started of expected, alive, result, CPU since start; safe while it runs | windows, linux |
| `fieldkit build-harness leakgate-summary` | A gate run scene by scene in plain words, for the release page | windows, linux |
| `fieldkit build-harness lessons` | Lessons recorded with hand ports that no harness rule covers yet (--all adds the ruled ones) | windows, linux |
| `fieldkit build-harness lists-refresh` | HSTS preload, key-pin and CT log lists from the upstream repository, taken only when pure data and fresher | windows, linux |
| `fieldkit build-harness log` | The last 40 journal events of a task | windows, linux |
| `fieldkit build-harness loss-audit` | Every line every Gorilla patch ever added (git history + patchset-* folders), counted in the ported tree | windows, linux |
| `fieldkit build-harness migrate` | Migration control - stages S0-S9, gates, SITREP, intents, intake, consistency, park/unpark tickets | windows, linux |
| `fieldkit build-harness netbench` | Network benches B1-B5 against local servers through an emulated link (headless throwaway copy); or compare two results | windows, linux |
| `fieldkit build-harness next` | The next job packet of a task (runs the script steps before it, then hands out one model step) | windows, linux |
| `fieldkit build-harness platform-steps` | What to do on THIS operating system for the task, computed from the machine | windows, linux |
| `fieldkit build-harness post-install` | The proof rows of the installed build - profile, prefs, excised, startup, egress, adblock, leaks, decisions, UI ... | windows, linux |
| `fieldkit build-harness pref-bisect` | Which of our graphics pref changes costs dropped frames, by halving (isolated, measured) | linux |
| `fieldkit build-harness preflight` | What must be true before a job starts - stale git locks, disk, fan control, model server (PASS/FAIL rows) | windows, linux |
| `fieldkit build-harness probe` | Ask a throwaway copy of the installed build a question, or try a JS/CSS fix in it, without a build | windows, linux |
| `fieldkit build-harness probe-compare` | Two saved probe outputs (TOK\|... lines) compared - LOST, changed, now-set | windows, linux |
| `fieldkit build-harness profile-check` | Every profile and which version last ran it, every launcher and the profile it names, the menu cache | linux |
| `fieldkit build-harness profile-diff` | The same probe on a fresh profile and on a copy of the real profile; exit 3 when they differ | windows, linux |
| `fieldkit build-harness profile-fresh` | The person's data into a fresh profile, none of the old version's settings; lists user.js settings the build lacks | windows, linux |
| `fieldkit build-harness profile-keep` | Which profile holds the saved logins and which install opens it; FAIL when the install would open an empty one | windows, linux |
| `fieldkit build-harness proposals` | The adapted ports waiting for the owner, with why each was adapted | windows, linux |
| `fieldkit build-harness rebase-set` | Bring the patch set onto the new release - failing patches superseded, 23.REBASE.<major> generated (dry unless write=1) | windows, linux |
| `fieldkit build-harness record` | Record a person's edit as a task step (kind=privacy\|port), then check and export it at once (check-change) | windows, linux |
| `fieldkit build-harness release-check` | Is this build releasable? techniques, decisions --strict, replay, claims, post-install, leak gate, release docs | windows, linux |
| `fieldkit build-harness release-cover` | The release page names every decision made since the last release and fits GitHub's 125000-character limit | windows, linux |
| `fieldkit build-harness release-page` | Assemble RELEASE-BODY.md from a draft folder's PAGE.parts, then hold it to release-cover (every decision named, under GitHub's limit) | windows, linux |
| `fieldkit build-harness release-roundtrip` | Remove the installed browser, download the published .deb from GitHub, check, install and verify it | linux |
| `fieldkit build-harness release-shots` | The release page's pictures from the real screen, the installed build in a visible maximised window on a fresh profile (About, app menu, search-mode list, tab menu) | linux |
| `fieldkit build-harness repair` | Repair known port failures in the tree (dangling excision, moved member), each proven with node --check | windows, linux |
| `fieldkit build-harness replay` | The replay proof - the public patch set on pristine upstream must give exactly the compiled tree | windows, linux |
| `fieldkit build-harness report` | Recorder report of a whole model run - incidents from the session, journal and MCP logs | windows, linux |
| `fieldkit build-harness repro` | The binary reproduced - plan (reads only), --run a second build in another folder, --compare packages byte for byte | windows, linux |
| `fieldkit build-harness rewind` | Undo a step that passed but is wrong - working copy reset to the checkpoint before it, later steps pending | windows, linux |
| `fieldkit build-harness runbook` | Run or show a runbook's steps under the build harness (so `window runbook run FILE` gets a window and keep-awake) | windows, linux |
| `fieldkit build-harness satellite` | Satellite mode judged on a copy - identity and scripts per level and per kind of site, call sites included | windows, linux |
| `fieldkit build-harness search-strip` | D-158-13 leftovers (other search engines' icons and wordmarks) in the source; --apply removes them | windows, linux |
| `fieldkit build-harness set-proof` | Pristine upstream + each platform's selection of the patch set must apply; excluded dependencies named | windows, linux |
| `fieldkit build-harness snapshot` | Capture the truth of a built tree against the pristine vault copy as a complete patch set (optional --prove) | windows, linux |
| `fieldkit build-harness start` | Plan a Firefox port task for the latest stable release (or a pinned one) and make it the current task | windows, linux |
| `fieldkit build-harness stats` | The task journal counted - builds, attempts, stops, hand edits, automation, installs, heat | windows, linux |
| `fieldkit build-harness status` | A task's step counts, checkpoints and current step | windows, linux |
| `fieldkit build-harness stops` | Every window run that did not pass, classified - the browser, the check or the machine - and false alarms per check | windows, linux |
| `fieldkit build-harness submit` | Check the current step's change in the working copy; pass = checkpoint, fail = change put back | windows, linux |
| `fieldkit build-harness symptom` | The tool already built and proven for a visible problem - the first command for a screenshot or complaint | windows, linux |
| `fieldkit build-harness techniques` | The ideas behind Gorilla's fixes - does each still hold in this tree? | windows, linux |
| `fieldkit build-harness timebombs` | Every date compiled into the tree that switches a protection off, judged against a date plus a margin (D-157-42) | windows, linux |
| `fieldkit build-harness truthbound` | Is every change the port made explained by the owner's 155 truth tree or a recorded step? | windows, linux |
| `fieldkit build-harness ui-check` | Prove what a user sees is readable and works - theme and UI rules, contrast, script errors, empty menus | windows, linux |
| `fieldkit build-harness unblock` | The owner unblocks a step - retry (fresh attempts), hand (a person ports it) or skip (owner at a real terminal) | windows, linux |
| `fieldkit build-harness vault` | The upstream vault - measure, fetch, verify, restore, restore-files, list untouched release sources | windows, linux |
| `fieldkit build-harness verify` | The verifier - what the tree really holds per hunk, false completions, stray edits, missing new files | windows, linux |
| `fieldkit build-harness visual` | Crisp icons and aligned pages - static rules on the tree, then a headless throwaway copy of the install | windows, linux |
| `fieldkit build-harness watch` | Live recorder - every incident of the model session and the task journal as it appears (Ctrl+C to stop) | windows, linux |
| `fieldkit build-harness weigh` | RAM and CPU per page - the installed build against the same build with a change (or another install) | windows, linux |
| `fieldkit build-harness whole-files` | Every NEW_FILES/REPLACE_FILES file of the patch set against the tree - missing or differing | windows, linux |
| `fieldkit build-harness window` | Run a build-harness command in its own visible window that outlives this session (screen-like), logged | windows, linux |
| `fieldkit build-harness withdraw` | Take back one waiting proposal with a reason (moved to proposals/withdrawn, step ends obsolete) | windows, linux |
| `fieldkit board` | Board rows - every finished session has its row in state/coordination/PLAN.md (plan R3): check counts sessions with a row / all for a day and lists each one without, add writes the row from the session's own transcript (title, id, branch, PRs, cost), no model | windows, linux, darwin |
| `fieldkit repair brace-parity-check` | C and C++ delimiter balance ({} () []) before a two-hour compile; comments, strings and preprocessor lines are blanked first; lexical only | windows, linux, darwin |
| `fieldkit canary` | Prove a CHECK can fail - each check on a planted input it must flag and a clean control it must pass; a second witness reads the artifact | windows, linux |
| `fieldkit capture` | Capture of the scripts, commands and procedures an agent makes or runs, only while a lesson is taught (capture on --lesson WHY --for MINUTES, capture off, capture state) - kept with its context, failed attempts linked to their fix, found again by words | windows, linux |
| `catchup` | Machine catch-up handover: repos (ahead/behind, token-free remotes), collect (memory, instructions, private tools, unpushed diffs/patches/untracked, bundles for repos with no remote), check (secrets fail it), public-check (Romanian text and private names before anything goes public), pack (verified zip to a stick, never overwrites) | windows, linux |
| `fieldkit ciaudit` | What CI really ran - per pull-request run of the tests workflow the selection ci_tests.py printed (whole suite, N test files, no code), its minutes, and the files that sent it to the whole suite; exit 3 over the whole-suite budget | windows, linux, darwin |
| `code-review` | Run the installed static analysers on a folder or file and return verified findings as JSON (builds the target: trusted code only) | windows, linux |
| `code-review-doctor` | Which code analysers are installed for a project's languages, what is missing and how to install it | windows, linux |
| `fieldkit.core.doctor` | What this computer still needs before Fieldkit works, and the line that installs it; --probe: the machine itself (OS, memory, disk, every Python, git real or Apple's stub, Homebrew, compiler, curl) and the way in (git or zip, lean or full); --lean: only the two packages the core needs | windows, linux |
| `fieldkit cost` | One daily cost line for the owner - a day's AI dollars on this machine (every agent session-cost measures), the most expensive session with its title and what it did (its Board row, else its first prompt), and both machines' total when the other machine's numbers came over the cable; and the report over a window: both machines per day and per source (Antigravity's Gemini priced too), the owner's real bill beside the API-equivalent | windows, linux, darwin |
| `fieldkit cost firstcall` | What a Claude Code session's first call carries on this machine, in tokens, measured by a real headless call (input + cache creation + cache read), the tools, skills and MCP servers it loaded, and with --parts the split by switching MCP, skills and tools off one at a time; the rules files by their size | windows, linux, darwin |
| `fieldkit repair cp1252-stdout-fix` | Windows cp1252 UnicodeEncodeError in a Python script: find text-mode open()/read_text()/write_text() calls with no encoding and the missing stdout UTF-8 setting; --apply adds exactly those arguments | windows, linux, darwin |
| `debian-kernel-project` | Kernel patches, audited config change-set, patch tooling, self-test (data for the debian-kernel pipeline) | linux |
| `fieldkit drift` | Does a committed generated file still match its generator? reports where they differ, never rewrites | windows, linux |
| `dual-track-cleanup` | Move the working files out of a docs folder once its Markdown is committed and pushed (refuses otherwise) | linux |
| `dual-track-code-render` | Turn the filled document specs into Markdown and check them (structure, banned phrases, a quality score) | windows, linux |
| `dual-track-generator` | Prepare plain-language and developer documents for a source file or folder: writes the fill-in specs, no model, no network | windows, linux |
| `dual-track-precheck` | Rule check of source files or a patch for left-behind defects: no model, no network | windows, linux |
| `dual-track-release-prep` | Prepare release notes from a changelog or diff file: no model, no network | windows, linux |
| `dual-track-release-render` | Turn the filled release-note specs into Markdown and check them | windows, linux |
| `dual-track-session-prep` | Prepare documents about the work done in a git folder (recent changes and commits, optional notes): no model, no network | windows, linux |
| `dual-track-session-render` | Turn the filled session specs into Markdown and check them | windows, linux |
| `fieldkit emerge` | Compile jobs like Gentoo's Portage, and better - one recipe per product (Firefox Windows, Firefox Debian, kernel, Gorilla OpenCode, gorilla-size), every stage content-addressed and skipped when its key was built and its outputs verify, offload to the other machine over the cable, test tiers (leak gate once per release), one fix-on-failure model run per attempt and zero on a green run, the inbox past the limit | windows, linux, darwin |
| `fieldkit eta` | How long will it take - from a plan, past runs, live progress or a timed trial on the same disk; never a guess | windows, linux |
| `fieldkit evidence check` | Check every claim of a research run: the output contract, cited-but-never-opened, snapshots, quotes | windows, linux |
| `fieldkit evidence fetch` | Fetch pages as evidence: kept by hash with their words, robots.txt obeyed, private addresses refused | windows, linux |
| `fieldkit evidence import` | A research run's findings file as records: one per lane, one per claim (tier or grade, URLs, quote) | windows, linux |
| `fieldkit repair evidence-macho-extract` | Mach-O binary (thin or universal): cpu, minimum macOS and loaded dylibs from the header alone; fails when it asks for newer than --max-minos (Mac 10.13 release check) | windows, linux, darwin |
| `fieldkit evidence render` | A checked research run as a report (.md, .docx, .pdf): unsupported claims first, every claim with its sources | windows, linux |
| `fieldkit evidence search` | Search a free scholarly or data API as JSON: OpenAlex, Crossref, Europe PMC, preprints, DOAJ, GDELT, World Bank, HDX, SEC and more | windows, linux |
| `fieldkit evidence show` | The latest snapshot of a URL: when, status, hash, and where its bytes and words are | windows, linux |
| `fieldkit evidence sources check` | Check the source registry as data: fields, access classes, grades, addresses | windows, linux |
| `fieldkit evidence sources find` | Find research sources in the 985-source registry: by words, category, free access and grade, best first | windows, linux |
| `fieldkit evidence sources probe` | Is each registry source still at its address? Fetches each one (robots.txt obeyed, paced), lists the dead | windows, linux |
| `fieldkit explain` | A written explanation read from a file: no model, no tokens, no variance | linux, darwin, windows, freebsd |
| `fieldkit audio` | Linux sound chain - every stage the sound passes through, what is done twice, and the fix (read-only) | linux |
| `fieldkit backup` | A complete, dated, verified zip of the Fieldkit folder into the owner's backup folder | windows, linux |
| `fieldkit backup remind` | How old the last backup copy on a USB stick is, and a reminder (aloud at most once a day) when it is due | windows, linux |
| `fieldkit backup usb` | Copy the newest backup to a USB stick and prove it there (never deletes, moves or overwrites anything on the stick) | windows, linux |
| `fieldkit cv` | Check a CV against fixed rules and the questions to ask; write UK CVs in English and Romanian | windows, linux |
| `fieldkit firmware check` | A device plugged in that does not work because its firmware is missing (USB Wi-Fi sticks, network cards), built-in kernel drivers included | linux |
| `fieldkit gtk-theme check` | A GTK theme that changes when a window loses focus, is scrambled after a login, or a theme fix that does not show on screen | linux |
| `fieldkit icons` | Sharp desktop icons - the icon cache (Windows/GTK/KDE), .ico frames and checks, crisp recoloured icons | windows, linux |
| `fieldkit icons cache` | Why the desktop icons are soft - the icon caches, their age and size (read-only; the fix is a person's --apply) | windows, linux |
| `fieldkit jobs` | UK job hunt - official APIs, scoring, application packs, tracking (never submits) | windows, linux |
| `fieldkit leftovers` | Leftovers on a Linux desktop (removed-not-purged packages, duplicate or dead login launchers, VM helpers on real hardware, dead menu entries and user services), shown first, cleaned on request, undoable | linux |
| `fieldkit mac` | Reach an old Mac over SSH (a MacBook Air 2011 on macOS 10.13 and the like): find it when its address changes, check key, login and alias, read what it is, run a command on it | windows, linux |
| `fieldkit macair` | Gorilla for the MacBook Air (old Intel Macs, macOS 10.13): status, preflight of every Mac decision in the source, the 11-inch layout budget, export the patch series, recreate, install on the Mac, verify there by measurement | linux |
| `fieldkit.aliases` | fk: short plain-English names for long Fieldkit commands (fk machine, fk ready, fk mac ...); prints the full command before running it, so a person always sees what runs | windows, linux |
| `fieldkit flaky` | A flaky test measured and explained - failure rate over fresh runs, swallowed thread exceptions, OS-wide name collisions | windows, linux |
| `fieldkit friction` | Friction audit - everything that can wait on a person, on this computer and on every peer over the cable: desktop session cards nobody started, sessions stopped on a question or not in bypass mode, Remote Control pending actions, a link.yaml that would send a peer's job to the inbox, mandate prerequisites not yet sealed (a UAC prompt or an owner yes), [NEEDS YOU] notes, open owner-inbox items; one line each with the fix | windows, linux, darwin |
| `fieldkit github` | Guided, verified GitHub setup for people new to GitHub - profiles (Full Speed Ahead, Long Horizon Tasks, AI Assisted Auditing, Check My Work, First Repository, Team, Public Release, Private Backup, Freeze), plain-words explanations from data, plan (now to wanted), apply with a yes before every change and a read-back after, check against six known mistakes, undo from a snapshot | windows, linux, darwin |
| `fieldkit goal` | Long horizon goals - a planner writes bounded jobs, fresh workers do them (budgets, checkpoints, resume), a separate verifier runs the acceptance criteria and decides; pre-approved decisions answered without a human; a supervisor resumes every unfinished goal at logon | windows, linux |
| `fieldkit pipeline run gorilla-opencode` | Every Gorilla OpenCode release file in one run, on Linux or Windows - exe, Linux binary, tarball, .deb (through xzcheck), .rpm (nfpm where dpkg-deb is missing, compared with the last release's .deb), Mac 10.13 zip on go1.24, SHA256SUMS of exactly the published files and the page's measured file table; optionally from a clean clone of one commit and compared byte for byte with the published files | windows, linux |
| `fieldkit size --json` | How big folders are and where the space went, biggest first (every core, read-only, links never followed) | windows, linux |
| `fieldkit size newest --json` | Files by age, newest or oldest first - what changed during the build | windows, linux |
| `fieldkit size apply --json` | Carry out a plan written for a large move or recycle, after it was reviewed; refused when the files changed | windows, linux |
| `fieldkit size bench --json` | Two programs, or the calibrated thread count against every core, timed alternately - a verdict or INCONCLUSIVE | windows, linux |
| `fieldkit size compare --json` | Two saved measurements compared - what grew, shrank, appeared or went, optionally as a folder tree | windows, linux |
| `fieldkit size copy --json` | Copy with every byte compared after, journalled for undo; an existing target is refused or kept aside | windows, linux |
| `fieldkit size dupes --json` | The same bytes in more than one place - size, then the first 64 KiB, then the whole file compared by hash | windows, linux |
| `fieldkit size empty --json` | Empty folders and zero-byte files - the leftovers of failed builds | windows, linux |
| `fieldkit size find --json` | Every file or folder matching name, extension, size or age - folder sizes included (fd cannot do that) | windows, linux |
| `fieldkit size free --json` | Free space on every drive with its kind (NVMe, SSD, HDD, removable) - preflight for builds and gates | windows, linux |
| `fieldkit size hash --json` | SHA-256 of files or whole folders, on the CPU's SHA instructions when it has them - proof for releases and vaults | windows, linux |
| `fieldkit size move --json` | Move - a rename on one drive, a verified copy then recycle across drives; journalled, a plan above the threshold | windows, linux |
| `fieldkit size profile --json` | This machine as gorilla-size sees it - CPU, P/E cores, SHA path, the fastest thread count per disk | windows, linux |
| `fieldkit size recycle --json` | To the Recycle Bin / trash, verified in the bin's own records; refused where Windows would destroy instead | windows, linux |
| `fieldkit size reparse --json` | Every junction and symlink with its target, broken ones flagged - never followed | windows, linux |
| `fieldkit size trash --json` | What is in the Recycle Bin / trash (original place, size, date) and putting items back | windows, linux |
| `fieldkit size types --json` | Bytes and file counts by extension - "is it the .pcapng or the .etl" in one call | windows, linux |
| `fieldkit size undo --json` | Reverse a journal - copies recycled, moves and recycled items put back - refusing anything that changed since | windows, linux |
| `fieldkit size watch --json` | Re-measure a folder every N seconds and raise an alert when it grows faster than a limit (a build filling the disk) | windows, linux |
| `gpudecode` | Prove a browser decodes video on the GPU's video engine (Windows; an old Mac over SSH from Linux: mac-ab, mac-verdict): doctor (GPUs, drivers, the no-driver trap, which methods work), engines (engine number -> type; WPA node = engine + 1), live (built-in counters, no admin), trace (wpr, one UAC prompt) + export (wpaexporter) + verdict (VideoDecode packets per process, steady rate), adk plan/install (Windows Performance Toolkit only, Microsoft-signed, CEIP off) | windows, linux |
| `fieldkit handover` | Fixes to someone else's tool as a handover - before/after, why, test, proof; checked to apply on their copy | windows, linux |
| `fieldkit harvest --find` | Which of the owner's scripts does this job? Search every script by what it does | windows, linux |
| `fieldkit hdmi diag` | Check the laptop and the TV: about 25 checks, each with what it measured and advice, changing nothing (about a minute) | windows |
| `fieldkit hdmi doctor` | Does this computer have what the HDMI Harness needs (Python, packages, the helper task)? Installs nothing | windows |
| `fieldkit hdmi fix` | Connect the TV now: activate it in the configured topology; if the driver is stuck (TV seen, 0 modes), restart the GPU driver - the repair the harness exists for. Elevated (Windows asks) | windows |
| `fieldkit hdmi profile-list` | The TV's EDID: the best HDMI mode for each profile and every mode with its score (the TV must be active) | windows |
| `fieldkit hdmi status` | Is the TV connected over HDMI right now: on the cable, active as a screen, the helper and the mode (Windows, offline, quick) | windows |
| `fieldkit hdmi test-quick` | Live HDMI checks H01-H08 (read-only), then the TV back in the configured topology; on a stuck driver that is the same repair as fix. Elevated (Windows asks) | windows |
| `fieldkit hdmi tv-info` | Find the Samsung TV on the local network and show its device info (model, power, network) | windows |
| `hdmi-tv-wake` | Turn the TV on with a Wake-on-LAN packet | windows |
| `fieldkit hdmi tv-wifi-report` | What would stop the TV joining a Wi-Fi network (hidden, WPA3-only, 6 GHz) | windows |
| `fieldkit heal-round` | A self-healing build loop that cannot oscillate - one written hypothesis per round; a round that does not lower the error count is undone, its diff and new files kept in the trash | windows, linux |
| `fieldkit hog` | The resource watchdog - RAM and commit charge, idle local models (LM Studio, Ollama) unloaded, leaks, orphaned test runners/listeners/browsers of ended sessions stopped, parents hoarding children (dozens of consoles/shells) reported, pinned CPU, heat, disk; check, act, every 5 minutes on its own, and the one preflight before builds and tests | windows, linux, darwin |
| `fieldkit i18n` | Translation tables checked - complete, unique per language, every used string covered; disjoint word lists | windows, linux |
| `fieldkit ideas` | Parked ideas - a suggestion made while a job runs is written down (add, list, brief) instead of added to the running plan; only the owner at a terminal accepts (a plan id and a brief for a small PR job), keeps parked or forgets (struck through, never deleted) | windows, linux |
| `fieldkit image accept` | Acceptance check that REQUIRES Tesseract and ImageMagick: tesseract reads a generated picture of known text, magick identify and convert run on it; an absent binary fails, a model's vision never counts | windows, linux, darwin |
| `fieldkit image compare` | Two pictures: SSIM, PSNR and one picture with A, B and the difference (amplified) side by side | windows, linux, darwin |
| `fieldkit image doctor` | Which picture tools are present (ImageMagick, Tesseract, cwebp, avifenc, mozjpeg cjpeg, oxipng): version, path, and for each missing one the exact install command and source; installs nothing | windows, linux, darwin |
| `fieldkit image ocr` | The text in each picture with Tesseract, word boxes in --json | windows, linux, darwin |
| `fieldkit image privacy` | Personal text inside pictures: OCR, then the detectors of privacy-scan; --redact writes a copy with those boxes blacked out, never the original; a picture that cannot be read is a finding | windows, linux, darwin |
| `fieldkit image squash` | Compress or convert pictures to WebP, AVIF, JPEG or PNG with the real codecs (ImageMagick when absent): bytes before/after, codec, settings, measured SSIM and PSNR; below the target is refused, the original is never overwritten | windows, linux, darwin |
| `fieldkit inbox` | The owner inbox holds only HARD LINE items (owner, 2026-11-01 - zero owner steps); every other item an agent, worker, ship push, goal or the net files goes to the agent ledger (fieldkit inbox ledger), read by Zero, never by the owner | windows, linux, darwin |
| `install-ps1` | Install Fieldkit on Windows without git (GitHub zip, private .venv, lean set, doctor --lean, INSTALLED.txt) | windows |
| `install-sh` | Install Fieldkit on a macOS or Linux computer WITHOUT git, Homebrew or admin rights: GitHub's zip with curl (or a browser), a private environment in the folder, the lean set, the doctor, and a list of everything it did; asks before each download or install | linux |
| `fieldkit install` | A program is installed the way its release says, on this computer, the peer and the Mac - the binary on PATH is the one the menu entry or shortcut launches (sha256 = the release's), every icon size installed with the release's hash, the .desktop / shortcuts / app bundle exist, caches newer than the icons, no older install shadows; self-test seeds each break | windows, linux, darwin |
| `fieldkit build-harness installer-test` | The shipped Windows installer installed and uninstalled for real - silent, no shortcuts, every file checked against the proved zip, the machine left as it was | windows |
| `fieldkit integration` | Is a capability built in? Carried, known to the AI, visible, on the release page, on every system, tested - check a repo or self-test the rules | windows, linux |
| `inventory` | Count code, tests, config and skills per project folder | windows, linux |
| `fieldkit repair keep-both` | Resolve git conflict blocks in an append-only file (log, Board table) by keeping ours then theirs; --apply removes the markers; a malformed file or generated registry is refused | windows, linux, darwin |
| `fieldkit laptop alerts say` | Hear the three alert sentences an AI assistant's state is spoken with (done, focus, look) | windows, linux |
| `fieldkit laptop cpu` | Is the processor locked slow by the firmware (BD PROCHOT)? Measured under load; guard unlocks and proves it | windows, linux |
| `fieldkit laptop rules` | The rule files AI tools read (CLAUDE.md, GEMINI.md, AGENTS.md ...): one source, every copy identical | windows, linux |
| `fieldkit laptop sound` | Low sound on Windows: diagnose (read-only), the safe fix with its undo, the driver listed for the exact chip | windows |
| `fieldkit laptop video` | An NVIDIA card without its Control Panel app (DCH drivers): found; --apply installs it from the Store | windows |
| `fieldkit laptop volume` | The master volume: read it, or set it (and unmute) on Windows and Linux | windows, linux |
| `fieldkit build-harness leakgate-runs` | Every leak-gate run - verdict, build, size and every file that cites it; only uncited runs are free to remove | windows, linux |
| `fieldkit lifecycle` | Install, verify, (upgrade,) uninstall a program and report anything it left behind | windows, linux |
| `linkcheck` | A direct cable between two computers: setup (a private address, no gateway, no DNS; idempotent, read back), serve (the other end: TCP sink/source + UDP echo bound to one address, one allowed peer, idle stop), check PEER (speed, duplex, Energy Efficient Ethernet, RTT p50/p99/jitter/loss, TCP throughput both ways with its own sender, path MTU; one LINK line), status, doctor; Windows + Linux, standard library | windows, linux |
| `fieldkit liveness` | Process liveness - every harness long run (goal supervisor, runbook, emerge, pipeline, link job) OK, STALLED or ORPHAN; --act stops the harness's own orphans and overdue steps by PID + creation time; the named failure modes and their checks | windows, linux, darwin |
| `fieldkit lmstudio-log` | Why a local model is slow - LM Studio's server log per request (prompt tokens, prefill and generation tok/s, n_tokens) and every Vulkan device lost with the fix | windows, linux |
| `fieldkit.core.longpath` | Long paths by default for every tool that walks or deletes files - the extended-length form (Windows up to 32,767 characters), the platform's standard direct delete with links never followed, and the machine's long-path state (LongPathsEnabled, git core.longpaths) | windows, linux, darwin |
| `fieldkit machine` | Machine tags - this machine's tag [MODEL-OS] ([VAIO-LNX], [L15-WIN], [AIR-MAC]) made once from the hardware model and the OS by fixed rules and stored in the household registry (never retyped); every device with its tag, OS, link address and last seen; a SessionStart hook line telling a session to title itself with the tag | windows, linux, darwin |
| `fieldkit mandate` | A project mandate: the owner's ONE approval for a whole project, valid on both computers: keygen (passphrase-protected Ed25519), trust (the other computer pins the public key), approve (every door shown, typed yes, passphrase), verify, allows (allow/ask/deny with the clause), stop, share | windows, linux, darwin |
| `fieldkit mandate doctor` | Is the owner-wide mandate working on this computer: in force or not, days left, the owner key here, the key trusted, the other computer's (VAIO's) review key pinned, and every covered repository's pre-push hook - honors the gate, needs none, by design, or NEEDS the gate - plus the expiry warning | windows, linux, darwin |
| `fieldkit mandate gate` | The pre-push gate a repository's hook calls: a push goes without the owner flag ONLY when it came through fieldkit ship push, a mandate in force names the repository, every branch is allowed and fast-forwards under its own name, every tag is new on a commit GitHub has; no deletes, no moved tags | windows, linux, darwin |
| `fieldkit mandate install-hooks` | The gate-aware pre-push hook in every covered clone on THIS computer that has a Fieldkit template (fieldkit/mandate/hooks/<repo>/): a preview without --yes; with --yes installed byte for byte, old files kept as *.bak-<date>, then verified by running the hook; --stick DIR writes the templates, their sha256, the clones the other computer must check and a README for the stick | windows, linux, darwin |
| `fieldkit mandate prereqs` | Machine prerequisites in the mandate, Android-permissions style - declared with what each changes and costs, each script pinned by sha256, approved ONCE on the approve/renew screen, applied by the harness at a session start (fieldkit mandate start), checked, undone by id | windows, linux, darwin |
| `fieldkit mandate receive` | Install a mandate received from the other computer ONLY when its seal verifies against the owner key already pinned here (not stopped, not expired, never an older approval over a newer one); the replaced pair to ~/Agents.Work.Trash; `mandate share` asks the far listener to run it | windows, linux, darwin |
| `fieldkit mandate renew` | A new period for a mandate: the same approve screen (every door, NOT COVERED, the threat model), one typed yes and the passphrase; only `expires:` changes; the passphrase checked before the file changes and a failed seal puts the old mandate back; a stopped mandate is never renewed | windows, linux, darwin |
| `fieldkit mcp-eval` | Evaluate an MCP server: a model answers the questions of an evaluation XML through the server's tools - the adopted mcp-builder skill's evaluation.py, explicit opt-in (no default endpoint) | windows, linux, darwin |
| `fieldkit mcp-probe` | Speak MCP to a server as a client does - handshake, tool list and its size, optionally one call | windows, linux |
| `fieldkit merge-driver` | Cards and generated indexes merged entry by entry: two branches that each add a tool card merge with no conflict in tools.yaml, platform_index.json or docs/groups.yaml | windows, linux, darwin |
| `fieldkit ship train` | The merge train - a session declares its PR final (ship ready), Zero merges the declared PRs one at a time, dependencies first, brought up to date with main (generated files regenerated, cards entry by entry), never red, only with --match-head-commit; a merge of main proven to be only a merge skips the full pre-commit suite | windows, linux, darwin |
| `fieldkit metrics` | Long-horizon proof from the harness's own records - days running, Zero epochs and changes, session rotations, sessions and subagents, headless sub-jobs per device, stations and machines, money, tokens, tool calls executed / refused / declined / repeated, PRs, CI runs, failures; each number names its source, what cannot be read is listed as NOT MEASURED | windows, linux, darwin |
| `fieldkit repair missing-import-fix` | Find modules a Python file uses (shutil.copy, re.sub) without importing them and, with --apply, add the import after the docstring and __future__ lines; the file must still parse | windows, linux, darwin |
| `model-eval` | Rank language models by honesty under pressure: runs scripted research tasks against paid model providers | windows, linux |
| `model-eval-grade` | Score the saved model-eval runs offline: no model, no network, the same runs always get the same score | windows, linux |
| `fieldkit models gguf` | Before a local model is loaded: read its GGUF header (a file, or a Hugging Face URL by HTTP range, header bytes only) and say dense or MoE, total and active parameters, bits per weight, MTP/vision notes and the expected tokens/s on THIS machine from its measured memory bandwidth, with a FAST / USABLE / SLOW verdict | windows, linux, darwin |
| `fieldkit models tune` | Make a local model start fast: analyse this host (performance and efficiency cores, GPU and its VRAM, free RAM, measured memory bandwidth) and the model's GGUF, pick the load settings (context sized to the job, threads, GPU offload, KV-cache type, flash attention, MTP/draft) with a reason each, and with --apply load it through LM Studio's lms and verify with lms ps; --record / --report keep first-word time and tokens/s per run | windows, linux, darwin |
| `fieldkit mutate` | Prove a test can fail - break a rule on purpose, run the test, put the file back byte for byte | windows, linux |
| `fieldkit net antenna` | What antenna the cellular bands in use actually require, with the loss budget | linux, darwin, windows, freebsd |
| `fieldkit net route` | Which interface the internet actually goes out of, before you measure anything | linux |
| `fieldkit next` | The one next thing to do in a build pipeline (DO, BLOCKED, CANNOT HERE or DONE) | windows, linux |
| `fieldkit build-harness night-pack` | The night pack - every owner-only step the night may need (release send, leak-gate re-judge), pre-approved once at the owner's terminal, run by a Windows scheduled task when its conditions hold, disarmed at the first surprise, with a spoken and written morning report | windows |
| `fieldkit observe` | See inside the machine, the same on Linux, Windows and macOS - who has a file open or locked, who uses a port, a process in full (exe, cwd, command line, user, children, memory map), watch a folder, which files a process opens or starts, a process's CPU/memory/disk over time, system event log search, every connection of a process, the DNS servers a process talks to | windows, linux, darwin |
| `fieldkit office` | Read, create, check and scrub Word/Excel/PowerPoint/PDF offline | windows, linux |
| `fieldkit office check` | Check an Office or PDF file is not broken before sending it | windows, linux |
| `fieldkit office deliver` | Make a Word, Excel, PowerPoint or PDF file safe to send (check, remove names, recheck, privacy scan) | windows, linux |
| `fieldkit office read` | Read a Word, Excel, PowerPoint or PDF file as plain text | windows, linux |
| `fieldkit office scrub` | Remove people's names from the hidden properties of a Word, Excel or PowerPoint file | windows, linux |
| `fieldkit orchestrate` | The boss's side of the household: every device (role, OS, cable or LAN, brain) and whether it answers now; one note to many workers | windows, linux |
| `fieldkit orchestrate job` | A job on a tree the boss sends: a worker runs a Fieldkit command line (run) or a Gorilla OpenCode brief on a cheap Gemini model (agent) on a copy of it, and the artefacts come back with their sha256 signed | windows, linux |
| `fieldkit orchestrate load` | Each device's load in one line: CPU, RAM, temperature against its hot line, the admission gate's slots, heavy jobs running, what it can run; IDLE or BUSY by a fixed rule | windows, linux |
| `fieldkit orchestrate offload` | The pre-commit suite on an idle Linux device over the cable: a signed result and its JUnit report back, only the test files that device skipped run here; down, BUSY, unsigned or 0 tests -> the full suite here | windows, linux |
| `fieldkit orchestrate place` | A test suite placed where there is room: here when a slot is free, else on an idle capable device over the cable (signed result recorded in testcost for that tree hash and OS), else here after the queue | windows, linux |
| `fieldkit orchestrate job session` | A job on ANY device with no click: the boss sends brief + repository + branch, the device runs fieldkit autopilot claude on its own worktree of the branch and pushes it with fieldkit ship push; authorized by the owner-wide mandate sealed once, never a per-job approval | windows, linux |
| `fieldkit orchestrate split` | Which devices of the household are up and what each can run (OS, cores, RAM, slots, toolchains, a local model); a test suite split into pieces, each accepted only by its pytest PASS line, placed on those devices and run as a queue, with pieces finished per hour | windows, linux |
| `fieldkit overhead` | How much longer than the bare pre-AI build script a job took, and where the extra time went - bare work, rework, tests and gates, LLM, waiting for the owner, idle; one line per job, WARN over the budget | windows, linux, darwin |
| `fieldkit parity` | Test parity - every machine runs the same pinned versions with the same capabilities before a test: a deterministic manifest per machine (versions + sha256 of gorilla-opencode, gorilla-size, python, go, git, gh, node; Fieldkit commit + dirty; installed skills, cards, Gorilla OpenCode provider NAMES, MCP servers, mandates in force, long paths; OS/arch), compared over the household link (SAME / PARTIAL / DRIFT, each difference named, an absent device reported, never a failure), aligned to releases/parity-pin.yaml from the release itself (asset + SHA256SUMS + GitHub digest, old binary kept, Linux/macOS get the root line), and the run guard's shared-ground check | windows, linux, darwin |
| `fieldkit parity helpers` | Gorilla OpenCode pins the same helpers for windows-amd64, linux-amd64 and darwin-amd64 (internal/toolbox/SHA256SUMS) and parityBacklog is empty; names what each system lacks | windows, linux, darwin |
| `fieldkit parity installed` | gorilla-opencode's version on this computer and on the peer (over the cable) equal each other and the newest release tag | windows, linux, darwin |
| `fieldkit office form-bbox-check` | Check form-field rectangles for overlap and entry height smaller than its font; reports indexes only | windows, linux, darwin |
| `fieldkit office fillable-check` | Count fillable PDF form fields without exposing field names or values; refuses encrypted PDFs | windows, linux, darwin |
| `fieldkit peers link` | Agents on the OTHER computer of a direct cable: pair (key carried by hand), serve (cable address, paired peer only), hello (its agents), send a note, ask it to start a session, read what it sent (fenced) | windows, linux |
| `fieldkit peers link agents` | Which AI agents this computer has: claude, codex, opencode, gorilla-opencode, antigravity/agy - installed (path, version), running (PIDs), messageable sessions | windows, linux |
| `fieldkit peers link check` | The 2026-10-22 Windows lessons as prechecks: clock skew against the peer (the link refuses > 120 s), the cable firewall rule (fix: setup, one UAC click via elevate), a stale 00-00-00-00-00-00 neighbor after a cable move, where put files land, Git Bash eating backslashes and PowerShell 5.1 refusing &&, CRLF from Python, privacy before the 16-minute commit hook, writes too big for one call, and what-you-see -> check | windows, linux |
| `fieldkit peers link find` | Where is the other computer? Cable counters, the cable peer (IPv6 all-nodes ping, maker from its MAC), the home network read passively, the cable peer and named hosts probed once on 22 (SSH banner) and 47077; --explain says each roadblock in plain words and writes the text to paste to the other computer's Claude | windows, linux |
| `fieldkit peers link heal` | Why a device of the household does not answer, rung by rung with evidence (this computer's cable address, its own listener, ARP/ip neigh, ping count/loss/rtt, TCP to the link port telling REFUSED / TIMEOUT / UNREACHABLE apart, the signed hello, link.log and the peer's logs through its rescue door), one VERDICT naming the broken layer and NEXT; --revive tries each DECLARED path (rescue door, key-only ssh with a forced command, Wake-on-LAN on the cable) and else puts ONE inbox item naming the machine and the exact command | windows, linux |
| `fieldkit peers link keepalive` | Keep the cable listener alive without a person: Linux systemd user units (fieldkit-link.service + fieldkit-link-rescue.service, Restart=always, RestartSec=5, no start limit) + loginctl enable-linger + the watchdog timer (KillMode=process, restarts through systemctl); Windows a scheduled task at logon and every 5 minutes (IgnoreNew, RestartCount 999, not elevated); status says what is missing, peers link doctor shows both ends | windows, linux |
| `fieldkit peers link probe` | Fixed read-only probes over the cable (agents, where PROGRAM, disk, temps, load, versions, sessions) and the one agent finder: PATH, the running process's own file, the known install places | windows, linux, darwin |
| `fieldkit peers link relay` | One session per computer that only listens on the cable: listen until a note comes (no time limit), the brief a relay follows, status, doctor (PRECHECK: exit 3 when nobody listens) | windows, linux |
| `fieldkit peers link rescue` | The rescue door: a second, tiny listener on the cable address only (link port + 1, 47078) that answers devices the household marks cable, signed with the household key, with two verbs only - rescue-status (listening? keep-alive? the last lines of watchdog.log, serve.log, link.log and the user journal of the two units) and rescue-restart (the listener, through the service manager) - every request in rescue.log | windows, linux |
| `fieldkit peers link restart` | Restart the cable listener on THIS checkout's code and check its port (stops only a process that is a link listener) | windows, linux |
| `fieldkit peers link setup` | The whole agent link in ONE command per computer: finds the cable port (never the internet one), sets 10.77.0.1/.2 (one UAC click / pkexec), pairs, opens the firewall for the peer only, writes the USB kit (code bundle + key + setup-linux.sh + SHA256SUMS), listens, says hello | windows, linux |
| `fieldkit peers link startcheck` | PRECHECK before this computer starts a session for the peer: approval, folder, python 3.11+, which fieldkit a started session loads, autopilot's options, claude found, the brief on stdin | windows, linux |
| `fieldkit peers link toolset` | This computer's tools for the cable link (python, pytest, PyYAML, psutil, git, claude, the link's commit) and, with --peer, the other computer's and each gap with the line that closes it | windows, linux |
| `fieldkit peers link verify-start` | Wait for a started session's [DONE] note with evidence after inbox number N: the only proof a session ran | windows, linux |
| `fieldkit peers link watchdog` | The cable listener on a heartbeat: restarted with no person involved, every step logged; two failed restarts in a row stop the restarts and go to the owner's inbox; on the boss it also looks after open jobs | windows, linux |
| `fieldkit peers list` | The AI sessions running on this computer that can take a message (Gorilla OpenCode 0.1.145+, fieldkit waits) | windows, linux |
| `fieldkit peers send` | Send one short text message to another AI session on this computer (same account only; it cannot be unsent) | windows, linux |
| `fieldkit peers wait` | Be a session for a while and return the messages that arrive (fenced: text from another program, not orders) | windows, linux |
| `pfind` | Hybrid local search in one Python file (name, content, meaning, snippet) | windows, linux |
| `photokit` | Photos: contact sheets, near-duplicate candidates + side-by-side compare, mapping check, sort into per-item folders, fetch report photos, doctor | windows, linux |
| `fieldkit repair pid-guarded-stop` | Stop a process only when its name AND start time match the PID you meant; check by default, --stop ends it and its children | windows, linux, darwin |
| `fieldkit pins` | Are the helper sources of a Gorilla OpenCode tree immutable and hash-pinned? Refuses latest, nightly, rolling, autobuild, master/main archives, unversioned paths and any download without a sha256 | windows, linux, darwin |
| `fieldkit pipeline` | Staged, resumable pipelines (Firefox on Windows, Debian kernel, Gorilla OpenCode and any Go app) | windows, linux |
| `fieldkit pipeline plan` | What a pipeline would do, fully resolved, and what changed since its last run (read-only) | windows, linux |
| `fieldkit plan` | Where the harness plan stands - the current phase of docs/HARNESS-PLAN.md's Order table, its open items, the items merged pull requests finished; --write marks them DONE; the one line zero, the handovers and the room hook carry | windows, linux |
| `fieldkit repair plan-row-insert` | Insert one Board or Waiting row into a markdown file before an anchor line that must match exactly one line; --apply writes | windows, linux, darwin |
| `fieldkit.desk.platformcheck` | Hold every tool card's platforms to its tests; a new single-system card needs a port or a reason | windows, linux |
| `fieldkit power audit` | Can the machine name who started a program (R9: who ran powercfg.exe before a cut)? Checks Security 4688 process-creation auditing, command lines and read access; --write-fix writes ONE self-elevating script for the missing admin steps (one UAC prompt); --callers PROCESS lists who started it (Windows) | windows |
| `fieldkit power drift` | Check that the Lenovo power service (ibmpmsvc.exe) never put the laptop to sleep during work in the last N days and that the sleep button is off on AC; --apply turns the sleep button off without admin and records before/after (Windows) | windows |
| `fieldkit power incidents` | Why the computer shut down, cut out or slept: every unexpected shutdown and sleep since a date, each with a named cause class, the thermal journal peak before it and the battery state; an unknown cause is a defect row (Windows, Linux, macOS) | windows, linux, darwin |
| `fieldkit power investigate` | Investigate an unexplained shutdown or restart: replays the checks of 2026-11-09 (unknown cuts, thermal journal's last 3 minutes, battery health, BIOS and model, vendor power daemons and the reset storm, drivers and updates since the first cut, fan control) and prints one ranked suspect table with the evidence line per suspect and the owner's physical steps (Windows full; Linux and macOS read the cuts, the journal and the model) | windows, linux, darwin |
| `fieldkit office pptx-chart-check` | Check bounded PPTX chart XML for known label-position and axis-reference failure patterns | windows, linux, darwin |
| `fieldkit office pptx-master-check` | Check bounded PPTX master relationships for a known shared-theme failure pattern | windows, linux, darwin |
| `fieldkit office pptx-schema-verdict` | Classify known fatal PowerPoint slide-schema diagnostic categories without echoing source text | windows, linux, darwin |
| `fieldkit build-harness pref-conflicts` | One pref, one value - no two definitions the Firefox patch set adds may disagree (also in set-proof and the build gate) | windows, linux |
| `fieldkit prefix-diff` | Offline: what changed between consecutive Messages API request bodies in the parts the prompt cache and thinking blocks bind to (system, tools, earlier messages), or --scan a repository for the code that usually causes it - the adopted claude-api skill's prefix_diff.py | windows, linux, darwin |
| `fieldkit.core.preflight` | The ONE run guard before and during heavy work (builds, test suites, mutate, pipelines, autopilot jobs, the leak gate) - reclaim idle models and orphans, refuse under the free-RAM floor, sample RAM and CPU temperature, one end line | windows, linux, darwin |
| `fieldkit privacy scan` | Find secrets, personal paths, emails and private names before publishing | windows, linux |
| `profilefix` | Windows profile repair + program purge: registry still pointing at a renamed profile folder (installers writing into C:\Users\<OLD>), and the Windows apt-get purge | windows |
| `fieldkit publish` | Push and release only after the privacy checks and the owner's typed yes; never inside autopilot | windows, linux |
| `fieldkit repair pwsh-parent-chains` | Who opened all those consoles: the parent chain of every PowerShell, pwsh and conhost process and the parents owning the most | windows, linux, darwin |
| `fieldkit repair pwsh-syntax-check` | PowerShell .ps1 files with bash written into them (heredoc, export, $(cmd)) or unbalanced braces; also asks powershell/pwsh's own parser when present | windows, linux, darwin |
| `fieldkit pyliterals` | Python strings with stray backslashes ("\d+", "C:\Users") - find them; --apply makes only those literals raw, and only when the value stays the same or it is a drive path | windows, linux, darwin |
| `fieldkit refcheck` | Which files named in a list (manifest), link or import do not exist | windows, linux |
| `fieldkit refusals` | Repeated refusals (P23 - one ledger for both computers over the cable via sync, CI failures too, defects closed only with a test, a read-only refusal in a job blocks its re-run, overhead causes shows hours lost) - train, ship, guard, ci, goal and task write every refusal to state/refusals.jsonl (tool, reason key with heads and ids stripped, text, time, session); the same reason twice opens ONE row in ZERO-OPEN-PROBLEMS.md (never an inbox item); list shows each reason, its count and whether its row is open | windows, linux |
| `fieldkit regress` | Compare test failures by name before and after a change (record LABEL -- CMD; compare A B; show) | windows, linux |
| `fieldkit release check` | Prove a release - the published files are the tested ones and every claim in the notes is proven | windows, linux |
| `fieldkit release-cycle` | One release, the whole cycle, each stage DONE only by its artefact - audit, build, checksums, screenshots, notes, on-base, publish, verify-page, leftovers - driven by a project's release file | windows, linux |
| `fieldkit release page` | The whole GitHub release page composed from the release's notes - opening, plain-language track, developer track, each in full - with the File \| Bytes \| SHA-256 table measured from the files; never written by hand again | windows, linux |
| `fieldkit build-harness release-publish` | One-step release - the harness proves everything first (hashes, SHA256SUMS, privacy push-check, set-proof, tag new), then the owner's one action pushes with visible output, creates the titled release, checks GitHub's digests and only then marks the old release superseded | windows, linux |
| `fieldkit release table` | The release page's File \| Bytes \| SHA-256 table measured from SHA256SUMS (each file hashed again), or a page's table checked row by row | windows, linux |
| `fieldkit release verify` | A published GitHub release checked - every asset downloaded into a new empty folder and compared with SHA256SUMS and GitHub's digest, the page's table, every .deb/.xz through xzcheck, then linux-install.yml run and deb-ubuntu / rpm-fedora reported | windows, linux |
| `fieldkit repro compare` | Two builds of the same source compared byte for byte, and what differs named (inside archives) with its cause | windows, linux |
| `fieldkit review` | ONE review of everything waiting on the owner - parked ideas (I-nn) first, then open inbox items (#nnn), each with its plain-language why and the command it would run, answered once (a / p / f / numbers / both, words after ':' recorded as the owner's note); --open opens state/REVIEW.md, rewritten on every inbox or ideas change | windows, linux, darwin |
| `fieldkit.autopilot.room` | The shared room - every AI session on this machine sees what the others did on shared ground (commits, history changes, shared-file edits, process starts and stops, network and heavy steps, messages and owner approvals), written by a hook, not by memory | windows, linux |
| `fieldkit rotate` | Session rotation - every live agent session measured (context per call, dollars), one verdict each OK / WARN / ROTATE, a monster session told at every prompt to hand over and start a fresh one, autopilot jobs restarted from their checkpoint in a new session, finished sessions listed for archive | windows, linux, darwin |
| `fieldkit route` | Solo or Multiplayer and the model router - the owner accepts a mode (solo = one job at a time, nothing on another device; multi = up to 3) and the strongest model (haiku, sonnet, opus, fable) once a day; every `task start` then scores the brief's risk and runs it on the cheapest model that needs, never above the accepted one; a failed round climbs one rung up to the ceiling; a job that needs Fable is parked in the owner inbox | windows, linux, darwin |
| `fieldkit rules` | The core rules every agent reads, as data (rules/rules.yaml): lint them for BOTTLENECKS, duplicates, size and misplaced text, build rules/CORE.md, install every block into each global rules file (Claude Code, Codex, Gemini) with a backup, check them | windows, linux |
| `fieldkit runbook` | The harness runs a runbook's steps (logs, ETA, retries, STOP, inbox for owner steps); a model only fixes a failed step | windows, linux |
| `fieldkit scratch` | The scratch root - one short folder (C:\fk-scratch; ~/.cache/fieldkit-scratch) for test copies, fixtures, transfers and probe output: marked job folders, deleted directly and fast, only when yours or stale; report with sizes, ages, owners; the owner's Defender exclusion script | windows, linux, darwin |
| `searchfox` | Search Firefox source on Mozilla's Searchfox index: matching lines with file and line number | windows, linux |
| `searchfox-blast-radius` | Every Firefox file that mentions a name, with how many times (impact map), from Searchfox | windows, linux |
| `searchfox-deps` | The include lines of one Firefox file, from Searchfox | windows, linux |
| `searchfox-file` | The Searchfox web address of a Firefox source file (works offline) | windows, linux |
| `searchfox-includers` | Firefox files that include a header, from Searchfox | windows, linux |
| `searchfox-map` | Where a Firefox class is defined, plus usages with nearby lines, from Searchfox | windows, linux |
| `searchfox-svg-keeplist` | Is an SVG referenced anywhere in Firefox, from Searchfox? (zero hits is not proof it is unused) | windows, linux |
| `searchfox-symbol` | Where a Firefox symbol is defined and used, from Searchfox | windows, linux |
| `sensors-gorilla` | Terminal dashboard of every readable hardware sensor (Linux) | linux |
| `fieldkit session-cost` | What an AI coding session costs in dollars, live or afterwards: Claude Code (CLI and desktop Code tab, subagents included), Antigravity (Gemini and Claude), OpenAI Codex, Gorilla OpenCode and any agent writing the generic usage contract, cost per call, the share spent re-reading old context, a loud warning past a dollar threshold | windows, linux, darwin |
| `fieldkit sessions` | Session lifecycle - finished and idle Claude desktop sessions with the RAM and CPU their process trees hold (finished: no open PR and idle; idle: waiting on an open PR; held: working now, the command post in NET.yaml, a parent of live sessions, exempt), for the command post to archive; the done protocol every brief ends with; the standing rule block for CLAUDE.md, GEMINI.md, AGENTS.md | windows, linux, darwin |
| `fieldkit sessions tag` | Session-title check - every live session, on this computer and on each peer over the link, whose title does not start with its machine's tag ([VAIO-LNX]); a peer that cannot be asked is NOT CHECKED, never a pass | windows, linux, darwin |
| `fieldkit ship` | The git side of shipping - status, sync a squash-merged branch (merge -s ours), commit (a known hook refusal fixed and tried again, the same one twice or an unknown one reported, never --no-verify), push, merge (into main only under a project mandate, every condition proven by the tool), audit (remote branches, open PRs, and since 2026-10-23 local worktrees and local branches left behind), release, prune; never forces | windows, linux, darwin |
| `fieldkit ship gh-release` | Publish a GitHub release of a tag that is already on GitHub, CI green, under the mandate's release: from_tag: gh release create --verify-tag, the repository taken from git (a -R/--repo/--target from the caller is refused) | windows, linux, darwin |
| `fieldkit ship push --tag` | Push ONE new version tag through the mandate gate: a tag the mandate's tags: patterns allow, on a commit GitHub already has (or that this push carries); a tag is never moved or deleted | windows, linux, darwin |
| `fieldkit ship tags` | Every tag of a repository classed (release, version without a release, archive/*, other) from git ls-remote --tags and git tag --sort=-creatordate, only real candidates flagged (unreachable, duplicate, local-only); one owner command for any GitHub deletion; with --all every non-archived repo of the owner in one table | windows, linux, darwin |
| `fieldkit shot` | Read numbers off a screenshot, and reason about what they mean | windows, linux |
| `fieldkit shot` | Compare two screenshots the way a person does: find the place (grid), cut it out (crop), enlarge without smoothing (zoom), stack two, contact sheet, and measure text (stroke width, blur, coloured smoothing) | windows, linux |
| `fieldkit shot speedtest` | Pull download, upload and ping off a speedtest screenshot (never guesses) | windows, linux |
| `fieldkit shot verdict` | From several speedtest shots, say WHICH LAYER is limiting | windows, linux |
| `fieldkit sitrep` | A situation report a reader can take in a few seconds at a time: template, check, render (six languages), doctor | linux, darwin, windows, freebsd |
| `fieldkit skills validate --strict` | Validate a skill's Agent Skills metadata and folder contract; recommendations are errors | windows, linux, darwin |
| `fieldkit skills package` | Package one valid skill directory into a deterministic .skill archive without overwriting existing files | windows, linux, darwin |
| `fieldkit skills sets` | A skill set per project, as data (fieldkit/skills/skillsets.yaml), written into Claude Code's own skillOverrides: skills outside the everywhere set hidden from the model in ~/.claude/settings.json, each project's own set turned on in its .claude/settings.local.json (kept out of git through .git/info/exclude); plus the user settings this machine never uses (claude.ai connectors off, unused tools denied) | windows, linux, darwin |
| `fieldkit skills` | Agent Skills (agentskills.io, the standard Anthropic gave the Agentic AI Foundation) - every spec rule checked on every skill, network doors in adopted third-party skills, installed copies against the sources, license/compatibility aligned, third-party skills rebuilt from a pinned upstream; plus the adopted skill-creator scripts (package, benchmark, eval) | windows, linux, darwin |
| `fieldkit snapshot diff` | Show exactly what changed between two recorded snapshots | windows, linux |
| `speaker-loudness-fix` | Loudness compressor for quiet laptop speakers (PipeWire/PulseAudio); undo puts back the output you had. A missing SC4 plugin stops it with the sudo command to run (never waits on a password) | linux |
| `speaker-loudness-status` | Is the speaker loudness compressor installed and active? (read-only) | linux |
| `fieldkit stranger` | The stranger test - download a project the way someone else would (fresh clone, fresh environment, empty home folder, the documented install) and see whether its tests and cards pass | windows, linux |
| `fieldkit strikes` | TWO STRIKES - a branch or PR that failed CI twice is parked (ci-wait, push and task start refuse it) and the plan goes on | windows, linux |
| `fieldkit survey` | Facts about a repository before reading it - languages, build systems, tests, licence, big files, privacy, git | windows, linux |
| `fieldkit sync` | Keep this machine's harness level with GitHub; take only fast-forwards CI proved on Windows and Linux | windows, linux |
| `fieldkit task` | Headless tasking - start a job from a brief file with no click: a one-step autopilot runbook in its own window here, or the peer's link listener over the cable (verb ask: fieldkit autopilot claude under that computer's approved link.yaml); the done protocol appended; reports come back by themselves (here: task report, over the cable: the link inbox) to the ledger, the ROOM log and the Board | windows, linux, darwin |
| `fieldkit teach` | A teaching log from a session transcript: every command a session ran, in order, with what came back | linux, darwin, windows, freebsd |
| `fieldkit telemetry` | Telemetry off and cut out - a project's source scanned for telemetry SDKs, endpoints and collectors; this machine's toolchain opt-outs and firewall blocks checked | windows, linux |
| `fieldkit terminal` | Open a terminal window in front of a person with a command ready: ask waits for their Enter, run runs at once | linux, darwin, windows |
| `fieldkit testcost` | What the checks cost and the cheapest set that is enough - a passed tree is never re-tested (tree-hash cache in the pre-commit hook), changed files mapped to their tests (data files through tests/testmap.yaml and their package; `unmapped --all` must print 0, CI enforces it), budgets from measured runs, every refusal logged | windows, linux, darwin |
| `fieldkit thermal ensure` | The machine thermal governor is always running: start it when it is down, install the keeper that checks it at logon, every 5 min and on resume; heavy work refuses while it is down (Windows and Linux) | windows, linux |
| `fieldkit thermal monitor` | Keep the processor cool while something heavy runs (a build): lowers the CPU cap when hot, speaks, puts it back (Windows and Linux) | windows, linux |
| `fieldkit thermal profile` | Which cooling profile the laptop is on now (run it with no value), or switch to quiet, balanced, performance or off (Windows and Linux) | windows, linux |
| `thermal-profile-daemon` | Step the Sony VAIO's firmware fan profile (silent, balanced, performance, forced) with the temperature (Linux) | linux |
| `fieldkit token-audit` | Where the tokens of Claude Code sessions went - API calls, context sent per call (average, peak, 100k bands), cache reads and writes, tools used most, text injected by the harness's own hooks, and the API-credit dollars | windows, linux, darwin |
| `fieldkit.autopilot.toolrouter` | Tool router - a command that does by hand what a harness tool does (recursive file search, folder sizes, text search over a tree) is refused with the tool's command, for Claude Code and Antigravity | windows, linux |
| `fieldkit tools external` | Inspect an external tool catalog's cards as data: schema, bounds, paths; never runs them, never grants trust | windows, linux, darwin |
| `fieldkit repair tpfan-config-verify` | TPFanControl.ini: switch the Active= mode safely and verify TPFanControl.exe against a caller-given SHA-256 pin | windows, linux, darwin |
| `fieldkit triage` | Name a build, install or release failure from its log, and run the stored checks (old icon shadowing the new one, stale GNOME icon cache, rolling download URL deleted, push refused by the mandate); `triage self-test --kind install\|release` proves they fire | windows, linux |
| `fieldkit update` | What on this machine is behind what was published, and the line that fixes it | linux |
| `fieldkit version-sync` | Every place a project states its version agrees - manifests, lock file, packaging, installer, headers, the built program | windows, linux |
| `videokit` | Phone video: doctor (check/install + per-video VFR/HDR/rotation), analyse (cached, real timestamps), events/draft, shake/shakecompare (phase-correlation jitter), flow (freezes, hard joins), lock (OpenCV gimbal/tripod/similarity, streamed), render (best-second waits, per-segment stabiliser by measured jitter, dissolves, gamma, sharpen) | windows, linux |
| `fieldkit voice` | A voice worth listening to on Linux (Windows is untouched) | linux |
| `fieldkit voice clean` | Remove voice models that are not the chosen one, only once it works | linux |
| `fieldkit voice doctor` | What this machine has for speech, what it needs, and how it will SOUND | linux |
| `fieldkit voice install` | Fetch and wire up the neural voice, system-wide, user-level only | linux |
| `fieldkit voice say` | Speak one sentence through whatever this machine is configured to use | linux |
| `fieldkit wait` | Wait on a condition, never on a clock - poll a command (exit code or regex) or a file, with a deadline | windows, linux |
| `fieldkit watchdog` | Keep this laptop online, or say so out loud when it cannot | windows, linux |
| `fieldkit watchdog install` | Start the watchdog at login (Linux only; Windows already has a scheduled task) | linux |
| `fieldkit watchdog pause` | Stop the alarm for a while, for a deliberate offline test | windows, linux |
| `fieldkit watchdog status` | Is this laptop online, on which network, and is the alarm paused | windows, linux |
| `fieldkit watchdog test` | Say the offline alarm once, to hear what it sounds like | windows, linux |
| `fieldkit web browsers` | Every browser installed on this computer (registry, .desktop files, app bundles, the Gorilla Firefox tree): version, engine and the stdlib method that drives it | windows, linux, darwin |
| `fieldkit web doctor` | Which installed browsers cannot be driven and the one step that would fix each (asks, never installs or changes anything) | windows, linux, darwin |
| `fieldkit web fetch` | A web page as text: the HTTP reader first, an installed headless browser (Chromium family dump-dom, Firefox 129+ over WebDriver BiDi, Safari) only when blocked or empty; throwaway profile, vendor channels off, nothing installed | windows, linux, darwin |
| `fieldkit web search` | A web search through the engines Gorilla OpenCode uses (Brave, Ecosia, Mojeek, Marginalia): plain HTTP, an installed browser when blocked; results as title, url, snippet | windows, linux, darwin |
| `fieldkit webtest` | Start local web servers, wait for their ports, run a test command (Playwright, pytest, anything), then stop every server's whole process tree - the adopted webapp-testing skill's with_server.py job, without its orphaned servers | windows, linux, darwin |
| `fieldkit wifi` | Why the wifi is slow, answered from the hardware rather than from guesses | linux |
| `fieldkit wifi card` | What this machine's radio can really tune, read from the card not guessed | linux |
| `fieldkit wifi channels` | Which 2.4 GHz channel is actually free, with the argument for the choice | linux, darwin, windows, freebsd |
| `fieldkit wifi doctor` | The link, the card's real limits, and which layer is the bottleneck | linux |
| `fieldkit wifi scan` | Every network in range, with the band named honestly rather than from the SSID | linux |
| `fieldkit wifi upgrade` | Can this machine's wifi card be replaced, and what cannot be known from software | linux |
| `fieldkit win11 run app-debloat apply` | Remove preinstalled apps by tier: light, moderate or super | windows |
| `fieldkit win11 run app-debloat check` | Remove preinstalled apps by tier: light, moderate or super: what is on now (read-only) | windows |
| `fieldkit win11 run copilot apply` | Turn Copilot off (settings; optionally remove the app) | windows |
| `fieldkit win11 run copilot check` | Turn Copilot off: what is on now (read-only) | windows |
| `fieldkit win11 fit` | Which service-profile entries would be WRONG on this PC (no SIM, WSL, shares, printers...): run before a services profile | windows |
| `fieldkit win11 modules` | The Windows 11 tweaks modules: is each complete (its check, its change with a preview, its undo)? | windows, linux |
| `fieldkit win11 oem` | Who made this PC, and are the maker's hardware-button services present (protected on Lenovo)? | windows |
| `fieldkit win11 run onedrive apply` | Block OneDrive on this machine (policy and scheduled tasks) | windows |
| `fieldkit win11 run onedrive check` | Block OneDrive on this machine: what is on now (read-only) | windows |
| `fieldkit win11 run recommendations apply` | Turn off Start menu recommendations, tips and suggestions (per user) | windows |
| `fieldkit win11 run recommendations check` | Turn off Start menu recommendations, tips and suggestions: what is on now (read-only) | windows |
| `fieldkit win11 run services apply` | Disable background services by profile: light, moderate or super (cumulative) | windows |
| `fieldkit win11 run services check` | Disable background services by profile: light, moderate or super: what is on now (read-only) | windows |
| `fieldkit win11 run text-input-guard apply` | Install a guard that ends a stuck TextInputHost (the touch-keyboard host) when it eats the CPU | windows |
| `fieldkit win11 run text-input-guard check` | Install a guard that ends a stuck TextInputHost: what is on now (read-only) | windows |
| `fieldkit win11 run update-deferral apply` | Hold Windows feature updates for 3, 6 or 12 months (security updates still install) | windows |
| `fieldkit win11 run update-deferral check` | Hold Windows feature updates for 3, 6 or 12 months: what is on now (read-only) | windows |
| `fieldkit win11 run update-distribution apply` | Stop Windows Update sharing downloads with other PCs (Delivery Optimization peering) | windows |
| `fieldkit win11 run update-distribution check` | Stop Windows Update sharing downloads with other PCs: what is on now (read-only) | windows |
| `fieldkit win11 run visual-effects apply` | Turn off Windows animation and transparency effects (per user) | windows |
| `fieldkit win11 run visual-effects check` | Turn off Windows animation and transparency effects: what is on now (read-only) | windows |
| `fieldkit win11 run xbox-services apply` | Disable the Xbox background services | windows |
| `fieldkit win11 run xbox-services check` | Disable the Xbox background services: what is on now (read-only) | windows |
| `wintrace` | Inspect your own Windows computer for intrusion, read-only, with Windows' own APIs (the Linux reflexes Windows lacks): ss (sockets + process + signer + hosted services; flags network-reachable listeners whose binary is not validly signed), fuser (who holds a file open, Restart Manager), modules (every DLL/EXE/.node mapped into a process and its signer), svc (services in a process), whodraws (windows in Z order; which windows of other processes lie over a process's window or sit at a point; no screenshots), opensnoop (files opened per process for N <= 120 s: Kernel-File ETW via logman, tracerpt to CSV, one UAC click), doctor | windows |
| `fieldkit repair worktree-classify-recycle` | Classify git worktrees against origin/main and PR state (merged, open-pr, unmerged, +dirty); lists only clean merged ones as recyclable, with the command; deletes nothing | windows, linux, darwin |
| `fieldkit xzcheck` | An xz archive is as small as xz can make it and unpacks within a memory cap (.xz, .tar.xz, a .deb's members) | windows, linux |
| `fieldkit zero` | The agent net, the military way - 'Zero has been established': one net control station (Zero) for every agent session, an epoch that fences a second Zero out (split-brain refused), a 2IC for failover, check-ins on a lease (comms check / radio check), roll calls answered with a fixed sitrep, LATE / SILENT / MISSING stations on the PLAN.md Board and in the owner inbox, a dead cable revived, clocks compared, an append-only hash-chained net log with acked, retried messages; the command layer: orders per station (objective, ground, done), ground claims enforced by the PreToolUse hook (a write on another station's ground or outside its own is refused), drift and idle-without-done flagged by the scheduled check, a station idle asked why, Zero woken only by a NEW finding (the 2IC through the inbox when Zero is silent), orders carried across rotation. Also `fieldkit net VERB`. | windows, linux, darwin |
<!-- tools:end -->

---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope

`v0.9.2..origin/main`: PRs #417 and #419.

- `inbox.migrate_legacy()` moves open owner-inbox items whose kind is not in `OWNER_KINDS` (only `hardline` since 2026-11-01) to the agent ledger with `moved_from`; the file keeps them as state `moved`. `review.entries()` runs it and lists only `OWNER_KINDS`. On the VAIO it moved #27 to #39 (13 items).
- `inbox.words_for(item)` builds what / if you approve / if you leave from the item's own title, why and command when it has none; `inbox.add` stores them for owner items. The test that asserted the placeholder now asserts its absence.
- `review.ideas_in_plan()` + `idea_row()`: a plan row counts for an idea only when it names the idea id and its date (ids were reused: the October I-01 is P2, the 2026-11-10 I-01 is P23). A built idea says "ALREADY BUILT as plan row P23 (#412)"; `ideas.set_status(accepted)` on it records the existing row and writes no brief.
- `plan.HEAD_IDS`: a merge whose PR title starts with the row id ("P24: ...") counts for that row; P23 and P24 are DONE (#412, #415).

- #419: plan status cells written from their merges: R10, R14, R15, R16, R19, P19, P20, P21 DONE; P4 and P7 DONE for `fieldkit image` with the Gorilla OpenCode toolbox part not verified; P5 PARTIAL (Linux 13/13 with the cloud model; the local-model run is missing: no local model runtime on the VAIO, LM Studio is on the ThinkPad). `plan next` now says P5 is the open row. A test string with an invalid escape (`"\\|"`) no longer warns on every commit.

### Not verified

The review page was read as rendered on the VAIO; the owner's own terminal run of `fieldkit review` after this release is not yet seen.
