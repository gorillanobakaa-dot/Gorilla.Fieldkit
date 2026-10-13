# Fieldkit documentation, two tracks per group

Every group of `fieldkit/` modules is documented twice by the workflow Gorilla.Documentation.IBM.Style (`fieldkit docs`): a **layman** track for someone who has never opened a terminal, and a **developer** track for someone who will audit or change the code. Neither is a summary of the other.

This page is written by `fieldkit docs index`; do not edit it by hand. The quality score is dual_track.py's own score out of 100 (structure and evidence, not prose quality). The Gorilla check is `fieldkit docs check`: PASS means every section, step, comparison, command, number and privacy rule held. Stale means a source file changed after the last render.

| Group | What it covers | Layman | Developer | Score (layman / developer) | Gorilla check | Last render | State |
|---|---|---|---|---|---|---|---|
| `watchdog` | The online watchdog (rejoin the wifi by itself, or say so out loud) | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `wifi` | Why the wifi is slow, answered from the hardware (card bands, streams, channels) | [layman](wifi/wifi_layman.md) | [developer](wifi/wifi_developer.md) | 98 / 90 | PASS | 2026-10-13 | fresh |
| `net` | Which interface carries your traffic, what kind of line it is, and what antenna the cellular bands need | [layman](net/net_layman.md) | [developer](net/net_developer.md) | 98 / 90 | PASS | 2026-10-13 | fresh |
| `shot` | Reading numbers off a screenshot, and reasoning about which layer is limiting | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `voice` | A voice worth listening to, on Linux (piper, speech-dispatcher, the loudness chain) | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `agent-door` | The agent door (command line, agent interface, MCP server) | [layman](agent-door/agent-door_layman.md) | [developer](agent-door/agent-door_developer.md) | 97 / 91 | PASS | 2026-10-02 | **stale** |
| `core` | The core engine (pipelines, next, privacy, settings, snapshots) | [layman](core/core_layman.md) | [developer](core/core_developer.md) | 98 / 91 | PASS | 2026-10-02 | **stale** |
| `tool-collection` | The tool desk (registry, cards, readiness, gather, harvest) | [layman](tool-collection/tool-collection_layman.md) | [developer](tool-collection/tool-collection_developer.md) | 98 / 92 | PASS | 2026-10-02 | **stale** |
| `office` | Office documents (read, create, check, scrub, deliver) | [layman](office/office_layman.md) | [developer](office/office_developer.md) | 98 / 91 | PASS | 2026-10-02 | **stale** |
| `builds-releases-thermal` | Builds, release checks and the thermal governor | [layman](builds-releases-thermal/builds-releases-thermal_layman.md) | [developer](builds-releases-thermal/builds-releases-thermal_developer.md) | 96 / 96 | PASS | 2026-10-02 | **stale** |
| `exam` | The exam (small models with and without the kit) | [layman](exam/exam_layman.md) | [developer](exam/exam_developer.md) | 97 / 88 | PASS | 2026-10-02 | **stale** |
| `port-engine` | The Firefox port engine (build harness tasks and decisions) | [layman](port-engine/port-engine_layman.md) | [developer](port-engine/port-engine_developer.md) | 98 / 92 | PASS | 2026-10-02 | **stale** |
| `verify-and-build` | Verify the ported tree, repair it and run the build | [layman](verify-and-build/verify-and-build_layman.md) | [developer](verify-and-build/verify-and-build_developer.md) | 97 / 92 | PASS | 2026-10-02 | **stale** |
| `install-and-proof` | Install the built browser and prove it | [layman](install-and-proof/install-and-proof_layman.md) | [developer](install-and-proof/install-and-proof_developer.md) | 98 / 91 | PASS | 2026-10-02 | **stale** |
| `audio` | The Linux sound chain, read-only (each stage, what is done twice, the exact fix) | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `career` | A CV and a UK job hunt for one candidate (fixed rules, verified facts only, never submits) | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `academic` | Academic work for students in seven countries (the dates asked, references, style, language, words, plagiarism pre-check, the next step) | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `laptop` | A laptop's everyday repairs: the processor locked slow, voice alerts when an AI needs you, low sound, the volume, the NVIDIA panel, the wallpaper, identical AI rule files | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `hdmi` | The HDMI Harness for an agent: read-only checks, and connect the TV with the GPU-driver repair, elevated and waited for | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `win11` | The Windows 11 tweaks framework, driven safely: modules, fit to this PC, the maker's buttons, elevated runs | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `peers` | Messages between AI sessions on this computer, in Gorilla OpenCode's protocol: list, send, wait | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `evidence` | Research evidence that can be checked later: pages kept by hash, every claim of a run against its page | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `methods` | How the work gets done, as tools (wait, mutate, regress, drift, mcp-probe, survey, i18n, ship, handover) | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `icons` | Sharp desktop icons on any computer (IconKit: the icon cache, .ico frames and checks, crisp recoloured icons) | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `netbench` | Network benches B1-B5 (bytes, load time, throughput, HTTP/3 buffers, RAM, keepalive) through an emulated link, local servers only | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `decision-briefs` | Decision briefs, never a bare question (what is affected, every option, its cost, the recorded answer) | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `migration-control` | Migration control (intent ledger, stages and gates, SITREP, drift guard) for porting to a new Firefox | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `leakgate` | Leakgate, the fail-closed leak and telemetry release gate | [layman](leakgate/leakgate_layman.md) | [developer](leakgate/leakgate_developer.md) | 97 / 90 | PASS | 2026-10-02 | **stale** |
| `visual` | Visual quality, crisp icons and aligned pages of the built browser | not rendered | not rendered | - | FAIL (2) | - | **never rendered** |
| `documentation` | Gorilla.Documentation.IBM.Style, the documentation workflow itself | [layman](documentation/documentation_layman.md) | [developer](documentation/documentation_developer.md) | 97 / 91 | PASS | 2026-10-04 | **stale** |

Coverage: every `.py` file under `fieldkit/` belongs to exactly one group.

Groups are defined in [`docs/groups.yaml`](../groups.yaml). Numbers in these documents come from [`MEASUREMENTS.md`](MEASUREMENTS.md) or from the source; anything else is written as "not measured".
