"""fieldkit.thermal.vaio: the Sony VAIO through sensors.gorilla (2026-10-15)."""
from fieldkit.thermal import vaio


class Fake:
    def get_cpu_temps(self):
        return {"CPU/GPU Package Peak (Intel i7 / HD 4000)": 77.0, "Core 0 (Intel i7 CPU Core)": 73.0,
                "Core 1 (Intel i7 CPU Core)": 79.0}

    def get_rapl_power(self):
        return {"package": 30.44}

    def get_frequencies(self):
        return (2.8934, 950, 1150)

    def get_throttle_stats(self):
        return {"pkg": 0, "pkg_ms": 0}


def test_snapshot_and_line_from_the_tool(tmp_path):
    sony = tmp_path / "sony"
    sony.mkdir()
    (sony / "fanspeed").write_text("58\n")
    (sony / "thermal_control").write_text("performance\n")
    s = vaio.snapshot(Fake(), sony)
    assert (s["package_c"], s["core_max_c"], s["fan_pct"], s["profile"], s["package_w"], s["cpu_ghz"]) == \
           (77.0, 79.0, 58, "performance", 30.4, 2.89)
    assert "throttling since boot: 0 events" in vaio.line(s)


def test_log_writes_one_line_per_interval_until_time_is_up(tmp_path, monkeypatch):
    monkeypatch.setattr(vaio, "_daemon", lambda: "active")
    out = tmp_path / "t.log"
    n = vaio.log(out, interval=0, seconds=0.01, mod=Fake())
    assert n >= 1 and out.read_text().count("\n") == n


def test_no_tool_is_said_not_invented(tmp_path):
    assert vaio.load(tmp_path / "nope") is None
