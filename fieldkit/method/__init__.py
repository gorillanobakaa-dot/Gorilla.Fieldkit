"""Methods turned into tools (docs/METHODS.md): how the work gets done, the same way every time.

    fieldkit wait       --until CMD      wait on a condition, never on a clock
    fieldkit mutate     FILE --swap A B -- TEST     prove a test can fail
    fieldkit regress    record|compare   compare test failures by name, before and after a change
    fieldkit drift      --gen CMD FILE   does a committed generated file still match its generator? (never rewrites)
    fieldkit mcp-probe  -- SERVER...     speak MCP to a server the way a client does: handshake, tools, size, one call

Each module has main(argv) -> exit code: 0 fine, 2 bad input, 3 the method found something (a mutation survived,
new failures, drift, a timeout, a server that does not speak the protocol).
"""
import json
import shlex
import sys


def split_command(text, platform=None):
    """A command line given as one string -> argv.

    POSIX quoting on Linux. On Windows, backslashes in paths must survive, so the non-POSIX split is
    used - and it keeps the quotes ON the words ('"raise SystemExit(4)"'), which a program then receives literally:
    the surrounding pair of quotes is taken off each word (found in CI: python -c "..." ran a string, not the code).
    """
    if (platform or sys.platform) != "win32":
        return shlex.split(text)
    out = []
    for w in shlex.split(text, posix=False):
        if len(w) >= 2 and w[0] == w[-1] and w[0] in "\"'":
            w = w[1:-1]
        out.append(w)
    return out


def emit(data, as_json, lines):
    if as_json:
        print(json.dumps(data, indent=1, ensure_ascii=False, default=str))
    else:
        for line in lines(data):
            print(line)
        if data.get("next"):
            print(f"NEXT: {data['next']}")
