# One cookbook per project (P41; owner vision 2026-11-16, reviewed by Zero)

## 0. The owner's vision in one paragraph (kept)

Every project, past, present and future, has ONE authoritative cookbook. It holds everything learned:
- procedures, scripts, builds, patches;
- failures and why they failed, fixes, pitfalls;
- for every OS the project runs on.

A fresh agent with no history can set up, build, patch, migrate, troubleshoot, release and maintain the project from
it alone. The cookbook is loaded deterministically (a registry, no file search). It learns continuously, but only
through validated changes, and CI tests only what a change touches. The full owner text is kept verbatim in
section 9.

## 1. What Zero changes in it, and why

| # | Owner text | Improvement | Why |
|---|---|---|---|
| 1 | "Capture ALL knowledge, nothing lost" | **Two layers.** (a) The ARCHIVE keeps everything (raw sessions, logs, failed attempts). It is searchable and never loaded whole. (b) The COOKBOOK is the curated, canonical, verified part that an agent loads. | If everything is loaded, every call re-sends thousands of pages: tokens explode and the agent drowns. "Nothing lost" holds for the archive; "only what is true now" holds for the cookbook. |
| 2 | "Document procedures step by step" | **A procedure that can run IS a script or an emerge stage.** Prose only for what cannot be automated (an owner decision, a UAC click). | Fieldkit's core rule: a model is called only when a step fails, and its fix becomes a script. Prose that a model must re-read and re-execute costs tokens every run. A stage costs zero. |
| 3 | "Lessons learned" | **No lesson without a rule.** Every lesson becomes one of: a triage signature (a known failure, with its fix command), a preflight check, a test, or a guard. If it can become none of these, it stays in prose with a reason. | Today docs/LESSONS.yaml has 88 lines, and the Firefox night had 109 lessons and 0 rules. Lessons in prose were read and forgotten. A rule cannot be forgotten. |
| 4 | "Never assume an old instruction is still valid" | **Each entry carries status and proof:** verified / experimental / deprecated / open, plus "last proven on run X, version Y, OS Z". An entry not proven for N versions is shown as STALE and never used silently. | This turns "don't trust old instructions" into something a machine checks. |
| 5 | "The ten-step execution chain" | **The ten steps are the fields of one record:** prerequisites, inputs, steps, expected output, verification, failure modes, recovery, next. The same shape for every project. emerge stages already have most of them. | One shape means one tool reads every project's cookbook. |
| 6 | "Platform-specific differences" | **One procedure, with per-OS variants as fields.** It is never three copies. | This kills "Windows, Linux and Mac each reinvent the same step". |
| 7 | "Continuous capture" | **One write door:** `fieldkit cookbook add/update/deprecate`. It runs the schema check, the duplicate check, the evidence link and the privacy scan; a change becomes authoritative only as a PR merged on green CI. Capture/distil only PROPOSES entries. | Without one door, five tools write five places again. |
| 8 | "Deterministic loading" | **The project registry:** `projects/<id>/project.yaml` maps the id to its repos (Windows and Linux paths), recipes, cookbook index, test map and owner decisions. Session start: cwd → project → `fieldkit cookbook load ID` gives a bounded brief (a token budget) to the agent. | "Selecting Firefox" becomes a registered operation, as the owner wrote. |
| 9 | (new) | **Measured.** Per job: the same failure diagnosed twice (target 0), model calls when nothing new fails (target 0), tokens to start working (target: one brief). | We know the cookbook works when these numbers drop, not when the file looks complete. |

## 2. How it fits Fieldkit: most pieces exist; they are scattered

| Cookbook part | Already in Fieldkit | Gap |
|---|---|---|
| Registry | `fieldkit emerge list` (15 recipes), `sync.repos`/`goapps` in fieldkit.local.json | No single project record. No cwd → project lookup. |
| Execution chain | emerge stages, preflight, triage signatures, `firefox next` | Only builds; no setup, migration or maintenance chains. |
| Problems → fixes | triage signatures, buildh lessonrules, capture ledger | Three stores. |
| Lessons | docs/LESSONS.yaml, FIREFOX.WORK/HARNESS-TEACHING-LOGS, docs/FIREFOX-COOKBOOK.md, docs/findings, state/briefs (99) | Five places, mostly prose. |
| Scripts catalogue | tools.yaml (567 tools), cards, `tools find`, sweep/capture adopt | Tools are not linked to the project or procedure that uses them. |
| Smallest test scope | `testcost staged` | Works for Fieldkit only, not per project. |
| Status / proof | emerge journal, honesty check, integration check | Not attached to knowledge entries. |

## 3. Duplicate tool families (measured 2026-11-16)

Fieldkit has 567 tools in 97 packages.

| Family | Where it lives now | Proposal |
|---|---|---|
| Firefox | buildh (28,121 lines, ~80 modules), firefox (P38 cookbook), migrate (3,615), leakgate (5,136), macair, mac, build/pipelines | One `projects/firefox` plugin: buildh + firefox + migrate + macair become its sub-commands. leakgate becomes a shared engine (every project's egress check). |
| Release page / release files | buildh releasepage/releasepublish/releasecheck/releasecover/releaseshots, build/relpage, relfiles, relbuiltin, release_roundtrip, release.py, goapp | ONE release engine (page, files table, sums, publish, verify), used by Firefox, OpenCode, the Hub and every recipe. |
| Job orchestration | orchestrate.py, orchestrate_load, orchestrate_session, orchestrate_split, orchestrate_work, suite_offload, testsplit, admission, lifecycle tasks, autopilot | One `jobs` engine: place, admit, split, offload, watch. |
| Knowledge | capture, teach, harvest.py, gather.py, briefs, ideas, review, plan.py, explain, buildh lessonrules, LESSONS.yaml | This IS the cookbook engine: `fieldkit cookbook` with archive, propose, add, load, stale. |
| Agent net | peers (7,054), net (1,195), zero (3,434), agent.py, antigravity, modelcatalog, lifecycle/models | One `agents` family: net, links, choice of agent/provider/model (P39). |
| Mac | mac, macair | Merge. |

Rule for all of them: project-specific code lives in the project plugin; anything two projects need moves to a shared
engine. A merge never drops a test. Each merge is its own PR; every old command keeps working as an alias for one
release, then goes.

## 4. Phases (each a plan row, each measurable)

1. **P41a Registry + load:** project.yaml for Firefox, OpenCode, the kernel and Fieldkit; `fieldkit cookbook load ID`;
   the session-start hook loads it by cwd. Done = a fresh session in FIREFOX.WORK starts with the brief and runs no
   file search.
2. **P41b Schema + one write door:** the entry record (section 1, rows 4 and 5), `cookbook add|update|deprecate|stale`,
   a CI check. Done = capture/distil can only propose.
3. **P41c Audit and consolidate Firefox first** (owner section 8): inventory every Firefox knowledge place, dedupe,
   turn lessons into rules, mark status. Done = 0 lessons without a rule or a reason, and FIREFOX.WORK's loose docs
   are archived.
4. **P41d The same for OpenCode, the Hub and the kernel**, then every recipe.
5. **P41e Tool merges** (section 3), one family per PR, smallest risk first: Mac, then release engine, then
   orchestration, then Firefox plugin.
6. **P41f Test scope per project:** `testcost` reads the registry's test map.

Not in scope: deleting the archive (it stays; recycle only), re-recording owner decisions (owner only), changing
guards to make merges pass.

## 5. Threat model

The cookbook holds paths, versions and decisions. It is public only after `privacy scan`. Owner decisions and machine
paths stay in git-ignored files (fieldkit.local.json, local/). No outside service is involved.

## 9. The owner's text, verbatim (the source of truth for intent)


> MANDATORY REQUIREMENT: A COMPREHENSIVE, UNIFIED COOKBOOK FOR EVERY PROJECT
>
> As I've stated before, and apparently need to reiterate, **every project, from now on, MUST have its own comprehensive, continuously maintained cookbook.** This is not optional.
>
> By "cookbook," I mean a complete, unified, structured collection of everything we have learned, discovered, developed, tested, implemented, and experienced throughout the entire lifecycle of a project.
>
> It must contain all the knowledge necessary to teach an LLM, an automated harness, or another development agent how to independently understand, build, compile, maintain, modify, troubleshoot, and evolve that project without repeatedly rediscovering solutions or making the same mistakes.
>
> **1. Capture and preserve ALL accumulated knowledge.** The cookbook must consolidate the entire history of working with a project, including, but not limited to: all procedures, instructions, and step-by-step processes; all workflows, development pipelines, and operational sequences; all scripts, utilities, commands, and automation tools; all build configurations, dependencies, environment requirements, and toolchains; all trials, experiments, errors, failures, and unexpected behaviours; all problems encountered and their corresponding solutions; all troubleshooting techniques and diagnostic procedures; all patches, modifications, workarounds, and fixes developed along the way; all successful and unsuccessful approaches, including explanations of why they worked or failed; all procedures invented, created, tested, and employed during development; all lessons learned and accumulated technical experience; all known limitations, compatibility issues, dependencies, and potential pitfalls. **Nothing of technical value should be lost, forgotten, or left undocumented.** Every time we discover something new, solve a problem, develop a workaround, or establish a better procedure, that knowledge must be incorporated into the cookbook.
>
> **2. Create ONE unified, coherent knowledge base per project.** The most important requirement is that all this information must be consolidated into a single, coherent, logically structured knowledge system. At present, this is one of the biggest problems across our projects. From what I've observed, whether we're talking about Firefox (Windows, Linux, and macOS), Gorilla Open Code, the kernel project (which we haven't even started yet), or any of our other projects, our accumulated knowledge is scattered everywhere. We probably have thousands of scripts, procedures, instructions, workflows, troubleshooting notes, and solutions distributed across countless directories, files, documents, repositories, and separate knowledge bases. The information exists, but it is fragmented. Different procedures don't know about one another. Scripts are disconnected from the workflows that use them. Troubleshooting solutions are separated from the problems they solve. Instructions are duplicated, outdated, incomplete, or inconsistent. In other words, **the recipe is spread across millions of pages in millions of separate books, rather than being consolidated into one coherent cookbook.** That's obviously a hyperbole and a metaphor, but it accurately illustrates the current situation. We must eliminate this fragmentation. All relevant knowledge must be collected, analysed, cross-referenced, reconciled, and organised into a unified structure that provides a complete picture of how the project works and how to work with it. This does not necessarily mean putting everything into one enormous file. It means creating one unified, interconnected knowledge system with a clear structure, a central entry point, and properly maintained references between its components.
>
> **3. The cookbook must teach the harness how to execute the entire project lifecycle.** The cookbook is not supposed to be passive documentation that somebody occasionally reads. Its primary purpose is to serve as an operational knowledge base for the development harness and the LLM agents working on the project. It must teach the harness, step by step, how to perform every relevant operation, from the very beginning to the final stages of development. This includes: (A) initial setup and preparation: project architecture and directory structure, repository setup and initialisation, required dependencies and prerequisites, development environment configuration, toolchain installation and configuration, platform-specific requirements, environment validation and verification; (B) development and implementation: source-code organisation and conventions, development workflows, implementation procedures, code generation and modification, script execution, configuration management, testing procedures; (C) building and compiling: complete build instructions, compilation procedures, build configurations and profiles, platform-specific build procedures, debug and release builds, incremental and clean builds, dependency resolution, build verification and validation, reproducible build procedures; (D) patches, changes, and integration: creating, applying, and validating patches, managing modifications, rebasing and merging, branch management, conflict resolution, integrating upstream changes, synchronising repositories, migration procedures, rollback and recovery procedures; (E) testing and troubleshooting: unit, integration, and regression testing, build failure diagnosis, runtime debugging, error analysis, log collection and interpretation, known problems and their solutions, recovery procedures, regression prevention, verification after applying fixes; (F) maintenance and evolution: updating dependencies, upstream synchronisation, version migrations, refactoring procedures, performance optimisation, security and compatibility considerations, long-term maintenance, releasing and packaging. The cookbook must cover the complete lifecycle, not just compilation.
>
> **4. Document the actual execution sequence, not just isolated instructions.** Every major operation must have a clearly defined, reproducible workflow. For example, instead of simply documenting a build command, the cookbook must explain: 1. What needs to be prepared before starting. 2. Which environment and dependencies are required. 3. Which scripts and procedures must be executed. 4. In what order they must be executed. 5. What each step accomplishes. 6. What the expected output should look like. 7. How to verify that the step succeeded. 8. What can go wrong. 9. How to diagnose and fix potential failures. 10. What to do next. In other words, the cookbook must describe the complete execution chain, from A to B, C, D, and all the way to completion. It must be possible for an LLM or a newly instantiated harness, with no previous knowledge of the project's development history, to follow the cookbook and successfully reproduce the required operations.
>
> **5. Establish a continuous knowledge-capture mechanism.** The cookbook must evolve alongside the project. It is not something we create once and subsequently forget about. Whenever the harness encounters a new problem, discovers a new solution, creates a script, modifies an existing procedure, or develops a more efficient workflow, it must update the relevant cookbook documentation. Every significant development activity should follow this principle: **Discover → Experiment → Implement → Validate → Document → Consolidate → Reuse.** Failed attempts must also be documented when they provide useful information. We must preserve not only what works but also what doesn't work, why it doesn't work, and under which circumstances a particular approach fails. Before inventing a new solution, the harness should consult the cookbook and search for existing procedures, scripts, and previously solved problems. Before introducing a new workflow, it should check whether an equivalent workflow already exists. When a better solution replaces an older one, the cookbook must identify the new canonical procedure while preserving relevant historical information.
>
> **6. Build a cross-platform, unified operational knowledge system.** Where a project supports multiple operating systems, its cookbook must consolidate the knowledge for all supported platforms. For example, Firefox must have a unified knowledge system covering Windows, Linux, and macOS (OS X). The same principle applies to every other multi-platform project. Platform-specific differences must be documented, but they must remain part of the same coherent knowledge architecture. We should not have three disconnected collections of instructions that independently reinvent the same procedures. Instead, we need shared workflows wherever possible, clearly documented platform-specific variations, and cross-referenced procedures.
>
> **7. Make the cookbook self-sufficient, searchable, and actionable.** The cookbook must have a clear hierarchy and a central entry point. It should include: a master index and table of contents; project architecture and technical overviews; a complete setup and execution guide; categorised procedures and workflows; a searchable script and utility catalogue; a troubleshooting knowledge base; a known-issues and solutions registry; dependency and environment documentation; platform-specific instructions; cross-references between related procedures; version and change history; links to the actual scripts, configurations, and supporting resources. Each documented procedure should identify its prerequisites, inputs, execution steps, expected outputs, validation criteria, failure conditions, and recovery instructions. The documentation must also distinguish between verified procedures, experimental approaches, deprecated instructions, and unresolved issues. The harness must never blindly assume that an old instruction is still valid.
>
> **8. Audit and consolidate the knowledge we ALREADY have.** Before creating yet another collection of documentation, we need to investigate what already exists. For each project: 1. Inventory the existing repositories, directories, scripts, documents, and knowledge bases. 2. Identify all existing procedures, workflows, and automation tools. 3. Discover duplicated, overlapping, incomplete, and outdated information. 4. Identify undocumented scripts and procedures. 5. Trace dependencies between scripts and workflows. 6. Extract lessons from previous build failures, patches, migrations, and troubleshooting sessions. 7. Consolidate the information into the new unified cookbook. 8. Identify missing knowledge and investigate how to fill those gaps. 9. Verify that the consolidated procedures are accurate and reproducible. 10. Establish the cookbook as the authoritative operational reference for that project. Do not simply copy files into a new directory and call the job finished. The objective is to understand, organise, connect, validate, and consolidate the knowledge.
>
> **9. The ultimate objective.** The ultimate objective is to ensure that **our accumulated experience becomes permanent, structured, reusable operational knowledge rather than remaining scattered across development sessions, individual scripts, repositories, and disconnected documents.** We should never have to rediscover how to build something when we have already figured it out. We should never have to repeat the same mistakes because a previous solution was buried somewhere and the current harness didn't know it existed. We should never have to reconstruct an entire development workflow from fragments of old conversations, scripts, and notes. Every project must have a complete operational memory, accessible to any authorised LLM or harness working on that project. A newly instantiated harness should be able to start with the cookbook, understand the project, configure its environment, execute the appropriate workflows, build and compile the code, apply patches, perform merges and migrations, troubleshoot failures, and continue development using the accumulated experience of every previous iteration. **That is what we were supposed to build in the first place.** Whether we're working on Firefox, Gorilla Open Code, the kernel, or any other project, the principle must be universal. One project. One unified knowledge architecture. One authoritative cookbook. All procedures, all workflows, all scripts, all accumulated experience, all lessons learned, and all troubleshooting knowledge connected and accessible. **We need to stop scattering our knowledge across thousands of disconnected places and start building a unified, intelligent, continuously evolving operational cookbook for every project.**
>
> The project cookbook must be loaded through a deterministic interface, not through an open-ended discovery process. For example, Fieldkit should maintain a project registry mapping project identifiers to their harness entry points, cookbook indexes, and test commands. That way, selecting Firefox does not trigger a filesystem search. It triggers a known, registered operation. Likewise, the knowledge-update mechanism should validate changes before making them authoritative, and the CI system should use dependency information to determine the smallest sufficient test scope. This gives you three important properties simultaneously: predictable access (the right knowledge is available immediately); persistent learning (new knowledge becomes part of the existing operational manual); efficient execution (only the relevant components and dependent tests need to run). That is the distinction between merely organising documentation and actually building the architecture.
