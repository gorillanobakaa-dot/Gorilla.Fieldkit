"""`fieldkit pycache ACTION ...`: Python compiles once and never again on the hot path (plan item P9, idea I-17).

Each verb module has `main(argv) -> int` and no import-time work. Exit: 0 fine, 1 error, 3 findings. Every output
ends with a `NEXT:` line.  A diagnostic probe an agent writes twice becomes a verb here (docs/METHODS.md).
"""
ACTIONS = {
    "audit": "audit",        # fresh/stale/missing .pyc, the hot-path modules, the .pth, import cost cold vs warm
    "warm": "warm",          # compile into the cache, idempotent; --install-pth keeps writing on despite the env
    "profile": "profile",    # where the time of MODULE[:FUNC] goes (cProfile / -X importtime)
    "compare": "compare",    # the hook-timing test in the main checkout vs a fresh worktree
}


def run(action, argv):
    import importlib
    return importlib.import_module(f"{__name__}.{ACTIONS[action]}").main(list(argv))
