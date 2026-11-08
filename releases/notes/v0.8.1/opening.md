## What this is

Fieldkit v0.8.1: a refused push now says why and how to retry, the wait for CI looks at the right checks for each repository, and the Gorilla OpenCode build now starts from a clean copy of the source and fetches the helper programs it needs. Gorilla OpenCode v0.1.158 then built green in 7.2 minutes with no model call.

## Should you download it?

Yes, if you already run Fieldkit or build Gorilla OpenCode releases: it removes three reasons a job stopped and waited for you. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

A job that stops without saying why costs more than one that fails with a clear reason. Each of the three changes turns a silent stop or a wrong answer into a clear result.
