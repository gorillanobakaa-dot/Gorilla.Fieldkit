# The Linux leak gate on a GitHub runner

**Why.** The leak gate builds a network namespace with a veth pair. The VAIO's kernel (7.1.2-unleashed) has
`CONFIG_VETH` unset, so `ip link add lg0 type veth peer name lg1` fails there. A GitHub `ubuntu-24.04` runner has
veth, passwordless sudo and the same tools. The gate code is unchanged; only where it runs changes.

**Threat model.** GitHub (Microsoft) sees the `.deb`, the context archive and every packet log of the run. The
Fieldkit repository is public, so the run's log is readable by anyone: the workflow prints no packet data, uploads only
the result JSON and the events (14 days), and never uploads the `.deb`. The `.deb` is an asset of a **draft** release:
never public, never a tag.

## The three steps

1. **Pack (owner's machine).** `fieldkit build-harness leakgate-ci pack TASK` writes `leakgate-context.tar.gz`
   (about 2 MB) next to the task: the allowlist, dispositions and SPEC (an absent file is recorded as absent), the real
   source files the static audit inventories, `Cargo.lock`, and the previous release's. Nothing is stubbed, so
   SOURCE_POLICY and DEPENDENCY_POLICY judge the same input as on the VAIO. It also prints the `.deb`'s sha256.
2. **Run (GitHub).** Make a DRAFT release on the Fieldkit repository (any unused tag name, no tag is created) holding the
   `.deb` and the context, then run the `leakgate-linux` workflow with `release` = that name. It installs the tools,
   pins mitmproxy 12.3.1, proves veth, records the CPU flags, checks the browser starts on that CPU, and runs
   `fieldkit build-harness leakgate-ci run` as root. The run refuses a `.deb` whose sha256 differs from the context's.
3. **Record (owner's machine).** `gh run download RUN -n leakgate-result -D DIR`, then
   `fieldkit build-harness leakgate-ci record TASK DIR`. It checks the result is for this task's `.deb` and version and
   that every policy was judged, then journals it and writes `leakgate_result.json` (`ran_on: github-ubuntu-runner`).

A FAIL is a finding to triage; the allowlist is never loosened to make it pass.

**The CPU.** The `.deb` is built `-march=native` for the VAIO (Ivy Bridge class). A newer runner CPU runs Ivy Bridge
code; the workflow prints the flags and starts the browser first, so a SIGILL reads as "the binary does not run
here", not as a gate failure.

**What is not here.** `fieldkit ship gh-release` cannot make this draft: Fieldkit's `release.yml` publishes on tags and the
door always passes `--verify-tag`, so the owner (or a mandate change they make) creates the draft. The workflow can
only be dispatched once it is on `main`.
