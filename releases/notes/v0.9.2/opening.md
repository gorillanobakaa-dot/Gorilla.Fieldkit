## What this is

Fieldkit v0.9.2: Zero, the session that coordinates all the others, now never rotates and can no longer be taken over by the other computer while it is alive. Every session now reports to Zero in four parts before it hands over, and an order closes only when a command proves the work is done. Mistakes are kept in one record for both computers. Tests no longer write into the real list of jobs.

## Should you download it?

Yes, if you run Fieldkit on more than one session or on two computers. Installing is unchanged (the install script, or `fieldkit update`).

## Why this matters

Long jobs were being dropped: a session filled its memory, said it had to rotate, and passed the job on unfinished. Twice a second Zero appeared on the other laptop. This release puts those rules in the code, so they hold without anyone remembering them.
