# projects/ - the project registry (P41a)

One folder per project, one `project.yaml` in it. `fieldkit cookbook load ID|--cwd PATH` reads it, so a fresh session
learns where a project's cookbook, recipes and tests are without a single file search. Why here: data lives in YAML
beside what it describes (like `fieldkit/emerge/recipes/`), at the repo root so the project's own cookbook pieces
(P41b+) can sit next to their `project.yaml`. Machine paths never go in: `repos[].local` uses `${DOCUMENTS}` and
`${LOCAL:...}`; the real paths stay in the git-ignored `fieldkit.local.json`.

Fields: id, name, purpose, repos[{name, github, local}], recipes[], cookbook[{repo, path, what}], entry[{cmd, what}],
tests[{cmd, paths[]}], platforms[], decisions{repo, path}, plan_words[], cwd_markers[] (folder names, fnmatch).
Check: `fieldkit cookbook check` (runs in CI).

## Installer releases

An optional `release` mapping names the GitHub repository, `distribution: installers`, and required `targets`.
Each target declares `platform`, `arch`, a full-match `installer` regular expression with `{version}`, and the
repository's trusted Actions `workflow` path. `minimum_os` identifies the OS series the install run must cover.
An archive containing an installer can declare its expected `installer_member`. Repositories can also declare
this policy in `.github/fieldkit-release.yaml` on the tagged default-branch history. `distribution: source`
with an empty target map explicitly selects a source-only release.

Use `fieldkit ship gh-release TAG -- --candidate FILE`. Keep the YAML candidate next to the release page and
assets. Its fields are `version` (quoted, to preserve the exact version), `title`, `notes` (page filename),
`assets` (file names), and `runs` (target ID to Actions run ID). The tag must be `v` followed by the version.
The page's file table must match the candidate files and their measured hashes.

For each target, the trusted workflow runs the installer, checks the installed version, and uploads
`proof.json` in an Actions artifact named `install-proof-TARGET`. The proof fields are `target`, `installer`,
`version`, `commit`, `installed` (boolean), `platform`, `arch`, and `assets` (file name to SHA-256 digest), plus
`os_version` and `installer_member` when the policy requires them. The installer must appear in that target's
hashes. The proofs together must cover every asset. A candidate's run IDs refer to successful runs of the trusted
workflow for the tagged commit, from the release repository itself.

On interrupted upload, rerun with the same candidate. The engine reuses matching draft assets, refuses
unexpected or incompatible assets, and promotes only a complete verified draft. It then checks GitHub Latest.
