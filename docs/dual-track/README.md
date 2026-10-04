# Fieldkit documentation, two tracks per group

Every group of `fieldkit/` modules is documented twice by the workflow Gorilla.Documentation.IBM.Style (`fieldkit docs`): a **layman** track for someone who has never opened a terminal, and a **developer** track for someone who will audit or change the code. Neither is a summary of the other.

This page is written by `fieldkit docs index`; do not edit it by hand. The quality score is dual_track.py's own score out of 100 (structure and evidence, not prose quality). The Gorilla check is `fieldkit docs check`: PASS means every section, step, comparison, command, number and privacy rule held. Stale means a source file changed after the last render.

| Group | What it covers | Layman | Developer | Score (layman / developer) | Gorilla check | Last render | State |
|---|---|---|---|---|---|---|---|
| `agent-door` | The agent door (command line, agent interface, MCP server) | [layman](agent-door/agent-door_layman.md) | [developer](agent-door/agent-door_developer.md) | 97 / 91 | PASS | 2026-10-02 | **stale** |
| `core` | The core engine (pipelines, next, privacy, settings, snapshots) | [layman](core/core_layman.md) | [developer](core/core_developer.md) | 98 / 91 | PASS | 2026-10-02 | fresh |
| `tool-collection` | The tool desk (registry, cards, readiness, gather, harvest) | [layman](tool-collection/tool-collection_layman.md) | [developer](tool-collection/tool-collection_developer.md) | 98 / 92 | PASS | 2026-10-02 | fresh |
| `office` | Office documents (read, create, check, scrub, deliver) | [layman](office/office_layman.md) | [developer](office/office_developer.md) | 98 / 91 | PASS | 2026-10-02 | fresh |
| `builds-releases-thermal` | Builds, release checks and the thermal governor | [layman](builds-releases-thermal/builds-releases-thermal_layman.md) | [developer](builds-releases-thermal/builds-releases-thermal_developer.md) | 96 / 96 | PASS | 2026-10-02 | **stale** |
| `exam` | The exam (small models with and without the kit) | [layman](exam/exam_layman.md) | [developer](exam/exam_developer.md) | 97 / 88 | PASS | 2026-10-02 | fresh |
| `port-engine` | The Firefox port engine (build harness tasks and decisions) | [layman](port-engine/port-engine_layman.md) | [developer](port-engine/port-engine_developer.md) | 98 / 92 | PASS | 2026-10-02 | **stale** |
| `verify-and-build` | Verify the ported tree, repair it and run the build | [layman](verify-and-build/verify-and-build_layman.md) | [developer](verify-and-build/verify-and-build_developer.md) | 97 / 92 | PASS | 2026-10-02 | **stale** |
| `install-and-proof` | Install the built browser and prove it | [layman](install-and-proof/install-and-proof_layman.md) | [developer](install-and-proof/install-and-proof_developer.md) | 98 / 91 | PASS | 2026-10-02 | **stale** |
| `netbench` | Network benches B1-B5 (bytes, load time, throughput, HTTP/3 buffers, RAM, keepalive) through an emulated link, local servers only | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `decision-briefs` | Decision briefs, never a bare question (what is affected, every option, its cost, the recorded answer) | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `migration-control` | Migration control (intent ledger, stages and gates, SITREP, drift guard) for porting to a new Firefox | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `leakgate` | Leakgate, the fail-closed leak and telemetry release gate | [layman](leakgate/leakgate_layman.md) | [developer](leakgate/leakgate_developer.md) | 97 / 90 | PASS | 2026-10-02 | **stale** |
| `visual` | Visual quality, crisp icons and aligned pages of the built browser | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `documentation` | Gorilla.Documentation.IBM.Style, the documentation workflow itself | [layman](documentation/documentation_layman.md) | [developer](documentation/documentation_developer.md) | 97 / 91 | PASS | 2026-10-04 | fresh |

Coverage: every `.py` file under `fieldkit/` belongs to exactly one group.

Groups are defined in [`docs/groups.yaml`](../groups.yaml). Numbers in these documents come from [`MEASUREMENTS.md`](MEASUREMENTS.md) or from the source; anything else is written as "not measured".
