"""The ONE preflight before heavy work (a build, a compile, a test run). Every guard calls run(); every check that must
happen before heavy work is a step in STEPS. Owner, 2026-10-26: the hog and the test-cost planner both run here, so
there is one wrapper, not one per tool.

    from fieldkit.core import preflight
    preflight.run(say, "firefox build")       fieldkit guard / buildguard.guard and fieldkit thermal monitor call it

A step is "module:function" taking (say, label) and returning anything; a step that fails says so and the next one
runs (preflight informs; the fan check and the governor are what refuse a build). Skipped inside pytest and with
FIELDKIT_PREFLIGHT=0.
"""
import importlib
import os

#: in order. fieldkit.hog: unload idle local models, stop dead sessions' runners, before the RAM is needed.
STEPS = ["fieldkit.hog:preflight"]


def run(say=print, label="heavy work", steps=None):
    if os.environ.get("FIELDKIT_PREFLIGHT") == "0" or (os.environ.get("PYTEST_CURRENT_TEST") and steps is None):
        return {}
    out = {}
    for s in steps or STEPS:
        mod, _, fn = s.partition(":")
        try:
            out[s] = getattr(importlib.import_module(mod), fn)(say, label)
        except Exception as e:
            say(f"  preflight {s}: not run ({str(e)[:120]})")
            out[s] = None
    return out
