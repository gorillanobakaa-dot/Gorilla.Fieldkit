# Put a newly built browser on your computer safely, then prove it keeps its promises — Plain Language Guide

> Generated 2026-10-02 from `install-and-proof`

---

## Should You Run This?

Run it only if you build Gorilla Firefox with this harness and want that build on your own computer: it is made for that one job. Run it only after `build-verify` has passed, with every Firefox closed. Do not add `--no-backup`. If Fieldkit asks for `--install-dir`, give it the Gorilla folder and double-check the path: it trusts the folder you name. Try `--restore` once before you depend on it, and know that it swaps your profiles folder too (the old one is kept). Do not run it at all if you want an installer that registers the browser with Windows from scratch; this one unpacks a zip into an existing install's place. Do not run it on Linux: the Linux branches in the code are untested.

## Worst Case, Honestly

The install deletes the old browser folder before it unpacks the new one. Which folder that is matters most. When you do not name the folder yourself, Fieldkit now only accepts an entry in the Windows uninstall list whose display name, or whose own install folder name, says `Gorilla`, and which holds a `firefox.exe`. An entry that only says Firefox, such as the ordinary Mozilla Firefox, is never picked. If no entry or more than one entry qualifies, it refuses and asks you to name the folder with `--install-dir`. So the realistic way to lose the wrong folder is to type the wrong folder after `--install-dir` yourself: Fieldkit trusts that folder and deletes it after taking the backup.

The real loss comes if you add `--no-backup`. Then the old browser folder is deleted with no copy. Example: you add `--no-backup` to save disk space, the new build turns out broken, and there is no older browser to go back to.

A backup can also be quietly incomplete: a file that another program holds open while the backup runs is left out, and the only clue is the file count in `manifest.json`.

If the packaged zip contained a file whose path tried to climb out of the browser folder, the install now stops with `would land outside` and writes nothing outside the folder. By then the old browser folder has already been removed, so you would put it back with `--restore`.

Last, a row of `[ok]` lines is not a guarantee. The egress test watches for 75 seconds and only sees what goes through the browser's own web layer. A leak that waits longer, or that uses another route, would not show up in that test.

## What Data This Touches

What it reads: the installed browser folder, the Windows uninstall list in the registry (to find where the Gorilla browser is installed), your Firefox profiles list (`profiles.ini`), and the build job's own records.

What it writes on your computer: the backup goes to `Documents\Gorilla.Firefox.Backups\<version>-<date>-<time>\` as `install-<version>.zip`, `profiles.zip` and `manifest.json`. Your profiles hold your browsing data, so `profiles.zip` is a full copy of it in an ordinary, unencrypted zip file. A zip install also writes the small marker file `gorilla-install.json` into the browser folder. A restore that puts your profiles back first renames your current profiles folder to `<folder>.before-restore-<date>-<time>` and keeps it; that kept folder is a second full copy of your browsing data, and the code never deletes it.

Each test makes a throwaway profile in your temporary folder and deletes it when the test ends. Fieldkit only deletes a folder that sits directly in your temporary folder, has one of its own name prefixes and carries its own label file; any other folder is refused, never deleted. Before deleting, the start check and the web-address check copy their logs into `fieldkit-logs` in your temporary folder. The web-address log lists every address the browser asked for during the test. Those kept logs are not deleted by the code. Throwaway profiles left behind by older versions of this code carry no label file, so they stay until you delete them by hand. The post-install checks keep the maintainer's scripts' output in the build job's folder.

What goes over the internet: the install itself sends nothing. The proofs do use the internet, on purpose. The egress test loads one fixed public web page for 75 seconds. The ad-blocking test loads the front page of a news site (`www.theguardian.com/international`) for 60 seconds. The leak test page asks a public Google server (`stun.l.google.com`) for your public address, the way a video call does. The separate `capture` test loads pages through a local proxy and saves the start of each request (up to 400 characters) to disk. Its packet pass, if you run it as administrator, records every network frame of the whole computer while it runs, not only the browser's.

Nothing in this code sends your data to the maintainer or to any collection service.

## Before You Trust It

This code deletes and replaces a browser folder, and a restore swaps your profiles folder. Before you let it, make sure it will replace the right browser, that the backup works, and that the way back works. You can do every step below without reading code.

**Step 1:** Open the Start menu, type `Installed apps`, press Enter, and look for every entry with 'Firefox' or 'Gorilla' in its name.
  - Look for: Pass: exactly one entry says Gorilla. Fieldkit will pick that one and never an entry that only says Firefox. Fail: no entry says Gorilla, or two do. Then Fieldkit refuses with a `NEXT:` line, and you add `--install-dir` with the Gorilla folder in quotes.
**Step 2:** Run `fieldkit build-harness install` once and read the first lines it prints.
  - Look for: Pass: the line `installed now:` names the Gorilla browser and the folder you expect, then a `backup:` line names `install-<version>.zip`. Fail: the folder or name is not the one you expect. Press Ctrl+C at once, before the `install:` line appears; until then nothing has been deleted.
**Step 3:** Open File Explorer, go to `Documents`, then `Gorilla.Firefox.Backups`, then the newest folder. Open `manifest.json` with Notepad.
  - Look for: Pass: you see `install-<version>.zip`, `profiles.zip` and `manifest.json`, both zips are larger than zero bytes, and `manifest.json` shows a `source` line naming your Firefox profiles folder. Fail: a zip is missing or tiny. Do not continue until you know why.
**Step 4:** Try the way back once: after an install, close every Firefox, then in PowerShell type `fieldkit build-harness install --restore ` (with a space at the end), paste the backup folder path, and press Enter.
  - Look for: Pass: a line `restored install-<version>.zip into ...`, then `current profiles kept as ...` and `restored profiles.zip into ...`, then `restored:` with the old version. In File Explorer you now see your profiles folder and, next to it, a folder ending in `.before-restore-` and a date. Fail: `does not match its manifest hash` means the backup is damaged; do not rely on it. Afterwards, run `fieldkit build-harness install` again to put the new build back.
**Step 5:** Run `fieldkit build-harness post-install --drive` when nobody needs the computer, and read every line that starts with a bracket.
  - Look for: Pass: every line shows `[ok]`, and the last line reads `POST-INSTALL OK`. Fail: any `[FAIL]` line; read its evidence, which names the setting, file or web address at fault. Any `[SKIPPED]` line also makes the run NOT OK: that check proved nothing.
**Step 6:** After a post-install run, press the Windows key and R together, type `%TEMP%` and press Enter. Look for folders whose names start with `gproof_`, `gegress_`, `gadblock_` or `gleaks_`, and for a folder called `fieldkit-logs`.
  - Look for: Pass: no new `g...` test folder from this run's date and time is left, and `fieldkit-logs` holds one subfolder per kept log. Fail: a new test folder from this run is still there; the clean-up did not finish (a browser may still have held a file). Older folders from before this change are expected and safe to delete by hand.

## The Big Picture

This part of Fieldkit does two jobs for the privacy browser that the maintainer builds (Gorilla Firefox). First, it replaces the browser on your computer with a new build. Before it touches anything, it packs a full copy of the old browser folder and your browser profiles into a backup in your `Documents` folder, so you always have a way back. The way back now puts both halves back: the old browser folder and, when the backup holds them, your old profiles.

Second, it proves that the browser you now run really does what the build promised. It does not trust the build recipe or the source code. It opens the installed browser itself, on a throwaway profile that it deletes again afterwards, and watches what it does: which settings it carries, which removed parts are still inside it, whether it starts without errors, which web addresses it asks for on its own, whether ads are blocked on a real news page, and what a web page can learn about your computer.

You run it with the `fieldkit` command in a PowerShell window. Each check prints `[ok]`, `[FAIL]` or `[SKIPPED]` with the evidence next to it, and the result goes into the build job's journal. A run is only called OK when every check it was asked for really ran and passed. On 2 October 2026 these checks measured build 11 of Firefox 157.0: zero Mozilla or Firefox addresses in the browser's own web log over 75 seconds, none of 16 ad and tracker addresses reached on a news front page, and no local network address shown to a page over WebRTC.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `backup` | A zip copy of the installed browser folder and of your Firefox profiles, with a `manifest.json` that records each zip's file count, size and fingerprint, and which profiles folder the profiles came from. | Photocopying a contract before you sign the new version, and noting the page count so you can tell later if a page went missing. |
| `hashed zip` | The packaged browser in one zip file whose fingerprint (a SHA-256 hash) the earlier `build-verify` step wrote down. The install refuses a zip whose fingerprint differs. | A parcel with a tamper seal: if the seal number does not match the delivery note, you do not open it. |
| `startupCache` | A folder inside each Firefox profile where the browser keeps ready-made copies of its own scripts so it starts faster. | A cook's pre-chopped ingredients: handy, but wrong if the recipe changed overnight. |
| `BuildID` | A date-and-time stamp inside the browser that tells Firefox which build it is. Firefox reuses the startup cache while the BuildID stays the same. | A batch number on a medicine box. |
| `headless` | The browser runs with no window on screen and needs no keyboard or mouse. | A car engine tested on a stand in the garage, not driven on the road. |
| `throwaway profile` | A new, empty Firefox profile made in your temporary folder for one test, so your own profile and settings play no part. Its folder name starts with a known prefix (such as `gproof_`) and it carries a small label file, `.fieldkit-throwaway`. After the test, Fieldkit deletes it. | A hotel room booked for one night instead of testing in your own house; the room is cleared when you check out, and the cleaner only clears rooms with your booking tag on the door. |
| `HTTP log` | A text file that the browser writes itself, listing every web address it asks for. Fieldkit switches it on with `MOZ_LOG=nsHttp:3`. | A shop's own till roll: every sale the till rang up is on it. |
| `prefs` | Firefox settings (preferences). The privacy build changes many of their default values and locks some of them. | The factory settings on a new phone. |
| `omni.ja` | The large archive file inside the browser folder that holds most of the browser's own scripts and default settings. | The sealed box of parts that comes inside a flat-pack wardrobe. |
| `WebRTC` | The part of a browser that makes video and voice calls. It can show a web page your computer's local network address unless the browser hides it. | A caller ID that might show your extension number inside the office as well as the main switchboard number. |
| `uBlock Origin` | The ad-blocking add-on that comes bundled with the privacy build. | A doorman who turns away known pushy salespeople. |
| `mitmproxy` | A program that sits between a browser and the internet and opens up encrypted requests so you can read them. Only the separate `capture` test uses it, and only on a test copy. | A post-room clerk who opens and photocopies every outgoing letter before it is posted. |
| `install marker` | A small file called `gorilla-install.json` that Fieldkit writes into the browser folder at the end of a zip install. It holds a random code made fresh for this run, the zip's name and fingerprint, the version and the time. The check after the install reads it back and only passes if the code and fingerprint are the ones this run wrote. | A raffle ticket torn in half: you keep one half, staple the other to the prize, and later only accept the prize whose half matches yours. An old ticket from last year's raffle never matches. |
| `uninstall list` | The list Windows keeps of installed programs (the one behind Settings, Installed apps). Each entry has a display name and an install folder. Fieldkit reads it to find the Gorilla browser and never writes to it. | The residents' board in the lobby of a block of flats: it says who lives in which flat, but the board can be out of date. |
| `kept logs folder` | A folder called `fieldkit-logs` in your temporary folder. Before a throwaway profile is deleted, Fieldkit copies the log files a check needs as evidence into a subfolder there, named after the profile. Only log files are copied: no cookies, no cache, no settings. | Keeping the receipt after you throw the shopping bag away. |

## How It Works — Step by Step

### Step 1: Check that the package is the one that passed the build checks

When you type `fieldkit build-harness install`, Fieldkit first opens the record that the `build-verify` step wrote. That record holds the fingerprint (SHA-256 hash) of the installer and of the zip. Fieldkit recomputes both fingerprints from the files on disk. If either differs, it stops with `the zip on disk is not the one build-verify hashed` and changes nothing. Think of a courier checking the seal number on a parcel against the delivery note.

### Step 2: Find where the browser is installed

If you named the folder with `--install-dir`, Fieldkit uses exactly that folder. If you did not, it reads every entry in the Windows list of installed programs, for your account and for the whole computer, and keeps only the entries whose display name says `Gorilla`, or whose own install folder name says `Gorilla`, and whose folder holds `firefox.exe`. It looks at the folder's own name, not the whole path, because an account name that happens to contain the word would otherwise make every program in that account look like a Gorilla install. An entry that only says Firefox is never picked. If exactly one folder is left, that is the target. If none is left, or several are, Fieldkit refuses and changes nothing, and prints a line starting with `NEXT:` that tells you to pass `--install-dir` with the Gorilla install folder. Think of a removal firm that only empties the flat whose door carries your name, and that phones you instead of guessing when two doors do. Fieldkit then reads `application.ini` in the chosen folder and prints the version that is installed now.

### Step 3: Take the backup

Fieldkit zips the whole browser folder into `install-<version>.zip` and your whole Firefox profiles folder into `profiles.zip`. It puts both in a new folder under `Documents\Gorilla.Firefox.Backups`, named after the old version and the date and time. It writes `manifest.json` next to them with the number of files, the size and the fingerprint of each zip, and the path of the profiles folder the profiles came from. It prints a line such as `backup: install dir -> install-<version>.zip (<files> files, <size> MB, <time> s)`. A file that another program holds open is left out without a warning; the file count in the manifest is your only clue.

### Step 4: Install the new browser from the hashed zip

Fieldkit refuses to go on while that browser is running. It then deletes the old browser folder (the backup now holds it) and unpacks the new browser from the zip into the same folder. While it unpacks, it works out where each file would land. If any file's path would land outside the browser folder (a trick known as a path that climbs out with `..`), it stops with `would land outside` and writes nothing there. Once every file is in place, it writes the install marker `gorilla-install.json` with a fresh random code. It does not run the normal Windows installer when a zip exists. The reason is a real failure: on 2 October 2026 the installer exited with a success code having installed nothing. A stale entry in the program list sent it down the administrator route, it could not get administrator rights, and it said nothing. Unpacking a zip has no such hidden decisions. It is like unpacking a box yourself instead of trusting a removal firm's 'all done' text.

### Step 5: Clear the old startup caches

Fieldkit deletes the `startupCache` folder in every Firefox profile on the computer. Firefox keeps ready-made copies of its scripts there and reuses them while the build stamp (BuildID) stays the same. On 2 October 2026 four builds in a row carried one BuildID, so a profile kept running a broken build's cached scripts while a fresh profile ran the new build. Now the BuildID changes with the source, and every install clears these caches as well. This step refuses if any Firefox is running, including an ordinary Firefox you have open, because a running browser holds those files.

### Step 6: Confirm the install and write it down

Fieldkit checks four things and prints `[ok]` or `[FAIL]` for each: `application.ini` names the expected version; `firefox.exe --version` names it too; the install marker in the folder carries this run's random code and this zip's fingerprint; and every profile's startup cache is empty. The marker check replaced an older check that looked at the Windows program list. That older check could pass because of an entry left behind by an earlier installer, even if this run had installed nothing. A marker made with a fresh random code cannot be left over from an earlier run. (If the build had no zip and the Windows installer was used instead, the third check is still the program-list entry, because the installer writes that entry.) Fieldkit writes the result, the backup folder and the list of cleared caches into the build job's journal. The last line reads `INSTALL OK` or `INSTALL NOT OK` followed by the reason.

### Step 7: The way back: browser folder and profiles

If the new build is wrong, `fieldkit build-harness install --restore` followed by the backup folder's path puts the old browser back. Fieldkit first works out which folder to restore into, the same way as in step 2, and refuses with `RESTORE REFUSED` and a `NEXT:` line if it cannot be sure. Then it checks everything before it touches anything: the fingerprint of the browser zip against `manifest.json`; and, when the backup holds `profiles.zip`, that your profiles folder is the same one the backup was taken from, that the fingerprint of `profiles.zip` matches, and that no Firefox at all is running (an ordinary Firefox shares the profiles folder). Any failure stops it with nothing changed. A damaged backup is refused with `does not match its manifest hash: not restoring from a damaged backup`. A profiles folder that differs from the backup's is refused with a message that tells you how to put the profiles back by hand. If everything checks out, Fieldkit empties the browser folder and unpacks the old one. Then it renames your current profiles folder to `<folder>.before-restore-<date>-<time>`, keeps it, and unpacks the backed-up profiles into a fresh folder. If that unpacking fails, it removes the half-written folder and renames the kept one back, so you end up with the profiles you had before. Think of moving into a furnished flat: the old furniture goes into the garage, not the skip, and comes back in if the new delivery is damaged.

### Step 8: Proof 1: the settings are really in the shipped browser

Now `fieldkit build-harness post-install` starts proving. The first check compares two lists. One is every setting the port added or changed against Mozilla's untouched files (`all.js` and `firefox.js`). The other is the setting files packed inside the installed browser's `omni.ja`. Each setting must be there with the same value and the same lock. Settings that sit inside an 'only on some systems' block in the source are counted but not judged, because the build decides those. If a setting is wrong, the evidence names it and says what was wanted and what shipped. Note what this reads: the browser's built-in defaults, not a settings page of a running browser.

### Step 9: Proof 2: the removed parts are really gone

The privacy build removes whole components: the machine-learning parts (`ml`), the AI window (`aiwindow`) and the generative-AI parts (`genai`). This check opens both `omni.ja` archives in the installed browser and lists every file inside. Any file under eight known folder prefixes of those components fails the check. So does any script whose name is on the maintainer's list of deleted files, unless a file that survives shares the same name. That exception exists because a name-only match on 2 October 2026 flagged two legitimate files (`TelemetryUtils.sys.mjs` and `Utils.sys.mjs`) that share names with deleted ones.

### Step 10: Proof 3: a clean headless start

Fieldkit starts the installed browser with no window, on a throwaway profile, for 25 seconds, and reads everything the browser prints. It looks for script errors (`SyntaxError`, `ReferenceError`, `is not defined`), a missing internal component (`No such JSWindowActor`), a missing internal file (`Missing chrome or resource URL`) and a hook that names a removed part (`Error in processing`). Any such line fails the check. It copies what the browser printed into `fieldkit-logs` in your temporary folder and deletes the throwaway profile. This exists because on 2 October 2026 a build passed every build check and shipped with a dead address bar. Four keyboard-taking test runs went by before someone read the one line the browser had printed at startup: a `SyntaxError` in `DesktopActorRegistry.sys.mjs`. This check finds that kind of fault in seconds, with no window and no keyboard.

### Step 11: Proof 4: which web addresses the browser asks for on its own

Fieldkit starts the installed browser with no window on a throwaway profile, loads one fixed public web page, and lets it live for 75 seconds. The browser writes its own HTTP log the whole time. Fieldkit pulls every host name out of that log. Any address under `mozilla.com`, `mozilla.net`, `mozilla.org`, `firefox.com`, `firefox-portal-detection.com`, `getpocket.com` or `mozilla.cloud` fails the check. The page's own addresses pass, and so do 16 named addresses from which uBlock Origin fetches its block lists. Any other address is listed for you to look at, not passed in silence. The log is copied into `fieldkit-logs` in your temporary folder as evidence, then the throwaway profile is deleted. The browser's own log is used because an older check was fooled: it compared the computer's address cache with open connections and passed Gorilla 155.0.1, while that build asked eight Mozilla hosts for things on its own. Build 11 of 157.0 showed zero Mozilla or Firefox hosts in this log.

### Step 12: Proof 5: ad blocking on a real news page

Fieldkit loads `https://www.theguardian.com/international` in the installed browser with no window, for 60 seconds, again with the browser's own HTTP log on. Two things must be true. The page must load (its own host appears in the log). And none of 16 known ad and tracker addresses may appear, such as `doubleclick.net`, `googlesyndication.com`, `criteo.com`, `taboola.com`, `google-analytics.com` and `facebook.net`. This proves the bundled uBlock Origin is switched on and able to block. Build 11 reached none of the 16. The throwaway profile, log included, is deleted afterwards. If you are offline, the check fails with 'did NOT load', which is a correct result: an unloaded page proves nothing.

### Step 13: Proof 6: what a web page can learn about your computer

Fieldkit starts a tiny web server on your own computer (address `127.0.0.1`) and points the installed browser at it, with no window. The page does what public leak-test sites do: it gathers WebRTC call addresses with the help of a public Google server, draws hidden pictures to make a canvas and graphics fingerprint, measures which of 30 common fonts are present, and reads the browser's reported system, screen, language and time zone. It sends the results back to the tiny server. Three things fail the check: a raw local network address shown over WebRTC (it must be hidden behind a `.local` name), the graphics card's model name shown to the page, and the battery-level feature being present. Everything else is printed as a report, because those are setting choices for the maintainer. The throwaway profile is deleted afterwards. Build 11 showed no local IP address over WebRTC.

### Step 14: The maintainer's own scripts, and the one that takes the keyboard

After its own proofs, Fieldkit runs four scripts from the maintainer's `Documents\Gorilla.firefox\working scripts` folder, in order, and keeps each one's output as a log: `verify_installed_build`, `verify_no_phone_home`, `webrtc_selftest` and `verify_address_bar`. The last one opens a real browser window and types into its address bar to check that typing an address really navigates. While it runs, it owns your keyboard and the front window. If you type at the same moment, your keys go into the test browser, or the test's keys go into whatever you are working on. So it never runs unless you add `--drive`. With `--drive`, Fieldkit first prints a warning that it will take the keyboard and the front window for about 70 seconds, then counts down 20 seconds in five-second steps, so you can step away or press Ctrl+C to stop it.

Every check ends in one of three states: `ok`, `FAIL` or `SKIPPED`. A check is SKIPPED when it did not run: the keyboard check without `--drive`, a maintainer script that is missing from that folder, or a name after `--only` that is no check at all (a typo). SKIPPED is never OK. The final line reads `POST-INSTALL OK` only when every check you asked for really ran and passed; otherwise it reads `POST-INSTALL NOT OK:` followed by one line per check that was not OK, such as `SKIPPED: verify_address_bar`. Think of an exam where an unanswered question scores zero instead of being left out of the total. Build 11 navigated correctly in all four typed cases.

### Step 15: A deeper, separate test: three witnesses

The browser's own log can only report what passes through the browser's own web layer. `fieldkit build-harness capture` adds two more witnesses on a test copy unpacked from the hashed zip; your installed browser is never touched. The first witness puts `mitmproxy` between the test copy and the internet and opens every request, including encrypted ones. The second records every name the browser looks up in the address book of the internet (DNS), without the proxy. The third, only in an administrator PowerShell, records every network frame on the computer and picks out the browser's traffic. It runs four scenes: an empty page for 120 seconds, the new tab page for 60, the add-ons page for 45, and a real web page for 60. Each scene uses its own throwaway profile, deleted after the scene. Any Mozilla or tracker address in any witness fails that row.

## Quirky Things Worth Knowing

### Your old profiles are moved aside, not deleted

When a restore puts your profiles back, your current profiles folder is renamed to `<folder>.before-restore-<date>-<time>` and kept next to it. The code never deletes it. It is a second full copy of your browsing data and takes disk space. Delete it by hand once you are sure you do not need it, with every Firefox closed.

### A restore only puts profiles back into the folder they came from

The backup records which profiles folder it copied. If the restore would put them somewhere else (another Windows account, or a moved folder), it refuses before touching anything and tells you how to unzip `profiles.zip` by hand. Backups made by older versions of this code do not record the folder, so their profiles go back into your normal Firefox profiles folder without that check.

### A plain post-install run ends NOT OK because of the keyboard check

Without `--drive`, the address-bar check cannot run, so it is SKIPPED, and SKIPPED is never OK. That means `fieldkit build-harness post-install` with no options always ends with `POST-INSTALL NOT OK:` and `SKIPPED: verify_address_bar`, even when everything else passed. This is on purpose: an OK must mean every check ran. To get an OK without the keyboard check, list the checks you want after `--only` (see How to use this), then run the keyboard check on its own with `--drive`.

### A typo after --only makes the run NOT OK

A name after `--only` that is no check is recorded as SKIPPED with the reason `no check has this name`, and the line lists the names that do exist. Older versions quietly ran nothing for such a name and could still report OK.

### Being offline makes the ad-blocking check fail

The ad-blocking proof needs the news page to load. With no internet, the page cannot load, and the check fails on purpose with 'did NOT load'. That is not a sign the browser is broken. Run it again when you are online.

### Clearing caches refuses if any Firefox is open

The install only refuses if the browser it is replacing runs. The cache step that follows refuses if any Firefox at all runs, even the ordinary one. If that happens, the new browser is already unpacked but the caches are not cleared and the result is not written to the journal. Close every Firefox and run the install again; a second backup is taken first.

### The proofs use the internet on purpose

A privacy tool that loads a news site and contacts a Google server can look odd. The news page is there to give the ad blocker something to block. The Google server is the standard way a page learns your public address for a call; the test needs it to see whether your local address leaks next to it.

### Some logs stay in your temporary folder

Throwaway profiles are deleted after each test, but the start check and the web-address check first copy their logs to `fieldkit-logs` in your temporary folder, one subfolder per test, and the code does not delete those. The web-address logs list the addresses visited during the tests. Throwaway profiles left by older versions of this code (names starting with `gproof_`, `gegress_`, `gadblock_`, `gleaks_`, `gcap_` or `gstartup_`, without the label file) are not cleaned up either. You can delete all of these by hand.

### What this cannot do

It cannot protect a browser folder you name wrongly with `--install-dir`; it trusts that folder. It cannot make a backup complete when another program holds a file open. It does not put back a browser folder that it has already emptied when the new zip turns out to be unsafe or broken; you use `--restore` for that. The restore does not clear startup caches and does not write to the journal. The egress check sees only the browser's own web layer for 75 seconds, and it does not judge a request sent to a bare number address instead of a name. The proofs depend on two live public web pages, so a change to those pages changes the result. It does not check the maintainer's four scripts before running them. It is written for Windows; the Linux branches in the code are untested.

## What This Means For You

### Battery, Processor & Memory

Not measured. During the proofs the browser runs with no window for fixed times set in the source: 25 seconds for the start check, 75 seconds for the egress check, 60 seconds for the ad-blocking check and up to 45 seconds for the leak page. Zipping the browser folder and your profiles uses the processor and disk for a time that depends on their size; that time is not measured.

### Speed

The install does not make the browser faster or slower. The first start after an install may take longer, because the startup cache was cleared and Firefox rebuilds it; how much longer is not measured. A full `post-install` run takes at least the sum of the fixed test times above plus the maintainer's four scripts, each allowed up to 900 seconds; the real total is not measured.

### Your Privacy

The point of the proofs is your privacy: they check, on the browser you really run, that it does not contact Mozilla on its own, that ad and tracker addresses stay blocked, and that a page cannot see your local network address or your graphics card's model name. The backup itself is a privacy risk to manage: `profiles.zip` holds your browsing data in an unencrypted file in your `Documents` folder, and after a restore the kept `.before-restore-` folder holds another copy. Throwaway test profiles are now deleted after each test; the logs kept in `fieldkit-logs` list the web addresses the tests visited.

### Your Internet

The install and the restore use no internet. The proofs load one public page for 75 seconds and a news front page for 60 seconds, and the leak page contacts a Google call server once. The data volume is not measured. The `capture` test loads pages in four scenes per witness, for about 285 seconds (not measured: the sum of the scene times written in the source) of browsing per witness.

## The Off Switch

**What it is:** There are several stops. The install refuses while the browser it would replace is running, and refuses a package whose fingerprint does not match the one recorded at build time. Without `--install-dir`, it refuses unless exactly one Gorilla install is registered with Windows. It refuses a zip file entry that would land outside the browser folder. The keyboard-taking check never runs without `--drive`, and with `--drive` you get a 20-second countdown in which Ctrl+C stops it. The restore refuses a damaged backup, a profiles folder that is not the one the backup came from, and any running Firefox while profiles are to be restored. The clean-up of test profiles refuses any folder it did not make. The packet pass of `capture` does nothing without administrator rights. The backup can only be turned off by adding `--no-backup`; it is on by default.

**Without it:** Without the running-browser stop, the install could delete files a running browser holds open and leave a half-replaced folder. Without the fingerprint check, a package that changed after testing could be installed. Without the one-Gorilla-install rule, the ordinary Firefox could be deleted and replaced. Without the `--drive` gate, a test could type into your email or document while you work. Without the label-file rule, a clean-up mistake could delete a folder that is not a test profile.

**Think of it like:** A table saw that will not start while the guard is open, and that sounds a horn for 20 seconds before it spins up so anyone nearby can step back.

## How to use this

**Before you start:**
- Windows, with Fieldkit installed so that the `fieldkit` command works in PowerShell. To open PowerShell, open the Start menu, type `PowerShell` and press Enter.
- A finished build job whose `build-verify` step has passed. The commands below use the current build job; to name another one, put its name after the action, for example `fieldkit build-harness install firefox-157.0`.
- Every Firefox window closed, the ordinary Firefox included.
- An internet connection for the post-install proofs (the install itself works offline).
- Free space in your `Documents` folder for a full copy of the browser folder and your profiles (the size is not measured).

**Step 1:** Close every Firefox window. Then type this and press Enter:

```
fieldkit build-harness install
```

If Fieldkit answers with a `NEXT:` line, or if you want to be certain, name the Gorilla folder instead: type `fieldkit build-harness install --install-dir ` (with a space), paste the folder path in quotes, and press Enter.
  - You should see: Lines for the backup, the install, the cleared caches and four checks, each `[ok]` (one of them names `gorilla-install.json`), then `INSTALL OK`. **Fail:** `INSTALL NOT OK` with a reason, a refusal such as `Firefox is running from ...: close it first`, or `INSTALL NOT OK: no uninstall entry names a Gorilla install` followed by `NEXT: pass --install-dir <the Gorilla install folder>`.
**Step 2:** Prove the installed browser keeps its promises without the keyboard check. Stay online, keep Firefox closed, and type:

```
fieldkit build-harness post-install --only prefs,excised,startup,egress,adblock,leaks,verify_installed_build,verify_no_phone_home,webrtc_selftest
```

Type it as one line, with commas and no spaces between the names.
  - You should see: One `[ok]` line per check: settings, removed parts, headless start, web addresses, ad blocking and leaks, then the maintainer's three scripts. The last line is `POST-INSTALL OK`. **Fail:** any `[FAIL]` or `[SKIPPED]` line, and `POST-INSTALL NOT OK:` at the end with one line per check that was not OK. If you run `post-install` with no `--only`, the run ends NOT OK with `SKIPPED: verify_address_bar`, because that check needs `--drive`.
**Step 3:** Run the address-bar check, which takes your keyboard. Save your work, make sure nobody needs the computer during the 20-second countdown and the roughly 70 seconds the check announces, and type:

```
fieldkit build-harness post-install --drive --only verify_address_bar
```

Then take your hands off the keyboard and mouse.
  - You should see: A warning that the check will take the keyboard and the front window for about 70 seconds, a countdown from 20 seconds, then a browser window opens and types by itself. The result line shows `[ok] verify_address_bar`. **Fail:** `rc=` followed by a number other than 0. To stop before it starts, press Ctrl+C during the countdown.
**Step 4:** To repeat only some proofs, list them after `--only` with commas and no spaces, for example:

```
fieldkit build-harness post-install --only prefs,excised,startup
```

The names you can use are `prefs`, `excised`, `startup`, `egress`, `adblock`, `leaks`, `verify_installed_build`, `verify_no_phone_home`, `webrtc_selftest` and `verify_address_bar`.
  - You should see: Only the named checks run, each with `[ok]` or `[FAIL]`. A misspelt name shows `[SKIPPED] <name>: no check has this name` with the list of real names, and the run ends NOT OK.
**Step 5:** For the deeper three-witness test, type:

```
fieldkit build-harness capture
```

For the packet witness, open PowerShell as administrator (right-click PowerShell in the Start menu, choose 'Run as administrator') and type `fieldkit build-harness capture --packets-only`.
  - You should see: Rows named `capture/mitm`, `capture/dns` and, as administrator, `capture/packets` for the scenes `idle`, `newtab`, `addons` and `page`, then `CAPTURE OK` and the folder where the results are kept. **Fail:** `CAPTURE NOT OK`. Without administrator rights the packet row fails with a message that tells you the command to run in an administrator PowerShell.
**Step 6:** If the new browser is wrong, go back. Close every Firefox, the ordinary one too. In File Explorer open `Documents`, then `Gorilla.Firefox.Backups`, hold Shift, right-click the backup folder you want, and choose 'Copy as path'. In PowerShell type `fieldkit build-harness install --restore ` (with a space at the end), paste with Ctrl+V, and press Enter.
  - You should see: `restored install-<version>.zip into ...`, then, when the backup holds profiles, `current profiles kept as ...before-restore-...` and `restored profiles.zip into ...`, then `restored:` with the old version. **Fail:** `does not match its manifest hash: not restoring from a damaged backup` (pick an older backup folder); `a Firefox is running and may hold the profiles` (close every Firefox and try again); or `RESTORE REFUSED:` with a `NEXT:` line (add `--install-dir` with the Gorilla folder).

## If Something Goes Wrong

**`Firefox is running from ...: close it first`**
The browser being replaced is still open, maybe in the background.
What to do: Close every Firefox window, wait a few seconds, and run the command again. If it still refuses, sign out of Windows and back in, then retry.

**`Firefox is running: close it before the startup caches are cleared`**
Some Firefox, maybe the ordinary one, is open. The new browser is already unpacked, but the caches are not cleared and the result is not in the journal.
What to do: Close every Firefox and run `fieldkit build-harness install` again. It takes a fresh backup first.

**`no uninstall entry names a Gorilla install (an entry that only says Firefox is never picked)` and `NEXT: pass --install-dir <the Gorilla install folder>`**
The Windows program list has no entry whose name or install folder name says Gorilla with a `firefox.exe` in it. This is normal if the browser was only ever unpacked from a zip.
What to do: Run the command again with `--install-dir` and the Gorilla folder, in quotes.

**`2 Gorilla installs are registered, not guessing which: [...]` (the number can differ)**
More than one Gorilla browser folder is registered with Windows, and Fieldkit will not guess which one to replace.
What to do: Pick the folder you mean from the list in the message and run the command again with `--install-dir` and that folder, in quotes.

**`the zip on disk is not the one build-verify hashed`**
The package changed after it was checked, for example because a later packaging step rewrote it.
What to do: Do not install it. Run `fieldkit build-harness build-verify` again so the package is checked and its fingerprint recorded afresh.

**`... would land outside ...` during the install**
A file inside the zip has a path that tries to climb out of the browser folder. A real build never does this; the zip is damaged or has been tampered with. The old browser folder was already removed.
What to do: Do not use this zip. Put the old browser back with `--restore` and the newest backup folder, then rebuild and run `fieldkit build-harness build-verify` again.

**`[FAIL] this install's own marker (gorilla-install.json) names this run and this zip` with `marker missing`**
The marker file was not found after the install, so Fieldkit cannot prove this run installed the files.
What to do: Run `fieldkit build-harness install` again and read the `install:` line; it must name the zip and a file count.

**`... was taken from ..., not ...: nothing restored`**
The backup's profiles came from a different profiles folder than the one the restore would write to, for example another Windows account.
What to do: Nothing was changed. Follow the steps in the message: close every Firefox, rename the named folder to keep it, then unzip `profiles.zip` into it.

**`[FAIL] adblock: ...` with 'did NOT load'**
The news page did not load, usually because you are offline.
What to do: Connect to the internet and run `fieldkit build-harness post-install --only adblock`.

**`[FAIL] egress: ...` naming a Mozilla address**
The installed browser asked a Mozilla or Firefox server for something on its own. The evidence names the address and the first web address it asked for.
What to do: Treat the build as not private. Restore the previous browser with `--restore` and report the named address to the maintainer.

**`[SKIPPED] verify_...: ... not found: this check did NOT run`**
The maintainer's scripts folder `Documents\Gorilla.firefox\working scripts` does not hold that script on this computer. The run ends NOT OK.
What to do: Put the script in that folder, or leave its name out of `--only` and accept that this check was not part of the result.

## Why a Developer Would Do This

A build can pass every build-time check and still fail the person who runs it. That happened on 2 October 2026: three faulty script modules built cleanly and the browser shipped with a dead address bar. A silent installer once reported success having installed nothing. Four builds shared one BuildID, so an old profile kept running broken cached scripts. An older privacy check passed a build that asked eight Mozilla hosts for things. Each of these is a case where the tool said 'fine' and the browser was not. So this code checks the browser you actually run, with the browser's own records as the witness, counts a check that did not run as not OK, and keeps a backup that can put back both the browser and your profiles, so a bad result costs you minutes, not your browser.

## Why It Matters That You Can Read This

You are trusting this code with your browser and a copy of all your browsing data. Because you can read it, anyone can check the claims in this guide: that the backup is taken before the delete, that an entry that only says Firefox is never picked, that the restore checks every fingerprint before it touches anything and keeps your old profiles, that the keyboard check waits for `--drive`, that test profiles are deleted only when they carry Fieldkit's own label, and that no test sends your data to the maintainer. The proofs are also open: you can see which addresses count as 'Mozilla', which 16 ad addresses are tested, and that unknown addresses are listed, not hidden. If this were a closed program, an `[ok]` line would only mean 'the program says so'. With the source, you can see what each `[ok]` measured and what it did not.

## Glossary

**Backup folder** — The folder under `Documents\Gorilla.Firefox.Backups` that holds the old browser, your profiles and a list of fingerprints.

**Fingerprint (SHA-256 hash)** — A long code worked out from a file's contents that changes completely if even one byte of the file changes.

**Profile** — The folder where Firefox keeps your bookmarks, history, settings and other browsing data.

**Headless** — Running a browser with no window, so it needs no screen, keyboard or mouse.

**Egress** — Traffic that leaves your computer for the internet.

**Host** — The name part of a web address, such as `www.theguardian.com`.

**DNS** — The internet's address book, which turns a name like a web host into a number the computer can connect to.

**STUN server** — A public server that tells your browser what its public internet address looks like from outside, used for calls.

**Journal** — The build job's running record, where Fieldkit writes what each step did and whether it passed.

**Build job** — One Fieldkit task that ports, builds and checks one browser version, for example `firefox-157.0`.

**Marker file** — The small file `gorilla-install.json` a zip install leaves in the browser folder so the check can prove this run put the files there.

**SKIPPED** — The state of a check that did not run; it always makes the overall result NOT OK.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| the backup is taken before the old folder is deleted | 📄 stated in input | install: removed the old {dest} (backed up first) |
| installs unpack the hashed zip because the installer once installed nothing | 📄 stated in input | The NSIS installer once exited 0 having installed nothing; installs now unpack the hashed zip. |
| every install clears startup caches because builds shared one BuildID | 📄 stated in input | Four builds in a row carried one BuildID, so a profile kept running a broken build's cached scripts |
| the restore refuses a damaged backup | 📄 stated in input | not restoring from a damaged backup |
| the keyboard-taking check needs --drive and a countdown | 📄 stated in input | never run unless asked with --drive, and announced with a countdown first |
| the browser's own HTTP log is the witness for egress | 📄 stated in input | The browser's own nsHttp log names every URL it asks for; that is the only honest witness. |
| the older DNS-cache check passed a build that contacted eight Mozilla hosts | 📄 stated in input | Gorilla 155.0.1 asked 8 Mozilla hosts for things on its own |
| build 11 passed egress, ad blocking and WebRTC checks | 📄 stated in input | 0 Mozilla or Firefox hosts in the browser's own HTTP log over 75 s on a real page; 0 of 16 ad and tracker domains reached on a news front page; no local IP address exposed over WebRTC |
| a cache-step refusal leaves the new build unpacked and unjournaled | 🤖 model inference | *(none — model judgment)* |
| profiles.zip is a full unencrypted copy of browsing data | 🤖 model inference | *(none — model judgment)* |
| the egress test cannot see traffic that bypasses the browser's web layer | 📄 stated in input | the browser's own log can only report what goes through the browser's own logging |
| the address-bar check passed in all four typed cases on build 11 | 📄 stated in input | the address bar navigated in all 4 typed cases |
| an entry that only says Firefox is never chosen as the install target | 📄 stated in input | An ordinary Mozilla Firefox is never picked; none or several -> refused with a NEXT hint. |
| the zip install is verified by its own marker with a fresh nonce | 📄 stated in input | a stale uninstall entry of an earlier installer can never pass it |
| a zip entry that would land outside the install folder is refused | 📄 stated in input | would land outside |
| a restore keeps the current profiles folder and never deletes it | 📄 stated in input | the current profiles folder is first moved |
| a skipped check is never OK | 📄 stated in input | SKIPPED is never OK |
| throwaway profiles are deleted only when they carry the harness's prefix and marker | 📄 stated in input | a path from anywhere else is refused, never deleted |
| kept logs hold no cookies, cache or prefs | 📄 stated in input | no cookies, no cache, no prefs |
| throwaway profiles from older versions are not cleaned up because they lack the marker | 🤖 model inference | *(none — model judgment)* |
| the old browser folder is already removed when a zip entry is refused | 🤖 model inference | *(none — model judgment)* |
| a plain post-install run without --drive ends NOT OK | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*