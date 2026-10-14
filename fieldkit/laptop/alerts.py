"""Voice alerts when an AI assistant needs the person: it finished, it is waiting for an approval, or it hit an error.

    fieldkit laptop alerts say done|focus|look      hear the three sentences (a test)
    fieldkit laptop alerts hook                     Claude Code's Stop and Notification hooks call this (JSON on stdin)
    fieldkit laptop alerts watch                    the watcher for Antigravity's transcripts (Gemini, Claude or any
                                                    model inside it); runs until stopped
    fieldkit laptop alerts install [--claude-code] [--antigravity] [--apply]
                                                    show (or, with --apply, make) what starts them

Born from the academic harness this generalises (voice_daemon.py and claude_voice_hook.py, 2026-10-08): a person who
looks away while an agent works should not come back an hour later to find it was waiting for one click. When it
speaks:
  * the agent ends its turn: the sentence written in the reply when there is one ("the task is done", "I need you to
    focus here", "I need you to have a look at this"), otherwise "have a look at this", so a model that forgets the
    phrase never leaves the person waiting in silence, and "done" is never claimed on the model's behalf;
  * a tool call has had no answer for APPROVAL_WAIT seconds (the agent waits for an approval);
  * the agent logs an error (quota reached, API failure): at most once per ERROR_QUIET.

The voice is the herald's (offline: Windows' own speech, espeak-ng on Linux). The words start with the name set in
fieldkit.local.json ({"alerts": {"name": "..."}}), or with nothing.
"""
import glob
import json
import os
import sys
import time
from pathlib import Path

from ..core import settings

POLL, SETTLE, APPROVAL_WAIT, ERROR_QUIET, ACTIVE_FOR = 0.5, 4, 20, 600, 6 * 3600
PHRASES = {"done": "the task is done", "focus": "I need you to focus here", "look": "I need you to have a look at this"}
TRIGGERS = [("the task is done", "done"), ("have a look at this", "look"), ("focus here", "focus")]
ANTIGRAVITY = Path.home() / ".gemini" / "antigravity" / "brain"


def phrases(local=None):
    """The three sentences, the owner's where set: fieldkit.local.json {"alerts": {"phrases": {"look": "..."}}}.
    2026-10-14, the owner, on hearing "I need you to have a look at this": "way too polite ... a sure way to make
    someone look" is his own line. The words are his, per machine; the shipped ones stay plain."""
    local = settings.local_settings() if local is None else local
    own = ((local.get("alerts") or {}).get("phrases") or {}) if isinstance(local, dict) else {}
    return {k: str(own.get(k) or v) for k, v in PHRASES.items()}


def sentence(kind, name=None, local=None):
    local = settings.local_settings() if local is None else local
    name = name if name is not None else (local.get("alerts") or {}).get("name", "")
    s = phrases(local)[kind]
    return f"{name}, {s}." if name else s[0].upper() + s[1:] + "."


def kind_in(text):
    low = (text or "").lower()
    return next((k for pattern, k in TRIGGERS if pattern in low), None)


def _speak(text, say=None):
    if say is None:
        from ..buildh import herald
        say = herald.say
    return say(text)


# -- Claude Code hooks -------------------------------------------------------------------------------------------
def last_assistant_text(payload):
    text = payload.get("last_assistant_message")
    if isinstance(text, str) and text.strip():
        return text
    path = payload.get("transcript_path")
    if not path or not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        rows = f.readlines()
    for line in reversed(rows):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "assistant":
            continue
        content = (e.get("message") or {}).get("content")
        parts = [content] if isinstance(content, str) else [c.get("text", "") for c in content or []
                                                              if isinstance(c, dict) and c.get("type") == "text"]
        joined = "\n".join(p for p in parts if p)
        if joined.strip():
            return joined
    return ""


def hook(payload, say=None, name=None):
    """-> the kind spoken, or None. Stop: the reply's own phrase, else 'look'. Notification: a permission prompt or a
    question for the person is 'look'; anything else is not spoken."""
    event = payload.get("hook_event_name", "")
    if event == "Stop":
        if payload.get("stop_hook_active"):
            return None
        k = kind_in(last_assistant_text(payload)) or "look"
    elif event == "Notification" and payload.get("notification_type") in ("permission_prompt", "elicitation_dialog"):
        k = "look"
    else:
        return None
    _speak(sentence(k, name), say)
    return k


# -- Antigravity transcripts -----------------------------------------------------------------------------------------
class Transcript:
    """One transcript, read incrementally, never held open."""

    def __init__(self, path, from_start):
        self.path = path
        self.offset = 0 if from_start else os.path.getsize(path)
        self.buffer = b""
        self.pending = None           # (kind, due) for a final reply
        self.tool_wait = None         # due time for an unanswered tool call

    def read_steps(self):
        size = os.path.getsize(self.path)
        if size < self.offset:
            self.offset, self.buffer = 0, b""
        if size == self.offset:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            self.buffer += f.read(size - self.offset)
        self.offset = size
        steps = []
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                steps.append(json.loads(line.decode("utf-8")))
            except ValueError:
                continue
        return steps


class Watcher:
    def __init__(self, brain=None, say=None, name=None, clock=time.time):
        self.brain = Path(brain or ANTIGRAVITY)
        self.files, self.clock, self.say, self.name = {}, clock, say, name
        self.started = clock()
        self.last_error = -1e9
        self.spoken = []

    def _say(self, kind):
        self.spoken.append(kind)
        _speak(sentence(kind, self.name), self.say)

    def discover(self):
        now = self.clock()
        for path in glob.glob(str(self.brain / "*" / ".system_generated" / "logs" / "transcript.jsonl")):
            if path in self.files:
                continue
            try:
                mtime, ctime = os.path.getmtime(path), os.path.getctime(path)
            except OSError:
                continue
            if now - mtime > ACTIVE_FOR:
                continue
            self.files[path] = Transcript(path, from_start=ctime >= self.started)   # a new conversation: from line 1
        for path in [p for p in self.files if not os.path.exists(p)]:
            del self.files[path]

    def handle(self, t, step):
        source, kind, now = step.get("source"), step.get("type"), self.clock()
        if source == "SYSTEM" and kind == "ERROR_MESSAGE":
            if now - self.last_error > ERROR_QUIET:
                self.last_error = now
                self._say("focus")
            return
        if source == "USER_EXPLICIT":
            t.pending = t.tool_wait = None
            return
        if source != "MODEL":
            return
        t.pending = t.tool_wait = None                    # the agent kept working
        if kind != "PLANNER_RESPONSE":
            return
        if step.get("tool_calls"):
            t.tool_wait = now + APPROVAL_WAIT
            return
        if step.get("content"):
            t.pending = (kind_in(step["content"]) or "look", now + SETTLE)

    def tick(self):
        self.discover()
        now = self.clock()
        for t in self.files.values():
            try:
                for step in t.read_steps():
                    self.handle(t, step)
            except OSError:
                continue
            if t.pending and now >= t.pending[1]:
                k, t.pending = t.pending[0], None
                self._say(k)
            if t.tool_wait and now >= t.tool_wait:
                t.tool_wait = None
                self._say("look")


def watch(brain=None, sleep=time.sleep, ticks=None):
    w = Watcher(brain)
    n = 0
    while ticks is None or n < ticks:
        try:
            w.tick()
        except Exception:                                 # one bad file never stops the watcher
            sleep(2)
        sleep(POLL)
        n += 1
    return w


# -- what starts them ------------------------------------------------------------------------------------------------
def claude_hooks(python=None):
    cmd = f'"{python or sys.executable}" -m fieldkit laptop alerts hook'
    entry = [{"hooks": [{"type": "command", "command": cmd}]}]
    return {"Stop": entry, "Notification": entry}


def install_claude(settings_path=None, apply=False, python=None):
    """Merge the two hooks into ~/.claude/settings.json; the file is copied to .bak first, every other setting kept."""
    p = Path(settings_path or Path.home() / ".claude" / "settings.json")
    data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    hooks = data.setdefault("hooks", {})
    want = claude_hooks(python)
    changed = []
    for event, entry in want.items():
        cmds = [h.get("command") for e in hooks.get(event, []) for h in e.get("hooks", [])]
        if entry[0]["hooks"][0]["command"] not in cmds:
            hooks.setdefault(event, []).extend(entry)
            changed.append(event)
    if apply and changed:
        if p.is_file():
            p.with_suffix(".json.bak").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"settings": str(p), "adds": changed, "written": bool(apply and changed), "hooks": want}


def autostart_entry(platform=None, home=None, python=None):
    """Where the Antigravity watcher is started at login: the Startup folder (Windows; a scheduled task cannot see
    the desktop session's files and registry, measured 2026-10-09) or ~/.config/autostart (Linux)."""
    home = Path(home or Path.home())
    py = python or sys.executable
    if (platform or sys.platform) == "win32":
        start = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "Microsoft" / "Windows" / \
            "Start Menu" / "Programs" / "Startup" / "Fieldkit AI alerts.lnk"
        pyw = str(Path(py).with_name("pythonw.exe")) if Path(py).with_name("pythonw.exe").is_file() else py
        return start, pyw
    return home / ".config" / "autostart" / "fieldkit-ai-alerts.desktop", py


def install_antigravity(apply=False, platform=None, home=None, run=None):
    import subprocess
    run = run or subprocess.run
    path, py = autostart_entry(platform, home)
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".desktop":
            path.write_text("[Desktop Entry]\nType=Application\nName=Fieldkit AI alerts\n"
                            f"Exec={py} -m fieldkit laptop alerts watch\nPath={settings.ROOT}\nX-GNOME-Autostart-enabled=true\n",
                            encoding="utf-8")
        else:
            q = lambda s: "'" + str(s).replace("'", "''") + "'"
            run(["powershell", "-NoProfile", "-Command",
                 f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(path)}); $s.TargetPath={q(py)}; "
                 f"$s.Arguments='-m fieldkit laptop alerts watch'; $s.WorkingDirectory={q(settings.ROOT)}; "
                 "$s.WindowStyle=7; $s.Save()"], capture_output=True)
    return {"autostart": str(path), "written": bool(apply) and path.is_file() if path.suffix == ".desktop" else bool(apply)}
