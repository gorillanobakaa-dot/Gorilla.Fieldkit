# v0.10.6, for developers

## Scope

`v0.10.4..main`:

- **P47 — Deterministic read-only installer preflight (`fieldkit/desk/install_preflight.py`, `fieldkit install preflight`)**
  - Stdlib-only preflight wired into `install.ps1` and `install.sh` at step 3 (before `.venv`, `pip`, or `compileonce`) and into `fieldkit size integrate` (`fieldkit/desk/size_install.py`).
  - Strictly ordered checks: existing installer prerequisites -> fresh user-terminal lookup and bounded validation outside the install root (`gorilla-size --version`, `gorilla-trash --help`) -> background service consumer lookup (`fieldkit-link.service` via `systemctl --user show`, live `/proc/<MainPID>/environ`, `systemctl --user show-environment`, user/system unit files, and systemd default `PATH`).
  - Stops with exit `3` and zero mutations when terminal validation passes and a background service cannot resolve the binary (`service_discovery_failed`); routes genuinely missing terminal tools to `fieldkit size integrate`.
  - Updated `fieldkit/peers/link_keepalive.py` to include `Environment=PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin` in the Linux systemd user service template.
  - Corrected `install-preflight` card metadata in `fieldkit/desk/tools.yaml` to use `effects: [reads-files]` with `safety: read-only` so the full card-effects suite passes.
- **P46 — Windows Take Ownership (`fieldkit/desk/ownership.py`, `fieldkit ownership`)**
  - Namespaced HKCU Explorer registration (`*`, `Directory`, `Drive`, `Drive` `.RecoverAccess`) targeting the installed Python interpreter with `-I`.
  - Handles selected files/folders and separate recursive local-drive ownership with explicit confirmation, skips reparse/symlink redirects, verifies selected root owner and unchanged DACL on ownership-only runs.
- **P41–P45 — Desktop binaries, clean-slate audit, search routing, prepared `pfind` bytecode**
  - `fieldkit size integrate` (`fieldkit/desk/size_install.py`) installs existing `gorilla-size` and `gorilla-trash` binaries for the current user on Windows and Linux.
  - `fieldkit capture audit` (`fieldkit/capture/audit.py`) provides one deterministic read-only audit across configured Windows/Linux roots with explicit Cable / SSH / Cable+SSH transport selection (`--via`).
  - Hook/tool-boundary routing enforces `pfind` and Gorilla Size over covered raw shell search commands while keeping live log following distinct.
  - `install.ps1` and `install.sh` prepare `pfind` bytecode via `fieldkit.compileonce build`; `fieldkit pfind` consumes validated artifacts and rejects missing/stale bytecode cleanly.
- **Windows mandate & release fixes**
  - Fixed Windows mandate ACL probing (#634) and removed unrequested self-hosted runner release gate from `gorilla-opencode` (#635).

## Verification

- Verified `tests/test_desk_cardcheck_effects.py` and `tests/test_methods.py` locally (fixing the only 2 failures out of 6,838 tests on the `v0.10.5` release workflow run).
- Verified `fieldkit release-page check`, `fieldkit version-sync . --expect 0.10.6`, `fieldkit parity os --check`, and `fieldkit integration check` locally, plus full Windows + Linux release workflow on `v0.10.6`.
