# v0.10.11, for developers

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

## Scope

`v0.10.9..main` (about 170 commits). The ones that matter:

- **P41 Gorilla Trash menu (PR 683).** Self-signed sparse MSIX package, trusted on the machine. Folders get "Recycle with Gorilla Trash" in the modern first menu; recycle-dir keeps desktop.ini and Thumbs.db. "Empty with Gorilla Trash" is registered in the legacy "Show more options" list for the Recycle Bin only, because MakeAppx rejects the Recycle Bin ItemType. The modern menu is kept (owner decision). The private signing key is not in the repository.
- **ps-compat-check fails closed (PR 684).** `fieldkit/repair/ps_compat_check.py` decoded subprocess output with the Windows default codepage (cp1252, byte 0x81 undefined) and crashed with UnicodeDecodeError while the pre-commit hook let the commit through. Output is now decoded as UTF-8 with errors=replace, and `main()` turns any uncaught exception into exit 1. Tests: `tests/test_ps_compat_decode.py` (a 0x81 generator output, and a crash returns 1).
- **CI cost.** Runner count follows the selected work (`fieldkit ci plan`, `.github/ci.yaml`), coverage-based test selection (covmap, C4), test-level receipts and reuse (a green main commit tag runs 0 shards), xdist per shard, a 10-minute PR shard timeout, quarantine of hung tests (release blocked while any is quarantined), slot budget across projects.
- **Console windows.** Every child process goes through `fieldkit.proc` (`fkproc`); shells and launchers get a hidden console; a static gate flags raw `subprocess` use (300 sites converted, FINDINGS A5).
- **Hooks and guards.** Fail-closed rotate rule, session modes enforced at the tool layer, hook-churn and kernel-limit checks in `hog`, mandate trust-store ACL read in process.
- **Board.** Open refusal-ledger defects show as rows (5.7).

### Verification

- CI green on the merged commits (Linux and Windows).
- Owner live test of P41 on 2026-11-23: 5 items recycled; 112 items (429 MiB) emptied; "bin items: 0".
- Not verified: other Windows builds for the shell menu; the Linux side of the trash menu does not exist.
