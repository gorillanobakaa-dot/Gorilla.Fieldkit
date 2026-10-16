## What this is

Fieldkit is a set of tested tools that let a small AI model, running on your own laptop, do real work safely: build
software and prove the result, keep backups, look after the laptop. This release is about one job above all:
building the Gorilla Unleashed browser (a hardened Firefox) on Linux, from Mozilla's source to an installed,
checked .deb, in a way the next model can repeat without guessing.

## Should you download it?

Yes, if you build or port Gorilla Unleashed, on Linux or Windows: the patch set can now be carried onto a new Firefox
release and proven, and the tools say what to do on the system you are on.

Yes, if you publish anything from a machine that also holds your browser: a new check refuses a push that would
upload bookmarks, history, cookies or saved passwords.

Yes, if you use Fieldkit v0.3.0: this is 81 changes later and replaces it.

No, if you only want the browser: download Gorilla Unleashed itself from its own release page.

## Why this matters

A model that has never seen a Firefox build will guess, and every guess costs time and money the people this is
built for do not have. Each fault found while building Firefox 158 on Linux is now a command that finds it again,
with a test that proves the command works, instead of a paragraph someone may not read.
