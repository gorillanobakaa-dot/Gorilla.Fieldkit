"""core/childrun (P29 cause 2): a harness child can never hang the job.

Firefox 160 lost 67 minutes to one patch.exe that never ended in replay._apply_patch. These tests hang a fake child
and prove it is stopped, named, and fails the step."""
import sys
import time
from pathlib import Path

from fieldkit.core import childrun

NL = chr(10)


def test_a_silent_idle_child_is_stopped_as_a_stall_and_named():
    t0 = time.monotonic()
    r = childrun.run([sys.executable, "-c", "import time; time.sleep(120)"], "patch 0122-hang.patch",
                     deadline_s=90, idle_s=3)
    assert time.monotonic() - t0 < 60
    assert r.status == "stalled" and r.stalled
    assert r.returncode == 124
    assert b"STALL patch 0122-hang.patch" in r.stderr


def test_the_wall_deadline_stops_a_child_that_keeps_talking():
    code = NL.join(["import time", "while True:", "    print('x', flush=True); time.sleep(0.2)"])
    r = childrun.run([sys.executable, "-c", code], "chatty", deadline_s=4, idle_s=60)
    assert r.status == "timeout" and r.returncode == 124
    assert b"TIMEOUT chatty" in r.stderr


def test_stdin_is_never_a_terminal_a_prompt_reads_end_of_file():
    # what matters is that an interactive prompt gets end-of-file at once and never waits for a keyboard. isatty() is
    # not asserted: on Windows the DEVNULL device (NUL) is a character device, so isatty() is True there (CI 2026-11-16)
    code = "import sys" + NL + "try:" + NL + "    input('continue? ')" + NL + "    print('GOT')" + NL + "except EOFError:" + NL + "    print('EOF')"
    r = childrun.run([sys.executable, "-c", code], "prompt", deadline_s=30, idle_s=10)
    assert r.returncode == 0 and r.stdout.split()[-1:] == [b"EOF"], r.stdout


def test_input_bytes_and_a_stdin_file_reach_the_child(tmp_path):
    echo = [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"]
    r = childrun.run(echo, "bytes", input=("hunk" + NL).encode(), deadline_s=30, idle_s=10)
    assert r.returncode == 0 and r.stdout.strip() == b"HUNK"
    f = tmp_path / "a.patch"
    f.write_bytes(("diff" + NL).encode())
    r = childrun.run(echo, "file", stdin_path=f, deadline_s=30, idle_s=10)
    assert r.stdout.strip() == b"DIFF"


def test_a_failing_child_keeps_its_exit_code_and_is_not_a_stall():
    r = childrun.run([sys.executable, "-c", "import sys; sys.stderr.write('Hunk #1 FAILED'); sys.exit(1)"], "fail",
                     deadline_s=30, idle_s=10)
    assert r.returncode == 1 and not r.stalled and b"Hunk #1 FAILED" in r.stderr


def test_patch_callers_pass_batch_or_force_so_patch_never_asks():
    root = Path(childrun.__file__).resolve().parents[1]
    for rel in ("buildh/replay.py", "buildh/firefox.py", "buildh/snapshot.py", "buildh/task.py", "build/kmigrate.py"):
        t = (root / rel).read_text(encoding="utf-8")
        assert "childrun.run(" in t, rel
        for line in t.splitlines():
            if "_patch_exe()" in line and "-p1" in line:
                assert "--batch" in line or "--force" in line, (rel, line)
