---
name: fieldkit-build
description: Run or resume staged build pipelines (Gorilla Firefox on Windows and on Debian, custom Debian kernel packages, every Gorilla OpenCode release file from Linux or Windows - .deb/.rpm with nfpm, the tarball, the Mac 10.13 zip on go1.24, SHA256SUMS and the page's measured file table), check a published release (release verify - download, checksums, the linux-install workflow), find out why a local LM Studio model is slow (lmstudio-log), and diagnose a failed build from its log with the fieldkit command. Use when asked to build Firefox, the kernel or a Gorilla OpenCode release, when a build or packaging step failed, before typing a checksum table or downloading release files by hand, when a local model is slow or says device lost, or to find out which stage a build is at.
---

# Builds with fieldkit

A pipeline is a list of stages in `fieldkit/build/pipelines/<name>.yaml`. A stage
counts as done only when its verify checks pass: an exit code of 0 is not enough.

```
fieldkit pipeline list
fieldkit pipeline plan firefox-windows            # resolved commands; changes nothing
fieldkit pipeline run  firefox-windows --only preflight
fieldkit pipeline run  firefox-windows --from patches
fieldkit pipeline run  debian-kernel --dry-run
fieldkit pipeline plan firefox-linux             # Debian: vault source, mozconfig, patches, bootstrap, build, .deb
fieldkit pipeline status debian-kernel
```

- Re-running skips stages that are still verified. `--force` re-runs them.
- A stage built for another system stops the run with `not-this-platform`. That is
  the true answer on the wrong machine, not a fault to work around.
- Stages marked `always` still run after a failure; one of them puts the power scheme
  back after a heat-capped build.
- Every run writes `state/<pipeline>/last-report.json`, and each stage writes a full
  log to `state/<pipeline>/logs/`.

## When a build fails

```
fieldkit triage LOG --set auto          # or firefox-windows | firefox-linux | debian-kernel | debian-packaging
```

The verdict is one of:
- **known** – cause and fix are given; follow the fix.
- **success** – the tool itself reported that it finished.
- **unrecognised** – a new failure. The distinct error lines come with a signature
  stub. Once the real cause is found, add the stub to
  `fieldkit/build/signatures/<set>.yaml`, so this failure is recognised next time.
- **no-error-lines** – the log holds no error at all. Look at the stage log instead.

A failed stage in `pipeline run` is triaged automatically; the report carries the
result.

## Gorilla OpenCode releases (Linux or Windows)

```
fieldkit pipeline run gorilla-opencode --var version=v0.1.153        # every release file, one run
fieldkit release verify v0.1.153 --repo gorillanobakaa-dot/Gorilla.Opencode   # after publishing
fieldkit pipeline run gorilla-opencode --only toolchain modules build repro published --var version=v0.1.152 git_ref=71d8223 compare_with=release out=PATH   # a published binary rebuilt byte for byte
fieldkit release table dist/SHA256SUMS-v0.1.153.txt --check PAGE.md            # the page's table is the measured one
fieldkit lmstudio-log --day 2026-10-25 --last 10                     # a local model is slow: why
```

- One run makes the .exe, the Linux binary, the tarball, the .deb, the .rpm, the Mac 10.13 zip
  (`...-mac-10.13-x86_64.zip`), `SHA256SUMS-<v>.txt` (exactly the published files) and
  `RELEASE-FILES-<v>.md` (the page's File | Bytes | SHA-256 table, measured: paste it, never type it).
- On Windows (no dpkg-deb, no rpmbuild, no exec bits, the Linux binary cannot run) the packages
  stage uses the project's `scripts/build-packages-nfpm.py` and compares the .deb with the last
  release's .deb from GitHub. The tarball is packed in Python and checked by its bytes. The Arch
  package comes from the `arch-package` workflow.
- The Mac stage builds with go1.24.x, the series the 10.13 patch and the MacBook Air builds use:
  the system go if it is 1.24, otherwise the newest `~/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.24.*`.
  If neither exists, the stage stops and prints the command that fetches one.
- A binary built from an exported folder (no `.git`) carries no `vcs.*` build info, so its bytes
  differ from a release built from the git checkout, even when the source is the same
  (`go version -m` shows it). To reproduce a published binary, build with `git_ref=<its commit>`
  (the vcs.revision in `go version -m` of the published file): a clean clone of that commit, nothing
  written into the source repository. `compare_with=release` (or a folder) then compares each binary
  with the published one and names what differs (bytes, build info, CRLF counts).
- The build stage refuses a working tree with CRLF files that `.gitattributes` says are LF
  (`git ls-files --eol`: w/crlf + eol=lf). Git calls such a tree clean, but go:embed takes the
  CRLF bytes: v0.1.152 was built from the shared checkout with 574 such files, so its published
  binaries equal 71d8223 + those CRLF files, not a clean clone of 71d8223.
- Every .deb/.xz the packages stage makes goes through `fieldkit xzcheck` (dictionary = what the
  content needs, unpacking proven within `xz_cap_mib`, default 128): a failing one fails the stage.
- `release verify` downloads into a new empty folder, checks every asset against SHA256SUMS and
  GitHub's digest, checks the page's table, passes every .deb/.xz through xzcheck, runs
  `linux-install.yml`, and reports deb-ubuntu and rpm-fedora (`--no-dispatch` skips the workflow;
  `--run-id N` reads an existing run).

## Kernel helpers

```
fieldkit kernel localversion --base 7.1.2 --tags unleashed gorilla eapd   # fits 64 chars
fieldkit kernel fragment kernel_config_injector.py --out fragment.yaml    # reads, never runs it
```

## Settings

Machine paths belong in `fieldkit.local.json` (git-ignored). Pipelines refer to them
as `${LOCAL:firefox.root}` and `${LOCAL:kernel.workdir}`. `--var key=value` overrides
a pipeline variable for one run.
