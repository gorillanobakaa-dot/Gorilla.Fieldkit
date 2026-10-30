"""The machine-wide gate for heavy jobs (stage O4, 2026-10-26): N slots held with OS locks, the rest queue, a hot CPU
is offered one fewer, a nested job never waits on itself, and a slot dies with its process."""
import json
import os
import subprocess
import sys

import pytest

from fieldkit import admission as A


def test_base_slots_from_cores_and_ram():
    assert A.base_slots({}, cores=4, ram_gb=16) == 2           # the laptop default
    assert A.base_slots({}, cores=2, ram_gb=16) == 1           # two cores: one heavy job
    assert A.base_slots({}, cores=8, ram_gb=4) == 1            # 4 GB (the Air): one
    assert A.base_slots({}, cores=1, ram_gb=1) == 1            # never zero
    assert A.base_slots({}, cores=32, ram_gb=128) == 2         # capped at 2 without a setting


def test_base_slots_setting_wins_and_bad_values_are_ignored():
    assert A.base_slots({"admission": {"slots": 4}}, cores=2, ram_gb=2) == 4
    assert A.base_slots({"admission": {"slots": 0}}, cores=4, ram_gb=16) == 2
    assert A.base_slots({"admission": {"slots": 99}}, cores=4, ram_gb=16) == 2
    assert A.base_slots({"admission": {"slots": "x"}}, cores=4, ram_gb=16) == 2


def test_hot_cpu_is_offered_one_fewer_never_zero():
    assert A.offered(2, 69.9, 70.0) == 2
    assert A.offered(2, 70.0, 70.0) == 1
    assert A.offered(1, 95.0, 70.0) == 1
    assert A.offered(2, None, 70.0) == 2                       # no reading: the gate does not invent heat


def test_slots_fill_then_refuse_then_free(tmp_path):
    a = A.try_take(2, "test", "one", tmp_path)
    b = A.try_take(2, "mutate", "two", tmp_path)
    assert a and b and a.i != b.i
    assert A.try_take(2, "build", "three", tmp_path) is None
    held = A.holders(tmp_path)
    assert sorted(h["kind"] for h in held) == ["mutate", "test"]
    assert all(h["pid"] == os.getpid() for h in held)
    a.release()
    assert [h["kind"] for h in A.holders(tmp_path)] == ["mutate"]
    c = A.try_take(2, "build", "three", tmp_path)
    assert c and c.i == a.i
    b.release()
    c.release()
    assert A.holders(tmp_path) == []


def test_a_slot_dies_with_its_process(tmp_path):
    code = ("import sys,time; from fieldkit import admission as A; s=A.try_take(1,'test','child',sys.argv[1]); "
            "print('held' if s else 'none', flush=True); time.sleep(30)")
    p = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True,
                         cwd=os.path.dirname(os.path.dirname(A.__file__)))
    try:
        assert p.stdout.readline().strip() == "held"
        assert A.try_take(1, "test", "me", tmp_path) is None   # another process holds it
        assert [h["label"] for h in A.holders(tmp_path)] == ["child"]
    finally:
        p.kill()
        p.wait()
    s = A.try_take(1, "test", "me", tmp_path)                  # killed: no stale slot to clean
    assert s
    s.release()


def test_queued_job_waits_says_one_line_and_never_asks(tmp_path):
    other = A.try_take(1, "test", "suite", tmp_path)
    said, sleeps = [], []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:
            other.release()                                   # the running job ends after three polls
    with A.admitted("mutate", "m", root=tmp_path, say=said.append, sleep=sleep, temp_fn=lambda: None,
                    local={"admission": {"slots": 1}}) as slot:
        assert slot is not None
        assert os.environ[A.NESTED].endswith(f":{slot.i}")
        assert A.waiting(tmp_path) == []                       # no longer queued once admitted
    assert len(sleeps) == 3
    assert said[0].startswith("ADMISSION  queued: mutate m waits for a slot (held: test suite)")
    assert said[1].startswith("ADMISSION  admitted after")
    assert len(said) == 2                                     # one line queued, one admitted: no chatter
    assert A.NESTED not in os.environ
    assert A.holders(tmp_path) == []


def _bounded(n=50):
    calls = []

    def sleep(s):
        calls.append(s)
        if len(calls) > n:
            raise AssertionError("never gave up")
    return sleep


def test_wait_limit_gives_up_without_running(tmp_path):
    other = A.try_take(1, "test", "suite", tmp_path)
    t, polls = [0], []

    def sleep(s):
        polls.append(s)
        t[0] += 5
        if len(polls) > 50:
            raise AssertionError("waited past --wait")
    with pytest.raises(TimeoutError):
        with A.admitted("build", "b", wait=10, root=tmp_path, say=lambda s: None, sleep=sleep,
                        now=lambda: t[0], temp_fn=lambda: None, local={"admission": {"slots": 1}}):
            pytest.fail("ran without a slot")
    assert len(polls) == 2                                    # 0, 5, 10 s: gave up at the limit
    assert A.waiting(tmp_path) == []
    other.release()


def test_run_returns_tempfail_and_starts_nothing_when_no_slot(tmp_path, monkeypatch):
    other = A.try_take(1, "test", "suite", tmp_path)
    monkeypatch.setattr(A, "base_slots", lambda local=None: 1)
    monkeypatch.setattr(A, "POLL_S", 0.01)
    started = []
    assert A.run(["x"], wait=0.05, root=tmp_path, call=started.append) == 75
    assert started == []
    other.release()


def test_nested_job_takes_no_second_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "temperature", lambda: None)
    with A.admitted("build", "guard", root=tmp_path, local={"admission": {"slots": 1}}, temp_fn=lambda: None):
        with A.admitted("test", "suite", root=tmp_path, local={"admission": {"slots": 1}}, wait=0,
                        temp_fn=lambda: None) as inner:
            assert inner is None                              # passed through: the guard's slot covers it
        assert len(A.holders(tmp_path)) == 1


def test_hot_cpu_queues_the_second_job(tmp_path):
    first = A.try_take(2, "build", "one", tmp_path)
    with pytest.raises(TimeoutError):
        with A.admitted("test", "two", wait=0, root=tmp_path, say=lambda s: None, sleep=_bounded(),
                        temp_fn=lambda: 80.0, local={"admission": {"slots": 2}, "thermal": {"build_target": 67}}):
            pytest.fail("a hot computer started a second heavy job")
    with A.admitted("test", "two", wait=0, root=tmp_path, say=lambda s: None, sleep=lambda s: None,
                    temp_fn=lambda: 60.0, local={"admission": {"slots": 2}}) as s:
        assert s is not None
    first.release()


def test_waiting_drops_dead_markers(tmp_path):
    d = tmp_path / "waiting"
    d.mkdir()
    (d / "1.json").write_text(json.dumps({"pid": 1, "kind": "test"}))
    (d / "2.json").write_text(json.dumps({"pid": 2, "kind": "mutate"}))
    assert [w["kind"] for w in A.waiting(tmp_path, alive=lambda pid: pid == 2)] == ["mutate"]
    assert not (d / "1.json").exists()


@pytest.mark.parametrize("bad", ["/etc", "../x", "tests/../../x", "--rootdir=/"])
def test_suite_paths_stay_inside_the_tree(bad):
    with pytest.raises(ValueError):
        A.suite_cmd([bad])


def test_suite_cmd_is_pytest_of_the_current_folder():
    cmd = A.suite_cmd(["tests/test_x.py"], basetemp="/bt")
    assert cmd[:3] == [sys.executable, "-m", "pytest"] and cmd[-1] == "tests/test_x.py" and "/bt" in cmd


def test_status_lines_name_holders_and_the_hot_rule(tmp_path):
    s = A.try_take(2, "mutate", "m1", tmp_path)
    st = A.state(root=tmp_path, local={"admission": {"slots": 2}}, temp_c=75.0)
    out = A.lines(st)
    assert out[0].startswith("ADMISSION  1 of 1 slot(s) held (2 on this computer; 75 C >= 70 C, one fewer offered)")
    assert "mutate m1" in out[1]
    assert st["free"] == 0
    s.release()


@pytest.mark.skipif(sys.platform != "win32" or not os.path.isfile(os.path.join(os.path.dirname(sys.executable),
                                                                              "pythonw.exe")),
                    reason="pythonw.exe exists on Windows only")
def test_under_pythonw_admit_suite_s_pytest_keeps_its_output(tmp_path):
    # C3/I6 (2026-10-28): every run job on the L15 worker (pythonw.exe) failed exit 1 in seconds with no output and
    # no report: `admit suite` started `pythonw -m pytest` with inherited handles, which under pythonw are none
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    root = os.path.dirname(os.path.dirname(os.path.abspath(A.__file__)))
    script = ("import sys\n"
              "from fieldkit import admission as A\n"
              "sys.exit(A.run([sys.executable, '-c', 'print(\"child said it\")'], root=sys.argv[1]))\n")
    out = tmp_path / "out.txt"
    env = {k: v for k, v in os.environ.items() if k != A.NESTED}
    env["PYTHONPATH"] = root
    with open(out, "wb") as f:
        rc = subprocess.call([pyw, "-c", script, str(tmp_path / "slots")], stdin=subprocess.DEVNULL, stdout=f,
                             stderr=subprocess.STDOUT, env=env, timeout=120)
    assert rc == 0 and "child said it" in out.read_text(encoding="utf-8", errors="replace")


def test_std_handles_hands_over_real_descriptors_only_and_only_on_windows(tmp_path):
    import io
    from fieldkit.core.proc import std_handles
    with open(tmp_path / "o.txt", "w") as real:
        assert std_handles((None, real, io.StringIO()), platform="win32") == {"stdout": real.fileno()}
        assert std_handles((None, real, real), platform="linux") == {}
