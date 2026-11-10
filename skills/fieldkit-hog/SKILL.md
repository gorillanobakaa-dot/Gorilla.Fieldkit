---
name: fieldkit-hog
description: Find what is eating this computer's memory, CPU, heat or disk and free it safely with fieldkit hog - RAM and commit charge with the top processes, idle local models in LM Studio or Ollama (unloaded after 15 idle minutes; they just reload), memory leaks with their growth curve, test runners, listeners and browsers our sessions left behind (stopped only when the harness registered them), a program hoarding dozens of consoles or shells it never closed, a core pinned at 100%, CPU temperature, low disk, and a laptop that shuts down or restarts by itself (fieldkit power investigate gives ranked suspects and the owner steps). Use when the laptop is slow, the fans scream, RAM is high, "what is using my memory", Task Manager shows dozens of "Console Window Host" rows, a model is loaded nobody uses, before a long build or test run, or after a session ended with things still running. Works on Windows, Linux and macOS.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML) on Windows, Linux, macOS. Works offline; no network access unless a step says so."
---

# fieldkit hog - the resource watchdog

Deterministic: every rule is a number in `fieldkit/hog/thresholds.json` (override in `state/hog/thresholds.json`).

```
fieldkit hog check [--json]     one pass, changes nothing; exit 0 OK, 1 WARN, 2 ACT
fieldkit hog act [--dry-run]    one pass, then the safe actions
fieldkit hog log                what it did: when, why, RAM before -> after
fieldkit hog doctor             psutil, lms, the schedule, the room hook
fieldkit hog install [--apply]  run `hog watch` every 5 min (Windows task as you / systemd user timer / launchd agent)
fieldkit hog register PID --cap 3h --why "link listener"   a long-lived process YOU started, so it is cleaned up
```

What it does on its own (`watch`, `act`, and the preflight before `fieldkit guard` and `thermal monitor`):
- unloads an LM Studio / Ollama model idle more than 15 min (5 min when RAM is at 90%); a model generating now,
  or one with no log evidence, is left alone;
- stops a REGISTERED process whose session ended, or that is past its cap; never by name, never anything the owner
  started (the registry holds pid + creation time; the room hook fills it).

Everything else (RAM, leaks, pile-ups, pinned CPU, heat, disk) is reported, not acted on.

Pile-up (rule `pileup`): a live parent holding more than 20 children of one class (conhost, bash, python, node,
powershell, cmd), or 10+ of them started over 12 h before its newest one, is one WARN line naming the parent, its
own parent, the count, their memory and the fix, e.g. `Claude (claude.exe 16824, started 2026-10-25 14:44:53,
parent claude.exe 17880) holds 33 consoles since 2026-10-25 14:44:53, 283 MB: restart Claude Desktop (or end that
claude session) when no session is working`. Do what the line says; the hog never stops them (not registered).
Numbers and fix texts: `pileup_*` in thresholds.json. Use it instead of a hand-written conhost census. Each action is one line in the ROOM
log, so every session sees it, and the prompt hook shows one line when the last watch found something.

Do not unload or kill by hand when the hog covers it: `fieldkit hog act`.

## The machine thermal governor is never down (fieldkit thermal ensure)

`fieldkit thermal ensure` says GOVERNOR UP / STARTED / DOWN and whether its keeper is installed; it starts the
governor (`thermal watch --forever`) when it is down. `fieldkit thermal ensure --install` installs the keeper (a user
task on Windows, a systemd user timer on Linux) that runs ensure at logon, every 5 minutes and on resume. While the
governor is down, `fieldkit guard` and the goal supervisor refuse heavy work. Its brake pauses the processes of
guarded jobs when it is hot and resumes them when it cools; the power plan is never changed. Run it first when the
laptop is hot, went down, or woke from sleep.

## The laptop shut down or slept by itself (fieldkit power incidents, power drift)

`fieldkit power incidents --since YYYY-MM-DD` lists every unexpected shutdown, cutoff and sleep from the system log
(Windows: Get-WinEvent; Linux: journalctl; macOS: pmset -g log), each with its class (heat, power-plan reset, program
sleep request (PROCESS), battery, button/lid, standby (REASON), unexpected cut (cause unknown)), the thermal journal's
peak CPU C in the 5 minutes before and the battery state. Exit 3 and a defect row in ZERO-OPEN-PROBLEMS.md for each
unknown cause. `fieldkit power drift` fails when ibmpmsvc.exe (Lenovo) asked for sleep on AC during work in the last 7
days or the sleep button on AC is not "do nothing"; `--apply` fixes the button without admin and records before/after;
what needs admin goes through the elevate tool (skill fieldkit-admin-unattended), never to the owner. `fieldkit power audit` says whether the machine can name who started a program (Security 4688, command lines, read access): PASS, SIGN-OUT (one new logon) or FAIL; `--write-fix` writes one self-elevating script (one UAC) for the missing steps, `--callers powercfg.exe` lists who started it.

## The one run guard (fieldkit/core/preflight.py)

Every Fieldkit-managed heavy run goes through it, so nothing is started by hand: `fieldkit guard -- CMD` and go
builds (with the thermal governor), `fieldkit mutate`, `fieldkit pipeline run`, autopilot jobs (runbook steps,
goals), the leak gate, and the pre-commit suite. Before: the checks (the hog reclaims, then refuses below 10% free
RAM; a job or the gate waits up to 10 min first). During: RAM and CPU temperature sampled, the hog's safe actions
every 5 min. After: ONE line, e.g. `run guard cargo: 12.0 min, peak RAM 30.1/63.7 GB, peak CPU 71 C, reclaimed
21.0 GB, 40 s capped`.

```
python -m fieldkit.core.preflight --label "my build" -- CMD ...    any other heavy command under the guard
fieldkit hog preflight                                           the checks alone, now
```
A new check (e.g. a test budget): `preflight.register("name", fn)` or a `"module:function"` in `preflight.STEPS`;
`fn(ctx) -> ("ok"|"warn"|"stop", "one line")`, ctx = {label, kind}. A check that raises is a warning, never a stop.

### "The laptop shuts down / restarts by itself": `fieldkit power investigate`

Follow these steps in order; do not skip one.

1. Run `fieldkit power investigate` (add `--since YYYY-MM-DD` to look further back than 7 days). It reads the cuts, the
   thermal journal, the battery, the BIOS and model, the vendor power daemons, what was installed, and fan control.
2. Read the table top to bottom. Each row is one suspect: a verdict (`suspect`, `unproven`, `ruled out`,
   `not-this-platform`, `no data`) and, under it, the evidence line the tool printed. The first row is the best suspect.
3. Quote the evidence lines in your report. Say which rows were `not-this-platform` or `no data`: those were NOT checked.
4. Give the owner only the "owner steps" the tool printed, one at a time (Lenovo F10 hardware diagnostics, another
   charger and cable, a day with the fan control stopped and its undo line). Never stop the fan control yourself
   when the owner did not order it; stopping a service needs admin (skill fieldkit-admin-unattended).
5. After the owner's test day, run it again with `--since` the day the test started and compare the cut count. A cut
   while fan control was stopped clears the EC writer; a cut on another charger clears the charger.

The rules, so you can explain a verdict: a cut below 50 C or below 40 % load is not heat, not load; battery health
(full charge / design) below 70 % is a suspect; 10 or more power-plan writes a day by ipf_uf.exe, WUDFHost.exe or
powercfg.exe is a suspect; a driver or firmware installed since the first cut minus 7 days is a suspect; TPFanControl
running at a cut is a suspect (it writes the embedded controller); hardware (charger, cable, board) is what is left.
Exit 3 means a cut is still unexplained.
