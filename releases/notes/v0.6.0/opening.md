## What this is

Fieldkit v0.6.0: several computers in one house can now work on one project together, with one owner approval for the whole project instead of a prompt for every step.

## Should you download it?

Yes, if you run Fieldkit on more than one computer, on a computer without git (a Mac with no Xcode or Homebrew, a Windows laptop without developer tools), or if you build Gorilla OpenCode releases. Fieldkit is used from its source: `install.sh` (macOS, Linux) and `install.ps1` (Windows) download the zip from this page, put everything in one folder, and list what they installed.

## Why this matters

Before this release, two AI sessions on two computers could not hand work to each other, and every step asked the owner for approval. Now one computer is the boss: it holds the owner's signed mandate, splits the job, and the other computers do their part and report back. Twenty devices mean twenty one-time setups, never twenty prompts a night.
