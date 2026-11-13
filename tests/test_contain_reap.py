"""P37: a supervisor that is the child subreaper must reap every exited orphan each tick (no zombie pile) and keep
its tick cheap. The driver runs in its own process: becoming the subreaper is permanent for a process."""
import json
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="subreaper is Linux only")

DRIVER = textwrap.dedent('''
    import json, os, sys, time
    from fieldkit.core import contain
    contain.enable_subreaper()
    N = int(sys.argv[1])
    worker = (
        "import os, time\\n"
        f"for i in range({N}):\\n"
        "    if os.fork() == 0:\\n"
        "        if os.fork() == 0:\\n"
        "            time.sleep(0.05); os._exit(0)\\n"
        "        os._exit(0)\\n"
        "    os.wait()\\n"
        "time.sleep(3)\\n")
    contain.POLL_S = 0.2
    zmax = 0
    samples = [0]
    def tick():                              # psutil's scan is costly: sample a few times, not every tick
        global zmax
        samples[0] += 1
        if samples[0] % 8 == 0:
            zmax = max(zmax, contain.zombie_children())
    t0 = os.times()
    with contain.Container("reap-test") as c:
        r = c.run([sys.executable, "-c", worker], on_tick=tick)
    t1 = os.times()
    print(json.dumps({"status": r.status, "zombies_after": contain.zombie_children(), "zmax": zmax,
                      "cpu": (t1.user - t0.user) + (t1.system - t0.system)}))
''')


def drive(n):
    out = subprocess.run([sys.executable, "-c", DRIVER, str(n)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_no_zombies_and_a_cheap_tick_with_2000_short_children():
    r = drive(2000)
    assert r["status"] == "ok"
    assert r["zombies_after"] == 0
    assert r["zmax"] < 300, r         # zombies never pile up (without the reaper: ~2000)
    assert r["cpu"] < 6.0, r          # the runner's own CPU for a ~8 s run


def test_a_popen_child_of_the_supervisor_keeps_its_exit_status():
    code = textwrap.dedent('''
        import subprocess, sys, time
        from fieldkit.core import contain
        contain.enable_subreaper()
        p = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(7)"])
        time.sleep(0.5)                      # it is now a zombie child of ours
        contain.reap_orphans()
        print(p.wait())
    ''')
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "7", out
