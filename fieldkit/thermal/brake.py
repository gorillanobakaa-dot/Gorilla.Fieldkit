"""Heavy-job holds and the lease of the one machine-wide governor. No power-plan or fan levers.

2026-10-28 (owner, after three cutoffs in 28 h): the guard no longer writes PERFBOOSTMODE, PROCTHROTTLEMAX or
guard-hold.txt. 439 and 408 powercfg scheme resets were logged on 10-27 and 10-28 (7-85 a day before); the
cutoffs of 10-27 ~04:18, 10-27 ~17:10 and 10-28 ~04:11 all came during them, the 10-28 one at 32 C and 13% load.
Heavy work goes to the VAIO; on this laptop a hold only registers the job. What follows is the 10-27 history.

Crash 2026-10-27 ~17:10 (the second that day): every `fieldkit guard` command ran at 100% with turbo on. The guard
printed "cap held at 80%" for a surface sensor but never passed it to its governor, a new governor started and
stopped per command, and nothing braked load outside the guard. Owner, 2026-10-27: "apply the 80% surface ceiling in
fieldkit guard, turbo off while a guard holds, and run fieldkit thermal watch --target 67 as the always-on
machine-wide governor started at logon."

    hold(label)          a guarded job is running: turbo off for as long as any hold is alive (the last one out puts
                         the original boost mode back); the machine governor sees the hold and lowers its ceiling
    holds()              the live holds (a hold whose process is gone is removed)
    machine_alive()      the machine governor's lease is fresh and its process runs
    restore_stale_boost  a crash left turbo off with no live hold: put the recorded original back

Turbo is the power plan's PERFBOOSTMODE (powercfg, no elevation, the same lever family as PROCTHROTTLEMAX). Without a
die sensor it is the one brake that does not wait for a lagging reading: it removes the 4.7 GHz bursts at the source.
Windows only; elsewhere boost_set returns False and nothing is claimed.

The fan: while a hold lives, %ProgramData%/TPFanControl/guard-hold.txt exists and Gorilla TPFanControl (Compile and
Gorilla mode, gorilla.5) runs the fan at full speed for the whole job instead of waiting for the lagging EC reading to
rise. A stale flag (a crash) only keeps the fan fast; the next guard or the machine governor removes it.
"""
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

BOOST_OFF = 0
LEASE_S = 15.0           # the machine governor rewrites its lease every 3 s


def _dir():
    from ..core import settings
    return settings.state_root() / "state" / "thermal"


def fan_flag_path():
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "TPFanControl" / "guard-hold.txt"


def fan_flag(on, label=""):
    """Tell the fan control a guarded job runs (full speed) or none does. -> True when the file is as asked."""
    f = fan_flag_path()
    try:
        if on:
            if not f.parent.is_dir():
                return False                                  # no fan control here: nothing to tell
            f.write_text(f"{os.getpid()} {label}\n", encoding="ascii", errors="replace")
        elif f.exists():
            f.unlink()
        return True
    except OSError:
        return False


def _powercfg(*args):
    r = subprocess.run(["powercfg", *args], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), text=True, errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def boost_read():
    """The AC boost mode index (0 off, 1 on, 2 aggressive, ...) or None."""
    if sys.platform != "win32":
        return None
    rc, out = _powercfg("/qh", "scheme_current", "sub_processor", "PERFBOOSTMODE")
    for line in out.splitlines():
        if "Current AC Power Setting Index" in line:
            return int(line.split(":")[-1].strip(), 16)
    return None


def boost_set(mode):
    if sys.platform != "win32":
        return False
    for rail in ("/setacvalueindex", "/setdcvalueindex"):
        _powercfg(rail, "scheme_current", "sub_processor", "PERFBOOSTMODE", str(int(mode)))
    rc, _ = _powercfg("/setactive", "scheme_current")
    return rc == 0


def _alive(pid):
    from ..peers.peers import pid_alive
    return pid_alive(int(pid))


def holds(folder=None, alive=None):
    alive = alive or _alive
    d = Path(folder or _dir()) / "holds"
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            h = json.loads(f.read_text(encoding="utf-8"))
            ok = alive(h["pid"])
        except (OSError, ValueError, KeyError):
            ok, h = False, None
        if ok:
            out.append(h)
        else:
            try:
                f.unlink()
            except OSError:
                pass
    return out


def restore_stale_boost(folder=None, read=None, write=None, alive=None, say=print):
    """No live hold but a boost marker: a crashed guard left turbo off. -> the restored mode or None."""
    write = write or boost_set
    m = Path(folder or _dir()) / "boost-original.json"
    if holds(folder, alive):
        return None
    fan_flag(False)                                           # no job holds: the fan control may slow down again
    if not m.exists():
        return None
    try:
        orig = int(json.loads(m.read_text(encoding="utf-8"))["original"])
    except (OSError, ValueError, KeyError):
        orig = 2
    write(orig)
    try:
        m.unlink()
    except OSError:
        pass
    say(f"  turbo: no guarded job holds it; boost mode restored to {orig}")
    return orig


@contextmanager
def hold(label, say=print, folder=None, read=None, write=None, alive=None, pid=None):
    """Register a guarded job (its file only; no power-plan or fan change). Yields the hold dict."""
    read, write = read or boost_read, write or boost_set
    d = Path(folder or _dir())
    (d / "holds").mkdir(parents=True, exist_ok=True)
    pid = pid or os.getpid()
    me = d / "holds" / f"{pid}.json"
    h = {"pid": pid, "label": label, "since": time.time()}
    me.write_text(json.dumps(h), encoding="utf-8")
    # 2026-10-28: no turbo switch, no fan flag (see the module text). A marker or flag an older guard left is still
    # put back once, when no hold lives.
    try:
        yield h
    finally:
        try:
            me.unlink()
        except OSError:
            pass
        if not [x for x in holds(d, alive) if x.get("pid") != pid]:
            restore_stale_boost(d, read, write, alive, say)


def lease_write(folder=None, pid=None, now=None):
    d = Path(folder or _dir())
    d.mkdir(parents=True, exist_ok=True)
    (d / "machine-governor.json").write_text(json.dumps({"pid": pid or os.getpid(), "t": now or time.time()}),
                                             encoding="utf-8")


def lease_clear(folder=None, pid=None):
    f = Path(folder or _dir()) / "machine-governor.json"
    try:
        if json.loads(f.read_text(encoding="utf-8")).get("pid") == (pid or os.getpid()):
            f.unlink()
    except (OSError, ValueError):
        pass


def machine_alive(folder=None, now=None, alive=None):
    """True while `fieldkit thermal watch --forever` owns the processor cap."""
    try:
        m = json.loads((Path(folder or _dir()) / "machine-governor.json").read_text(encoding="utf-8"))
        return (now or time.time()) - float(m["t"]) < LEASE_S and (alive or _alive)(m["pid"])
    except (OSError, ValueError, KeyError):
        return False
