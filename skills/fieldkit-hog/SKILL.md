---
name: fieldkit-hog
description: Find what is eating this computer's memory, CPU, heat or disk and free it safely with fieldkit hog - RAM and commit charge with the top processes, idle local models in LM Studio or Ollama (unloaded after 15 idle minutes; they just reload), memory leaks with their growth curve, test runners, listeners and browsers our sessions left behind (stopped only when the harness registered them), a core pinned at 100%, CPU temperature, low disk. Use when the laptop is slow, the fans scream, RAM is high, "what is using my memory", a model is loaded nobody uses, before a long build or test run, or after a session ended with things still running. Works on Windows, Linux and macOS.
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

Everything else (RAM, leaks, pinned CPU, heat, disk) is reported, not acted on. Each action is one line in the ROOM
log, so every session sees it, and the prompt hook shows one line when the last watch found something.

Do not unload or kill by hand when the hog covers it: `fieldkit hog act`. Before heavy work, nothing to do: the one
preflight (`fieldkit/core/preflight.py`) runs it.
