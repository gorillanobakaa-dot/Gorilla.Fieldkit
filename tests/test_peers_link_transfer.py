"""fieldkit peers link put, the big transfer (stage O2, 2026-10-25): folders, files over 50 MB and git bundles,
streamed on one connection, resumable, sha256 per file, never overwriting. End to end against a real `serve`
process, and in-process over a socket pair where a drop or a damaged byte has to be made on purpose."""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import tracemalloc
from pathlib import Path

import pytest

from fieldkit.peers import link, link_transfer as lt

sys.path.insert(0, str(Path(__file__).parent))
from test_peers_link import serve_a, two  # noqa: E402,F401  (the two-computer fixture)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def make_tree(root):
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "a.txt").write_text("alpha\n")
    (root / "zero.bin").write_bytes(b"")
    (root / "sub" / "big.bin").write_bytes(os.urandom(3 * lt.BLOCK + 123))        # several blocks
    (root / "sub" / "deep" / "c.txt").write_text("gamma\n" * 1000)
    run = root / "run.sh"
    run.write_text("#!/bin/sh\necho hi\n")
    os.chmod(run, 0o755)
    os.utime(root / "a.txt", (1_600_000_000, 1_600_000_000))
    return root


def files_of(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


# ---- end to end, two real processes --------------------------------------------------------------------------------
def test_a_whole_folder_arrives_identical_with_modes_times_and_empty_folders(two, tmp_path):
    src = make_tree(tmp_path / "tree")
    p = serve_a(two)
    try:
        r = link.put(src)
    finally:
        p.kill()
        p.wait()
    assert r["ok"] and r["verified_both_ends"] and r["files"] == 5, r
    got = two["a"] / "received" / "tree"
    assert files_of(got) == files_of(src)
    assert (got / "empty").is_dir()
    assert int((got / "a.txt").stat().st_mtime) == 1_600_000_000
    if os.name != "nt":
        assert os.stat(got / "run.sh").st_mode & 0o111
    assert not list((two["a"] / "received" / ".partial").glob("*"))      # nothing half-written left
    receipt = json.loads(next((two["a"] / "receipts").glob("*.json")).read_text())
    assert receipt["digest"] == r["digest"] and len(receipt["sha256"]) == 5


def test_a_file_over_the_old_50_mb_cap_crosses_and_never_overwrites(two, tmp_path):
    big = tmp_path / "kernel.tar.xz"
    with open(big, "wb") as f:
        for _ in range(link.MAX_FILE // lt.BLOCK + 3):
            f.write(os.urandom(lt.BLOCK))
    assert big.stat().st_size > link.MAX_FILE
    p = serve_a(two, seconds=60)
    try:
        first = link.put(big)
        second = link.put(big)
    finally:
        p.kill()
        p.wait()
    assert first["ok"] and second["ok"], (first, second)
    assert first["sha256"] == sha(big) == second["sha256"]
    assert Path(first["saved"][0]).name == "kernel.tar.xz" and Path(second["saved"][0]).name == "kernel.tar-1.xz"
    assert sha(two["a"] / "received" / "kernel.tar.xz") == sha(big)


def test_a_git_bundle_crosses_and_the_receiver_reads_its_refs(two, tmp_path):
    if not shutil.which("git"):
        pytest.skip("no git")
    repo = tmp_path / "repo"
    repo.mkdir()
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f").write_text("x")
    subprocess.run(g + ["add", "f"], check=True)
    subprocess.run(g + ["commit", "-qm", "one"], check=True)
    b = lt.make_bundle(repo, tmp_path)
    p = serve_a(two)
    try:
        r = link.put(b)
    finally:
        p.kill()
        p.wait()
    assert r["ok"] and r["bundle"].endswith("ref(s)") and not r["bundle"].startswith("0 "), r


# ---- in process: drops, damage, refusals ---------------------------------------------------------------------------
class Faulty:
    """The sender's socket, made to drop after `drop_at` bytes or to flip the byte at `flip_at`."""

    def __init__(self, s, drop_at=None, flip_at=None):
        self.s, self.drop_at, self.flip_at, self.n = s, drop_at, flip_at, 0

    def sendall(self, data):
        data = bytearray(data)
        if self.flip_at is not None and self.n <= self.flip_at < self.n + len(data):
            data[self.flip_at - self.n] ^= 0xFF
        if self.drop_at is not None and self.n + len(data) > self.drop_at:
            self.s.sendall(bytes(data[:self.drop_at - self.n]))
            self.s.close()
            raise ConnectionResetError("cable pulled (test)")
        self.n += len(data)
        self.s.sendall(bytes(data))

    def __getattr__(self, name):
        return getattr(self.s, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.s.close()


@pytest.fixture
def here(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "home"))
    return tmp_path


def receiver(faults=(), windows=False, case_insensitive=False):
    """connect() for put_stream: each call is a new connection to an in-process receiver; faults[i] for the i-th."""
    k, calls, threads = os.urandom(32), [0], []

    def connect():
        a, b = socket.socketpair()

        def srv():
            try:
                w = lt.Wire(b)
                req = w.line()
                lt.receive_stream(b, req, bytes(w.buf), windows=windows, case_insensitive=case_insensitive)
            except (OSError, ConnectionError, ValueError):
                pass
            finally:
                b.close()
        t = threading.Thread(target=srv, daemon=True)
        t.start()
        threads.append(t)
        f = faults[calls[0]] if calls[0] < len(faults) else {}
        calls[0] += 1
        return (Faulty(a, **f) if f else a), k
    connect.threads = threads
    return connect


def test_a_drop_mid_file_resumes_where_it_stopped_and_sends_only_the_rest(here):
    src = make_tree(here / "tree")
    total = sum(p.stat().st_size for p in src.rglob("*") if p.is_file())
    con = receiver(faults=[{"drop_at": 2 * lt.BLOCK}])
    first = lt.put_stream(src, retries=0, connect=con, sleep=lambda s: None)
    for t in con.threads:
        t.join(5)
    assert not first["ok"] and "carries on" in first["error"]
    part = next((here / "home" / "received" / ".partial").iterdir())
    assert (part / "done.json").is_file()                     # saved although the connection broke
    second = lt.put_stream(src, retries=0, connect=con)
    assert second["ok"] and second["verified_both_ends"], second
    assert second["resumed_bytes"] > lt.BLOCK and second["received_bytes"] < total
    assert files_of(here / "home" / "received" / "tree") == files_of(src)


def test_put_retries_by_itself_after_a_drop(here):
    src = make_tree(here / "tree")
    r = lt.put_stream(src, retries=2, connect=receiver(faults=[{"drop_at": lt.BLOCK + 50}]), sleep=lambda s: None)
    assert r["ok"] and r["attempts"] == 2 and r["resumed_bytes"] > 0, r


def test_a_damaged_byte_is_caught_by_sha256_discarded_and_resent_next_time(here):
    src = here / "one.bin"
    src.write_bytes(os.urandom(200_000))
    man = lt.manifest_for(src)
    head = len(lt.manifest_bytes(man[0]))
    con = receiver(faults=[{"flip_at": 5000}])                # 5000: past the header line, inside the data
    assert head < 5000
    r = lt.put_stream(src, retries=0, connect=con)
    assert not r["ok"] and r["bad"] == ["one.bin"], r
    assert not (here / "home" / "received" / "one.bin").exists()
    r = lt.put_stream(src, retries=0, connect=con)
    assert r["ok"] and r["received_bytes"] == 200_000 and sha(here / "home" / "received" / "one.bin") == sha(src)


def test_paths_this_computer_cannot_hold_are_refused_never_renamed(here):
    tree = here / "t"
    (tree / "net").mkdir(parents=True)
    (tree / "net" / "xt_MARK.h").write_text("a")
    (tree / "net" / "xt_mark.h").write_text("b")
    (tree / "aux.c").write_text("c")
    man = lt.manifest_for(tree)[0]
    assert lt.cannot_hold(man, case_insensitive=False, windows=False) == []
    probs = dict(lt.cannot_hold(man, case_insensitive=True, windows=True))
    assert "aux.c" in probs and "net/xt_mark.h" in probs
    r = lt.put_stream(tree, retries=0, connect=receiver(windows=True, case_insensitive=True))
    assert not r["ok"] and r["count"] == 2 and "tarball or a git bundle" in r["error"]
    assert not list((here / "home" / "received").glob("t*"))


@pytest.mark.parametrize("rel", ["../etc/passwd", "/etc/passwd", "a/../../b", "C:/x", "a\\b", "a//b", "x\x07y"])
def test_a_path_that_climbs_out_or_is_not_plain_is_refused(rel):
    assert lt.bad_path(rel) is not None
    assert lt.bad_path("linux-7.1.2/drivers/net/wireless/ath/ath9k/main.c") is None


def test_a_manifest_that_does_not_match_its_signed_sha256_is_refused(here):
    a, b = socket.socketpair()
    raw = b'{"dirs":[],"files":[["x",1,false,0]],"kind":"file","name":"x"}'
    req = {"verb": "put-stream", "tid": lt.tid_of(raw), "manifest_size": len(raw), "manifest_sha256": "0" * 64}
    t = threading.Thread(target=lambda: lt.receive_stream(b, req), daemon=True)
    t.start()
    w = lt.Wire(a)
    assert w.line()["go"] == "manifest"
    a.sendall(raw)
    assert "does not match" in w.line()["error"]
    t.join(5)


def test_sending_a_big_file_keeps_memory_flat(here):
    big = here / "big.bin"
    with open(big, "wb") as f:
        for _ in range(40):
            f.write(os.urandom(lt.BLOCK))                     # 40 MiB
    con = receiver()
    tracemalloc.start()
    try:
        r = lt.put_stream(big, retries=0, connect=con)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert r["ok"], r
    assert peak < 12 * lt.BLOCK, f"peak {peak / 1e6:.1f} MB for a 40 MiB file: something holds the whole file"


def test_an_older_receiver_gets_a_small_file_the_old_way_and_a_folder_is_explained(here, monkeypatch, tmp_path):
    monkeypatch.setattr(lt, "put_stream", lambda *a, **k: {"ok": False, "error": "unknown verb 'put-stream'"})
    sent = []
    monkeypatch.setattr(link, "put_chunked", lambda path, to_desktop, call_fn: sent.append(path) or {"ok": True})
    f = tmp_path / "small.txt"
    f.write_text("x")
    assert link.put(f)["ok"] and sent == [f]
    (tmp_path / "d").mkdir()
    r = link.put(tmp_path / "d")
    assert not r["ok"] and "older link" in r["error"]


def test_a_receiver_whose_digest_disagrees_is_not_taken_at_its_word(here):
    """A receiver that says ok with a different digest: the sender's own sha256 list decides, not the word ok."""
    src = here / "f.txt"
    src.write_text("hello")

    def connect():
        a, b = socket.socketpair()

        def liar():
            w = lt.Wire(b)
            req = w.line()
            w.send({"ok": True, "go": "manifest"})
            w.exact(req["manifest_size"])
            w.send({"ok": True, "files": 1, "have": {}})
            w.exact(5 + 32)
            w.send({"ok": True, "saved": ["x"], "files": 1, "bytes": 5, "digest": "0" * 64})
            b.close()
        threading.Thread(target=liar, daemon=True).start()
        return a, os.urandom(32)
    r = lt.put_stream(src, retries=0, connect=connect)
    assert not r["ok"] and r["verified_both_ends"] is False and "differs" in r["error"]


def test_the_resume_answer_for_a_big_tree_stays_one_short_line(here, monkeypatch):
    """Live 2026-10-25: 473,104 files made the old answer (one offset per file) a line over 1 MiB, refused 4 times."""
    tree = here / "many"
    tree.mkdir()
    for i in range(3000):
        (tree / f"f{i:05d}").write_bytes(b"")
    monkeypatch.setattr(lt, "LINE", 4096)                     # one offset per file would be ~9 KB here
    r = lt.put_stream(tree, retries=0, connect=receiver())
    assert r["ok"] and r["files"] == 3000, r
