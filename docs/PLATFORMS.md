# One command, both systems

Owner, 2026-11-11: every rule, tool and workflow of the harness works on Linux and on Windows by itself. A fieldkit
command never refuses a job with "runs on X only". When a command's card names one system, the command line
(`fieldkit/core/crossos.py`, rows in `fieldkit/core/crossos.yaml`) does one of three things on the other system:

- **runs the equivalent here**: the same job has its own command on this system, so that command runs and says so
  (`DISPATCHED: ...`);
- **runs on the other machine over the cable**: the job needs the other system's machine, so it goes there as an
  `orchestrate job run`. The signed result and the log tail come back here; exit 0 on pass, 3 otherwise. The
  MacBook is never the cable;
- **not-this-platform**: the job cannot exist here at all (a Windows registry, a Linux `/sys` reader, this machine's
  own hardware). It prints one line with the reason and exits 0, so a job that calls it carries on (rule 6).

`--on peer` on any command line sends that command to the other machine.

`tests/test_crossos.py` fails when a single-system command has no row, and when this table is not the generated one
(`python -m fieldkit.core.crossos --markdown`). "PORT TO DO" marks a reader that could exist on the other system
and has not been written yet.

Before: 63 of 410 commands (38 Windows, 25 Linux) stopped with `REFUSED ... exit 2`.

| command | card says | before (2026-11-11) | after | why |
|---|---|---|---|---|
| `audio` | linux | REFUSED (exit 2) | runs the equivalent here (windows: fieldkit laptop sound) | the Windows sound chain is `fieldkit laptop sound` |
| `build-harness build-run` | windows | REFUSED (exit 2) | runs the equivalent here (linux: fieldkit emerge run firefox-debian) | the Linux build of Gorilla Firefox is the firefox-debian recipe (firefox-linux pipeline); the release it builds comes from fieldkit.local.json firefox.version / firefox.src (the newest ported tree), so the task word is not passed |
| `build-harness capture` | windows | REFUSED (exit 2) | runs on the other machine over the cable | firefox.exe copies, pktmon and taskkill: the capture of the Windows build runs on the Windows laptop |
| `build-harness clip-check` | linux | REFUSED (exit 2) | runs on the other machine over the cable | the stock control is Mozilla's linux-x86_64 tarball: runs on the Linux machine |
| `build-harness decode-test` | linux | REFUSED (exit 2) | runs on the other machine over the cable | intel_gpu_top and /proc on the Linux machine |
| `build-harness installer-test` | windows | REFUSED (exit 2) | runs on the other machine over the cable | the NSIS installer is Windows-only: tested on the Windows laptop |
| `build-harness leakgate-selftest` | linux | REFUSED (exit 2) | runs on the other machine over the cable | checks the Linux leak-gate runner (root, namespaces, nftables) on the Linux machine |
| `build-harness night-pack` | windows | REFUSED (exit 2) | runs on the other machine over the cable | the scheduler half is Windows Task Scheduler on the Windows laptop |
| `build-harness pref-bisect` | linux | REFUSED (exit 2) | runs on the other machine over the cable | built on decode-test: the Linux machine |
| `build-harness profile-check` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on); PORT TO DO | reads THIS machine's Linux Firefox profiles (~/.mozilla); a Windows reader (%APPDATA%\Mozilla) is not written yet |
| `build-harness release-roundtrip` | linux | REFUSED (exit 2) | runs on the other machine over the cable | a .deb, dpkg and apt-get: the Linux machine |
| `build-harness release-shots` | linux | REFUSED (exit 2) | runs on the other machine over the cable | Linux desktop capture tools: the Linux machine |
| `firmware check` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | reads /sys, the kernel log and /lib/modules: a Linux kernel |
| `gtk-theme check` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | GTK user stylesheets and libraries exist only on a Linux desktop |
| `hdmi diag` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the HDMI Harness drives this Windows PC's display stack |
| `hdmi doctor` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the HDMI Harness helper task is Windows |
| `hdmi fix` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the HDMI Harness drives this Windows PC's display stack |
| `hdmi profile-list` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the HDMI Harness profiles are Windows display profiles |
| `hdmi status` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the HDMI Harness drives this Windows PC's display stack |
| `hdmi test-quick` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the HDMI Harness drives this Windows PC's display stack |
| `hdmi tv-info` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the HDMI Harness reads the TV through Windows |
| `hdmi tv-wifi-report` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the HDMI Harness reads the TV through Windows |
| `laptop sound` | windows | REFUSED (exit 2) | runs the equivalent here (linux: fieldkit audio) | the Linux sound chain is `fieldkit audio` |
| `laptop video` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on); PORT TO DO | the Windows display/video driver check; a Linux reader (DRM, mesa, VA-API) is not written yet |
| `leftovers` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | XDG autostart, menu entries, systemd user units and dpkg exist only on a Linux desktop |
| `macair` | linux | REFUSED (exit 2) | runs on the other machine over the cable | the Mac browser is cross-built on Linux and reaches the Mac over SSH from the VAIO |
| `net route` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on); PORT TO DO | reads the Linux routing table (ip route); a Windows reader (Get-NetRoute) is not written yet |
| `power audit` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | Security 4688 and Event Log Readers are Windows |
| `power drift` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | ibmpmsvc.exe is Lenovo's Windows power service |
| `update` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on); PORT TO DO | compares this machine's Linux installs with what was published; the Windows side is not written yet |
| `voice` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on); PORT TO DO | Linux speech (piper / speech-dispatcher); Windows SAPI is not wired yet |
| `voice clean` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on); PORT TO DO | Linux speech files |
| `voice doctor` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on); PORT TO DO | Linux speech stack check |
| `voice install` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on); PORT TO DO | Linux speech install |
| `voice say` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on); PORT TO DO | Linux speech |
| `watchdog install` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | Windows already starts the watchdog from a scheduled task; nothing to install there |
| `wifi` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | reads this machine's Linux Wi-Fi card (/sys/class/net, iw, dmidecode) |
| `wifi card` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | reads this machine's Linux Wi-Fi card |
| `wifi doctor` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | reads this machine's Linux Wi-Fi driver and firmware |
| `wifi scan` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | Linux iw scan |
| `wifi upgrade` | linux | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | reads Linux DMI and slot data (/sys/class/dmi, dmidecode) |
| `win11 fit` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 oem` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run app-debloat apply` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run app-debloat check` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run copilot apply` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run copilot check` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run onedrive apply` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run onedrive check` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run recommendations apply` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run recommendations check` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run services apply` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run services check` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run text-input-guard apply` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run text-input-guard check` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run update-deferral apply` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run update-deferral check` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run update-distribution apply` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run update-distribution check` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run visual-effects apply` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run visual-effects check` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run xbox-services apply` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
| `win11 run xbox-services check` | windows | REFUSED (exit 2) | not-this-platform (exit 0, the job carries on) | the Windows 11 framework changes Windows itself (registry, services, apps) |
