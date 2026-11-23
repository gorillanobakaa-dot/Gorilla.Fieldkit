# CI plan adapters (CI-SPEED-PLAN C8)

`fieldkit ci plan` sizes and balances CI shards. Until C8 it only understood pytest. A runner adapter
(`fieldkit/method/ciadapters.py`) lets any project use the same plan, the same checks and the same slot budget.

```
fieldkit ci plan --adapter pytest|go|firefox|kernel --collect LIST.txt [--raw-durations REPORT] \
    [--config .github/ci.yaml] [--check] [--slice I/N --os linux|windows]
```

Without `--adapter` nothing changed: `--selection FILE` is the pytest plan of C1/C2, byte for byte (a test compares it).

## What an adapter supplies

| Part | Meaning |
|---|---|
| collect | the runner's list output (text) to sorted, unique units of work |
| durations | the runner's own timing report (text) to `{unit: seconds}` |
| slice | the commands that run exactly one shard's units |

Unknown units weigh the p90 of the known ones (10 s when there is no report: the shards then balance by count).

## Same checks for every adapter

- C2: the union of the shards equals the selection (nothing dropped, nothing twice), and the heaviest shard is at
  most `max_spread` (1.25) times the lightest when N > 1 (a single indivisible unit, or a gap within one unit's weight,
  is exempt, as for pytest).
- C3: `.github/ci.yaml` `slots` (account limit, reserve, per-project budgets) and the runner budget per OS.
- `--check` exits 3 on any problem.

## Adapters

| Adapter | Unit | Collect | Slice | Durations |
|---|---|---|---|---|
| pytest (default) | test file | `pytest --collect-only -q` | `pytest FILE...` (node ids of those files) | junit XML |
| go | `pkg::TestName` | `go test -list . ./...` | `go test PKG -run '^(A\|B)$'` per package | `go test -json` (top-level tests; subtests ride in the parent) |
| firefox | test path | mach test manifests (JSON `[{"path":..}]`, `{"tests":[..]}` or one path per line) | `./mach test PATH...`, an explicit list instead of `--this-chunk` | mozlog (`test_end.time - test_start.time`) |
| kernel | `kernel-build` | none | `build` | none (the build is cut by ccache/sccache and the emerge stage cache) |

## How each project adopts its adapter

The adapter reads text, so the project's workflow collects first, then plans. The Fieldkit commit is pinned as in
Gorilla.Opencode's `ci.yml` (`FIELDKIT_COMMIT`).

### Gorilla.Opencode (Go)

Today `ci.yml` has one `build-test` job that runs `go test ./...` (no shards). To adopt:

1. Plan job: `go test -list . ./... > list.txt`, optionally `go test -json ./... > go.json` from the weekly main
   run (upload as an artifact, as Fieldkit's `ci-durations`), then
   `python -m fieldkit ci plan --adapter go --collect list.txt --raw-durations go.json --check --github-output`.
2. Test job matrix `shard: [1..N]` from the `matrix_ubuntu` output; each shard runs
   `python -m fieldkit ci plan ... --slice $I/$N --os linux | grep '^go test' | sh` (the lines are shell-quoted).
3. Add a `.github/ci.yaml` with its own `slots.projects` row (O-CI1: the rest share 8 slots).

### Firefox (gorilla-firefox)

The checkout has no `.github`; tests run through mach. To adopt: dump the manifest list
(`./mach test --list` or the manifests' JSON) to a file, feed mozlog output of the last full run as durations, and run
each shard as the printed `./mach test PATH...` instead of `--this-chunk I --total-chunks N`. The explicit list is
what makes C2 provable: the union of the chunks is the selection, which `--this-chunk` cannot show.

### Kernel

Build only. The adapter plans one unit (`kernel-build`), so the C3 slot budget is still counted and nothing is sharded.
The speed comes from ccache/sccache and the `fieldkit emerge` stage cache, not from splitting tests.

### Python projects

Use `--adapter pytest --collect` with `pytest --collect-only -q` output and a junit report. Fieldkit itself keeps its
selection-JSON path (`ci_tests.py`), which feeds the same `ciplan.plan`.

## Not covered

No project workflow is changed by this feature: each project adopts it in its own repository. The Go, mach and
mozlog formats are covered by fake outputs in `tests/test_ciadapters.py`, not by a live run of those tools.
