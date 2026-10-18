

def test_the_elevated_packet_helper_closes_its_admin_window(tmp_path):
    # 2026-10-18: elevate.py run keeps the window open (-NoExit) unless --close; the helper must pass it
    from fieldkit.leakgate import sensors
    calls = []

    class R:
        returncode, stdout, stderr = 1, "", "refused"
    ep = tmp_path / "elevate.py"
    ep.write_text("")
    h = sensors.ElevatedPktmon(tmp_path, elevate_py=ep, runner=lambda argv, **k: calls.append(argv) or R())
    try:
        h.start(say=lambda m: None)
    except RuntimeError:
        pass
    assert calls and "--close" in calls[0]
