# projects/ - the project registry (P41a)

One folder per project, one `project.yaml` in it. `fieldkit cookbook load ID|--cwd PATH` reads it, so a fresh session
learns where a project's cookbook, recipes and tests are without a single file search. Why here: data lives in YAML
beside what it describes (like `fieldkit/emerge/recipes/`), at the repo root so the project's own cookbook pieces
(P41b+) can sit next to their `project.yaml`. Machine paths never go in: `repos[].local` uses `${DOCUMENTS}` and
`${LOCAL:...}`; the real paths stay in the git-ignored `fieldkit.local.json`.

Fields: id, name, purpose, repos[{name, github, local}], recipes[], cookbook[{repo, path, what}], entry[{cmd, what}],
tests[{cmd, paths[]}], platforms[], decisions{repo, path}, plan_words[], cwd_markers[] (folder names, fnmatch).
Check: `fieldkit cookbook check` (runs in CI).
