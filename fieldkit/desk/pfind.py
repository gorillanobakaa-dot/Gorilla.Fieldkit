"""Fieldkit's pfind launcher. Only installation and updates prepare code."""
import marshal
import os
import sys
import time
from types import CodeType

from .. import compileonce
from ..core import settings


def main(argv=None):
    started = time.perf_counter()
    source = settings.ROOT / compileonce.PFIND_FILE
    if not source.is_file():
        print("pfind: tool source missing. Prepare it with fieldkit gather --only pfind.", file=sys.stderr)
        return 2
    try:
        code = marshal.loads(compileonce.checked_pfind(source))
        if not isinstance(code, CodeType):
            raise ValueError("artifact does not contain a code object")
    except (OSError, ValueError, TypeError, EOFError) as exc:
        print(f"pfind: {exc}. Prepare the bytecode with fieldkit compileonce build.", file=sys.stderr)
        return 3
    original_argv = sys.argv
    sys.argv = [str(source), *(sys.argv[1:] if argv is None else argv)]
    sys.path.insert(0, str(source.parent))
    try:
        namespace = {"__name__": "_fieldkit_pfind", "__file__": str(source), "__package__": None}
        exec(code, namespace)
        search_started = time.perf_counter()
        timing = os.getenv("FIELDKIT_PFIND_TIMING") == "1"
        if timing:
            print(f"pfind bytecode startup: {(search_started - started) * 1000:.2f} ms", file=sys.stderr)
        try:
            return namespace["main"]()
        finally:
            if timing:
                print(f"pfind search command: {(time.perf_counter() - search_started) * 1000:.2f} ms", file=sys.stderr)
    finally:
        sys.path.pop(0)
        sys.argv = original_argv


if __name__ == "__main__":
    sys.exit(main())
