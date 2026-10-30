"""A peer that is switched off is "off", not "lost" (C4, docs/findings/c4-link-drops.yaml).

On 2026-10-27 the Linux laptop was shut down on purpose and the heal step retried it every 15 minutes for 10.5 hours
(42 runs, none could help). A peer that answers nothing on any rung (no ping, no neighbor entry) is recorded here; the
comms check then reports it as off and does not run the ladder again until the peer answers a hello, which clears it.

    off.json in the link home: {"DEVICE": {"since": "YYYY-MM-DD HH:MM:SS", "why": "..."}}
"""
import json
import time

from . import link


def _path():
    return link.home() / "off.json"


def load():
    p = _path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}                       # a damaged marker means "not known to be off": the ladder runs again


def _save(d):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1), encoding="utf-8")


def is_off(device):
    return device in load()


def mark(device, why):
    d = load()
    if device not in d:
        d[device] = {"since": time.strftime("%Y-%m-%d %H:%M:%S"), "why": why[:300]}
        _save(d)
    return d[device]


def clear(device):
    d = load()
    if d.pop(device, None) is not None:
        _save(d)
        return True
    return False


def looks_off(layer):
    """The broken layer text from link_heal.verdict: nothing answers, so the device is down, asleep or unplugged."""
    return "is down or asleep" in (layer or "")
