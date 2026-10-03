"""A binary hand edit (a logo) gets a step judged by its bytes."""
import subprocess

from fieldkit.buildh import firefox, handedit


def _git(w, *a):
    subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)


def test_binary_file_in_a_commit_gets_a_hunk_and_is_checked_by_bytes(tmp_path):
    w = tmp_path / "tree"
    w.mkdir()
    _git(w, "init", "-q")
    _git(w, "config", "user.email", "t@example.invalid")  # privacy-scan: allow (a file name or a fake address, not an email)
    _git(w, "config", "user.name", "t")
    (w / "logo.png").write_bytes(bytes(range(256)) * 4)
    _git(w, "add", ".")
    _git(w, "commit", "-qm", "a")
    (w / "logo.png").write_bytes(bytes(reversed(range(256))) * 4)
    assert handedit.binary_hunks(w, "logo.png")
    _git(w, "commit", "-qam", "b")
    c = subprocess.run(["git", "-C", str(w), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    h = handedit.binary_hunks(w, "logo.png", c)
    assert h and h[0]["binary"]
    t = {"workdir": str(w)}
    assert firefox.check_port(t, {"hand_port": True}, "hand/x", "logo.png", h[0])["ok"]
    (w / "logo.png").write_bytes(b"other")
    assert not firefox.check_port(t, {"hand_port": True}, "hand/x", "logo.png", h[0])["ok"]


def test_a_text_file_gets_no_binary_hunk(tmp_path):
    w = tmp_path / "tree"
    w.mkdir()
    _git(w, "init", "-q")
    (w / "a.txt").write_text("x\n")
    _git(w, "add", ".")
    _git(w, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "a")  # privacy-scan: allow (a file name or a fake address, not an email)
    (w / "a.txt").write_text("y\n")
    assert handedit.binary_hunks(w, "a.txt") == []
