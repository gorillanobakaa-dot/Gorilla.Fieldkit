# Fieldkit v0.2.0, for developers

## Scope

v0.1.0..v0.2.0 is 109 commits, merged as PRs #1 to #11. Every PR ran the whole suite on `windows-latest` and
`ubuntu-latest` (`.github/workflows/tests.yml`), and every commit passed the pre-commit hook (the full suite plus
`fieldkit privacy scan . --git`). This release is published by `.github/workflows/release.yml` only after the same
suite passes on both runners for the tagged commit.

## Reproducible binaries

- `fieldkit repro compare A B [--root RA RB]` (`fieldkit/build/repro.py`): files or folders byte for byte. Archives are
  opened recursively (ar/.deb, tar in gz/bz2/xz/zst, zip, so omni.ja too) and a finding names the deepest member that
  differs. Causes recognised: a build root or the home, user or host name present in one side only, a date or time near
  the first differing byte, member order, uid/gid/mtime in headers, PE COFF link time and RSDS GUID (`/Brepro`).
- Go (`go-app.yaml`, `gorilla-opencode.yaml`): a `repro` stage between `build` and `checksums` rebuilds every target
  from a `copytree` of the source in another folder with its own `GOCACHE` and `TMPDIR`, `TZ=Pacific/Kiritimati`,
  `LC_ALL=C`, the verified `GOMODCACHE`, and the build record's ldflags. Gorilla OpenCode b0ce82f: both binaries
  identical.
- Kernel (`debian-kernel.yaml`): a `repro` stage copies the tree without build products (top-level only for names
  such as `debian` and `vmlinux`, which also exist as sources lower down), runs `mrproper`, restores `.config` and
  the build stamp, and runs `bindeb-pkg` with the first build's `LOCALVERSION`. `build_env` now pins
  `SOURCE_DATE_EPOCH` to the top Makefile's mtime (so dpkg-deb clamps every built file), puts a `date` shim on PATH
  (changelog dates), and adds `-ffile-prefix-map=<tree>=.` to `HOSTCFLAGS` (objtool's DWARF). Proven on 7.2.9 with
  tinyconfig plus modules: linux-image and linux-headers identical; the RPM's vmlinuz still equals the .deb's.
- Firefox: `fieldkit build-harness repro TASK [--run|--compare]` (`fieldkit/buildh/reprobuild.py`): a git worktree of
  HEAD (HEAD is already replay-proven), a mozconfig with every spelling of the objdir rewritten, the pinned
  `MOZ_BUILD_DATE`, `mach build` and `mach package` under the thermal governor, then `repro.compare` on the zip and
  the installer. Refuses without a pin, without room, or when a target folder exists that it did not create.

## Academic (`fieldkit academic`)

`check` (gates in `academic/check.py`), `brief`, `date`, `record` (`grades` per locale; Germany's scale runs the
other way), `portfolio`, `local` (copy, SHA-256 every file, rename the cloud original), `preflight` (real documents
built and measured for each locale: page size, page breaks, empty author fields, the header, the language tag),
`menu` and `shortcut`. `build` no longer leaves `[Module Code]` in the header when no module is set; `check` found it.
Not carried over from the original: randomised "Accessed" dates (an access date is the real one; `accessed` stamps
it), and a migration of one student's old folder layout.

## Backups, laptop, thermal

- `fieldkit backup usb|remind` (`core/usbcopy.py`): `.part` then rename, read back (SHA-256, `testzip`), a `.sha256`
  beside it, an existing name never overwritten (`_2`), a full stick refused; the test asserts every pre-existing
  file on the stick is byte-identical afterwards.
- `fieldkit laptop cpu|alerts|sound|volume|video|wallpaper|rules`: BD PROCHOT guard (typeperf against the base clock,
  event 37, an optional unlock tool, `governor.set_cap(100)`, a logon task; on Linux cpufreq and `thermal_throttle`
  counters, read-only); voice alerts from Claude Code's Stop/Notification hooks and Antigravity transcripts (merged
  into `~/.claude/settings.json` with a backup); Windows Core Audio through `-EncodedCommand` PowerShell with an undo
  file written before any change; `wpctl`/`pactl` on Linux.
- `fieldkit thermal profile|monitor|autostart` (`thermal/profiles.py`) and an `hwinfo` provider in
  `thermal/sensors.py`, still subject to `prove_live`.

## Also since v0.1.0

Research evidence records and `fieldkit evidence` (#8); the owner's other repositories gathered, reviewed and carded,
`fieldkit win11` (#8); `fieldkit hdmi` with the elevated GPU-driver repair (#9); dual-track and speaker-loudness fixes
carded (#10); methods turned into tested commands (#7); Gorilla OpenCode MCP wiring and the Go pipeline (#6); academic
documents and templates (#4, #5); `fieldkit icons` (#3); CV and job-hunt tools and the privacy gate (#2); Linux
parity, kernel migration, packaging and the release gate (#1). Each PR's description, at
https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit/pulls?q=is%3Apr+is%3Amerged, lists its tests.

## Known limits

Firefox not yet reproduced (needs the build machine); the kernel proof used a small config; the `laptop` Windows paths
are tested against simulated `run`/`ps` answers only; the 109-commit range is summarised here by PR, not commit by
commit.
