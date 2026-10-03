# Verified measurements (dual-track --context)

Numbers absent from this file must be written as "not measured", never estimated.

## Fieldkit itself (measured 2026-10-02 on the author's Windows 11 laptop, Intel i7-1255U)
- Python source: 17,298 lines in fieldkit/ across 10 documented groups: agent-door 1,258; core 1,017; tool-collection 974; office 598; builds-releases-thermal 1,240; exam 578; port-engine 5,594; verify-and-build 2,881; install-and-proof 1,137; leakgate 2,021.
- Test suite: 584 passed, 1 skipped, 2 xfailed in 194.89 s (python -m pytest tests).
- Exam: the same small model (Gemma) got 1 of 5 tasks right with basic tools and 5 of 5 with Fieldkit (README).

## Gorilla Firefox 157 port, 2026-10-01/02
- Firefox 157.0 built from Mozilla's release tag FIREFOX_157_0_RELEASE; 11 builds on 2026-10-02; build 11 BuildID 20261002161913.
- Hand steps exported: 20 patches (3 port fixes, 17 privacy cuts); replayed in order they reproduce the built tree exactly (identical git tree hash).
- Gorilla 155.0.1 asked 8 Mozilla hosts for things on its own (Remote Settings, its attachment and signature CDNs, the location service, push, the add-ons API, the system add-on updater, the connectivity probe), measured with the browser's own HTTP log; the earlier DNS-cache check had passed it.
- Build 11: 0 Mozilla or Firefox hosts in the browser's own HTTP log over 75 s on a real page; 0 of 16 ad and tracker domains reached on a news front page; no local IP address exposed over WebRTC; the address bar navigated in all 4 typed cases.
- leakgate source inventory: 103 network-capable source files in 25 components, each with a disposition; 1 left as an owner decision (OCSP).
- Laptop reset 2026-10-02 05:28 during a compile: the build governor had read a chassis ACPI zone stuck at 41.85 C for 386 samples; the thermal suite now accepts only sensors proven to rise under load.
- The NSIS installer once exited 0 having installed nothing; installs now unpack the hashed zip.
- Four builds in a row carried one BuildID, so a profile kept running a broken build's cached scripts; the BuildID now changes with the source tree and every install clears the startup caches.
