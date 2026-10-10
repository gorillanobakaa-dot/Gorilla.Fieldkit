# Check builds, check releases and keep a laptop cool during long builds — Plain Language Guide

> Generated 2026-10-02 from `builds-releases-thermal`

---

## Should You Run This?

Run `fieldkit triage`, `fieldkit refcheck` and `fieldkit thermal status` freely: they only read. Run `fieldkit thermal prove` and `fieldkit thermal watch` only on a laptop plugged into mains power, because they load every core and change a power setting. If you must stop the watch early, press Ctrl+C and wait; never close its window with the X button. Do not use the watch if `fieldkit thermal prove` says `NO LIVE SOURCE`. Run `fieldkit lifecycle` and `fieldkit release check` only with spec files you wrote or have read line by line. Do not run the kernel stages on Windows; they need Debian or Ubuntu.

## Worst Case, Honestly

The most likely real harm is a lowered processor speed that stays lowered. `fieldkit thermal watch` changes the Windows maximum processor state, which can drop to 30%. It puts the old value back when it stops normally, and since 2026-10-02 it also puts it back when you press Ctrl+C. It still cannot put it back if the program never gets the chance: if you close the window with its X button, end the program from Task Manager, or the laptop loses power. Your laptop then runs at a fraction of its speed until you reset the setting, as described under If Something Goes Wrong. A second harm is a false sense of safety: if the only temperature source on your computer is slow or only measures the case, the guard reacts late, and a build can still push the processor hot. A third harm comes from spec files: `fieldkit lifecycle` and `fieldkit release check` run the commands written in the spec file you give them, so a spec file from someone you do not trust can run anything on your computer.

## What Data This Touches

`fieldkit triage`, `fieldkit refcheck` and `fieldkit thermal` read files and system readings on your computer and send nothing anywhere. `fieldkit thermal status`, `prove` and `watch` read the TPFanControl status file and window if that fan program is installed, ask Windows for its thermal zone and processor performance readings through PowerShell, and read your power plan with the Windows `powercfg` program. `fieldkit thermal watch` also writes to your power plan's maximum processor state, on mains power and on battery, and writes it back when it stops. `fieldkit release check` contacts GitHub through the `gh` program to read the published release notes, published files and release downloads. It also runs a privacy scan on the exported release before the tests run, and fails the release if the scan finds private material. `fieldkit release prove` writes a small evidence file that records the computer's operating system name and version, processor type, processor count, Python version, the time, and the last lines of each test's output. The kernel pipeline downloads the Linux source and its checksum list from `cdn.kernel.org`. `fieldkit lifecycle` records the folders its spec file names and, if the spec asks, the lists of installed programs, services and scheduled tasks, before and after; it keeps those records and its logs on your computer under the Fieldkit folder's `state` folder. It runs whatever install and uninstall commands its spec file lists, so what those commands send depends on that file. The governor can write temperature, processor load and cap readings to a CSV file, but only when the code that calls it asks for one; `fieldkit thermal watch` does not ask for one.

## Before You Trust It

Part of this group changes a Windows power setting, installs software, or runs commands from a spec file. Check what it does on your computer before you let it near a long build.

**Step 1:** Open PowerShell: press the Windows key, type `PowerShell`, and press Enter. Type the following line and press Enter:

```
fieldkit thermal status
```

Write down the number after `cap (PROCTHROTTLEMAX):`.
  - Look for: Pass: five source lines and a cap line appear. A source showing `None` gives no reading. Fail: `fieldkit` is not recognised, which means Fieldkit is not installed for this account.
**Step 2:** Type `fieldkit thermal prove` and press Enter. Your fan may get loud: the test keeps every core busy for 20 seconds or more per source it tries.
  - Look for: Pass: a line starting `PROVEN:` with the source name and `[die sensor]` or `[surface sensor]`. Fail: `NO LIVE SOURCE`, which means no source on your computer moved under load and the governor cannot protect you.
**Step 3:** Type `fieldkit thermal status` again and press Enter.
  - Look for: Pass: the cap number matches the one you wrote down in step 1. Fail: the cap is lower; reset it as described under troubleshooting.
**Step 4:** Type `fieldkit kernel localversion --base 7.1.2 --tags unleashed gorilla eapd` and press Enter. This only calculates a name; it builds nothing.
  - Look for: Pass: a block that shows `uname` starting `7.1.2-unleashed.gorilla-eapd-` followed by the date and time, a `length` below 64, and an empty `dropped` list.
**Step 5:** Check that Ctrl+C puts the cap back. Plug the laptop into mains power. Type the following line and press Enter:

```
fieldkit thermal watch --seconds 120
```

Wait until lines with `cap` and `peak` start to appear every three seconds. Then press Ctrl+C once and wait. When the prompt comes back, type the following line and press Enter:

```
fieldkit thermal status
```
  - Look for: Pass: the cap number matches the one you wrote down in step 1. Python's few lines ending in `KeyboardInterrupt` are expected. Fail: the cap is lower; reset it as described under If Something Goes Wrong, and do not rely on the watch on this computer.
**Step 6:** Before you run `fieldkit lifecycle` or `fieldkit release check` with a spec file you did not write, open the spec file in Notepad and read every line under `cmd:`, `tests:` and `proof:`.
  - Look for: Pass: you recognise every command. Fail: a command you do not understand; do not run the spec.

## The Big Picture

This part of Fieldkit helps you build software on your own computer and publish it without fooling yourself. It has seven pieces. Four of them read things and tell you what is wrong: `fieldkit triage` finds the real error in a long build log, `fieldkit refcheck` checks that every file a project names really exists, `fieldkit release check` checks that what you published is what you tested, and `fieldkit thermal status` shows what each temperature source on a Windows laptop reports.

Three pieces change your computer. `fieldkit lifecycle` installs a program, checks it, removes it again and lists anything the removal left behind. If it could not read one of the lists it compares, such as the list of Windows services, it now says NOT CLEAN and names the list, instead of quietly calling the removal clean. The kernel pieces download and build a custom Linux kernel on a Debian or Ubuntu computer. `fieldkit thermal watch` lowers and raises the top speed of your processor on Windows while a long build runs, so the laptop does not overheat. If you stop it early with Ctrl+C, it now puts your processor's top speed back the way it found it.

The temperature part exists because of a real failure. On 2026-10-02 at 05:28 a laptop switched itself off in the middle of a build. The build guard had trusted a temperature source that showed 41.85 C for 386 readings in a row while the processor worked hard. That source was readable and looked normal, but it never moved. Fieldkit now only trusts a temperature source after it proves the reading rises when the processor is busy.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `Build log` | The long text a build program prints while it turns source code into a program. | A flight recorder: thousands of lines, and the one line that explains the crash is somewhere in the middle. |
| `Signature` | A saved pattern that recognises one known failure, with its cause and its fix written next to it. | A doctor's list of symptoms: when the rash looks like this, it is that illness, and this is the treatment. |
| `Reference check` | A check that every file, link or module that one file names really exists on disk. | Checking every address in your address book still has a house at it. |
| `Release gate` | A list of checks that must all pass before you are told it is safe to publish. | A pre-flight checklist: one failed item and the plane does not take off, and there is no button to skip it. |
| `Hash (SHA-256)` | A long fingerprint calculated from the exact bytes of a file. Two files with the same fingerprint are the same file. | A fingerprint at a crime scene: names can be faked, fingerprints cannot. |
| `Lifecycle` | Install, check, optionally upgrade, uninstall, and then compare the computer with how it was before. | A house-sitter who photographs every room before you arrive and again after you leave, then lists what moved. |
| `Kernel` | The core of the Linux operating system, the part that talks to the hardware. | The engine of a car: everything else is bodywork around it. |
| `Proven temperature sensor` | A temperature source that Fieldkit has watched rise while it deliberately kept every processor core busy. | Testing a smoke alarm with smoke before you trust it, instead of trusting the green light on its case. |
| `Processor cap` | The Windows power setting called maximum processor state, a percentage of the processor's full speed. | A speed limiter on a van: the engine is the same, but it cannot go above the set speed. |
| `Fail closed` | When a check cannot see something, it treats the unseen part as a failure, not as a pass. | A night guard who finds one door he cannot open does not write 'all doors locked' in the log; he writes 'door 3 not checked' and the building is not signed off. |
| `Snapshot` | A recorded list of what is on the computer at one moment: files in the watched folders and, if the spec asks, installed programs, services and scheduled tasks. | The photographs a removal firm takes of every room before the move, so damage can be proved afterwards. |

## How It Works — Step by Step

### Step 1: Triage reads a failed build log

You give `fieldkit triage` a log file. It walks through every line and keeps only lines that look like errors, such as `make: *** ...` or `error: ...`, without repeats. It then compares those lines with saved signatures for three kinds of build: `firefox-windows`, `debian-kernel` and `debian-packaging`. If a signature matches, it prints KNOWN with the cause and the fix. If nothing matches, it prints UNRECOGNISED, shows the error lines, and prints a blank signature you can fill in so the same failure is named next time. It never guesses: an unknown failure stays unknown. Think of a mechanic with a book of known faults: if the noise is in the book, you get the page; if it is not, you get told so, not a guess.

### Step 2: Refcheck checks that named things exist

You tell `fieldkit refcheck` which kind of list to check: a manifest file of paths, links inside Markdown files, Python imports of the project's own modules, or any pattern you describe. It collects every name and checks each one on disk. The answer is short on purpose, for example `1 of 2 missing (markdown):` followed by the missing link, and a line that starts with `NEXT:` saying what to do. It is like a postman walking the whole round with the address list and noting each house that is not there.

### Step 3: Release check compares the published thing with the tested thing

`fieldkit release check` reads a release spec file. It exports the exact tagged version of the project with git, so unsaved work on your computer cannot slip in. It runs a privacy scan, then the spec's tests on that clean copy. It downloads the published files from GitHub and compares their fingerprints with the tested files. It reads the release notes, and for every claim the notes make, such as a number of tests, it runs the proof written in the spec. The verdict is CLEAR or DO NOT PUBLISH, with every failed check listed. There is no option to override it. Compare it to a shop checking that the parcel handed to the courier is the same sealed box that passed inspection, by its serial number and not by its label.

### Step 4: Release prove records a result from another computer

Some claims, such as runs on Linux, cannot be proven from a Windows computer. On the other computer you run `fieldkit release prove` with the same spec. It runs the tests on the exported tag and writes an evidence file. You carry that file back. `fieldkit release check` accepts it only if it names the right platform, the same version of the code (by git tree id), and a passing result. It works like a signed delivery note: the other site signs for exactly this version, and the note is only accepted if every detail matches.

### Step 5: Lifecycle installs, checks and removes a program

`fieldkit lifecycle` refuses to start without `--approve`, because it installs and removes real software. It takes a snapshot of the folders and system lists named in the spec, runs the install command, checks the result, and takes another snapshot. Then it runs the upgrade and uninstall commands if the spec has them. If the install fails, it stops and does not uninstall something that never installed properly. At the end it compares the snapshot from before the install with the one from after the uninstall, and lists LEFTOVERS: everything that exists afterwards that did not exist before, minus the leftovers the spec allows.

Since 2026-10-02 it also guards against a blind spot. If Windows (or Linux) failed to answer one of the system questions, for example the list of services timed out, that list is marked as not captured. The comparison then does not pretend the list was empty. Lifecycle prints `NOT COMPARED:` with the reason under `LEFTOVERS in` that list, and the verdict is NOT CLEAN. Before this fix, an unread list could look like nothing was left behind. Think of a house-sitter whose camera failed in one room: an honest one writes 'kitchen not photographed', not 'kitchen untouched'.

### Step 6: The kernel pieces build a Linux kernel step by step

These run as stages of the `debian-kernel` pipeline on Debian or Ubuntu. One stage checks that the build tools are installed. One downloads the kernel source and checks its fingerprint against the list published by `kernel.org`; a mismatched download is renamed with `.bad` at the end and not used. One unpacks it. One applies the project's list of kernel options and then checks that every option survived, because the kernel's own tidy-up step silently drops options whose dependencies are switched off. One builds the packages with a name kept under the 64-character limit, and one moves the finished packages to an output folder. On Windows you can plan and rehearse the pipeline, but the stages that need Debian do not run. Picture a builder who, after the plasterer leaves, checks every socket on the plan is still there, because some get covered over without anyone saying.

### Step 7: Thermal finds a temperature source and makes it prove itself

On Windows there is no single trusted way to read the processor's temperature. Fieldkit tries five sources in order: the status file of TPFanControl, a ThinkPad fan control program, the window of that program, its CSV log, the Windows thermal zone counter, and a Windows management reading. For the first source that gives a number, it starts one busy program per processor core, waits eight seconds, and takes six readings two seconds apart. The source passes only if the reading rises or moves by at least two degrees. A source that rises by 10 degrees or more is graded as a die sensor, which tracks the chip itself; one that moves less is graded as a surface sensor. A surface sensor gets a second, longer test of about one minute, because a slow chip sensor can look like a surface sensor in a short test. It is the smoke-alarm test: you hold smoke under it and see it react before you trust it to guard the house.

### Step 8: The governor moves the processor cap during a build

With a proven source, the governor reads the temperature every three seconds. Above the target plus three degrees (target 75 C by default) it lowers the cap by 10 points, never below 30%. At 90 C it drops the cap to 30% at once. When the reading stays more than six degrees under target for 45 seconds, it raises the cap by five points, never above the starting cap. At 95 C it stops the build. It also stops the build when the source gives no reading for 30 seconds, or when a die sensor shows the same number for 60 busy readings in a row, which is the frozen-sensor failure that caused the 05:28 reset. When it stops, it puts the original cap back.

The `fieldkit thermal watch` command runs this for a set time and prints what it would have stopped, but stops nothing. Since 2026-10-02, if you press Ctrl+C while it watches, it first tells the governor to stop, waits for it to finish, and the governor puts the original cap back before the program ends. Python then prints a few lines ending in `KeyboardInterrupt`; that is the normal sign of Ctrl+C, not a fault. It is like cruise control on a car: it eases off on hills and speeds up on the flat, and when you tap the brake it hands the car back exactly as it was.

## Quirky Things Worth Knowing

### A readable temperature is not a trusted temperature

The reset on 2026-10-02 came from a source that worked, showed a believable 41.85 C, and never changed for 386 readings while the laptop worked hard. Fieldkit therefore ranks sources by proof, not by whether they give a number. You will see `NO LIVE SOURCE` instead of a number that has not moved under load.

### The proof test makes your fan roar on purpose

`fieldkit thermal prove` and `fieldkit thermal watch` keep every processor core fully busy for about 20 seconds per source, and about one more minute if a source first looks like a surface sensor. That is the test, not a fault.

### `thermal watch` changes a real Windows setting

The command says nothing is killed, and that is true for builds. It still changes your power plan's maximum processor state, on mains power and on battery, while it runs, and sets both back to the starting mains value when it stops. Stopping it with Ctrl+C also sets them back. Closing the window with its X button does not give it that chance.

### Some numbers from the fan program are ignored on purpose

The fan control program reports some sensors that never change, such as a fixed 148 on an empty slot and a fixed 66 on another. Fieldkit ignores every value of 140 or more, and reads only the processor sensor, so a fixed number cannot pretend to be the processor.

### A failed release check has no override

There is no `--force`. When a check fails you either fix the release or fix the check. This is deliberate: an earlier browser release reached people with a broken address bar, and its notes claimed something no one had tested.

### A claim the notes do not make is not checked

`fieldkit release check` only tests claims it finds in the published release notes. A claim in the spec that the notes do not mention is skipped, not failed.

### Triage says no-error-lines when a build dies silently

If the log has no error lines at all, triage does not invent a cause. It suggests two real possibilities: output was hidden, or the process was killed for lack of memory.

### NOT COMPARED means NOT CLEAN, even with no leftovers listed

If `fieldkit lifecycle` could not read one system list, you may see `LEFTOVERS in services:` followed only by a `NOT COMPARED:` line and no `+` or `~` lines. That is not a contradiction. It means Fieldkit does not know what was left there, so it refuses to say clean. Run the lifecycle again when the system is less busy.

### What this cannot do

The governor cannot read a temperature Windows does not offer: on a computer with no source that moves under load it can only say `NO LIVE SOURCE`. It cannot put your processor cap back if the window is closed with its X button, the process is ended from Task Manager, or the power fails. It restores both the mains and battery settings to the mains value it found, so a different battery value you had set is not kept. `fieldkit thermal watch` never stops a build; only a build harness that hands the governor a stop action can do that. `fieldkit lifecycle` does not sandbox the spec's commands and does not watch folders the spec does not name. `fieldkit release check` does not check claims the notes do not make, and the kernel checksum list is not checked against its signature. Triage only knows the failures in its signature files.

## What This Means For You

### Battery, Processor & Memory

`fieldkit thermal prove` and `fieldkit thermal watch` load every processor core fully during the proof test, which drains battery and heats the laptop for that time; the exact energy use is not measured. `fieldkit thermal watch` can then lower the processor cap to as little as 30%. The kernel build and the release tests use as much processor and memory as the build or tests need; not measured. `fieldkit triage` and `fieldkit refcheck` cost: not measured.

### Speed

When the governor lowers the cap, the build runs slower, by an amount that is not measured. The governor exists to trade some speed for not switching off mid-build. The whole Fieldkit test suite of 584 passing tests takes 194.89 s on the author's laptop; the time for this group alone is not measured.

### Your Privacy

Nothing in triage, refcheck or thermal leaves your computer. `fieldkit release check` scans the exported release for private material before anything is compared. The evidence file from `fieldkit release prove` names your operating system, processor type and count, and Python version; check it before you share it.

### Your Internet

`fieldkit triage`, `fieldkit refcheck` and `fieldkit thermal` use no internet. `fieldkit release check` talks to GitHub and may download release files. The kernel fetch stage downloads the full Linux source archive from `cdn.kernel.org`; its size is not measured here. `fieldkit lifecycle` uses whatever the install commands in its spec use.

## The Off Switch

**What it is:** There are four stops. The thermal governor stops a build at 95 C, when the sensor goes silent for 30 seconds, or when a chip sensor freezes under load; it puts your original processor cap back when it ends. You can stop `fieldkit thermal watch` yourself at any time by pressing Ctrl+C once in its window and waiting a few seconds: the cap is put back before the program ends. `fieldkit lifecycle` refuses to run without `--approve`, and refuses to say CLEAN when any system list could not be read. `fieldkit release check` refuses to say CLEAR while any check fails, and has no `--force`.

**Without it:** Without the thermal stop, a build can run on a frozen sensor until the laptop's firmware switches it off, which is what happened at 05:28 on 2026-10-02. Without the Ctrl+C restore, stopping the watch early could leave your laptop running at as little as 30% of its speed. Without `--approve`, a typing slip could install and remove software. Without the not-compared rule, a list that failed to load could make a messy uninstaller look clean. Without the no-override rule, a release could go out with a file nobody tested.

**Think of it like:** A kettle that switches itself off when it boils, and also switches off if its thermostat stops reporting, instead of trusting a thermostat that has shown the same number for an hour.

## How to use this

**Before you start:**
- Fieldkit installed so that `fieldkit` works in PowerShell.
- Windows for the `thermal` commands. The thermal status file and window sources need the ThinkPad fan control program running; without it, only the Windows sources are tried.
- Debian or Ubuntu to run the kernel build stages.
- The `git` and `gh` programs, and a GitHub sign-in in `gh`, for `fieldkit release check`.
- A spec file you wrote or have read, for `fieldkit lifecycle` and `fieldkit release`.

**Step 1:** Open PowerShell: press the Windows key, type `PowerShell`, and press Enter. A window with a blinking cursor opens. Go to the Fieldkit folder: type the line below, with the real location of your Fieldkit folder between the quotes, and press Enter.

```powershell
cd (fieldkit where)
```
  - You should see: Pass: the prompt now ends with the Fieldkit folder's name. Fail: `Cannot find path`, which means the location is typed wrong; check it in File Explorer's address bar.
**Step 2:** To find out why a build failed, type the command below, using the real location of your log file, and press Enter.

```powershell
fieldkit triage C:\path\to\build.log --set auto
```
  - You should see: Pass: `verdict: KNOWN` with a cause and a fix. For a kernel log that says `Unmet build dependencies: libdw-dev:native`, the answer is `KNOWN unmet-build-deps (debian-kernel)` with the fix to install the named packages. Also normal: `SUCCESS`, `UNRECOGNISED` with the error lines and a blank signature, or `NO-ERROR-LINES` with a hint. Fail: `No such file`, which means the log location is wrong.
**Step 3:** To check the links in a folder of Markdown files, type the command below and press Enter.

```powershell
fieldkit refcheck markdown C:\path\to\folder
```
  - You should see: Pass: `all N references exist (markdown).` Fail: a line such as `1 of 2 missing (markdown):`, the missing links, and a `NEXT:` line telling you what to fix.
**Step 4:** To watch the temperature during a build without stopping anything, type the command below and press Enter, then start your build in another window. If you need to stop early, press Ctrl+C once in this window and wait for the prompt; do not close the window with its X button.

```powershell
fieldkit thermal watch --seconds 600 --target 75
```
  - You should see: Pass: first the proof test runs and prints the source it is watching. Then every three seconds a line with the temperature, the cap and the peak. Lines starting `thermal: cap:` show each change, and the last says the cap is `restored to` the starting value (you only see that line if the cap was changed). Fail: `no live source:`, which means no temperature source moved under load and nothing is watched.
**Step 5:** To check a release before you announce it, type the command below, using your own spec file name, and press Enter.

```powershell
fieldkit release check releases/fieldkit-v0.1.0.yaml
```
  - You should see: Pass: a list of `ok` lines, then `CLEAR` and `NEXT: publish.` Fail: `DO NOT PUBLISH` with a count of failed checks and a `FAIL` line for each.
**Step 6:** To test that an installer cleans up after itself, write a lifecycle spec, read it, and type the command below, using your spec's name, and press Enter.

```powershell
fieldkit lifecycle myapp.yaml --approve
```
  - You should see: Pass: `CLEAN: installed, verified, removed, nothing left behind`. Fail: `NOT CLEAN: see failed steps and leftovers`, with a `LEFTOVERS` list of added (`+`) and changed (`~`) items, or a `NOT COMPARED:` line naming a system list that could not be read.

## If Something Goes Wrong

**After `fieldkit thermal watch` your laptop feels slow, and `fieldkit thermal status` shows a cap lower than before.**
The watch ended without the chance to put the original cap back: the window was closed with its X button, the program was ended from Task Manager, or the laptop lost power. Pressing Ctrl+C does not cause this, because Ctrl+C now puts the cap back.
What to do: Open Control Panel, then Power Options, then Change plan settings, then Change advanced power settings. Under Processor power management, set Maximum processor state back to its previous value for both On battery and Plugged in. Next time, stop the watch with Ctrl+C.

**`fieldkit thermal prove` prints `NO LIVE SOURCE`.**
No temperature source on your computer moved by two degrees while every core was busy.
What to do: Do not rely on the governor on this computer. If you use the ThinkPad fan control program, start it and run the command again.

**`fieldkit triage` prints `UNRECOGNISED`.**
No saved signature matches this failure yet.
What to do: Read the error lines it shows. When you know the cause and fix, add the printed blank signature to the right file in `fieldkit/build/signatures/`.

**`fieldkit lifecycle` stops with a message that it needs `--approve`.**
It installs and removes software, so it will not run without your explicit go-ahead.
What to do: Read the spec file, then run the same command with `--approve` at the end.

**`fieldkit release check` says `no evidence from linux`.**
The release notes claim the code runs on Linux, and no passing Linux run of this exact version has been recorded.
What to do: On the Linux computer, run `fieldkit release prove` with the same spec, then copy the evidence file it names back into the `evidence` folder next to the spec.

**`fieldkit lifecycle` says `NOT CLEAN` and shows `NOT COMPARED:` under a `LEFTOVERS in` line.**
One of the system lists, such as services or installed programs, could not be read before or after; the reason follows `NOT COMPARED:`, for example a query that timed out.
What to do: Close other heavy programs and run the same `fieldkit lifecycle` command again. If the same list fails every time, remove that part from the spec's `parts:` line and check it by hand instead.

## Why a Developer Would Do This

Each piece comes from a real failure. A laptop switched off mid-build because a sensor that never moved looked believable. A browser release reached people with a broken address bar, and its notes claimed something no one had tested. A cloud kernel build differed from every local build because a step was skipped. The developer's answer each time is to measure the real thing, refuse to guess, and make the failure impossible to repeat quietly.

## Why It Matters That You Can Read This

You can read every threshold in these files: 75 C target, 90 C hot, 95 C stop, 30% floor, three seconds between readings. If you could not read them, you would be trusting that a program changing your processor speed restores it afterwards, and that a green CLEAR means the published file matches the tested file. Because the source is readable, someone you trust can confirm that the release check compares fingerprints, not names, and that triage leaves an unknown failure unknown instead of guessing.

## Glossary

**ACPI thermal zone** — A temperature reading that the laptop's firmware offers to Windows, often from the case instead of the processor.

**Die** — The silicon chip inside the processor, which heats up first under load.

**EC (embedded controller)** — A small chip on a laptop's main board that runs the fans and reports temperatures.

**Git tag** — A fixed name, such as `v0.1.0`, for one exact saved version of a project.

**Git tree id** — A fingerprint of a version's files that is the same on every computer.

**Maximum processor state** — The Windows power setting that limits how fast the processor may run, as a percentage.

**Spec file** — A short text file in YAML format that tells a Fieldkit command what to check or run.

**uname** — The full name a running Linux kernel reports about itself, limited to 64 characters.

**Ctrl+C** — Holding the Ctrl key and pressing C, which asks the program in a PowerShell window to stop.

**Not compared** — Fieldkit's label for a system list it could not read, which it refuses to count as clean.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Laptop reset came from a frozen chassis sensor | 📄 stated in input | a chassis ACPI zone stuck at 41.85 C for 386 samples |
| Thermal suite trusts only sensors that rise under load | 📄 stated in input | the thermal suite now accepts only sensors proven to rise under load |
| Release check has no override | 📄 stated in input | no --force: a failing gate means fix the release or fix the check |
| Published file is compared by fingerprint | 📄 stated in input | the file published must be the file tested (hashes, not names) |
| Lifecycle needs explicit approval | 📄 stated in input | a lifecycle run installs and removes software: it needs --approve |
| Triage does not fake unknown failures | 📄 stated in input | an unknown one is reported as UNRECOGNISED |
| Kernel options can vanish silently | 📄 stated in input | olddefconfig also silently drops options whose dependencies are off |
| Kernel stages need Debian | 📄 stated in input | Stages that need Debian say so; on Windows the pipeline can still be planned and dry-run |
| Governor restores the original cap on stop | 📄 stated in input | The original cap is restored on stop, whatever happened. |
| Restoring sets the battery value to the mains value | 🤖 model inference | *(none — model judgment)* |
| Proof test lasts about 20 seconds per source, plus about one minute for a surface sensor | 🤖 model inference | *(none — model judgment)* |
| Spec files can run any command | 🤖 model inference | *(none — model judgment)* |
| Evidence file records machine details | 🤖 model inference | *(none — model judgment)* |
| Lowering the cap slows the build | 🤖 model inference | *(none — model judgment)* |
| An unreadable system list makes a lifecycle not clean | 📄 stated in input | unread is not clean: fail closed |
| Ctrl+C during thermal watch puts the cap back (code in the command line file, outside this group, and its test) | 🤖 model inference | *(none — model judgment)* |
| Closing the window, ending the task or a power cut may leave the cap lowered | 🤖 model inference | *(none — model judgment)* |
| Python prints KeyboardInterrupt after Ctrl+C | 🤖 model inference | *(none — model judgment)* |
| Re-running lifecycle when the system is less busy may avoid a timed-out list | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*