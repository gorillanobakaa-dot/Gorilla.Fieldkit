# The rules every AI reads, kept lean by a tool

## In plain words

Every AI helper (Claude, Codex, Gemini) reads the same short list of rules before each job. Rules pile up: the same
rule said twice, a rule that makes the AI stop and wait for you, a rule only one helper needs but all of them read.
Each extra rule costs money on every call and can slow every job.

`fieldkit rules lint` reads the rules and points at the bad ones. It looks for:

- **bottlenecks**: a rule that makes a job stop and wait for you, makes a step manual when a tool could do it, makes
  work go one at a time without a real reason (a dated incident), stops a background job from finishing its own
  work, or counts failures that were not the job's fault;
- **duplicates**: two rules about the same thing;
- **too big**: a rule over 1000 bytes, or a helper's rules file over 8192 bytes;
- **in the wrong place**: text only the coordinating session (Zero) needs, put where everyone reads it;
- **contradictions**: one rule says "do not ask", another asks you;
- **no enforcer** (a warning): a rule no tool or test makes true; it should be enforced or deleted.

It prints one line per problem, with the rule's name and the words that caused it. `0 bottleneck(s)` means no rule
makes a job wait on you or on a manual step.

What it does not do: it does not change any rule by itself, and it reads words, not intentions; a rule can still be
unwise in a way no pattern sees.

## For developers

- Data: `rules/rules.yaml` (id, subject, audience, enforced_by, owner, uses, owner_decision, serial_reason, kind,
  text), the generated lifecycle and rotate blocks (`generator: module:function`) and each agent's tail.
- `fieldkit rules build` writes `rules/CORE.md` from the data; `lint` fails on drift.
- `fieldkit rules lint [--json]`: drift, budget (per rule and the assembled file per agent), duplicate (subject, or a
  `fieldkit X Y` command named by two rules unless listed in `uses:`), stale (`uses:` or `owner_decision` with
  nothing behind it), misplaced (Zero-only commands in an `all` rule), contradiction, BOTTLENECK (1)-(5), unenforced
  (warning). Exit 3 on any error-level finding.
- `fieldkit rules install`: core, tail, lifecycle and (Codex, Gemini) rotate blocks into each global file; an
  unmarked `# Agent` tail right after the core is marked once. One backup per changed file in `state/rules-backup/`.
- `fieldkit rules check`: the files (size, every block current, the shared blocks SHA-256 identical) plus lint.
- Tests: `tests/test_rules_lint.py` (each check fires on a bad rule; the old B1-B3 texts are found),
  `tests/test_rules.py`.
- Code: `fieldkit/rules_lint.py`, `fieldkit/rules.py`.
