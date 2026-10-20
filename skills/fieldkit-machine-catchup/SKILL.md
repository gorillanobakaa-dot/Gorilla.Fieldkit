---
name: fieldkit-machine-catchup
description: Bring another machine's AI session up to date with this one, and keep identifying text off GitHub, with the catchup tool. Covers listing every git repository with what is ahead, behind, uncommitted or never pushed; gathering the context GitHub does not carry (memory files, CLAUDE.md, GEMINI.md, AGENTS.md, skills, private tools, local tool registry, notes, earlier handovers) plus unpushed work as diffs, patches and git bundles; checking the bundle for secrets; zipping it to a USB stick with a verified checksum; and the public-check that finds Romanian text and the owner's private names and places before a push. Use when the owner says another laptop, the Linux machine, Debian, the other Claude, Codex or Gemini is behind, "catch it up", "sync the machines", "handover", "spoonfeed", "put it on the flash drive", "what is not on GitHub", or before publishing anything when the owner asks that nothing personal, Romanian or identifying reaches GitHub.
---

# Machine catch-up handover with catchup

Tool: `catchup` (card in `fieldkit/desk/tools.yaml`; code `kits/catchup/catchup.py`). Run it with `python -I`.
First used 2026-10-12: a month of work on the Windows laptop carried to the Debian machine on a 64 GB stick.

GitHub carries pushed code. It does **not** carry what makes a session useful: memory, instruction files,
private tools, the local registry, internal notes, earlier handovers, and work never pushed. That is what the
handover carries. **The handover is internal documentation: hand-carried only, never committed or uploaded.**

## 0. Machine check

```
python -I catchup.py doctor --dest E:\Handover        # git, Python >= 3.9, free space on the stick
fieldkit tools check --id catchup --run-tests
```

## 1. What is where

```
python -I catchup.py repos --config local/catchup.json --since 2026-09-12 --fetch
```
- `--fetch` asks every remote first (network). Without it, ahead/behind is as of the last fetch.
- Remotes are printed with any token stripped.
- For each repository with work not on GitHub, decide with the owner: push, carry in the bundle, or leave.
  Never push without the owner's yes. Build outputs (binaries) and private notes are never pushed.

## 2. Collect

```
python -I catchup.py collect --out Documents\Linux-Catchup-Handover-YYMMDD --config local/catchup.json
```
- The config (private, `local/catchup.json`) names the sets to copy: memory folders, instruction files,
  skills, private tool folders, the local registry, notes, earlier handovers. `repo_exclude` keeps build trees,
  vaults and upstream source checkouts out.
- Per repository, `unpushed-work/` holds the uncommitted diff, unpushed commits as patches, small untracked
  files, and a whole-repository **git bundle** when it has no remote (`git clone NAME.bundle NAME` on the other side).
- The output folder must be new; nothing is overwritten.
- Then write `HANDOFF.md` yourself from `HANDOFF-SKELETON.md`: per project what it is in plain words, what
  changed, where it stands, open items, traps, and the first commands to run. Spoon-feed a receiving model that
  is a version behind: exact commands, expected output, what to do if it differs.

## 3. Check, then pack

```
python -I catchup.py check DIR          # exit 3 = a token, key or credential URL: remove it, check again
python -I catchup.py pack DIR E:\Handover
```
- `pack` refuses while `check` finds secrets (fail closed). Router passwords, API keys and tokens never travel.
  A planted fake in an evaluation sandbox (a value spelling FAKE, EXAMPLE, DUMMY, PLACEHOLDER) is not a secret.
- It writes `.zip.part`, tests every member, renames, re-reads the SHA-256, writes `.sha256` (LF: Debian's
  `sha256sum -c` fails on CRLF) and a `README-FIRST.txt` beside the zip. A file of the same name gets `_2`;
  `--replace` replaces only this bundle's previous zip, `.sha256` and README.

## 3a. Refreshing a bundle the same night (work went on after the first pack)

```
python -I catchup.py collect --out DIR --config local/catchup.json --fetch     # DIR removed first: it is yours
python -I catchup.py patch REPO --out DIR/unpushed-work/NAME --message MSG.txt  # staged work, proved to reapply
copy HANDOFF.md DIR\                                                            # your narrative, updated
python -I catchup.py manifest DIR && python -I catchup.py check DIR
python -I catchup.py pack DIR E:\Handover --replace
```
Then do what the receiving side will do: `python3 catchup.py verify E:\Handover\NAME.zip` (the zip against its
`.sha256`, every file against `MANIFEST.json`; exit 3 names what is missing, different or unlisted). The receiver
runs the same command, never an inline script. Update HANDOFF.md for every decision made since the last pack
(pushes done, owner choices).

## 3b. Questions to the other machine: the ledger

```
python -I catchup.py ask "Did leakgate-selftest pass on the VAIO?" --to linux   # -> Q-YYYYMMDD-N
python -I catchup.py questions [--all]                                         # open ones, oldest first, with age
python -I catchup.py answer Q-20261019-1 "READY, report attached"              # when the report comes back
```
Every open question goes into every bundle as QUESTIONS-OPEN.md until it is answered: a question is never
repeated by hand ("may still be unanswered") or lost between handovers (2026-10-19). The ledger is private:
Fieldkit's local/catchup-questions.json.

## 4. Before anything goes public: public-check

```
python -I catchup.py public-check REPO [REPO...] [--history] [--github gorillanobakaa-dot]
fieldkit privacy scan REPO --git                     # tokens, user paths, e-mail addresses
```
- Finds **Romanian text** (Romanian-only letters, or two common Romanian words on one line) and the owner's
  **private terms** (real names, places, background) from `fieldkit.local.json` `privacy.terms`.
- Binary files are skipped. A line marked `privacy-scan: allow` is skipped (use only for deliberate fixtures).
- Judge each hit: third-party data matches by chance (uBlock locale and filter lists, the public suffix list,
  paper author lists, a CamelCase test name whose letters spell a surname across two words). Fix real hits in
  the tree; report anything already in pushed history to the owner (rewriting history is the owner's call).
- `--history` reads every commit on every ref (added lines and messages); a hit on a local-only branch is
  not public until that branch is pushed. `--github ACCOUNT` reads what lives outside git: repository
  descriptions, release pages and issues (needs `gh` signed in; network).
- Keep private notes out with `.git/info/exclude` (local, never pushed), not by remembering.
