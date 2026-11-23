# v0.10.4, for developers

## CI scheduling and proof

`fieldkit ship batch before-pr` assembles compatible branches before CI. The protected tests workflow shares test selection across shards, maps audited changes to targeted E2E jobs, and checks the E2E verdict. Exact-tree merge proof bypasses redundant provisioning only after trusted PR CI completes successfully. Scoped test receipts include declared inputs and runner identity. The CI watcher follows the current commit, reads paginated checks and preserves failing test IDs.

## Build and leak-gate restart

Emerge binds reused stages to verified dependency outputs. Leak-gate checkpoints retain scenario records and sensor digests. Full release runs can resume a verified prefix with matching inputs and runner state. A hard interruption also requires proof that the previous workers are gone and cleanup can be confirmed. Fresh setup and the final policy verdict still run.

## Handoff and stalled processes

`fieldkit rotate handover` can bind a work ID, the full plan and pending CI. `fieldkit rotate takeover` retrieves the record and allows one successor claim. CI shards report progress, use bounded deadlines and stop their own processes when a stall is detected. CI timing separates active attempts from gaps between retries.

## Managed release publication

Candidate publication goes through the machine authorized by the signed mandate. A per-repository lock covers draft lookup, uploads and promotion. Existing draft files are reused only when their hashes match. Publication checks required artifacts, installer proof where applicable, and the public Latest release.

## Release approval compatibility

Candidate promotion accepts signed release approval without a publisher name. A signed publisher or orchestrator device, when provided, still restricts promotion to that computer. Candidate verification, remote tag proof, green CI, and the per-repository lock on the publishing computer still apply.
