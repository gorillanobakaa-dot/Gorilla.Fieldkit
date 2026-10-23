## What this is

Fieldkit v0.6.0: several computers in one house can now work on one project together, with one owner approval for the whole project instead of a prompt for every step.

## Should you download it?

Yes, if you run Fieldkit on more than one computer, on a computer without git (a Mac with no Xcode or Homebrew, a Windows laptop without developer tools), or if you build Gorilla OpenCode releases. Fieldkit is used from its source: `install.sh` (macOS, Linux) and `install.ps1` (Windows) download the zip from this page, put everything in one folder, and list what they installed.

## Why this matters

Before this release, two AI sessions on two computers could not hand work to each other, and every step asked the owner for approval. Now one computer is the boss: it holds the owner's signed mandate, splits the job, and the other computers do their part and report back. Twenty devices mean twenty one-time setups, never twenty prompts a night.

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->



---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope

`v0.5.0..origin/main`:

- test_buildh_owner_terminal: the bash ask-window test is skipped on Windows (its window is PowerShell; the runner's bash printed UTF-16)
- test_release_cycle: the GitHub stand-in reads the assets by position, not by a leading /
- Linux 158.0-2 post-install and release fixes: one BuildID per build, Linux theme spec and visual masters, release-check on Linux, release-shots tool, owner terminal on Linux
- release-cycle on-base: git paths with / on Windows too
- test_wintrace: ntdll's signature is also checked strictly only off CI (catalog-signed DLLs read Unreadable on the runner)
- Windows CI: the two tests main has failed on windows-latest since the mandate merges
- mandate guarded: find the elevate kit where it was adopted (local/kits/Elevate), the old place last
- test_wintrace: the strict signature check runs on a real machine only (the CI runner's Python is not Authenticode-signed)
- fieldkit mandate: the flags after a command reach it (found by the first real job)
- install.ps1: hash the zip with .NET (Get-FileHash is missing when Windows PowerShell 5.1 starts from PowerShell 7)
- fieldkit mandate: restore the status and install commands
- Linux 158.0-2 harness fixes: rebase-set keeps another platform's parts, .deb revision = build number, platform-aware binary-patch and decision checks
- fieldkit mandate: the Windows half of the protected store
- wintrace: read a window's exe as a Windows path on any OS (the Linux CI runner could not find claude.exe)
- fieldkit mac keys: type only into the window that has the keys; a paste is proven by new text; watch-dialogs counts each error once
- test_goapp: the mac-package-absent test uses a POSIX sh stand-in, so it skips on Windows like its siblings
- fieldkit mac watch-dialogs: type 'continue' after a provider error (capped), stop on an idle screen
- fieldkit mac sensors + macair heat with Macs Fan Control off (sensors.gorilla 2.2.0 reader)
- Recovery loops: the commit loop, the relay watchdog, silent jobs, health that repairs
- recovery loops: install.sh resumes a dropped download; tui-start waits out a Google sign-in
- fieldkit mac watch-dialogs: answer, count and log every permission dialog of a running Gorilla OpenCode
- fieldkit mac tui-start: v0.1.150's '? help' bar also means the prompt is ready
- fieldkit mandate tasks/start/report and fieldkit heal-round (plan 17.2 items 3, 5, 6, 7, the report)
- fieldkit mandate: the boss named in the mandate, an append-only ledger, device health (plan 17)
- fieldkit mandate: one orchestrator, many workers (plan section 16)
- fieldkit mandate: no model approves a merge (the verdict tool), and the release door
- fieldkit release-cycle: one release, the whole cycle, each stage done only by its artefact
- fieldkit mac release-install: install from the GitHub release page as a new user, old versions quarantined
- ship audit: local worktrees and local branches left behind
- fieldkit mandate: doors() says the review proof shows which computer approved, not that a person read the diff
- fieldkit mandate (M1.2): trust where the agent's OS user cannot write; the review proof on the reviewer's own key
- fieldkit mandate (M3): the callers ask the mandate first
- gorilla-opencode pipeline: the mac stage also makes the installer's package (folder + .zip), summed
- fieldkit mac run: retry a Wi-Fi drop like the screen commands do
- fieldkit mandate (M1.1): close the six holes the Windows review of ba40f28 found
- fieldkit mandate (M1): one owner approval per project, valid on both computers, checkable by each
- gorilla-opencode pipeline: every release file from one Linux machine (Mac 10.13 binary, deb, rpm, Arch, tarball)
- tui-start: the lowest title is the current screen; the help bar alone means ready
- fieldkit mac: never type into an unidentified screen (keys --expect, tui-start)
- fk: short plain-English names for long commands, the full command always shown; a paste is proven by its text
- doctor: --lean and --probe at the fieldkit door too; the git-stub check only on a Mac
- install.ps1: Fieldkit on Windows without git (GitHub zip, private .venv, lean set, doctor --lean, INSTALLED.txt)
- test_gtktheme: the symlink test needs symlinks (needs_symlinks; Windows without admin or Developer Mode: WinError 1314 refused every commit on the laptop)
- Fieldkit on a computer without git: doctor --probe/--lean, install.sh from GitHub's zip, fieldkit mac drives a Mac's screen
- peers link restart: card opened to Windows (the laptop had to restart its listener by hand)
- platform_gate --write: LF on every system (Windows text mode wrote CRLF)
- peers link: the start brief names the interpreter in forward slashes (the started session runs it in Git Bash, which eats unquoted backslashes)
- peers link (Windows items a-e): approve --allow/--cwd/--wall-minutes in one command; hello carries toolset + time; ask refuses when link_ops.startcheck(agent) fails (fail closed); the brief names the interpreter by full path; peers link check: clock skew, firewall, stale neighbor, put, shell (Git Bash backslashes, PowerShell &&), CRLF, commit (privacy before the hook), write size, symptom
- peers link: the night's manual steps as tools - toolset, startcheck, restart, verify-start, relay doctor
- fieldkit macair: app-check (nothing a decision removed may ship), a safe install, heat measurements
- peers link: send --needs-you/--done/--evidence; relay brief paths that survive Git Bash and PowerShell
- peers link send: the tool stamps [FROM <OS> #N] (signed seq, advanced on ok), [NEEDS YOU]/[DONE] from fields, [DONE] needs evidence; the inbox flags gaps, repeats, wrong tags and [DONE] without evidence
- peers link ask: a started session loads the link's own code (-P, PYTHONPATH = the link's root), not its working folder's
- autopilot claude: the prompt goes on stdin, so a brief starting with '---' is not read as an option
- peers link relay brief: start the listener on its own and check it runs; a chained send left a relay deaf
- peers link relay: one session per computer that only listens, so notes from the other computer are heard as they come
- peers link ask: never report a dead session as started; fit the start command to this autopilot
- peers link put: files over the cable, signed and checksummed
- tests: the four Linux-only tools' tests skip on Windows CI (the way platformcheck accepts)
- fieldkit macair: build, check, ship and verify Gorilla for the MacBook Air the same way every time
- fieldkit shot grid|crop|zoom|stack|sheet|text|text-compare and fieldkit mac shot: compare two screens and measure their text; fieldkit mac run quoting fix
- wintrace: read-only intrusion inspection on Windows (ss, fuser, modules, svc, whodraws, opensnoop)
- publish: push a branch that was never pushed (no upstream)
- peers link find: where the other computer is, and what is in the way, in plain words
- fieldkit mac: reach an old Mac over SSH, find it when its address changes, check it, run things on it
- peers link setup: the whole link in one command per computer
- fieldkit firmware check: a plugged-in device that does not work because its firmware is missing, built-in kernel drivers included
- peers link: agents on the other computer of a direct cable
- fieldkit leftovers: find what removed packages, other desktops and VMs left behind on Linux, clean it without touching the running session, undo it
- linkcheck kit + fieldkit eta calibrate (multi-machine step A)
- gtk-theme check: a stylesheet scrambled by two writers, and the duplicate login launchers that scramble it
- goal card: Windows only until proven on Linux
- autopilot: long horizon goals (step 0) + job budgets, checkpoints, resume (step 1)
- gpudecode: trace proven end to end; visible admin window, read command, INCOMPLETE verdict
- gpudecode: prove hardware video decoding on Windows with Microsoft's own tools (wpr + wpaexporter)
- gtk-theme check: a GTK theme that changes on unfocus, or a fix that does not show
