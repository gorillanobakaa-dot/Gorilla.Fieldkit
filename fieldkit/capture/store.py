"""The capture store: an append-only, hash-chained, fsync'd journal of everything an agent made or ran.

Layout (dir = $FIELDKIT_CAPTURE_DIR, default <Fieldkit>/state/capture, git-ignored, never published):
    journal.jsonl   one JSON line per entry: seq, ts, prev, type, data, hash (sha256 over prev + the rest)
    journal.torn    bytes of a write that was cut off by a crash; kept, never deleted
    blobs/<sha>     big fields (> SPILL bytes) moved out of the journal line, addressed by content hash
    index.json      derived cache of the journal (rebuilt whenever it does not match); never the source of truth
    journal.lock    OS lock (released by the OS when a process dies)

Entry types: event (a raw hook payload, written BEFORE it is parsed), item (a captured record, state raw), dup (the same
content seen again: counted, never dropped), state (raw|processing|tool|procedure|promoted, with evidence), link (a
failed attempt -> its later correction), error (an internal failure, journaled, never raised to the agent).
"""
import contextlib
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STATES = ("raw", "processing", "tool", "procedure", "promoted")
DONE_STATES = ("tool", "procedure", "promoted")
SPILL = 16 * 1024
STUCK_AFTER = 3600            # a record in `processing` this long is stuck (its worker died)
GENESIS = "0" * 64

_SECRET_KEY = re.compile(r"(key|token|secret|passw|credential|auth|cookie|private|bearer|pat$)", re.I)
_SECRET_VALUE = [re.compile(p) for p in (
    r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"sk-[A-Za-z0-9_\-]{16,}", r"AKIA[0-9A-Z]{16}",
    r"AIza[0-9A-Za-z_\-]{30,}", r"xox[abprs]-[A-Za-z0-9\-]{10,}", r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]*",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")]
_SECRET_ARG = re.compile(r"""(?ix)(
    (?:--?(?:password|passwd|pwd|token|secret|api[-_]?key|apikey|auth|credentials?)[=\s]+)
  | (?:\b[A-Za-z_]*(?:password|passwd|token|secret|api[-_]?key|apikey)[A-Za-z_]*\s*=\s*)
  | (?:authorization:\s*(?:bearer|basic)\s+)
  | (?:bearer\s+)
)(?:"[^"]*"|'[^']*'|\S+)""")
REDACTED = "[REDACTED]"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def capture_dir():
    d = os.environ.get("FIELDKIT_CAPTURE_DIR")
    if d:
        return Path(d)
    from ..core import settings
    return Path(settings.ROOT) / "state" / "capture"


def redact_text(s):
    if not isinstance(s, str) or not s:
        return s
    for rx in _SECRET_VALUE:
        s = rx.sub(REDACTED, s)
    return _SECRET_ARG.sub(lambda m: m.group(1) + REDACTED, s)


def redact(obj, key=None):
    """Secrets out of any structure, at capture time: by key name, by value shape, by `--password X` style arguments."""
    if isinstance(obj, dict):
        return {k: (REDACTED if isinstance(v, str) and v and _SECRET_KEY.search(str(k)) else redact(v, k))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return redact_text(obj) if isinstance(obj, str) else obj


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(text):
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def entry_hash(prev, e):
    return sha(prev + canon({k: e[k] for k in ("seq", "ts", "type", "data")}))


def content_hash(rec):
    """Identity of a record for dedup: what was done, never when or by whom. A failed run and a passing run differ."""
    return sha(canon({k: rec.get(k) for k in ("kind", "source", "command", "cwd", "exit_code", "path")}))


@contextlib.contextmanager
def _locked(path):
    f = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            for _ in range(600):
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise OSError("capture journal lock busy")
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()


def _empty():
    return {"offset": 0, "seq": 0, "last_hash": GENESIS, "items": {}, "by_hash": {}, "counts": {}, "occ": {},
            "state": {}, "links": {}, "fixes": {}, "events": {}, "paths": {}, "open_fail": {}, "dups": 0,
            "errors": 0, "last_ts": None}


def fold(st, e):
    """Apply one journal entry to the derived state (the only place meaning is given to entries)."""
    t, d = e["type"], e["data"]
    st["seq"], st["last_hash"], st["last_ts"] = e["seq"], e["hash"], e["ts"]
    if t == "event":
        st["events"][d["id"]] = None
    elif t == "item":
        iid = d["id"]
        st["items"][iid] = d
        st["by_hash"][d["chash"]] = iid
        st["counts"][iid] = 1
        st["state"][iid] = {"state": d.get("state", "raw"), "ts": e["ts"], "evidence": None, "error": None}
        if d.get("event"):
            st["events"][d["event"]] = iid
        if d.get("path"):
            st["paths"][d["path"]] = iid
        if d.get("kind") == "command" and d.get("exit_code") not in (0, None) and d.get("session"):
            st["open_fail"].setdefault(d["session"], []).append(iid)
    elif t == "dup":
        iid = d["item"]
        st["dups"] += 1
        st["counts"][iid] = st["counts"].get(iid, 1) + 1
        occ = st["occ"].setdefault(iid, [])
        occ.append({"ts": e["ts"], "session": d.get("session"), "agent": d.get("agent"), "exit_code": d.get("exit_code")})
        del occ[:-20]
        if d.get("event"):
            st["events"][d["event"]] = iid
    elif t == "state":
        iid = d["item"]
        st["state"][iid] = {"state": d["state"], "ts": e["ts"], "evidence": d.get("evidence"), "error": d.get("error")}
    elif t == "link":
        st["links"][d["failed"]] = d["fixed"]
        st["fixes"].setdefault(d["fixed"], []).append(d["failed"])
        for lst in st["open_fail"].values():
            if d["failed"] in lst:
                lst.remove(d["failed"])
    elif t == "error":
        st["errors"] += 1


def _words(cmd):
    return [w for w in re.split(r"\s+", cmd) if w] if isinstance(cmd, str) else []


def related(failed_cmd, ok_cmd):
    """A success corrects a failure when it runs the same program or touches the same argument."""
    a, b = _words(failed_cmd), _words(ok_cmd)
    if not a or not b:
        return False
    if a[0] == b[0]:
        return True
    return bool({w for w in a[1:] if not w.startswith("-")} & {w for w in b[1:] if not w.startswith("-")})


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root else capture_dir()
        self.journal = self.root / "journal.jsonl"
        self.torn = self.root / "journal.torn"
        self.blobs = self.root / "blobs"
        self.cache = self.root / "index.json"
        self.lock = self.root / "journal.lock"
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- reading -------------------------------------------------------------------------------------------
    def _lines(self, offset=0):
        """(entry|None, raw_bytes, end_offset) for every COMPLETE line from offset; a partial tail is not returned."""
        if not self.journal.is_file():
            return
        with open(self.journal, "rb") as f:
            f.seek(offset)
            pos = offset
            for raw in f:
                if not raw.endswith(b"\n"):
                    return
                pos += len(raw)
                try:
                    yield json.loads(raw.decode("utf-8")), raw, pos
                except ValueError:
                    yield None, raw, pos

    def replay(self):
        """The folded state from the whole journal, plus every problem found (the source of truth; verify uses it)."""
        st, problems, prev, n = _empty(), [], GENESIS, 0
        for e, raw, end in self._lines():
            n += 1
            if e is None:
                problems.append(f"line {n}: not JSON")
                continue
            if e.get("seq") != st["seq"] + 1:
                problems.append(f"line {n}: seq {e.get('seq')} follows {st['seq']}")
            if e.get("prev") != prev:
                problems.append(f"seq {e.get('seq')}: prev hash does not match the entry before it (chain broken)")
            if e.get("hash") != entry_hash(e.get("prev", ""), e):
                problems.append(f"seq {e.get('seq')}: hash does not match its content (entry altered)")
            prev = e.get("hash", prev)
            fold(st, e)
            st["offset"] = end
        return st, problems

    def state(self):
        """Cached fold, extended from the cache's offset; falls back to a full replay when the cache cannot be trusted."""
        try:
            c = json.loads(self.cache.read_text(encoding="utf-8"))
            size = self.journal.stat().st_size if self.journal.is_file() else 0
            ok = c["offset"] <= size
            if ok and c["offset"]:
                with open(self.journal, "rb") as f:
                    f.seek(max(0, c["offset"] - 4096))
                    tail = f.read(min(4096, c["offset"])).rstrip(b"\n").rsplit(b"\n", 1)[-1]
                ok = json.loads(tail.decode("utf-8")).get("hash") == c["last_hash"]
            if ok:
                for e, raw, end in self._lines(c["offset"]):
                    if e is None:
                        raise ValueError("bad line")
                    fold(c, e)
                    c["offset"] = end
                return c
        except (OSError, ValueError, KeyError):
            pass
        return self.replay()[0]

    def _save_cache(self, st):
        tmp = self.cache.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(st), encoding="utf-8")
            os.replace(tmp, self.cache)
        except OSError:
            pass

    # ---- writing -------------------------------------------------------------------------------------------
    def _heal(self):
        """A crash can leave a half line at the end. Keep its bytes in journal.torn and cut the journal back."""
        if not self.journal.is_file():
            return
        with open(self.journal, "rb") as f:
            data = f.read()
        if not data or data.endswith(b"\n"):
            return
        cut = data.rfind(b"\n") + 1
        with open(self.torn, "ab") as t:
            t.write(f"\n--- cut {now()} ---\n".encode() + data[cut:])
            t.flush()
            os.fsync(t.fileno())
        with open(self.journal, "r+b") as f:
            f.truncate(cut)
            f.flush()
            os.fsync(f.fileno())

    def _append_locked(self, st, typ, data):
        e = {"seq": st["seq"] + 1, "ts": now(), "prev": st["last_hash"], "type": typ, "data": data}
        e["hash"] = entry_hash(e["prev"], e)
        line = (json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with open(self.journal, "ab") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        fold(st, e)
        st["offset"] = (self.journal.stat().st_size)
        return e

    @contextlib.contextmanager
    def batch(self):
        """Many records in one go (a scan): the lock, the heal and the state load happen once; every append is still
        fsync'd, so a crash loses nothing that was acknowledged. The cache is saved at the end (and on an error)."""
        if getattr(self, "_mem", None) is not None:
            yield
            return
        with _locked(self.lock):
            self._heal()
            self._mem = self.state()
            try:
                yield
            finally:
                mem, self._mem = self._mem, None
                self._save_cache(mem)

    @contextlib.contextmanager
    def tx(self):
        """Exclusive access: heal a torn tail, load state, let the caller append, refresh the cache."""
        if getattr(self, "_mem", None) is not None:
            yield self._mem
            return
        with _locked(self.lock):
            self._heal()
            st = self.state()
            yield st
            self._save_cache(st)

    def append(self, typ, data):
        with self.tx() as st:
            return self._append_locked(st, typ, data)

    def spill(self, rec):
        """Fields over SPILL bytes go to blobs/<sha>; the journal keeps the hash, size and a preview."""
        out = {}
        for k, v in rec.items():
            if isinstance(v, str) and len(v.encode("utf-8", "surrogatepass")) > SPILL:
                h = sha(v)
                self.blobs.mkdir(exist_ok=True)
                p = self.blobs / h
                if not p.is_file():
                    with open(p, "wb") as f:
                        f.write(v.encode("utf-8", "surrogatepass"))
                        f.flush()
                        os.fsync(f.fileno())
                out[k] = {"blob": h, "size": len(v), "preview": v[:300]}
            else:
                out[k] = v
        return out

    def blob(self, ref):
        return (self.blobs / ref["blob"]).read_text(encoding="utf-8", errors="surrogatepass")

    # ---- the capture operations --------------------------------------------------------------------------
    def begin_event(self, payload, agent, origin="hook"):
        """Step 1 of a hook: the raw payload is on disk (redacted, fsync'd) before anything parses it."""
        eid = "ev-" + sha(now() + canon(payload) + str(os.getpid()))[:16]
        self.append("event", {"id": eid, "agent": agent, "origin": origin, "payload": self.spill_value(redact(payload))})
        return eid

    def spill_value(self, v):
        s = canon(v)
        if len(s.encode("utf-8", "surrogatepass")) <= SPILL * 4:
            return v
        return self.spill({"payload": s})["payload"]

    def record(self, rec, event=None):
        """Step 2: the parsed record becomes an item (new content) or a counted repeat of one. Returns (id, is_new)."""
        rec = redact(rec)
        rec.setdefault("timestamp", now())
        chash = content_hash(rec)
        with self.tx() as st:
            iid = st["by_hash"].get(chash)
            if iid:
                self._append_locked(st, "dup", {"item": iid, "event": event, "session": rec.get("session"),
                                                "agent": rec.get("agent"), "exit_code": rec.get("exit_code")})
                new = False
            else:
                iid = "cap-" + chash[:12]
                data = dict(self.spill(rec), id=iid, chash=chash, state="raw", event=event, count=1)
                prev = st["paths"].get(rec.get("path")) if rec.get("path") else None
                if prev:
                    data["prev_version"] = prev
                    data["version"] = (st["items"][prev].get("version") or 1) + 1
                self._append_locked(st, "item", data)
                new = True
            if rec.get("kind") == "command" and rec.get("exit_code") == 0 and rec.get("session"):
                for fid in list(st["open_fail"].get(rec["session"], [])):
                    if fid != iid and related(st["items"][fid].get("command"), rec.get("command")):
                        self._append_locked(st, "link", {"failed": fid, "fixed": iid})
        return iid, new

    def set_state(self, iid, state, evidence=None, error=None):
        if state not in STATES:
            raise ValueError(f"state must be one of {STATES}")
        return self.append("state", {"item": iid, "state": state, "evidence": evidence, "error": error})

    def run_processing(self, iid, steps):
        """Process an item that is already on disk. A failing step leaves it in the queue (raw) with the error."""
        self.set_state(iid, "processing")
        try:
            result = steps(iid)
        except Exception as exc:                       # noqa: BLE001 - any failure keeps the item queued
            self.set_state(iid, "raw", error=f"{type(exc).__name__}: {exc}")
            return None
        state, evidence = result
        self.set_state(iid, state, evidence=evidence)
        return state

    def log_error(self, where, exc, extra=None):
        try:
            self.append("error", {"where": where, "error": redact_text(f"{type(exc).__name__}: {exc}"), "extra": redact(extra)})
        except Exception:                              # noqa: BLE001 - the last resort is stderr
            print(f"capture: {where}: {exc}", file=sys.stderr)

    def recover(self):
        """After a crash: events with no item get theirs made from the raw payload; processing items that died go back to raw."""
        from . import hook
        made = reset = 0
        st = self.state()
        for eid, res in list(st["events"].items()):
            if res is None:
                ev = self._event(eid)
                if ev is not None:
                    payload = self.event_payload(ev)
                    try:
                        rec = hook.parse(ev["agent"], payload, self)
                    except Exception:             # noqa: BLE001
                        rec = None
                    rec = rec or hook.unparsed(ev["agent"], payload)
                    self.record(rec, event=eid)
                    made += 1
        st = self.state()
        for iid, s in st["state"].items():
            if s["state"] == "processing" and _age(s["ts"]) > STUCK_AFTER:
                self.set_state(iid, "raw", error="processing worker died; back in the queue")
                reset += 1
        return {"events_recovered": made, "requeued": reset}

    def _event(self, eid):
        for e, raw, end in self._lines():
            if e and e["type"] == "event" and e["data"]["id"] == eid:
                return e["data"]
        return None

    def event_payload(self, ev):
        p = ev["payload"]
        return json.loads(self.blob(p)) if isinstance(p, dict) and "blob" in p else p

    # ---- views ---------------------------------------------------------------------------------------------
    def view(self, st, iid):
        d = dict(st["items"][iid])
        d.pop("chash", None)
        s = st["state"].get(iid, {})
        d.update(state=s.get("state"), state_ts=s.get("ts"), evidence=s.get("evidence"), error=s.get("error"),
                 count=st["counts"].get(iid, 1), occurrences=st["occ"].get(iid, []),
                 corrected_by=st["links"].get(iid), fixes=st["fixes"].get(iid, []))
        return d

    def find(self, words):
        st = self.state()
        ws = [w.lower() for w in words]
        out = []
        for iid, d in st["items"].items():
            hay = canon(d).lower()
            if all(w in hay for w in ws):
                out.append(self.view(st, iid))
        return out

    def ledger(self):
        st = self.state()
        items, outstanding, lost, unaccounted, stuck = [], [], [], [], []
        for iid, d in st["items"].items():
            s = st["state"].get(iid, {})
            state = s.get("state")
            row = {"id": iid, "kind": d.get("kind"), "state": state, "count": st["counts"].get(iid, 1),
                   "agent": d.get("agent"), "session": d.get("session"), "timestamp": d.get("timestamp"),
                   "evidence": s.get("evidence")}
            items.append(row)
            if state in ("raw", "processing"):
                outstanding.append(iid)
            if state == "processing" and _age(s.get("ts")) > STUCK_AFTER:
                stuck.append(iid)
            if state in DONE_STATES and not s.get("evidence"):
                unaccounted.append(iid)
            for v in d.values():
                if isinstance(v, dict) and "blob" in v and not (self.blobs / v["blob"]).is_file():
                    lost.append(iid)
                    break
        orphan = [e for e, r in st["events"].items() if r is None]
        events = len(st["events"])
        return {"items": items, "hook_events_total": events, "deduplicated": st["dups"], "outstanding": outstanding,
                "lost": lost, "unaccounted": unaccounted + orphan, "stuck": stuck, "errors": st["errors"]}

    def verify(self):
        st, problems = self.replay()
        seen = {}
        for iid, d in st["items"].items():
            h = d.get("chash")
            if h in seen:
                problems.append(f"duplicate content kept as two items: {seen[h]} and {iid}")
            seen[h] = iid
        n_items = sum(1 for _ in st["items"])
        # every item journaled once (a repeated id would have overwritten the first)
        ids = [e["data"]["id"] for e, _, _ in self._lines() if e and e["type"] == "item"]
        if len(ids) != len(set(ids)):
            problems.append("an item id appears twice in the journal")
        for iid, d in st["items"].items():
            for v in d.values():
                if isinstance(v, dict) and "blob" in v:
                    p = self.blobs / v["blob"]
                    if not p.is_file():
                        problems.append(f"{iid}: blob {v['blob'][:12]} is missing")
                    elif hashlib.sha256(p.read_bytes()).hexdigest() != v["blob"]:
                        problems.append(f"{iid}: blob {v['blob'][:12]} does not match its hash")
        for eid, r in st["events"].items():
            if r is None:
                problems.append(f"event {eid} has no item (a crash before parsing; `capture status` recovers it)")
        info = {"entries": st["seq"], "items": n_items, "deduplicated": st["dups"], "events": len(st["events"]),
                "torn_bytes_kept": self.torn.stat().st_size if self.torn.is_file() else 0}
        return problems, info


def _age(ts):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
    except (TypeError, ValueError):
        return 0
