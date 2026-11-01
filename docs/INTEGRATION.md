# What "built in" means

## In plain words

When we say a feature is **built in**, we mean it is really part of the product, not something that happens to sit
next to it. A feature can be written, work on one computer, and still be unusable by everyone else because nobody
told the AI about it, nobody can find it, or it was never tried on the other computers. Built in rules that out.

A feature is **built in** only when all six of these are true:

1. **Carried.** It ships with the product. It is pinned to an exact version and checksum (sha256), and it is never
   downloaded while you are using it.
2. **Known to the AI.** The AI is told that it exists, where it lives and how to call it. An AI that does not know a
   tool exists will never use it.
3. **Visible to you.** You can see it. In Gorilla OpenCode it shows up in `/arsenal`, `/help`, `/context` and
   `/settings`. In Fieldkit it shows up in `fieldkit tools list`, `fieldkit cards show`, the skills and `--help`.
4. **On the release page.** The release page has a table called "What is built in". A program writes that table; a
   person never types it, so it cannot go out of date.
5. **On every system.** It works on Windows, Linux and macOS, or the closest equivalent is named. If one system
   cannot have it, `/arsenal` says so on that system instead of hiding it.
6. **Tested.** There is a test, and the test fails if any of 1 to 5 is missing.

If even one is missing, the feature is not built in, and the job is not done.

One program checks this the same way every time, so nobody has to remember the list:
`fieldkit integration check`.

## For developers

### The rule

A capability (a helper program, a tool, a command, a feature) counts as built in only when all six hold. The
definition is written here once; `AGENTS.md`, `rules/CORE.md` and the Gorilla.Opencode `AGENTS.md` point to it.

| # | Condition | Evidence the checker reads |
|---|-----------|----------------------------|
| 1 | Carried | An entry pinned by version and sha256 in the repo's carried-tools registry. No run-time fetch of an unpinned file. |
| 2 | Known to the AI | A line in the AI-facing registry/prompt of the repo: name, where, how to call. |
| 3 | Visible | Gorilla OpenCode: listed by `/arsenal`, `/help`, `/context`, `/settings`. Fieldkit: a card in `fieldkit tools list` / `cards show`, a skill, a `--help` entry. |
| 4 | On the release page | The generated "What is built in" table (never hand-typed) lists it. |
| 5 | On every system | Per-system status for Windows, Linux and macOS: supported, or an equivalent named, or an explicit "not on this system" that `/arsenal` prints. |
| 6 | Tested | A test that fails when any of 1 to 5 is removed for this capability. |

### The enforcing command

```
fieldkit integration check --repo PATH [--changed BASE]
fieldkit integration self-test --kind opencode|fieldkit
```

- `check` reads the registries of the repo at `PATH` (one per surface, below) and exits 0 when every capability
  satisfies all six, 3 when it finds a gap (each gap names the capability and the missing condition), 1 on error.
- `--changed BASE` limits the check to capabilities added or touched since git ref `BASE`, so a pull request only
  pays for its own change (TESTS ONCE).
- `self-test` seeds one omission per condition into a fixture of the given kind and exits 0 only when `check`
  caught every one. A checker that cannot fail proves nothing.

### The registries each surface reads

Each repo keeps the data (YAML or Go tables); the checker compares them against each other and against the
capability list.

| Surface | Fieldkit | Gorilla OpenCode |
|---------|----------|------------------|
| 1 Carried | `tools.yaml`, recipe pins | carried-tools table (version + sha256) in the build/package scripts |
| 2 Known to the AI | skills (`fieldkit-*`), `AGENTS.md`, `rules/` | the system prompt's tool list |
| 3 Visible | `tools list`, `cards show`, skills, `--help` | `/arsenal`, `/help`, `/context`, `/settings` |
| 4 Release page | `fieldkit release-page compose` table | generated "What is built in" table |
| 5 Every system | `platform_index.json`, card `platforms` / `platforms_why` | per-OS availability, shown in `/arsenal` |
| 6 Tested | `tests/` covering the card or command | Go tests of the table and the commands |

### Adding a capability

1. Add it to every registry above in the same change.
2. Run `fieldkit integration check --repo . --changed origin/main`.
3. Commit only when it exits 0. A model with little reasoning needs nothing else: the command lists what is
   missing.

Never weaken the checker to get a pass. If it is wrong on a real tree, prove that and change the data.
