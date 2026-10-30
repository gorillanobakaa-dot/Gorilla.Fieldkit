# v0.7.3, in plain language

**Two strikes, then stop.** If a change fails the automatic checks twice, the tools now put it aside, write a note for you, and refuse to push or start more work on it. Nobody can retry it a third time by accident. You can lift the block with one command when a stronger model has repaired it.

**Every job names its plan item.** A job brief with no plan item, or a very long one, is refused before it starts. This keeps work tied to the approved plan.

**A stalled goal stops.** A long goal that stops making progress is ended and reported instead of burning time. Each goal is now judged by its own command first.

**Proof on the second computer.** The orchestrate-session job was run live on the Linux laptop on 27 October, and the proof is recorded.

**One map.** A new page, `docs/WHERE-THINGS-LIVE.md`, says where plans, ideas, hand-overs and briefs live, so nothing has to be searched for.

**Not in this release.** Some checks were not run on a long real job, and two tests in the full local run on the Windows laptop are still unidentified. The automatic checks on both systems were green for every merged change.
