# v0.8.1, in plain language

## Main goal: work that runs without you

**A refusal explains itself.** Fieldkit has a few actions it never lets a helper program do on its own, such as publishing code. When one is refused, the message now gives the reason the owner's standing permission states, instead of a bare "no" (B1).

**The wait for checks looks at the right checks.** After code is pushed, an automatic test service (CI) runs checks on Windows, Linux and sometimes more. Before, Fieldkit guessed which checks to wait for. Now each project has a short list of the checks that count, kept in one data file, and the waiting, syncing and permission proof all read that list (P14, idea I-11). Fieldkit also counts bad versions of the code, not bad runs, so one flaky rerun no longer looks like two failures.

**A push is asked for in full.** When the permission is checked, the command is now written with its complete location, and the refusal says plainly when the permission was never asked.

## Everyday problems fixed

**Gorilla OpenCode builds from a clean copy.** A build now refuses a working copy whose text files use Windows line endings, because that broke the build on one machine. It starts from a fresh clean copy of the source instead.

**Missing helper programs are fetched.** A clean copy lacks six small helper programs (for archives, video, and images). The build now downloads the exact official versions, checks each one against a known fingerprint, and goes on. Gorilla OpenCode v0.1.158 then built green in 7.2 minutes with no model call.

## Not in this release

Not checked on a real run: the new CI list on a repository other than Fieldkit and Gorilla OpenCode, and the helper download on Linux and macOS. No tag or release page is published by this change; that follows after it is merged.
