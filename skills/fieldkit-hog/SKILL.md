---
name: fieldkit-hog
description: Find what is eating this computer's memory, CPU, heat or disk and free it safely with fieldkit hog - RAM and commit charge with the top processes, idle local models in LM Studio or Ollama (unloaded after 15 idle minutes; they just reload), memory leaks with their growth curve, test runners, listeners and browsers our sessions left behind (stopped only when the harness registered them), a program hoarding dozens of consoles or shells it never closed, a core pinned at 100%, CPU temperature, low disk. Use when the laptop is slow, the fans scream, RAM is high, "what is using my memory", Task Manager shows dozens of "Console Window Host" rows, a model is loaded nobody uses, before a long build or test run, or after a session ended with things still running. Works on Windows, Linux and macOS.
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
