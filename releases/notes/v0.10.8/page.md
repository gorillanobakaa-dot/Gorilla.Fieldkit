## What this is

Fieldkit v0.10.8 fixes CI parallelism and adds a deterministic pre-CI health check tool that prevents the entire class of failures that broke the pipeline today.

## Should you download it?

Yes, if you use Fieldkit on Windows 11 or Linux. CI now runs 40 parallel jobs (20 Windows + 20 Linux) instead of one process, and a new tool catches misconfigurations before they reach CI.

## Why this matters

Before this release, the full 6838-test suite ran in a single process on a single runner, crashing Windows after 47 minutes. The release workflow was also missing shard jobs that the test workflow had. Both are now fixed and enforced automatically.

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->

### What changed for you

- **Tests now run on 40 machines at once:** The test suite splits across 20 parallel jobs on Windows and 20 on Linux. Before this release, all 6838 tests ran in a single process on one machine, which crashed the Windows runner every time.

- **The release pipeline now matches the test pipeline:** The workflow that publishes a new version was running tests differently from the workflow that checks pull requests. Both now use the same 20-shard setup.

- **A new check runs before every commit:** Whenever you change a PowerShell script, a workflow file, or add a new tool, Fieldkit now automatically checks that nothing is misconfigured before the change is pushed. It catches stale generated files, missing tool registrations, and workflow shard mismatches on the spot.

- **Windows installer fixed:** The installer script broke on newer Windows runners because a zip extraction command was removed from PowerShell 7. It now uses a compatible method that works on both PowerShell 5.1 and 7.

### What did not change

No existing tools were removed or changed in behaviour.

---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope
v0.10.6..main:

- **CI shards: 4 -> 20 per OS (fieldkit/method/batch.yaml, .github/workflows/tests.yml, .github/workflows/release.yml)**
  - batch.shards.default raised from 4 to 20.
  - tests.yml shard matrix updated to [1..20], --shard I/4 -> I/20.
  - release.yml was entirely unsharded (ran all 6838 tests in one pytest process, OOM-crashed Windows after 47 min). Added shard-windows-latest and shard-ubuntu-latest jobs matching tests.yml.
  - Added probe job to dump runner hardware (CPU, RAM, disk) before tests run.

- **install.ps1: Expand-Archive -> ZipFile.ExtractToDirectory**
  - Expand-Archive calls a 3-arg ExtractToDirectory overload removed in .NET 6 / PowerShell 7.
  - Replaced with [IO.Compression.ZipFile]::ExtractToDirectory() which works on PS 5.1 and PS 7.

- **fieldkit/repair/ps_compat_check.py: new deterministic pre-CI health check**
  - PS 5.1/7 compat: Expand-Archive, Get-FileHash, ConvertFrom-Json -AsHashtable (auto-fix for Expand-Archive).
  - Repair tool registration: module in ACTIONS, toolcard in tools.yaml, entry in SKILL.md.
  - Stale generated files: cmdtree.json, platform_index.json, docs/PLATFORMS.md.
  - BOM in Python source files (SyntaxError: U+FEFF on Linux).
  - Workflow shard consistency: tests.yml and release.yml must match batch.yaml shards.default.
  - Registered in repair/__init__.py ACTIONS, toolcard in tools.yaml, entry in SKILL.md.
  - Pre-commit hook fires on repair/*.py, tools.yaml, SKILL.md, *.ps1, .github/workflows/*.yml, docs/PLATFORMS.md.

### Verification
- fieldkit repair ps-compat-check --repo . -> PASS
- fieldkit parity os --check -> PASS
- Full CI: 40 shards (20 Windows + 20 Linux), completed in ~10 minutes.
