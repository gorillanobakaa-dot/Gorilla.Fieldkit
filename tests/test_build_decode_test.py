# Version: 1.0.0 · updated 26-10-15-12-05
"""decode-test: intel_gpu_top parsing and the verdict rules (the live run needs the VAIO's GPU and a display)."""
from fieldkit.build import decode_test as d

I965 = """[
{
	"engines": {
		"Render/3D": {"busy": 1.000000, "sema": 0.0, "wait": 0.0, "unit": "%"},
		"Blitter": {"busy": 0.000000, "sema": 0.0, "wait": 0.0, "unit": "%"},
		"Video": {"busy": 0.930000, "sema": 0.0, "wait": 0.0, "unit": "%"}
	},
	"clients": {}
},
"""


def test_parses_the_ivy_bridge_engine_names():
    s = d.parse_gpu_top(I965)
    assert s == [{"Render/3D": 1.0, "Blitter": 0.0, "Video": 0.93}]


def test_verdicts():
    assert d.verdict(0.93, 36.0) == "HARDWARE"
    assert d.verdict(0.0, 300.0) == "SOFTWARE"
    assert d.verdict(None, 10.0) == "UNMEASURED"


def test_what_was_closed_is_reopened_even_when_the_test_crashes(tmp_path, monkeypatch):
    # 2026-10-15: the test raised after closing the owner's browser and nothing reopened it
    from fieldkit.build import isolation
    calls = []
    monkeypatch.setattr(d, "make_clip", lambda *a, **k: (True, ""))
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": ""})())
    monkeypatch.setattr(isolation, "wait_own", lambda **k: [])
    monkeypatch.setattr(isolation, "warn", lambda *a, **k: None)
    monkeypatch.setattr(isolation, "browsers", lambda: [{"pid": 1, "name": "firefox", "cmdline": ["x"]}])
    monkeypatch.setattr(isolation, "heavy_apps", lambda: [])
    monkeypatch.setattr(isolation, "quiet", lambda found, out=None: found)
    monkeypatch.setattr(isolation, "survey", lambda: {})
    monkeypatch.setattr(isolation, "noisy", lambda s: [])
    monkeypatch.setattr(isolation, "restore", lambda closed, out=None: calls.append(("restore", closed)))
    monkeypatch.setattr(isolation, "finished", lambda *a, **k: calls.append(("finished",)))

    def boom(*a, **k):
        raise NameError("a bug in the test")
    monkeypatch.setattr(d, "_measure", boom)
    import pytest
    with pytest.raises(NameError):
        d.run(tmp_path, out=lambda *a: None)
    assert calls[0] == ("restore", [{"pid": 1, "name": "firefox", "cmdline": ["x"]}]) and calls[1] == ("finished",)
