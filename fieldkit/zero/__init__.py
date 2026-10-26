# Version: 1.0.0 · updated 26-10-26
"""The net: "Zero has been established" - one net control station for every agent session, check-ins on a lease,
roll calls with a fixed sitrep, clock checks, an append-only net log, handover of Zero with a fencing epoch.

    fieldkit zero ...   ==   fieldkit net ... (the zero verbs; `fieldkit net route|compare|...` stay the network tool)

net.py holds the state and the verbs, channels.py how a message reaches a station, clock.py the time check,
schedule.py the periodic comms check, command.py the orders, drift, idle and the wake (set 2), claims.py the
ground-claim refusal (PreToolUse). Read net.py's docstring for the military-to-IT glossary.
"""


def main(*a, **k):
    """Lazy: claims.py runs inside the PreToolUse hook and must not load net.py (YAML) on every tool call."""
    from .net import main as m
    return m(*a, **k)
