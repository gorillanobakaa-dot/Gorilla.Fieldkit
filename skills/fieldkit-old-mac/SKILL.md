---
name: fieldkit-old-mac
description: Reach and support an old Mac that Apple no longer supports (a MacBook Air 2011 on macOS 10.13 High Sierra and the like) from the Linux box over SSH - check the key, find the Mac when its address changes (mDNS name, hardware address, ping sweep), log in, read what it is, fix the ssh alias, run commands and tests on it. Use when asked to connect to the MacBook Air or any Mac, when ssh to a Mac fails or its address changed, before running a test or measurement on a Mac, or when adding a new old Mac to look after.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML). Works offline; no network access unless a step says so."
---

# Old Macs over SSH

Born 2026-10-22. The owner's MacBook Air 4,1 (2011, macOS 10.13.6, 4 GB, Intel HD 3000) is the Gorilla Mac
browser's test target. The aim, in the owner's words: expose the harness "to a whole range of old forgotten computers
that have no support anymore and provide them with support".

## The order

1. `fieldkit mac doctor` - every known Mac: key and its permissions, the address today, login and macOS version,
   whether `ssh NAME` works. Exit 1 on any FAULT; each FAULT line says the fix.
2. `fieldkit mac info NAME` - model, CPU, memory, macOS, disk, uptime, read from the Mac itself.
3. `fieldkit mac run NAME -- COMMAND` - work on the Mac. The exit code is the Mac's.
4. `fieldkit mac fix-alias NAME` prints the `Host NAME` entry; `--apply` writes it (old `~/.ssh/config` kept in
   Trash). `--ip` writes today's address instead of the mDNS name, for a network where mDNS never answers.
5. New Mac: `fieldkit mac add NAME --user U --key ~/.ssh/KEY --mdns NAME.local --hw-addr aa:bb:cc:dd:ee:ff`.
   The hardware address is what finds it when mDNS is silent: on the Mac, `ifconfig en0 | grep ether`.

## Sensors: temperature, fan, battery, memory (sensors.gorilla)

```bash
fieldkit mac sensors macair           one reading, the 11-inch panel, printed here
fieldkit mac sensors macair --json     the same as JSON (what heat tests read)
fieldkit mac sensors macair --open     live in a Terminal window on the Mac's own screen
```

The reader is sensors.gorilla 2.2.0 (github gorillanobakaa-dot/sensors-gorilla, gathered into `toolbox/`): the SMC
read through IOKit with Python's ctypes, so no password, no Homebrew, no Xcode. The command copies it to the Mac's
`~/bin/` only when the file differs. On Linux the same program is `~/.local/bin/sensors.gorilla`.
Never read temperatures from Macs Fan Control's menu-bar text again: with that app off (the rule for tests, owner
2026-10-23) there is no text, and tesseract read nothing even with it on.

## Facts that cost time

- A home network's mDNS answers some minutes and not others, and DHCP moves addresses. Never conclude "the alias is
  stale" or "the Mac is off" from one failure: `doctor` tests, and `find` falls back to the hardware address, then to
  a ping sweep that wakes the neighbour table. All three proven on the Air, 2026-10-22 (sweep: 254 addresses, 9 s).
- The host key is filed under one name (`HostKeyAlias`), so it stays valid whatever address the Mac has.
- `sudo` on the Mac needs the owner's password. Never write a passwordless sudo rule on the Mac (refused before, a
  security weakening). Use a Desktop `.command` file the owner double-clicks.
- On 10.13 there is no Xcode: no `strings`. `vmmap -summary PID`, `sample`, `lsof`, `top -o power` work as the user;
  `footprint` and `powermetrics` need root; powermetrics on 10.13 gives package power only.
- Never touch on the Air: `keymap.sh` and `com.local.keyremap` (broken keys), Macs Fan Control, `cloudd`/`bird`,
  `fontd` (must stay ON). One exception, owner 2026-10-23: Macs Fan Control is OFF while a test measures the
  browser ("as on a normal MacBook Air"), and started again after. AppleScript "quit" is refused by the app
  (error -128): it takes `pkill -TERM -x "Macs Fan Control"`; `open -a "Macs Fan Control"` starts it again.
- An Air's fan never stops: 2000 rpm is Apple's own minimum (SMC F0Mn). 2000 rpm with Macs Fan Control off is
  normal, not a fan still being driven.

## Private data

Names, addresses, hardware addresses and key paths live in `local/macs.json` (git-ignored). The code holds none; a
test fails if a real hardware address appears in it.
