# v0.10.14, in plain language

This is the complete explanation, not a summary of one. Nothing below is behind a link.

### What changed for you

- **The cable comes first:** when Fieldkit needs the other laptop, it uses the direct cable (`fieldkit peers link`). SSH is only tried when the cable does not answer. Installing or removing a program on the other computer also goes over the cable now. If an AI agent types `ssh` by hand to a cable computer, it gets a warning.
- **Linux passes the same checks as Windows:** the leak gate, the build tools and the temperature tools now know the Linux paths; stopping a process and everything it started works on Linux; Windows-only steps are skipped on Linux instead of failing. The platform table (PLATFORMS.md) was regenerated.
- **No more false quota stops:** the words "overloaded", "rate limit", "credit balance" and the number 429 now only count as a quota error when the real error message is around them. A planned budget pause (exit code 75) no longer looks like a quota error.
- **Check Claude on the other computer:** two new cable checks start one real Claude session on the other laptop and report whether it worked, and read the five one-hour cache settings. A third one writes those five settings, keeping a backup first, without needing a login.
- **Cable jobs report back properly:** when a job runs on the other computer, its return note carries Claude's exit code and the last lines of its log.
- **Plan board:** rows R14 and R18 are marked done, with the code and test that prove it.

### What did not change

No tool was removed. Existing commands keep their behavior.

### What was not checked

The cable-first route was checked on one pair of laptops only. A Claude session on the Linux laptop was started once over the cable (it worked, exit 0, 21.9 seconds); long jobs there were not run for this release.
