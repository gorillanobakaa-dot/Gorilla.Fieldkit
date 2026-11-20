# v0.10.8, for developers

## Scope
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

## Verification
- fieldkit repair ps-compat-check --repo . -> PASS
- fieldkit parity os --check -> PASS
- Full CI: 40 shards (20 Windows + 20 Linux), completed in ~10 minutes.