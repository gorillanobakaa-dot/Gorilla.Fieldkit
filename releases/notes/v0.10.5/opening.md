## What this is

Fieldkit v0.10.5 brings together the desktop and cleanup tools built since v0.10.4: one-step Gorilla Size and Gorilla Trash installation, a single read-only clean-slate audit across Windows and Linux, prepared fast search startup, Windows Explorer Take Ownership, and a read-only installer preflight check that separates missing commands from background-service search path gaps.

## Should you download it?

Yes, if you use Fieldkit on Windows 11 or Linux. Installers now check what is already working before making changes, prepare Python search bytecode during setup, and give background services on Linux access to user-installed commands.

## Why this matters

Before this release, a command could work in a normal terminal while a background service reported it missing, clean-slate checks took dozens of separate tool calls, and Windows Explorer had no built-in Fieldkit Take Ownership action. v0.10.5 puts those checks behind deterministic, repeatable tools on both operating systems.
