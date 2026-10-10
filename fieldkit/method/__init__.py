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


def split_command(text):
    """A command line given as one string -> argv (POSIX quoting; Windows paths keep their backslashes)."""
    return shlex.split(text, posix=sys.platform != "win32")


def emit(data, as_json, lines):
    if as_json:
        print(json.dumps(data, indent=1, ensure_ascii=False, default=str))
    else:
        for line in lines(data):
            print(line)
        if data.get("next"):
            print(f"NEXT: {data['next']}")
