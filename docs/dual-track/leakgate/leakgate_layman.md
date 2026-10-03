# A release gate that refuses a privacy browser build unless every connection it makes is proven and approved — Plain Language Guide

> Generated 2026-10-02 from `leakgate`

---

## Should You Run This?

Run it if you build or publish Gorilla Firefox and want evidence, not belief, that a build does not leak. Use quick runs while fixing, and trust only a release run in an administrator window that ends in `FINAL_RESULT PASS`.

Do not run it on a machine where the packet capture would record traffic you are not allowed to record, because on Windows it captures every program. Do not run it from an automated helper and expect approvals to work: they are refused on purpose. Do not treat a Windows `PASS` as the final word: the source names the Linux network-namespace runner as the authoritative environment, and that runner has not been run on Linux yet. Its certificate preparation step now closes its helper program the Linux way, and a test checks this on both systems. Reading the Linux code still suggests that several witnesses are not wired in there, so expect several rules to fail for missing witnesses on its first run, and expect to finish that work before relying on it.

## Worst Case, Honestly

The realistic worst case is a false `PASS` or a misleading `FAIL`. A false `PASS` would let the maintainer publish a build that still reports to the browser maker or leaks private data, while the documentation says it was checked. The design makes this hard: a missing witness is a `FAIL`, and several witnesses must agree. The remaining gap is the approvals themselves. If the maintainer approves a wrong entry in `allow.json`, or an approval is written into `dispositions.json`, the checkpoint trusts it completely. Anyone who can change files in the maintainer's Gorilla Firefox folder can change the verdict this way.

A second real harm is on your own machine. With `--firewall`, the checkpoint adds a Windows Firewall rule. It removes the rule at the end, but if the process is killed hard (for example the laptop loses power), the rule named `leakgate-<date-time>` stays behind and blocks only the test copy, which is harmless but leaves clutter. In a release run, the packet capture holds traffic of every program on the laptop for the whole test; if you share the run folder, you share that traffic.

## What Data This Touches

The checkpoint reads and stores a lot of data, all of it on your own machine. It records every web address the test browser requests, every name it looks up, the full decrypted contents of requests through the proxy, the programs it starts, the files it writes, and (in a release run) a packet capture of all network traffic on the laptop. The packet capture is not limited to the browser: on Windows it records traffic from every program running at the time. These records go into a run folder on your disk and stay there until you delete them.

The checkpoint itself does not upload anything. It does make outbound connections on purpose: two scenarios open one real public web page, and the test name server forwards approved names to your normal name lookup service to get real answers. It also creates a temporary security certificate authority on your machine for the proxy. That authority is installed only into the throwaway browser copies, through their own policy file, not into Windows.

## Before You Trust It

You are trusting this checkpoint to tell the truth about a browser's privacy. Before you rely on a `PASS`, check that it fails when it should, that it ran with all its witnesses, and that the list it judged against is the list you expect.

**Step 1:** Run the project's own tests for this group. Open PowerShell (press the Windows key, type `PowerShell`, press Enter), go to the Fieldkit folder, and type:

```powershell
python -m pytest tests/test_leakgate.py -q
```

  - Look for: - **Pass:** the last line says `passed` with no `failed`. These tests check that an unapproved entry is a failure, that approval is refused without a real terminal and cannot be forced by a setting, that no other approval route exists anywhere in the checkpoint, that the certificate step closes only the helper program it started on Windows and on Linux, and that missing packets or too few repeats fail.
  - **Fail:** any line with `failed` or `error`. Do not trust the checkpoint until that is fixed.
**Step 2:** Look at the per-rule lines printed at the end of a run.
  - Look for: - **Pass:** you see all 18 rule names, each with `PASS` or `FAIL` and a reason, then a `FINAL_RESULT` line.
  - **Fail:** a `FINAL_RESULT PASS` from a run without `--release`. That should be impossible, because a quick run always fails the reproducibility rule.
**Step 3:** Open `test-results.json` in the run folder with Notepad and search for `required sensor`.
  - Look for: - **Pass:** no matches in a release run. Every witness reported.
  - **Fail:** a match means a witness did not collect, and the matching rule failed for that reason.
**Step 4:** Open `build-manifest.json` in the run folder and compare `ALLOWLIST_SHA256` with the value from the previous release run.
  - Look for: - **Pass:** a changed value matches a change to `allow.json` that the maintainer knows about.
  - **Fail:** the value changed and nobody can say why. Someone edited the list.
**Step 5:** Read `allow.json` and look at the `approval` field of each entry.
  - Look for: - **Pass:** every entry has `"by": "owner"` and `"how": "terminal"` with a date.
  - **Fail:** an entry with `"approval": null` that you expected to be approved, or an approval recorded another way.
**Step 6:** Open `dispositions.json` in the maintainer's Gorilla Firefox folder (inside `leakgate`) with Notepad and look at the `approval` field of each entry.
  - Look for: - **Pass:** every approval is one the maintainer remembers giving, with a date.
  - **Fail:** an approval nobody can account for. The checkpoint believes any approval it finds in this file, because Fieldkit has no command that writes these approvals, so this check is yours to do.

## The Big Picture

This group of files is a checkpoint for a privacy web browser called Gorilla Firefox. Before the maintainer publishes a new build, the checkpoint runs the build on this laptop, watches everything the browser does, and gives one final answer: `PASS` or `FAIL`. It looks for leaks (your private words or pages reaching the internet), telemetry (the browser reporting about you to its maker) and any other network behaviour nobody asked for.

The checkpoint is "fail closed". That means three things. First, anything the browser does that is not on an approved list is a failure. Second, an entry on the list that the maintainer has not approved is also a failure. Third, a check that did not collect any evidence is a failure, never a pass. Silence is not proof. The final answer is `PASS` only when all 18 separate rules pass.

The checkpoint does not trust a single witness. It watches the browser through up to seven independent witnesses at the same time: the browser's own logs, a decrypting proxy, the table of open network connections, the list of running programs, the files on disk, a packet capture, and a test name server that logs every name the browser looks up. One witness can be fooled or can miss something. Seven witnesses that must all agree are much harder to fool.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `Fail closed` | Anything unknown, unapproved or unmeasured counts as a failure. A check passes only when there is positive evidence that nothing unexpected happened. | A nightclub door with a guest list: if your name is not on the list, you do not get in, and if the list is missing, nobody gets in. |
| `Allowlist (`allow.json`)` | The written list of every connection, name lookup, program, file and open port the browser is allowed to produce, with a reason for each one. | The guest list itself, where every guest has a written reason for being invited. |
| `Approval` | A signature on a list entry. Only the maintainer can give it, and only by typing at a real keyboard in a real terminal window. There is exactly one piece of code in the checkpoint that can write an approval, and it checks for the real keyboard itself. No setting, switch or chat message can stand in for that check. | A cheque that needs a handwritten signature at the bank counter: anyone can fill it in, only the account holder can sign it, and the bank accepts no signature by phone. |
| `Witness (sensor)` | One independent way of watching what the browser does, for example its own logs or a packet capture. | Several security cameras pointed at the same door from different angles. |
| `Scenario` | One short, scripted thing the browser is made to do, such as sitting idle or opening a test page. | One drill in a fire safety test: each drill tests one specific thing. |
| `Canary string` | A unique made-up word, such as `GORILLA_CANARY_COOKIE_001`, planted in a test page. If that word ever appears in traffic leaving the machine, something leaked. | Marked banknotes: if one turns up somewhere it should not be, you know where it came from. |
| `Decrypting proxy (mitmproxy)` | A program that sits between the browser and the internet and opens every encrypted request so the checkpoint can read its full contents. | A post room that opens every outgoing letter, reads it, and seals it again. |
| `Disposition` | A written explanation, for one piece of the browser's source code that could use the network, of what stops it from doing so. | A note on each door of a building saying why that door is locked. |
| `Quick run and release run` | A quick run is a short practice run that can never reach `PASS`. A release run uses the full durations, repeats every test three times and needs administrator rights. | A rehearsal against the real performance. |
| `Stopping only what it started` | When the checkpoint closes a helper program it opened, it closes that one program by its own process number and nothing else. On Windows it uses the Windows command for this; on Linux it asks the program to stop, waits up to 10 seconds, and only then forces it. | A cleaner at the end of a shift who switches off the machines they switched on, and leaves every other machine in the building alone. |

## How It Works — Step by Step

### Step 1: Make three throwaway copies of the build

The checkpoint takes the finished build from its zip file and unpacks it three times into a new run folder. It never touches the browser you use every day. Copy one is the build as shipped ("direct"). Copy two is pinned to the decrypting proxy and locked so it cannot be changed ("proxied"). Copy three is pinned to a test name server and is not allowed to fall back to any other one ("dns-controlled"). Think of three identical cars, each fitted with a different measuring device.

### Step 2: Start the test servers on this machine

The checkpoint starts a small web server on `127.0.0.1`, which is an address that only exists inside your own computer. It serves the test pages byte for byte, so the test knows exactly what each page contains. It also starts four tiny secure servers for the certificate test and a test name server (DNS over HTTPS). The test name server gives real answers only for approved names and the scenario's own names. Every other name gets "no such name", and every question is written down.

Before any of this, the checkpoint makes a private security certificate for its decrypting proxy. To do that it starts the proxy program once, waits for the certificate file to appear, and then closes that one proxy program by its own process number. It does this the Windows way on Windows and the Linux way on Linux, and it never closes any other program. Think of a locksmith who cuts one key, then puts away only the tools they brought.

### Step 3: Run each scenario on a fresh profile

For each of the 13 scenarios, the checkpoint creates a brand-new, empty browser profile, starts the browser in the background (no window, except for the shutdown test), lets it run for a set time, then stops it. Afterwards it keeps watching for a set time, to catch anything that starts after the browser is gone, such as an updater or a crash reporter.

### Step 4: What each scenario would catch

Idle start (`startup-idle`, 300 seconds, 120 in a quick run): the browser does nothing, so any connection is the browser acting on its own. New tab, home, add-ons and preferences pages: these pages are where a browser usually fetches news, sponsored tiles, recommendations and add-on listings. Canary page: the test page plants 10 unique words in the address, the page title, a cookie, local storage, the browser database, a form, a search box, the history, a download name and a background beacon; any of those words seen leaving the machine fails the canary rule. Private canary: the same in a private window. Workers: the page starts a service worker, a shared worker and a plain worker, background scripts that could keep sending after the page closes. WebRTC: the page sets up a call with no outside servers, so nothing may leave the machine at all, and a raw local address (such as one starting `192.168.`) in the offered addresses fails. Real page: one real public web page, to see what else loads alongside it. Crash: the browser's own crash test page, to catch a crash reporter sending a report. Certificates: the page tries four local secure servers (valid, expired, wrong name, self-signed); if the browser accepts any of the three bad ones, the TLS rule fails. Poisoned responses (release runs only): the proxy answers every outside request with garbage, such as an error, an empty reply, broken data or 2,000,000 zero bytes; any address that appears only then is a hidden fallback and fails. Graceful shutdown: the browser opens minimised, never takes focus, and is asked to close the normal way; it must exit within 45 seconds and no build program may still be running 60 seconds later.

### Step 5: Watch with independent witnesses

While a scenario runs, the witnesses collect evidence. The browser writes its own detailed log of web requests, name lookups and connections. Every two seconds the checkpoint photographs the list of the browser's programs and its open connections. It compares the files on disk before and after (the profile plus the folders Mozilla uses for crash reports and updates). In the proxied copy, the proxy decrypts and records every request in full. In an administrator window, Windows' packet monitor (`pktmon`) records every packet. No witness judges anything at this stage; each one only writes down what it saw.

### Step 6: Check every observation against the approved list

Each observation becomes one line of evidence: which witness, which scenario, what kind of thing (a web address, a name lookup, a program, a file, an open port) and its value. The checkpoint then looks each one up in `allow.json`. The answer is one of three: allowed (an approved entry covers it), pending (an entry exists but nobody approved it) or unexpected (no entry). Only "allowed" passes. Known telemetry, experiment, remote settings and update addresses of the browser maker are flagged separately under the telemetry rule, whatever the list says.

### Step 7: Fail any rule whose witnesses did not report

Each of the 18 rules names the witnesses it needs. For example, the network rule needs the browser's own log, the proxy and the connection table, plus the packet capture if the window has administrator rights. If any needed witness produced nothing, the rule fails with the message "required sensor(s) did not collect". This is the heart of fail closed: no evidence means no pass.

### Step 8: Audit the source code and the finished files

The checkpoint also reads the browser's source code without running it. It lists every file in 25 named components (telemetry, crash reporting, updates, push messages and others) that could use the network, and checks that each one has an approved written disposition. It then reads the finished build: it pulls every web address written inside the packed browser files, lists which programs and libraries link to Windows' network libraries, and compares the Rust code libraries with the previous release. Anything new since the previous release needs an approved disposition.

### Step 9: Write the verdict and the evidence

The checkpoint prints one line per rule with `PASS` or `FAIL` and the first reason, then `FINAL_RESULT PASS` or `FINAL_RESULT FAIL` and the run folder. It saves a machine-readable result (`test-results.json`), every observation (`events.jsonl`), lists of unexpected addresses, names and ports, and a record of exactly which build, tools and list it tested. It also writes a short result file that the maintainer's publish step reads. The command ends with code 0 for `PASS` and code 3 for `FAIL`.

### Step 10: Windows today, Linux as the real referee

On this Windows laptop the test is not sealed off: other programs share the network, the name lookups go through Windows' normal name service, and there is no firewall that drops everything by default (the result file says so in `FIREWALL_CONFIGURATION`). There is also no way here to trace every system call the browser makes. The Linux runner is designed to close those gaps. It puts the browser in a sealed private network where the firewall drops every packet except those to the local test server, the proxy and the test name server, and it writes down every dropped packet as an attempted leak. Its name server answers "no such name" to everything not approved. It records packets from inside the sealed network with two separate capture tools and traces every network, program and file call with `strace`. The source states plainly that this runner was written on the Windows laptop and is untested on Linux as of 2026-10-02. Think of a new testing laboratory that is built and wired but has never had its first experiment run in it.

## Quirky Things Worth Knowing

### A quick run can never pass

The default run is a quick run. It runs every scenario once, with shorter times. The rules say a release needs at least three identical runs and full shutdown watch times, so a quick run always fails the reproducibility and shutdown rules. This is on purpose: a quick run tells you what to fix, never that you are finished.

### The first release run also fails

The regression rule compares request counts with an approved baseline from the previous release. When no baseline exists, the rule fails with "no approved baseline". The baseline can only be saved from a release run that already passed. The source does not say how the very first baseline is meant to be created; read this as an open question, not a fault you caused.

### Without administrator rights, several rules fail

Packet capture needs an administrator window. Without it, the network, name lookup, shutdown, WebRTC, IPv6 and TLS rules all fail with "packet-level sensor not run". The checkpoint does not quietly skip them.

### An automated helper can propose list entries but never approve them

The approval command checks that it is running at a real keyboard and screen. A program driving the command line (such as a coding agent) has neither, so the approval is refused. Earlier, the approval code accepted an extra setting that could tell it "a real keyboard is present" without checking. That setting no longer exists: if a program tries to pass it, Python stops with an error before anything is written. Web addresses and name lookups are never even proposed from what was seen: an unexpected address stays unexpected until the maintainer writes it in by hand.

### Name lookups on Windows are noisy

On this laptop, other programs look up names at the same time as the test browser. A lookup on the wire that no browser witness saw is reported as "unattributed" and fails only if it names the browser maker or a known advertising or tracking domain. A clean, isolated test machine would not need this exception; that is why the Linux runner is the authoritative one.

### There is one approval route, and a test keeps it that way

Earlier, the disposition file (the notes that explain why each piece of the browser's code cannot reach the network) contained a second way to record approvals, from a chat message, that skipped the real-keyboard check. It was removed together with an unused helper that generated dispositions. Now the only code in the checkpoint that can write an approval is the allowlist approval, which checks for the real keyboard itself. One of the project's tests reads every file of the checkpoint and fails if any other piece of code writes an approval or takes a setting that could stand in for the keyboard check. Think of a bank that closed its telephone signature line and put a guard on the door who checks every new counter that opens.

One consequence to know: Fieldkit now has no command that writes disposition approvals at all. The checkpoint only reads them from `dispositions.json`. How the maintainer records them there is not available in the source material.

### What this cannot do

- It cannot prove a browser is private everywhere. It proves only what its witnesses saw during its scripted scenarios, on this machine, for the scripted times. Code that runs rarely may not run during the test; the source and file audits exist to narrow that gap, not to close it.
- It cannot judge whether an approval is right. It trusts every approved entry in `allow.json` and every approval it finds in `dispositions.json` completely.
- It does not check who typed an approval. It checks only that a real keyboard and screen were attached. Anyone at the maintainer's keyboard counts as the maintainer.
- It does not protect its own files. Anyone who can change `allow.json`, `dispositions.json` or `baseline.json` in the maintainer's Gorilla Firefox folder can change the verdict. The `ALLOWLIST_SHA256` value in `build-manifest.json` lets you notice a changed list; it does not stop the change.
- On Windows it is not sealed off. A name lookup on the wire that no browser witness saw passes unless it names the browser maker or a known advertising or tracking domain.
- The Linux runner is untested. Reading its code suggests it does not yet feed the proxy, the browser's own log, the file watch or the process list into the verdict, so the first Linux run would fail several rules for missing witnesses (a reading of the code, not a measured result).
- It does not measure its own cost: speed, battery, processor and memory use are not measured.

## What This Means For You

### Battery, Processor & Memory

Not measured. The checkpoint runs the browser repeatedly, a proxy, several local servers, a PowerShell snapshot every two seconds, and (in a release run) a packet capture. The processor, memory and battery cost of this has not been measured.

### Speed

Not measured as a total. The scripted times are in the source: in a quick run, the idle scenario lasts 120 seconds and most others 30 to 60 seconds, each run in two or three copies. A release run uses 300 seconds for idle, repeats everything three times and adds the poisoned-response runs, so it takes much longer. With `--soak`, the idle scenario lasts as long as you ask (the specification suggests 1800 or 3600 seconds).

### Your Privacy

The run folder holds decrypted requests, browser logs and, in a release run, a packet capture of every program on the laptop during the test. Treat the run folder as private. The point of the checkpoint is your privacy as a future user of the browser: it is how the maintainer shows that a build does not report about you.

### Your Internet

Small, but not zero. Two scenarios load one real public web page, the test name server forwards approved names to your normal name service, and anything the browser tries on its own goes out during the direct runs unless you use `--firewall`. The amount of data used has not been measured.

## The Off Switch

**What it is:** There are three. First, the fail-closed rule: any missing evidence or unapproved entry stops a release with `FINAL_RESULT FAIL` and exit code 3. Second, the real-terminal check: list approvals and the regression baseline are refused unless a person types them at a real keyboard, and no setting can switch that check off. Third, the optional `--firewall` switch (administrator window only), which blocks the direct test copy from reaching any address outside your computer and removes the block at the end.

**Without it:** Without fail closed, a broken witness would look the same as a clean result, and a build could pass because nothing was measured. Without the real-terminal check, an automated helper could approve its own exceptions. Without `--firewall`, an unexpected connection from the direct copy does reach the internet, and the checkpoint only records it.

**Think of it like:** A bank vault that stays shut if any one of its alarms is unplugged, needs the manager's own key to add a name to the access list, and has an optional second door you can lock during the inspection.

## How to use this

**Before you start:**
- A finished Gorilla Firefox build job in the Fieldkit build harness. The checkpoint reads the build's zip file from that job.
- The maintainer's Gorilla Firefox folder with `leakgate/allow.json` (the approved list) and, ideally, `leakgate/dispositions.json`.
- `mitmproxy` installed (it provides `mitmdump`), and the Python packages `dpkt`, `cryptography` and `pefile`.
- A backup of the previous release, so the file audits have something to compare with. Without it the binary rule fails.
- For a release run: a PowerShell window opened with "Run as administrator", because the packet capture needs it.

**Step 1:** Open PowerShell. Press the Windows key, type `PowerShell`, and press Enter. A window with a blinking cursor opens.
  - You should see: - **Pass:** you see a line ending in `>` waiting for you to type.
**Step 2:** Run one short scenario first, to check that everything is installed. Type this and press Enter:

```powershell
fieldkit build-harness leakgate --only canary
```

  - You should see: - **Pass:** you see `leakgate: build ...` and lines such as `canary [direct] run 1/1: 60 s + 10 s after shutdown`, then the 18 rule lines and `FINAL_RESULT FAIL` with a folder in brackets. `FAIL` is expected here: a quick run never passes.
  - **Fail:** an error naming `mitmdump`, `dpkt` or `build-result.json` means a prerequisite is missing.
**Step 3:** Run the full quick run. It runs all 13 scenarios once:

```powershell
fieldkit build-harness leakgate
```

  - You should see: - **Pass:** every scenario prints a line, then the static source audit, binary audit and executable and dependency audits run, then the rule lines. Read the reason after each `FAIL`: these are the things to fix.
**Step 4:** If the run saw programs, files, ports or local connections that are not on the list, ask the checkpoint to write them in as unapproved proposals:

```powershell
fieldkit build-harness leakgate-propose
```

  - You should see: - **Pass:** you see `proposed N entr(ies)` and a reminder that only the maintainer can approve them. Web addresses and name lookups are never proposed this way.
**Step 5:** The maintainer, at a real keyboard, reads each proposal and approves it. With no list of names, the command approves every unapproved entry, printing each one in full first:

```powershell
fieldkit build-harness leakgate-approve
```

  - You should see: - **Pass:** each entry is printed, then `approved N: [...]`.
  - **Fail:** `approval is the owner's, at a real terminal` means the command did not run at a real keyboard (for example, an automated helper ran it). That refusal is correct.
**Step 6:** For the real verdict, open PowerShell as administrator (right-click PowerShell, choose "Run as administrator") and run the release run. Add `--firewall` if you want the direct copy blocked from the internet during the test:

```powershell
fieldkit build-harness leakgate --release
```

  - You should see: - **Pass:** the first line says `packets ON` and `repeat 3`, and the run ends with `FINAL_RESULT PASS` and exit code 0.
  - **Fail:** `packets OFF (not elevated)` means the window is not an administrator window. `FINAL_RESULT FAIL` lists the reasons per rule.
**Step 7:** After the first approved release `PASS`, the maintainer saves the request counts as the baseline for the next release:

```powershell
fieldkit build-harness leakgate-baseline
```

  - You should see: - **Pass:** `baseline saved:` followed by the file path.
  - **Fail:** `a baseline is taken only from a release-mode PASS` means the last run was quick or failed.

## If Something Goes Wrong

**`FINAL_RESULT FAIL` with `ran 1 time(s); the spec requires at least 3 identical runs`**
You ran a quick run. It always fails this rule.
What to do: Use `fieldkit build-harness leakgate --release` in an administrator window for the real verdict.

**Several rules say `packet-level sensor not run (needs an elevated shell)`**
PowerShell was not opened as administrator, so the packet capture could not start.
What to do: Close the window, right-click PowerShell, choose "Run as administrator" and run again.

**`PROXY_POLICY` fails with `proxy did not take`**
In the proxied copy, the real test page never reached the decrypting proxy, so that copy's traffic was not read.
What to do: Check that `mitmdump` starts on its own from PowerShell, and that nothing else blocks the local proxy port. Then run again.

**`REGRESSION_POLICY` fails with `no approved baseline`**
No baseline from a previous release exists yet.
What to do: This clears only after a release `PASS` and `leakgate-baseline`. The source does not describe how the first baseline is created.

**`BINARY_POLICY` fails with `no previous release to diff against`**
The checkpoint found no backup of an earlier installed version to compare the files with.
What to do: Make sure the previous release was installed through the harness, which keeps a backup.

**`approval is the owner's, at a real terminal; an agent's shell has none`**
The approval command was not run by a person at a real keyboard.
What to do: The maintainer runs `fieldkit build-harness leakgate-approve` in a normal PowerShell window.

**A Windows Firewall rule named `leakgate-` and a date stays after a crash**
The run stopped before it could remove its own `--firewall` rule.
What to do: In an administrator PowerShell window, run `Remove-NetFirewallRule -DisplayName 'leakgate-<the date in the name>'`.

## Why a Developer Would Do This

A privacy browser is only as private as its least-watched corner. Mozilla's browser has many parts that can reach the network: telemetry, crash reports, experiments, remote settings, updates, push messages, safe browsing lists and more. Removing them by hand is error-prone, and the measurements show why: Gorilla 155.0.1 passed one earlier check but contacted 8 Mozilla hosts on its own.

The developer made the checkpoint fail closed so that a forgotten check cannot pass by silence. They used several witnesses so that one blind spot does not hide a leak. They kept approval with the maintainer so that a helper program cannot excuse its own findings. And they audit the source and the finished files as well as the running browser, because some code only runs rarely and a short test would miss it.

## Why It Matters That You Can Read This

A privacy browser asks you to believe a negative: that it does not send your data anywhere. You cannot see a negative. Without this checkpoint you would be trusting a statement such as "we removed telemetry". With it, the claim becomes a list you can read: every address, name, program, file and port the browser may produce, each with a reason, a privacy impact and an approval date, plus the evidence from seven witnesses for each test.

Because the checkpoint itself is readable, you can also check the checker. You can see that a missing witness counts as a failure, that approvals need a real keyboard, and which parts are untested (the Linux runner). A closed tool that printed only "PASS" would ask for the same blind trust the browser does. Real examples from the measurements show why this matters: an earlier check that read the name lookup cache passed Gorilla 155.0.1, but the browser's own request log showed it contacting 8 Mozilla hosts on its own. One witness missed what another caught.

## Glossary

**Allowlist** — The list of things the browser is allowed to do, each with a reason and an approval.

**Canary** — A unique made-up word planted in a test page so that any leak of it can be spotted.

**Certificate** — A digital identity card a secure website shows to prove who it is.

**DNS** — The internet's phone book, which turns a name such as a website address into a number.

**DNS over HTTPS (DoH)** — Looking up names through an encrypted web connection instead of in plain text.

**Elevated shell** — A PowerShell window opened with "Run as administrator".

**ICE candidate** — An address a browser offers during a WebRTC call setup, which can reveal your local network address.

**Network namespace** — On Linux, a sealed-off private network for one program, with its own firewall.

**Packet capture** — A recording of every piece of data that passes through the network card.

**Profile** — The folder where a browser keeps your settings, history and cookies; the checkpoint uses a new empty one each time.

**Proxy** — A go-between program that every web request passes through.

**Service worker** — A small script a website can leave running in the browser in the background.

**Telemetry** — Usage reports a program sends back to its maker.

**WebRTC** — The browser feature for live video and voice calls, which can reveal network addresses.

**Process number (PID)** — The number Windows or Linux gives each running program, so one program can be closed without touching the others.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| A missing witness makes a rule fail, never pass | 📄 stated in input | A policy whose required sensors did not collect -> FAIL (not collected), never PASS by absence. |
| Only the maintainer at a real terminal can approve list entries | 📄 stated in input | Approval is the owner's alone, given at a real terminal (an agent's shell has none) |
| Destinations and name lookups are never proposed from observation | 📄 stated in input | destinations and DNS names are never proposed from observation |
| The Linux runner has not been run on Linux | 📄 stated in input | UNTESTED ON LINUX AS OF 2026-10-02 |
| The Linux runner is the authoritative environment | 📄 stated in input | The Linux network-namespace runner (linux.py) is the authoritative environment. |
| Unattributed wire DNS fails only for vendor or tracker names on Windows | 📄 stated in input | it fails only when it names a vendor or tracker domain |
| A release run needs packet capture and three repetitions | 📄 stated in input | A release run (--release) requires the packet sensor (elevated shell) and 3 repetitions. |
| Gorilla 155.0.1 passed a DNS-cache check but contacted 8 Mozilla hosts | 📄 stated in input | Gorilla 155.0.1 asked 8 Mozilla hosts for things on its own |
| The source inventory covers 103 files in 25 components with one maintainer decision | 📄 stated in input | 103 network-capable source files in 25 components, each with a disposition; 1 left as an owner decision (OCSP) |
| A quick run can never reach PASS | 🤖 model inference | *(none — model judgment)* |
| The first release run fails the regression rule until a baseline exists | 🤖 model inference | *(none — model judgment)* |
| A hard kill can leave the firewall rule behind | 🤖 model inference | *(none — model judgment)* |
| The Windows packet capture records traffic from every program | 🤖 model inference | *(none — model judgment)* |
| Several independent witnesses are harder to fool than one | 🤖 model inference | *(none — model judgment)* |
| No parameter can stand in for the terminal check on approval | 📄 stated in input | There is no parameter that stands in for the terminal check |
| The chat approval route for dispositions was removed | 📄 stated in input | a chat route (`approve_from_chat`) that skipped the terminal check was removed with the unused `build()` |
| The certificate step closes only the process it started, the Linux way on Linux | 📄 stated in input | taskkill exists only on Windows: the Linux runner terminates its own child and kills it if it lingers |
| A test fails if any other approval route appears | 🤖 model inference | *(none — model judgment)* |
| Fieldkit has no command that writes disposition approvals | 🤖 model inference | *(none — model judgment)* |
| The first Linux run would fail several rules for missing witnesses | 🤖 model inference | *(none — model judgment)* |
| Anyone who can write the maintainer's leakgate files can change the verdict | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*