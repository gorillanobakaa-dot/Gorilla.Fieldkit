---
name: fieldkit-build
description: Run or resume staged build pipelines (Gorilla Firefox on Windows, custom Debian kernel packages) and diagnose a failed build from its log with the fieldkit command. Use when asked to build Firefox or the kernel, when a build or packaging step failed, or to find out which stage a build is at.
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
fieldkit triage LOG --set auto          # or firefox-windows | debian-kernel | debian-packaging
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

## Kernel helpers

```
fieldkit kernel localversion --base 7.1.2 --tags unleashed gorilla eapd   # fits 64 chars
fieldkit kernel fragment kernel_config_injector.py --out fragment.yaml    # reads, never runs it
```

## Settings

Machine paths belong in `fieldkit.local.json` (git-ignored). Pipelines refer to them
as `${LOCAL:firefox.root}` and `${LOCAL:kernel.workdir}`. `--var key=value` overrides
a pipeline variable for one run.
