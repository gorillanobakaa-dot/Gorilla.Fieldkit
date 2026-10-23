# Version: 1.5.0 · updated 26-10-23-17-55
"""macdrive.py - drive a Mac's screen from another computer, the way a person would, and check each step took.

    fieldkit mac keep-awake NAME [--minutes N | --stop]    caffeinate on the Mac for a long job (display, disk, network)
    fieldkit mac open-app NAME APP [--folder DIR] [--process P]
                                                           open APP; answer its "choose folder" dialog with DIR; close
                                                           only the idle Terminal windows that appeared with it; report
                                                           the process, its tty and its working folder
    fieldkit mac screen-text NAME --tty TTY [--lines N]    the text in the Terminal tab on that tty (no picture, no OCR)
    fieldkit mac keys NAME --tty TTY (--key enter|esc|up|down|tab|LETTER | --paste FILE)
                                                           raise THAT tab's window, then type; a multi-line FILE is sent
                                                           as one line so Enter does not split it; exit 3 when the
                                                           screen text did not change (the key did not land)
    fieldkit mac tui-start NAME --tty TTY [--seconds S]  walk Gorilla OpenCode's start-up screens (folder, provider,
                                                           speed) to the prompt; a key only on a screen it identified
    fieldkit mac keys ... --expect REGEX                   type only if REGEX is on the screen first
    fieldkit mac wait-text NAME --tty TTY --for REGEX [--seconds S]
                                                           wait until the tab shows REGEX (a dialog, a prompt, READY)
    fieldkit mac watch-dialogs NAME --tty TTY [--answer allow|session|deny] [--until-file PATH] [--log FILE]
                                                           stay with a running Gorilla OpenCode: every "Permission
                                                           Required" dialog is read (command, why it asked), answered
                                                           with one key, counted and logged, until PATH exists on the
                                                           Mac or --seconds run out; ends with the count

Born 2026-10-23, the night the harness met its first Mac (the owner's 2011 MacBook Air, macOS 10.13, 4 GB): the Linux
Claude drove Gorilla OpenCode on the Air by hand to run a stranger's install test, and the owner said every one of
those operations becomes a tool. What was learned, each now a rule here:
  - the Air's Wi-Fi dropped SSH for 10-40 s at a time, awake and on mains (5 times in an hour): every remote step
    is retried, and a drop is told apart from a refusal
  - the Air first fell asleep mid-test (mDNS still answered through Apple's sleep proxy, ping did not): keep-awake
  - "tell Terminal to activate" brings Terminal's OWN front window, not the one wanted: twice a paste landed in an
    idle bash window, which ran it as a command. The window is found by the tab's tty and raised AFTER activate, in
    the same script as the keystroke
  - macOS opens an extra empty Terminal window when Terminal starts: open-app closes the idle windows that were not
    there before (never one that was, never a busy one)
  - a wait that timed out did not stop the typing after it: a test prompt went into the "somewhere else" folder
    field of the picker (2026-10-23, 02:47). keys --expect and tui-start refuse to type into a screen not identified
  - a key can be "sent" and not arrive (the window moved under the owner's hand): keys reads the screen text before
    and after, and says so when nothing changed
  - Terminal's "contents" clashes with AppleScript's own word; "history" of the tab is the text, and it showed the
    real cause of a missing letter (a row padded to the last column, so the next character wrapped off screen)
System Events keystrokes over SSH worked on 10.13 without an Accessibility prompt (SIP was off on that Mac).
"""
import re
import shlex
import subprocess
import time
from pathlib import Path

DROP = ("Connection timed out", "Connection closed", "Connection reset", "No route to host", "Network is unreachable",
        "ssh_dispatch_run_fatal", "Operation timed out", "lost connection")
KEYS = {"enter": "code:36", "esc": "code:53", "up": "code:126", "down": "code:125", "tab": "code:48"}

TAB_SCRIPT = '''on run argv
	set theTTY to item 1 of argv
	tell application "Terminal"
		repeat with w in windows
			repeat with t in tabs of w
				if tty of t is theTTY then return history of t
			end repeat
		end repeat
	end tell
	return "NO TAB ON " & theTTY
end run'''

KEY_SCRIPT = '''on run argv
	set theTTY to item 1 of argv
	set theAction to item 2 of argv
	tell application "Terminal"
		activate
		delay 0.8
		set found to false
		repeat with w in windows
			repeat with t in tabs of w
				if tty of t is theTTY then
					set selected of t to true
					set index of w to 1
					set frontmost of w to true
					set wantName to name of w
					set found to true
					exit repeat
				end if
			end repeat
			if found then exit repeat
		end repeat
	end tell
	if not found then return "NO TAB ON " & theTTY
	delay 0.8
	tell application "System Events" to set frontmost of process "Terminal" to true
	delay 0.4
	-- the window that TAKES the keys is the frontmost one, not Terminal's index 1 (10.13: a window moved to index 1
	-- stayed grey while the bash window behind it kept the keys). System Events may not read windows (no assistive
	-- access), so Terminal's own frontmost property is the check.
	tell application "Terminal"
		set keyName to ""
		repeat with w2 in windows
			if frontmost of w2 then set keyName to name of w2
		end repeat
	end tell
	if keyName is not wantName then return "FRONT IS " & keyName
	tell application "System Events"
		if theAction is "paste" then
			keystroke "v" using {command down}
			delay 1.5
			key code 36
		else if theAction starts with "code:" then
			key code ((text 6 thru -1 of theAction) as integer)
		else
			keystroke theAction
		end if
	end tell
	return "SENT"
end run'''

WINDOWS_SCRIPT = '''tell application "Terminal"
	set out to ""
	repeat with w in windows
		set t to selected tab of w
		set out to out & (id of w as string) & tab & (tty of t) & tab & (busy of t as string) & linefeed
	end repeat
	return out
end tell'''

FOLDER_DIALOG = ('tell application "System Events"\n set frontmost of (first process whose name is "osascript") to true\n'
                 ' delay 0.5\n keystroke "g" using {command down, shift down}\n delay 1\n keystroke "%s"\n delay 0.7\n'
                 ' key code 36\n delay 1.2\n key code 36\nend tell')


def remote(ssh_argv_fn, line, stdin=None, tries=5, wait=6, timeout=60, runner=None, sleep=time.sleep):
    """-> (rc, output, attempts). A Wi-Fi drop (ssh 255 with a connection error) is retried; anything else returns."""
    out = ""
    for attempt in range(1, tries + 1):
        if runner:
            rc, out = runner(ssh_argv_fn(line), stdin)
        else:
            try:
                r = subprocess.run(ssh_argv_fn(line), input=stdin, capture_output=True, text=True, timeout=timeout)
                rc, out = r.returncode, (r.stdout + r.stderr).strip()
            except subprocess.TimeoutExpired:
                rc, out = 255, "Operation timed out (local timeout)"
        if not (rc == 255 and any(d in out for d in DROP)):
            return rc, out, attempt
        if attempt < tries:
            sleep(wait)
    return rc, f"{out}\n(the Mac dropped off the network {tries} times: Wi-Fi; try again, or move it nearer the router)", tries


def osa(ssh_argv_fn, script, args=(), **kw):
    """Run an AppleScript on the Mac from stdin (no quoting through two shells)."""
    return remote(ssh_argv_fn, "osascript - " + " ".join(shlex.quote(a) for a in args), stdin=script, **kw)


def screen_text(ssh_argv_fn, tty, lines=40, **kw):
    rc, out, _ = osa(ssh_argv_fn, TAB_SCRIPT, [tty], **kw)
    if rc != 0 or out.startswith("NO TAB ON"):
        return None, out
    rows = [r.rstrip() for r in out.splitlines() if r.strip()]
    return rows[-lines:], ""


def one_line(text):
    """A multi-line prompt pasted into a TUI would be sent at the first newline: send it as one line."""
    return re.sub(r"\s+", " ", text).strip()


def keys(ssh_argv_fn, tty, key=None, paste_text=None, settle=4, sleep=time.sleep, expect=None, **kw):
    """-> (code, message). 0 sent and the screen changed, 3 sent but nothing changed (or refused: the expected
    screen is not there), 1 failed.

    expect: a regex that must be on the screen BEFORE anything is typed. 2026-10-23: a wait timed out, the loop typed
    anyway, and a test prompt landed in Gorilla OpenCode's "somewhere else" folder field. Never type into a screen
    that was not identified."""
    before, err = screen_text(ssh_argv_fn, tty, 60, **kw)
    if before is None:
        return 1, err
    if expect and not any(re.search(expect, r, re.I) for r in before[-40:]):
        return 3, f"refused: '{expect}' is not on the screen, so nothing was typed (wrong screen?)"
    if paste_text is not None:
        rc, out, _ = remote(ssh_argv_fn, "pbcopy", stdin=one_line(paste_text), **kw)
        if rc != 0:
            return 1, f"pbcopy failed: {out}"
        action = "paste"
    else:
        k = (key or "").lower()
        action = k if len(k) == 1 else KEYS.get(k)
        if not action:
            return 2, f"unknown key {key!r} (enter, esc, up, down, tab, or one letter)"
    rc, out, _ = osa(ssh_argv_fn, KEY_SCRIPT, [tty, action], **kw)
    if out.startswith("FRONT IS "):
        return 3, (f"refused: the window in front is {out[9:]}, not {tty}, so nothing was typed "
                   f"(2026-10-23: three pastes went into another window)")
    if rc != 0 or out != "SENT":
        return 1, out
    sleep(settle)
    after, err = screen_text(ssh_argv_fn, tty, 400 if paste_text is not None else 60, **kw)
    if paste_text is not None:
        # "the screen changed" is not proof for a paste: on 2026-10-23 a quota line vanished while the paste itself was
        # swallowed by a TUI still redrawing. The proof is the start of the text on the screen.
        # ...and it must be NEW text near the prompt: "continue" was "found" in a code listing higher up while all three
        # pastes had gone into another window (2026-10-23 17:18), so only rows that were not there before count
        probe = one_line(paste_text)[:40]
        old = set(before)
        if not any(probe in r for r in (after or [])[-15:] if r not in old):
            return 3, f"pasted, but '{probe}' is not on the screen: the program did not take it (still starting?)"
        return 0, "pasted; the text is on the screen"
    if after == before:
        return 3, "sent, but the screen text did not change: the key may not have landed (a moved window?)"
    return 0, "sent; the screen changed"


def wait_text(ssh_argv_fn, tty, pattern, seconds=120, every=5, now=time.time, sleep=time.sleep, **kw):
    end = now() + seconds
    rx = re.compile(pattern, re.I)
    while True:
        rows, err = screen_text(ssh_argv_fn, tty, 60, **kw)
        hit = next((r for r in reversed(rows or []) if rx.search(r)), None)
        if hit:
            return 0, hit
        if now() >= end:
            return 3, f"not seen in {seconds:g} s: {pattern}"
        sleep(every)


# Gorilla OpenCode's start-up screens, as data: what identifies each one, and the key that accepts its default.
# The folder picker is accepted only when its cursor is on the first entry (the folder it was started in): a cursor
# on "somewhere else" means typing goes into a path field.
TUI_SCREENS = [
    ("folder", r"Which folder are you working in", None),
    ("provider", r"Choose a provider for this session", "enter"),
    ("speed", r"How fast is your connection", "enter"),
]
# the prompt is ready: the old status line ("model ... | in FOLDER") or, from the layout fixes on, only the help bar:
# "ctrl+? help" in the fix builds, "? help" from v0.1.150 (2026-10-23, live on the Air: tui-start timed out at the prompt)
TUI_READY = r"^model .+\|\s+in \S+|^\s*(?:ctrl\+)?\? help\b"


def _last(rows, pattern):
    """Index of the last row matching pattern, or -1. The tab's text keeps OLD screens above the current one
    (2026-10-23: the provider screen was 'seen' again after Enter and got a second Enter)."""
    return max((i for i, r in enumerate(rows) if re.search(pattern, r)), default=-1)


# A Google sign-in started by the provider screen finishes on its own when the Mac's browser is already signed in
# (2026-10-23: ~60 s, then the model list refresh); until "Signed in" shows, the start-up is waiting, not stuck.
SIGN_IN_START = r"Starting Google sign-in"
SIGN_IN_DONE = r"^Signed in as |^Ready\. Plan:"


def tui_start(ssh_argv_fn, tty, seconds=180, every=4, sign_in_seconds=180, now=time.time, sleep=time.sleep, **kw):
    """-> (code, lines). Walks the start-up screens to the prompt, pressing a key ONLY on a screen it has
    identified; 0 at the prompt, 3 stuck or timed out (the lines say on what). A Google sign-in in progress
    extends the wait once by sign_in_seconds and, if it never finishes, is named as the reason (a person must
    finish it in the browser), never typed into."""
    lines, end, last, extended = [], now() + seconds, None, False
    rows = None
    while now() < end:
        rows, err = screen_text(ssh_argv_fn, tty, 40, **kw)
        if rows is None:
            lines.append(f"could not read the screen ({err[:80]}); retrying")
            sleep(every)
            continue
        tail = rows[-30:]
        if _last(rows, SIGN_IN_START) > _last(rows, SIGN_IN_DONE):
            if not extended:
                end, extended = max(end, now() + sign_in_seconds), True
                lines.append("Google sign-in started: waiting for it (a browser already signed in finishes alone)")
            sleep(every)
            continue
        heads = {name: _last(tail, pat) for name, pat, _ in TUI_SCREENS}
        newest = max(heads.values())
        if _last(tail, TUI_READY) > newest:
            return 0, lines + ["at the prompt: ready for text"]
        if newest < 0:
            sleep(every)
            continue
        name = max(heads, key=heads.get)                     # the screen whose title is lowest is the current one
        key = dict((n, k) for n, _, k in TUI_SCREENS)[name]
        if name == "folder":
            first = next((r for r in tail if re.match(r"^[\s│|]*(>\s*)?1\.\s", r)), "")
            key = "enter" if re.match(r"^[\s│|]*>\s*1\.", first) else "up"
        if (name, key) == last and key == "up":
            return 3, lines + ["the folder picker's cursor does not move to entry 1: stopped, nothing typed"]
        code, msg = keys(ssh_argv_fn, tty, key, sleep=sleep, **kw)
        lines.append(f"{name}: {key} ({msg})")
        last = (name, key)
        sleep(every)
    if rows and _last(rows, SIGN_IN_START) > _last(rows, SIGN_IN_DONE):
        return 3, lines + ["stuck at the Google sign-in: a person must finish it in the browser on the Mac"]
    return 3, lines + [f"not at the prompt within {seconds:g} s"]


# Gorilla OpenCode's permission dialog, as data. It is current only while its buttons row sits among the last rows
# of the tab, above the footer (prompt, status lines): the tab's text keeps every OLD dialog above.
DIALOG_TITLE = r"\bPermission Required\b"
DIALOG_BUTTONS = r"Allow \(a\).*Deny \(d\)"
DIALOG_KEYS = {"allow": "a", "session": "s", "deny": "d"}


def _cell(row):
    return re.sub(r"^[\s|│┃]*|[\s|│┃]*$", "", row)


def current_dialog(rows, tail=12):
    """-> {"command", "why", "tool"} when a permission dialog is on screen NOW, else None."""
    if not rows:
        return None
    last = _last(rows[-tail:], DIALOG_BUTTONS)
    if last < 0:
        return None
    end = len(rows) - tail + last if len(rows) > tail else last
    top = max((i for i in range(end + 1) if re.search(DIALOG_TITLE, rows[i])), default=-1)
    if top < 0:
        return None
    box = [_cell(r) for r in rows[top:end + 1]]
    command, why, tool = "", [], ""
    for i, r in enumerate(box):
        if r == "Command" and i + 1 < len(box):
            command = box[i + 1]
        elif r.startswith("Tool:"):
            tool = r
        elif command and r and r != command and not r.startswith(("Allow", "Command", "Permission")) and not tool:
            why.append(r)
    return {"command": command, "why": " ".join(why), "tool": tool}


# A turn that ended in a provider error leaves the run idle at the prompt (2026-10-23 16:37: "Antigravity: Internal
# error encountered (HTTP 500)", then 40 minutes of nothing while the watcher waited for dialogs only).
TURN_ERROR = r"\(error\)\s*$"
TOOL_ROW = r"^[┃│|]\s*(Bash|Fetch|View|Write|Edit|Read|Grep|Find|Patch):"


def ended_in_error(rows, tail=12):
    """The error's identity (its timestamp row) when the newest event on screen is a turn that ended in an error
    (nothing ran after it), else None. The identity tells a NEW error from the old one still on screen."""
    if not rows:
        return None
    recent = rows[-tail:]
    err = _last(recent, TURN_ERROR)
    if err < 0 or err <= _last(recent, TOOL_ROW):
        return None
    return recent[err - 1].strip() if err > 0 else "error"


def _log(log, entry):
    if log:
        import json
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def watch_dialogs(ssh_argv_fn, tty, answer="allow", until_file=None, seconds=3600, every=15, log=None,
                  nudges=2, idle_seconds=900, now=time.time, sleep=time.sleep, **kw):
    """-> (code, lines). Stays with a running Gorilla OpenCode and answers each permission dialog with ONE key,
    counting and logging what it asked and why; 0 when until_file exists on the Mac, 3 when time ran out.
    A turn that ended in a provider error gets "continue" typed, at most `nudges` times (then it stops and says so);
    a screen that has not changed for idle_seconds stops the watch with the reason, instead of waiting it out.

    Born 2026-10-23: during the stranger's install test on the Air the Linux Claude waited only for REPORT.txt, and
    Gemini sat behind an unanswered dialog until the owner noticed ("Gemini awaits for you to click accept"). An
    earlier dialog went unanswered and was denied. Watching a run means watching its dialogs too."""
    key = DIALOG_KEYS.get(answer)
    if not key:
        return 2, [f"unknown answer {answer!r} (allow, session, deny)"]
    seen, lines, end = [], [], now() + seconds
    pushed, last_rows, changed_at, nudged = 0, None, now(), None
    while True:
        rows, err = screen_text(ssh_argv_fn, tty, 60, **kw)
        if rows is not None and rows != last_rows:
            last_rows, changed_at = rows, now()
        d = current_dialog(rows) if rows else None
        failed = None if d else ended_in_error(rows)
        if failed and failed != nudged:
            if pushed >= nudges:
                return 3, lines + [f"the model ended in an error again after {pushed} 'continue'; stopped "
                                   f"({len(seen)} dialog(s) answered)"]
            code, msg = keys(ssh_argv_fn, tty, paste_text="continue", sleep=sleep, expect=r"\? help", **kw)
            if code != 0:          # not typed (wrong window in front, screen not identified): say so and stop
                return 3, lines + [f"turn ended in an error, but 'continue' could not be typed: {msg}"]
            pushed, nudged = pushed + 1, failed
            why = next((r.strip() for r in reversed(rows[-12:]) if re.search(r"error", r, re.I)
                        and not re.search(TURN_ERROR, r)), "an error")
            lines.append(f"turn ended in an error ({why[:120]}): typed 'continue' ({pushed} of {nudges}; {msg})")
            _log(log, {"at": time.strftime("%H:%M:%S"), "event": "continue after error", "why": why, "n": pushed})
            continue
        if d:
            code, msg = keys(ssh_argv_fn, tty, key, sleep=sleep, expect=DIALOG_TITLE, **kw)
            entry = {"n": len(seen) + 1, "at": time.strftime("%H:%M:%S"), "answer": answer, "sent": code == 0, **d}
            seen.append(entry)
            line = f"dialog {entry['n']} {entry['at']}: {answer} -> {d['command'][:100]} ({d['why'][:160]})"
            if code != 0:
                line += f" [key not confirmed: {msg}]"
            lines.append(line)
            _log(log, entry)
            continue
        if until_file:
            rc, _, _ = remote(ssh_argv_fn, "test -f " + shlex.quote(until_file), tries=2, **kw)
            if rc == 0:
                return 0, lines + [f"{until_file} exists; {len(seen)} dialog(s) answered"]
        if now() >= end:
            return 3, lines + [f"stopped after {seconds:g} s; {len(seen)} dialog(s) answered"]
        if now() - changed_at >= idle_seconds:
            return 3, lines + [f"the screen has not changed for {idle_seconds:g} s and no report: the run is idle "
                               f"or stuck ({len(seen)} dialog(s) answered)"]
        sleep(every)


def windows(ssh_argv_fn, **kw):
    """-> list of (id, tty, busy) for every Terminal window."""
    rc, out, _ = osa(ssh_argv_fn, WINDOWS_SCRIPT, **kw)
    rows = []
    for line in out.splitlines() if rc == 0 else []:
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit():
            rows.append((int(parts[0]), parts[1], parts[2] == "true"))
    return rows


def extra_windows(before, after, keep_tty):
    """Idle windows that appeared with the app and are not its own: the ones safe to close."""
    old = {w[0] for w in before}
    return [w for w in after if w[0] not in old and not w[2] and w[1] != keep_tty]


def open_app(ssh_argv_fn, app, folder=None, process=None, wait=30, sleep=time.sleep, **kw):
    """-> (code, lines). Opens APP, answers a choose-folder dialog with FOLDER, closes only new idle windows."""
    lines = []
    before = windows(ssh_argv_fn, **kw)
    target = '"$HOME"/' + shlex.quote(app[2:]) if app.startswith("~/") else shlex.quote(app)   # the MAC's home
    rc, out, _ = remote(ssh_argv_fn, f"open -a {target}", **kw)
    if rc != 0:
        return 1, [f"could not open {app}: {out}"]
    if folder:
        line = (f"for i in $(seq 1 {wait}); do pgrep -f 'choose folder' >/dev/null && break; sleep 1; done; "
                "pgrep -f 'choose folder' >/dev/null && echo DIALOG || echo NODIALOG")
        rc, out, _ = remote(ssh_argv_fn, line, timeout=wait + 30, **kw)
        if "DIALOG" in out and "NODIALOG" not in out:
            osa(ssh_argv_fn, FOLDER_DIALOG % folder.replace('"', ''), **kw)
            lines.append(f"answered the folder dialog: {folder}")
        else:
            lines.append("no folder dialog appeared (the app may not ask)")
    pid = tty = cwd = None
    if process:
        line = (f"for i in $(seq 1 {wait}); do P=$(pgrep -x {shlex.quote(process)} | head -1); [ -n \"$P\" ] && break; "
                "sleep 1; done; [ -n \"$P\" ] && echo \"$P /dev/$(ps -o tty= -p $P | tr -d ' ') "
                "$(lsof -a -p $P -d cwd -Fn | grep ^n | cut -c2-)\"")
        rc, out, _ = remote(ssh_argv_fn, line, timeout=wait + 30, **kw)
        parts = out.split(" ", 2) if rc == 0 and out else []
        if len(parts) == 3:
            pid, tty, cwd = parts
            lines.append(f"{process}: PID {pid}, tty {tty}, working folder {cwd}")
        else:
            return 3, lines + [f"{process} did not start within {wait} s"]
    sleep(1)
    after = windows(ssh_argv_fn, **kw)
    for wid, wtty, _ in extra_windows(before, after, tty):
        osa(ssh_argv_fn, f'tell application "Terminal" to close (every window whose id is {wid})', **kw)
        lines.append(f"closed the extra idle Terminal window {wid} ({wtty}) that opened with the app")
    if folder and cwd and Path(cwd).name != Path(folder.rstrip("/")).name:
        return 3, lines + [f"it runs in {cwd}, not in {folder}"]
    return 0, lines + ([f"NEXT: fieldkit mac screen-text NAME --tty {tty}"] if tty else [])


def keep_awake(ssh_argv_fn, minutes=120, stop=False, **kw):
    pidf = "/tmp/fieldkit-keep-awake.pid"
    if stop:
        line = f"[ -f {pidf} ] && kill $(cat {pidf}) 2>/dev/null; rm -f {pidf}; echo stopped"
    else:
        line = (f"[ -f {pidf} ] && kill $(cat {pidf}) 2>/dev/null; nohup caffeinate -dimsu -t {int(minutes * 60)} "
                f">/dev/null 2>&1 & echo $! > {pidf}; echo \"awake for {minutes:g} min (caffeinate PID $(cat {pidf}))\"")
    rc, out, _ = remote(ssh_argv_fn, line, **kw)
    return (0 if rc == 0 else 1), out
