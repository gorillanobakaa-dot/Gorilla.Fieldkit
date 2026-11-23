## What this is

Fieldkit v0.10.11 adds the Gorilla Trash right-click menu on Windows 11 (recycle a folder, empty the Recycle Bin), makes CI cheaper and faster (it runs only the tests your change touches, and reuses a green result), stops Fieldkit from flashing console windows, and fixes a pre-commit check that crashed and let the commit through.

## Should you download it?

Yes, if you use Fieldkit on Windows 11 or Linux. The trash menu is Windows only. Everything else is on both systems.

## Why this matters

The Recycle Bin menu was verified by the owner on a real machine: recycling a Screenshots folder moved its 5 items to the Recycle Bin, and "Empty with Gorilla Trash" emptied 112 items (429 MiB) and the check afterwards printed "bin items: 0". The pre-commit health check no longer fails open: if it crashes, the commit is blocked.

v0.10.11 is v0.10.10 plus one fix: the v0.10.10 tag's release run failed a card check (the Gorilla Trash menu card lacked its inputs and its METHODS row), so that tag was never published. This release carries the same changes.
