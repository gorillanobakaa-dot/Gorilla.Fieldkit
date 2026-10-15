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
