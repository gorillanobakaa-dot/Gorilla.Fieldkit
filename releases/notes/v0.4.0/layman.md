# Fieldkit v0.4.0, in plain language

## Building the browser on Linux, start to finish

`fieldkit pipeline run firefox-linux` takes the browser's source, builds it, packs it as a Debian package and
installs it, checking each step before the next one starts. Before it builds anything it refuses to work on the
wrong folder, and it says which fonts, logos and graphics fixes must be in the build. After it installs, it checks
what a person sees: the name in the menu, the icon, and whether an old picture on the computer hides the new one.

## When something looks wrong

`fieldkit build-harness symptom "<what you see>"` takes a description in plain words, in English or Romanian, and
names the tool that was built for that problem: a dialog whose text is cut off, an old name or icon in the menu,
video that stutters, an add-on that refuses to install. Some of these are not faults at all: an add-on that will not
install is the browser's protection doing its job, and the tool says so.

`fieldkit build-harness clip-check` opens the browser's dialogs, measures every line of text in them, compares with
an unmodified Firefox, and names the exact style rule that cut a message short.

## Carrying the work to a new Firefox version

When Mozilla releases a new Firefox, many of Gorilla's changes no longer fit where they used to.
`fieldkit build-harness rebase-set` finds every change that no longer fits, writes its new form, and proves the whole
set: Mozilla's source plus Gorilla's changes must give exactly the browser that was built. It also checks, line by
line, that the idea of every old change made it into the new version, and lists anything that did not.

The same list of changes now serves Linux and Windows. The tools know which system they run on and leave out what
belongs only to the other one. `fieldkit build-harness platform-steps` prints what to do on the machine you are on.

## Publishing without leaking

`fieldkit privacy push-check` looks at exactly what a push would send and refuses it if it finds a password, a key,
a personal folder, or a browser's private files: bookmarks, history, cookies, saved passwords, open tabs.
`fieldkit privacy install-hook` makes it run by itself before every push.

## Nothing left unreleased

`fieldkit ship audit` says whether all the work has reached the main copy on GitHub and how long it has been since the
last release; every session starts with that answer. `fieldkit ship release` publishes a new version only when the
tests on GitHub passed, the version number is right and the notes are complete. This release, v0.4.0, came two days
and 81 changes after the last one because nothing asked; now something does.

## Proving the download, not just the build

`fieldkit build-harness release-roundtrip` removes the installed browser, downloads the published package from
GitHub the way anyone would, checks its fingerprint, installs that file and checks the result. A release counts only
when the file people download installs and works.
