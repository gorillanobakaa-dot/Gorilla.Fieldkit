"""A content-addressed store of what was fetched: the bytes once, every fetch of them recorded.

    STORE/objects/ab/abcdef...      the body exactly as received (named by its sha256)
    STORE/text/abcdef....txt        the words extracted from it, when it has words
    STORE/snapshots.jsonl           one line per fetch: url, final url, status, when, hashes, headers, run

Nothing is ever rewritten: a page that changed is a new object and a new line, so a claim checked today can be
checked again against exactly what was read, a year from now. Owner-only permissions: a store holds what someone
was investigating.
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from . import urls


def default_dir():
    env = os.environ.get("FIELDKIT_EVIDENCE")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "fieldkit" / "evidence"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "fieldkit" / "evidence"


def _write_once(path, data):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for d in (path.parent, path.parent.parent):       # mkdir's mode is masked and skips existing parents
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root else default_dir()

    def _ready(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def object_path(self, sha):
        return self.root / "objects" / sha[:2] / sha

    def text_path(self, sha):
        return self.root / "text" / f"{sha}.txt"

    def put(self, body, text=None):
        self._ready()
        sha = hashlib.sha256(body).hexdigest()
        _write_once(self.object_path(sha), body)
        if text is not None:
            _write_once(self.text_path(sha), text.encode("utf-8"))
        return sha

    def record(self, rec):
        self._ready()
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
        with open(self.root / "snapshots.jsonl", "a", encoding="utf-8") as f:
            f.write(line)
        try:
            os.chmod(self.root / "snapshots.jsonl", 0o600)
        except OSError:
            pass

    def snapshots(self):
        p = self.root / "snapshots.jsonl"
        if not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue                  # one damaged line must not hide every other fetch
        return out

    def latest(self, url, run=None, snaps=None):
        """The most recent snapshot of a URL (matched on its normalized spelling, requested or final)."""
        want = urls.normalize(url)
        best = None
        for s in snaps if snaps is not None else self.snapshots():
            if run and s.get("run") != run:
                continue
            if want in (s.get("url_norm"), s.get("final_norm")):
                if best is None or s.get("fetched_at", "") >= best.get("fetched_at", ""):
                    best = s
        return best

    def text(self, snap):
        if not snap or not snap.get("has_text"):
            return None
        p = self.text_path(snap["sha256"])
        return p.read_text(encoding="utf-8") if p.is_file() else None
