"""Shared helpers: output with a NEXT line, atomic write that keeps bytes exact."""
import json
import os
import tempfile
from pathlib import Path


def emit(as_json, verdict, message, next_step, data=None):
    if as_json:
        print(json.dumps({"verdict": verdict, "message": message, "next": next_step, **(data or {})}, indent=2))
    else:
        print(f"{verdict}: {message}")
        print(f"NEXT: {next_step}")


def write_bytes(path, data):
    """Write through a temporary file in the same folder, then replace: a crash never leaves half a file."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
