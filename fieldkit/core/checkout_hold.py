"""A running build holds the checkouts it reads; nothing fast-forwards a held checkout (P29, owner order 2026-11-16).

Firefox 160's install was aborted twice ("the checkout moved during the run") after build and package were green:
the peers-link listener's self_update fast-forwarded ~/Documents/Scripts/Fieldkit main under the emerge run. The
runner already detects a move (emerge/runner.moved); this stops the move at its source.

  hold(label, checkouts) -> a lock file under state/checkout-holds/ naming the PID and the checkouts;
  release(path)          -> the lock is gone (the runner's finally);
  busy(checkout)         -> why the checkout is held, or None. A lock whose PID is dead is stale and ignored.

Every code path that fast-forwards a checkout asks busy() first and leaves the checkout alone when held:
peers/link.self_update, core/sync.apply (sync --apply) and method/train.unfreeze (ship unfreeze).
Pure file reads plus one PID check; no git call.
"""
import json
import os
import time
from pathlib import Path


def _dir(root=None):
    from .settings import state_root
    return Path(state_root(root)) / "state" / "checkout-holds"


def _norm(p):
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except OSError:
        return os.path.normcase(str(p))


def _alive(pid):
    try:
        import psutil
        p = psutil.Process(int(pid))
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except Exception:  # noqa: BLE001  gone, a bad PID, or no psutil
        return False


def hold(label, checkouts, root=None, pid=None):
    """Write the lock; -> its path. Never raises: a build is never failed by its own lock (it still detects a move)."""
    d = _dir(root)
    pid = pid or os.getpid()
    p = d / f"{pid}-{int(time.time() * 1000)}.json"
    try:
        d.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"pid": pid, "label": label, "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "checkouts": [str(c) for c in checkouts]}, indent=1), encoding="utf-8")
    except OSError:
        return None
    return p


def release(path):
    if path:
        try:
            Path(path).unlink()
        except OSError:
            pass


def holds(root=None):
    """-> the live locks (a dead PID's lock is skipped)."""
    out = []
    for f in sorted(_dir(root).glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _alive(rec.get("pid")):
            out.append(dict(rec, file=str(f)))
    return out


def busy(checkout, root=None):
    """-> a sentence naming the run that holds `checkout`, or None when nothing live holds it."""
    me = _norm(checkout)
    for rec in holds(root):
        if any(_norm(c) == me for c in rec.get("checkouts", [])):
            return f"held by {rec.get('label', '?')} (PID {rec['pid']}, since {rec.get('since', '?')})"
    return None
