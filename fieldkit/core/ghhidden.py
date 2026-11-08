"""gh hidden by the autopilot run: fieldkit/autopilot/run.py gives a headless job no GitHub credential (GH_CONFIG_DIR
points at an empty folder, FIELDKIT_AUTOPILOT=1). Anything that reads gh status there says so, never "no login"."""
import os

MESSAGE = "gh hidden by the autopilot run"


def gh_hidden(env=None):
    """True inside an autopilot run (the run hid gh's account on purpose)."""
    return (env if env is not None else os.environ).get("FIELDKIT_AUTOPILOT") == "1"


def explain(reason, env=None):
    """MESSAGE when gh is hidden, else `reason` (what gh or the lookup said)."""
    return MESSAGE if gh_hidden(env) else reason
