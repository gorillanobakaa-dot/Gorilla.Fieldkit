# v0.7.2, in plain language

**No more waiting for nothing.** A watch looks at every job that was started. If one ended without a follow-up, went quiet, or died, it says so and says the next step. A timer runs it every five minutes, and the coordinator sees the result on its next prompt. The link between the two computers also listens continuously now, so it no longer has to be restarted by hand after each message.

**A guard on spending.** Which model a job may use is now written down as data. A job that asks for an expensive model without a signed yes is refused before anything starts. The default is the cheap model.

**Ideas are parked, not added.** A suggestion made while a job runs goes into a list (`fieldkit ideas`) instead of moving the finish line. You see the list when a session starts, and only you accept or forget an idea.

**One writer in the main folder.** Only the coordinating session may change a repository's main checkout. Everyone else works in their own copy.

**Release safety.** A tag whose commit could not be published is refused, and a dry run says whether a release is ready.

**Not in this release.** Three changes are still set aside for repair by a stronger model: briefs must name their plan item (O3, L3) and the code that enforces the two-strikes rule. Two tests in the full local run on the Windows laptop failed and were not identified; the automatic checks on both systems were green for every merged change.
