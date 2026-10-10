"""The leak gate's run, scene by scene, in plain words: the section every Gorilla release page carries.

    fieldkit build-harness leakgate-summary <task> [log=<launcher log>] [out=<file.md>]

Born 2026-10-10. The owner, after asking why the gate takes four hours and reading the breakdown: "make sure that the
table with the added layman explanation makes it on the release page every time we release a new version and that
the user knows the extreme length of the tests we are running". The table is built from the run's own log (each
"scene [setup] run i/3: N s + M s after shutdown" line), never typed: what a scene checks comes from PLAIN below,
and a scene without a plain-words line stops the summary (a new scene must be explained before it can be published).
releasecover.py requires HEADING on every release page.
"""
import collections
import json
import re
from pathlib import Path

HEADING = "## 🕵️ The leak test, scene by scene"
LINE = re.compile(r"^\s*(\S+) \[(\S+)\] run (\d+)/(\d+): (\d+) s \+ (\d+) s after shutdown")
PLAIN = {
    "startup-idle": "The browser is started and left alone, nothing clicked, so anything it sends on its own shows.",
    "newtab": "A new tab is opened: the page that, in ordinary Firefox, fetches news stories and sponsored tiles.",
    "home": "The home page is opened.",
    "addons": "The add-ons page is opened: in ordinary Firefox it asks Mozilla for recommendations.",
    "preferences": "Settings is opened.",
    "canary": ("A local test page full of planted marker words is visited and typed into; the marker words must never "
               "appear in anything that leaves the computer."),
    "canary-private": "The same marker test in a private window.",
    "workers": "A page starts background workers, the hidden helpers pages use to fetch things on their own.",
    "webrtc": "A page tries a video-call connection to find your computer's local network address.",
    "page": "A real web page on the internet is opened: only that page's own servers may be contacted.",
    "crash": "A tab is crashed on purpose: a crash report must not be sent anywhere.",
    "certs": "Pages with good and bad security certificates: checking them must not ask anyone outside.",
    "certerror-toplevel": "A 'this site may be an impostor' warning page is opened.",
    "download-exe": "A program file is downloaded: no outside 'is this file safe?' lookup may be made.",
    "lan-probe": "A website tries to knock on devices on your home network (router, printer); it must not get through.",
    "drm-request": ("A page asks for protected video: the only allowed contact is Google's video plug-in download "
                    "(the one recorded compromise, decision D-157-12)."),
    "h264-call": "A video call starts: the only allowed contact is Cisco's video plug-in download (decision D-157-12).",
    "profile-idle-actions": "Settings' privacy page and the welcome page are opened and left: nothing may be sent.",
    "early-hints": "A server hints at other servers before the page arrives; the browser must not contact them.",
    "shutdown-graceful": "The browser is closed the normal way: nothing may be sent while it shuts down.",
}
SETUPS = {
    "direct": "the computer's ordinary connection",
    "proxied": "every connection forced through a recording proxy that decrypts and logs it",
    "dns-controlled": "name lookups answered by the test itself, so a hidden lookup cannot escape",
    "poisoned": "name lookups answered with wrong addresses, to catch a browser that tries another way out",
}


def read(log):
    """Launcher log (UTF-16 or UTF-8) -> text."""
    b = Path(log).read_bytes()
    if b[:2] == b"\xff\xfe" or b[1:2] == b"\x00":
        return b.decode("utf-16-le", "replace").lstrip("﻿")
    return b.decode("utf-8", "replace").lstrip("﻿")


def scenes(text):
    """-> OrderedDict {scene: {"setups": [..], "runs", "seconds"}} from the log's run lines."""
    out = collections.OrderedDict()
    for l in text.replace("\r", "").split("\n"):
        m = LINE.match(l)
        if not m:
            continue
        s = out.setdefault(m.group(1), {"setups": [], "runs": 0, "seconds": 0, "repeat": int(m.group(4))})
        if m.group(2) not in s["setups"]:
            s["setups"].append(m.group(2))
        s["runs"] += 1
        s["seconds"] += int(m.group(5)) + int(m.group(6))
    return out


def render(sc, result=None, build=None):
    """-> (markdown, missing): the release page section; `missing` names scenes without a plain-words line."""
    missing = [n for n in sc if n not in PLAIN]
    total = sum(v["seconds"] for v in sc.values())
    runs = sum(v["runs"] for v in sc.values())
    hours, mins = divmod(round(total / 60), 60)
    verdict = ""
    if result:
        ok = result.get("exit_code") == 0
        verdict = (f"\n**Result on this build{f' ({build})' if build else ''}: {'PASS' if ok else 'FAIL'}**"
                   f" (the whole run took {result.get('minutes', '?')} minutes, "
                   f"{str(result.get('started', '')).replace('T', ' ')[:16]} to "
                   f"{str(result.get('ended', '')).replace('T', ' ')[11:16]}).\n")
    lines = [HEADING, "",
             f"Before a Gorilla release is published, its browser goes through **{len(sc)} scenes, {runs} browser runs, "
             f"about {hours} hours {mins} minutes of watched browsing**. In every run the browser is started fresh, used "
             "the way the scene says, closed, and watched a little longer; meanwhile several independent witnesses "
             "record everything: the browser's own network log, a recording proxy, the list of open connections, every "
             "program started and every file written, and a packet capture of the network card itself. A single "
             "connection nobody can explain fails the whole test.", "",
             "Why so long: each scene runs **three times** (something that phones home only now and then must be "
             "caught), and the scenes where it matters run in up to **four network setups**:", ""]
    lines += [f"- **{k}**: {v}." for k, v in SETUPS.items()]
    lines += ["", "| Scene | What it checks, in plain words | Setups | Runs | Minutes |", "|---|---|---|---|---|"]
    for n, v in sc.items():
        lines.append(f"| `{n}` | {PLAIN.get(n, '(not explained)')} | {len(v['setups'])} | {v['runs']} | "
                     f"{round(v['seconds'] / 60, 1)} |")
    lines.append(f"| **Total** | | | **{runs}** | **{round(total / 60)}** ({hours} h {mins} min) |")
    lines.append(verdict)
    return "\n".join(lines).rstrip() + "\n", missing


def run(log, result_file=None, build=None):
    """-> {"markdown", "missing", "scenes"}."""
    sc = scenes(read(log))
    res = None
    if result_file and Path(result_file).is_file():
        res = json.loads(Path(result_file).read_text(encoding="utf-8-sig"))
    md, missing = render(sc, res, build)
    return {"markdown": md, "missing": missing, "scenes": sc}
