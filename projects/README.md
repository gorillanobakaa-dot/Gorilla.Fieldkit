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

Put `.github/fieldkit-release.yaml` in the repository and merge it through the default branch before tagging.
Any GitHub repository can use the following policy.

```yaml
schema: 1
distribution: installers
targets:
  linux-amd64:
    platform: linux
    arch: amd64
    installer: 'pocketclock-{version}-amd64\.deb'
    workflow: .github/workflows/install.yml
```

List every supported installer target. Each installer pattern matches the full filename and includes `{version}`.
`minimum_os` is a quoted OS series that the install run must cover. For an archive containing an installer,
`installer_member` names the expected member. Use `distribution: source` and omit targets for a source-only
release. The repository policy is read from the commit named by the tag. The Firefox and Gorilla OpenCode
registry policies remain available as a migration fallback for tags without a repository policy.

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

When an install check uses other assets, put their filename patterns in that target's `install_inputs` list.
Each pattern must match exactly one asset, and `{version}` expands to the candidate version. The install run
must record those assets alongside the installer. If another target's installer changes during a retry, retain
the runs whose installer and declared inputs still match. Every final asset still needs a matching digest from
at least one trusted run. A new commit requires fresh install evidence.

After those runs finish, use the same candidate as a gate in CI. Check out the tag with its Git refs, install a
trusted pinned Fieldkit build, and make the release page, candidate file and assets available together.

```yaml
permissions:
  contents: read
  actions: read
steps:
  # Checkout, install Fieldkit and fetch the candidate bundle before this step.
  - name: Verify release candidate
    env:
      GH_TOKEN: ${{ github.token }}
      RELEASE_TAG: ${{ github.ref_name }}
    run: fieldkit ship gh-release "$RELEASE_TAG" -- --check-candidate bundle/candidate.yaml
```

Keep release write access in the protected publishing path and use `--candidate` there. Remove direct
publishing steps when enrolling an existing workflow. Fieldkit checks the commands it runs. Repository
administrators control which other workflows and credentials have GitHub release write access.

On interrupted upload, rerun with the same candidate. The engine reuses matching draft assets, refuses
unexpected or incompatible assets, and promotes only a complete verified draft. It then checks GitHub Latest.
The candidate publisher counts hotfixes using GitHub publication history. Tag pushes, drafts and failed
publication attempts leave the quota unchanged. Publishers sharing one Fieldkit state directory serialize the
final check and promotion locally. Separate machines can read the same history, and a concurrent promotion
on separate machines has no shared lock.

Fieldkit CI records passing test files with the tested tree, runner image, Git, Python, and dependency versions.
For files listed in `.github/test-proof-inputs.json`, CI also records the identities of their declared Git inputs.
The test file, workflow and runner scripts, declaration file, project configuration, and shared test setup are
always inputs. Only use a scoped declaration when it covers the test's transitive imports, fixtures, and other
files it reads. Tests without a declaration need matching whole-tree evidence. A missing path or changed input
leaves that file scheduled. The release workflow reports how many files it reused and the lookup time.
