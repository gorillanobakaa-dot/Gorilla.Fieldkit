"""fieldkit peers link check: the Windows lessons of 2026-10-22 as prechecks, so the next session does not find them
again by trial and error. Each check is read-only, says OK / PROBLEM, and gives the exact fix.

    fieldkit peers link check                     clock, firewall, neighbor, put (the link's own state)
    fieldkit peers link check clock               the two clocks: the link refuses requests more than 120 s apart
    fieldkit peers link check firewall            the cable rule exists (else: setup, one UAC click via elevate)
    fieldkit peers link check neighbor            a stale 00-00-00-00-00-00 entry for the peer after a cable move
    fieldkit peers link check put                 where received files land, and that the folder is writable
    fieldkit peers link check shell --shell bash|powershell "COMMAND"
                                                  Git Bash eating backslashes; Windows PowerShell 5.1 refusing &&
    fieldkit peers link check crlf [FILE ...]     CRLF written by Python on Windows (default: the staged files)
    fieldkit peers link check commit              privacy + CRLF on the staged files BEFORE the 16-minute commit hook
    fieldkit peers link check write FILE          a file too big for one write (long writes get a session cut off)
    fieldkit peers link check symptom "WORDS"     what you see -> the check (or tool) that diagnoses it

Exit 0 all OK, 3 a PROBLEM, 2 bad usage. Own machines, defensive: nothing here changes the system.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import link

OK, PROBLEM = "OK", "PROBLEM"


def row(name, ok, detail, fix=""):
    return {"check": name, "verdict": OK if ok else PROBLEM, "detail": detail, "fix": "" if ok else fix}


# ---- clock --------------------------------------------------------------------------------------------------------
# 2026-10-22: the hello was stored 9 s before the VAIO's own job time. The Verifier refuses a request whose stamp is
# more than link.SKEW (120 s) from this clock, so a drifting clock silently breaks every verb. Warn at half of it.
def clock(call_fn=None, now=time.time):
    call_fn = call_fn or link.call
    t0 = now()
    r = call_fn({"verb": "hello"})
    t1 = now()
    if not r.get("ok"):
        return row("clock", False, f"the peer did not answer: {r.get('error')}",
                   "fieldkit peers link doctor, then fieldkit peers link check firewall")
    if not isinstance(r.get("time"), (int, float)):
        return row("clock", False, "the peer's hello carries no time (older link code there)",
                   "update the peer's link code (fieldkit sync), then run this again")
    skew = r["time"] - (t0 + t1) / 2
    detail = f"peer clock {skew:+.1f} s from this one (round trip {t1 - t0:.2f} s; the link refuses > {link.SKEW} s)"
    fix = ("Windows: w32tm /resync (admin, through the elevate kit); Linux: timedatectl set-ntp true. "
           "No internet on the cable: set both from the same clock by hand")
    return row("clock", abs(skew) < link.SKEW / 2, detail, fix)


# ---- firewall -----------------------------------------------------------------------------------------------------
# 2026-10-22: the rule is made by `setup` with one UAC click through the elevate kit (local/kits/Elevate); a session
# that runs an admin PowerShell itself either fails (not elevated) or makes the owner paste commands.
def firewall(platform=sys.platform, exists_fn=None):
    if platform != "win32":
        return row("firewall", True, "not Windows: setup opens the Linux firewall (pkexec) itself")
    from . import link_setup
    ok = (exists_fn or link_setup.windows_rule_exists)()
    return row("firewall", ok, f"Windows firewall rule '{link_setup.RULE}' " + ("present" if ok else "missing"),
               "fieldkit peers link setup --first   (one UAC click through the elevate kit; never an admin "
               "PowerShell typed by a model)")


# ---- neighbor -----------------------------------------------------------------------------------------------------
# 2026-10-22: after the cable moved to another port Windows kept the peer as 00-00-00-00-00-00 (Unreachable) and
# every connect timed out although the peer was up. The entry has to go (admin).
ZERO_MAC = re.compile(r"^\s*(\d+\.\d+\.\d+\.\d+)\s+(00-00-00-00-00-00)\b", re.M)


def neighbor(peer=None, platform=sys.platform, run=subprocess.run):
    peer = peer or (link.config() or {}).get("peer")
    if not peer:
        return row("neighbor", False, "no paired peer on this computer", "fieldkit peers link setup")
    if platform != "win32":
        return row("neighbor", True, "not Windows (Linux: ip neigh flush to <peer> if it shows FAILED)")
    r = run(["arp", "-a"], capture_output=True, text=True)
    stale = [ip for ip, _ in ZERO_MAC.findall(r.stdout or "") if ip == peer]
    return row("neighbor", not stale, f"{peer}: " + ("stale 00-00-00-00-00-00 entry" if stale else "no stale entry"),
               f"arp -d {peer}   (admin: put it in a .ps1 and run it through the elevate kit), then "
               "fieldkit peers link hello")


# ---- put ----------------------------------------------------------------------------------------------------------
# 2026-10-22: a sent file was looked for on the Desktop; it lands in ~/.fieldkit/link/received/ (the Desktop only
# when link.yaml approves accept_to_desktop), and on this laptop the Desktop is not always %USERPROFILE%\Desktop.
def put(home_fn=None):
    d = (home_fn or link.home)() / "received"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".probe-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        return row("put", False, f"{d} is not writable: {e}", "fix the folder's permissions; nothing is received")
    reg, why = link.approvals()
    desk = "also the Desktop" if reg.get("accept_to_desktop") is True else f"not the Desktop ({why})"
    return row("put", True, f"received files land in {d}; {desk}")


# ---- shell --------------------------------------------------------------------------------------------------------
# 2026-10-22: a relay brief told Windows to run  cd C:\Users\...\Fieldkit && python ...  - Git Bash read the unquoted
# backslashes as escapes (cd to C:Users..., exit 127) and Windows PowerShell 5.1 refuses && outright. A heredoc
# without a quoted delimiter also eats backslashes in Bash.
def shell(cmd, target):
    found = []
    if target == "powershell":
        if re.search(r"&&|\|\|", cmd):
            found.append("Windows PowerShell 5.1 refuses && and ||: put each command on its own line, or use ; "
                         "with if ($?) { ... }")
        if re.search(r"(^|[;\s])export\s+\w+=|(^|\s)~/", cmd):
            found.append("bash syntax in a PowerShell line (export X=, ~/): use $env:X = ... and $HOME")
    elif target == "bash":
        for tok in re.findall(r"(?:^|\s)([^\s'\"]*\\[^\s'\"]*)", cmd):
            if re.match(r"[A-Za-z]:\\|\\\\|.*\\[A-Za-z]", tok):
                found.append(f"unquoted backslash path {tok!r}: Git Bash eats the backslashes; quote it or use "
                             "forward slashes (C:/Users/...)")
        if re.search(r"<<-?\s*[A-Za-z_]+\b", cmd) and "\\" in cmd:
            found.append("heredoc with an unquoted delimiter and backslashes: write <<'EOF', or write the file with "
                         "a file tool")
    else:
        return row("shell", False, f"unknown shell {target!r}", "--shell bash or --shell powershell")
    return row("shell", not found, "; ".join(found) or f"nothing {target} would misread", "rewrite as said")


# ---- crlf ---------------------------------------------------------------------------------------------------------
# 2026-10-22: Python's text mode writes CRLF on Windows. git normalizes tracked files (eol=lf), but a script carried
# on the stick (setup-linux.sh: "/bin/sh^M: bad interpreter") or a file checked by SHA256SUMS breaks. Write with
# newline="" or "\n". Windows-only scripts may keep CRLF.
CRLF_OK = (".ps1", ".bat", ".cmd")


def staged(run=subprocess.run, cwd="."):
    r = run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"], capture_output=True, text=True, cwd=cwd)
    return [Path(cwd) / f for f in (r.stdout or "").split("\n") if f.strip()]


def crlf(files):
    bad = []
    for f in map(Path, files):
        if f.suffix.lower() in CRLF_OK or not f.is_file():
            continue
        data = f.read_bytes()
        if b"\0" not in data[:8192] and b"\r\n" in data:
            bad.append(str(f))
    return row("crlf", not bad, f"CRLF in {', '.join(bad)}" if bad else f"{len(files)} file(s), no CRLF",
               "rewrite each with open(path, 'w', newline='\\n') (or Path.write_bytes); git add again")


# ---- commit -------------------------------------------------------------------------------------------------------
# 2026-10-22: the commit hook runs the whole suite (about 16 minutes) and only then the privacy scan; a finding threw
# the 16 minutes away. The fast checks go first.
def commit(run=subprocess.run, cwd="."):
    rows = [crlf(staged(run, cwd))]
    r = run([sys.executable, "-m", "fieldkit", "privacy", "scan", ".", "--git"], capture_output=True, text=True,
            cwd=cwd)
    last = ((r.stdout or "").strip().splitlines() or [""])[-1]
    rows.append(row("privacy", r.returncode == 0, last or f"privacy scan exit {r.returncode}",
                    "remove each finding (fieldkit privacy scan . --git lists them), then commit"))
    return rows


# ---- write --------------------------------------------------------------------------------------------------------
# 2026-10-22: sessions that wrote long files in one call were cut off mid-write by an automated safety classifier;
# the half-written file was lost with the session. Write in parts and say plainly what the code is for.
WRITE_LINES, WRITE_BYTES = 200, 12000


def write(path):
    p = Path(path)
    if not p.is_file():
        return row("write", False, f"{p} is not a file", "give the file to be written (a draft)")
    data = p.read_bytes()
    n = data.count(b"\n")
    ok = n <= WRITE_LINES and len(data) <= WRITE_BYTES
    return row("write", ok, f"{p.name}: {n} lines, {len(data)} bytes (one write: <= {WRITE_LINES} lines, "
               f"<= {WRITE_BYTES} bytes)", "write it in parts (a first Write, then Edits that add a section each), "
               "and say in the file's first lines what it is for")


# ---- symptom ------------------------------------------------------------------------------------------------------
# What a session SEES -> the check that diagnoses it. Data, tested: every example must find its own entry.
SYMPTOMS = [
    {"id": "stale-request", "words": r"stale|clock|skew|timestamp|too old", "check": "clock",
     "example": "the peer answers REFUSED: stale request"},
    {"id": "firewall", "words": r"firewall|access denied|admin|elevat|uac", "check": "firewall",
     "example": "New-NetFirewallRule: Access is denied"},
    {"id": "neighbor", "words": r"00-00-00-00-00-00|unreachable|cable mov|timed? ?out", "check": "neighbor",
     "example": "hello timed out after the cable was moved to the other port"},
    {"id": "put", "words": r"\bput\b|received|sent file|not on the desktop", "check": "put",
     "example": "the file I put is not on the Desktop"},
    {"id": "bash-backslash", "words": r"exit 127|backslash|no such file or directory: c:users|cd: c:", "check": "shell",
     "example": "bash: cd: C:UsersgorillaDocuments: No such file or directory, exit 127"},
    {"id": "powershell-and", "words": r"&&|not a valid statement separator", "check": "shell",
     "example": "The token '&&' is not a valid statement separator in this version"},
    {"id": "crlf", "words": r"\^m|crlf|bad interpreter|\\r", "check": "crlf",
     "example": "/bin/sh^M: bad interpreter"},
    {"id": "hook-privacy", "words": r"privacy|commit hook|16 min", "check": "commit",
     "example": "the commit hook failed on a privacy finding after 16 minutes"},
    {"id": "cut-off", "words": r"cut off|classifier|mid-write|half.written", "check": "write",
     "example": "the session was cut off mid-write by a safety classifier"},
    {"id": "publish-new-branch", "words": r"publish.*(refus|no upstream)|no upstream|never pushed", "check": None,
     "example": "fieldkit publish refused: the branch has no upstream",
     "tool": "fixed in 1f48c1c (feat/long-horizon): publish compares a never-pushed branch with origin's default "
             "branch and pushes it with -u. Until that is merged, run fieldkit publish from a checkout that has it"},
]


def symptom(words):
    w = words.lower()
    hits = [s for s in SYMPTOMS if re.search(s["words"], w)]
    if not hits:
        return row("symptom", False, f"no recorded symptom matches {words!r}",
                   "diagnose it, build the check, add the entry to SYMPTOMS (link_checks.py)")
    return row("symptom", True, " | ".join(f"{s['id']}: " + (f"fieldkit peers link check {s['check']}"
                                                              if s["check"] else s["tool"]) for s in hits))


# ---- main ---------------------------------------------------------------------------------------------------------
def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit peers link check", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", nargs="?", default="link",
                    choices=["link", "clock", "firewall", "neighbor", "put", "shell", "crlf", "commit", "write",
                             "symptom"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--shell", choices=["bash", "powershell"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    one = {"clock": clock, "firewall": firewall, "neighbor": neighbor, "put": put}
    if a.what == "link":
        rows = [f() for f in one.values()]
    elif a.what in one:
        rows = [one[a.what]()]
    elif a.what == "crlf":
        rows = [crlf(a.rest or staged())]
    elif a.what == "commit":
        rows = commit()
    elif a.what in ("shell", "write", "symptom") and not a.rest or a.what == "shell" and not a.shell:
        ap.print_usage()
        return 2
    elif a.what == "shell":
        rows = [shell(" ".join(a.rest), a.shell)]
    elif a.what == "write":
        rows = [write(a.rest[0])]
    else:
        rows = [symptom(" ".join(a.rest))]
    bad = [r for r in rows if r["verdict"] != OK]
    if a.json:
        print(json.dumps({"ok": not bad, "rows": rows}, indent=1, ensure_ascii=False))
    else:
        for r in rows:
            print(f"{r['verdict']:8} {r['check']:9} {r['detail']}" + (f"\n         FIX: {r['fix']}" if r["fix"] else ""))
        print(f"VERDICT: {'PROBLEM' if bad else 'OK'}\nNEXT: " + (bad[0]["fix"] if bad else "nothing to fix"))
    return 3 if bad else 0
