# v0.7.1, in plain language

**Cheaper jobs.** Cheap workers do the work; an expensive model only reviews the result. Each job states which plan item it serves, so nothing runs loose.

**Fewer pointless tests.** A change that contains no code (a document, a rule text, a version number) now runs no tests. Before, such a change started a long test run that failed on things unrelated to it.

**Two strikes.** The rule is now written for every assistant: a change that fails the automatic checks twice is set aside with a note for later repair, and work goes on. (The program that enforces this automatically is not in this release.)

**Every project has a recipe.** All 23 projects can be built by one command, and the two computers share one set of rules.

**Windows fixes.** Hook windows no longer flash on screen, and several tests no longer depend on other programs running on the machine.

**Not in this release.** Three changes are set aside for repair: briefs must name their plan item, a stalled long goal stops by itself, and the two-strikes enforcement. A full local test run on the Windows laptop showed 2 failing tests out of 4942 that were not identified; the automatic checks were green on Windows and Linux for every change.
