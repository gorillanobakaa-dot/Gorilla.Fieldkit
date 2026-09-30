"""Fieldkit - deterministic tools, harnesses and orchestration for agents.

One Python codebase for Windows 11 and Debian. The model decides *what* to do;
Fieldkit does it the same way every time and reports in JSON so any agent
(Gorilla OpenCode, Claude Code, Codex, Gemini CLI) can read the result.

Harnesses:
    fieldkit.office  read, create, check and scrub Word/Excel/PowerPoint/PDF, offline
    fieldkit.build   staged pipelines (Firefox, Debian kernel) and build-log triage
    fieldkit.desk    the everyday tools (icons, validators, encoding, watchdog)
Shared:
    fieldkit.core    host detection, settings, processes, privacy scan, pipeline engine
"""
__version__ = "0.1.0"
