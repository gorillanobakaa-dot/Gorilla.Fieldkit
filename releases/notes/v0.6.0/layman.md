# v0.6.0, in plain language

**Computers that work together.** Two computers joined by a cable (here a Linux laptop and a Windows laptop) can now send each other notes, files and whole jobs. Every message is signed, so a computer only accepts work from the one it was paired with.

**One approval per project.** The owner signs a "mandate" once: what the project may do, on which computers, and until when. One computer is named the boss. The others are helpers: they do their part, say if they see a problem, offer a fix, and carry on. No helper approves or blocks anything on its own. The owner's key is kept where an AI on that computer cannot change it.

[![Gorilla OpenCode on a 2011 MacBook Air (macOS 10.13, 4 GB): a model from another company ran Fieldkit's doctor --probe, which found git is only Apple's stub, no Homebrew, no compiler, and chose the zip and the lean install](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.6.0/docs/screenshots/doctor-probe-on-macbook-air-2011.png)](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.6.0/docs/screenshots/doctor-probe-on-macbook-air-2011.png)

**Fieldkit without git.** A computer with no git, no Xcode and no Homebrew (like a 2011 MacBook Air) can install Fieldkit from the zip on this page. Fieldkit first looks at the computer (memory, disk, which Python, what is missing) and picks the lighter install when the computer is small. A model from another company installed it on that Mac from the README alone.

**Driving an old Mac's screen.** Fieldkit can open a program on a Mac, read what is on its screen, type only into the window that really has the keys, answer the permission dialogs of a running Gorilla OpenCode and count them, and type "continue" when the AI's server returns an error.

**Every release file from one computer.** One command on the Linux laptop builds every Gorilla OpenCode release file: Linux, Windows, macOS 10.13, .deb, .rpm, Arch, tarball and the checksums.

**Repair loops.** A save that fails its checks is repaired for known causes and tried again; a helper that goes silent gets its job moved to another one; a download that breaks on a weak connection continues where it stopped. Each loop stops after the same failure twice and says why.

**Also:** a read-only intrusion check for Windows (wintrace), the cable link checker, the Mac's heat and fan sensors, and many fixes for tests that failed only on Windows.
