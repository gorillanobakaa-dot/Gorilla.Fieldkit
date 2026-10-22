"""gpudecode (kits/gpudecode/gpudecode.py): the hardware-decoding verdict, from a REAL trace.

testdata/packets-iris-xe.csv is 3 s (200-203 s) of a wpaexporter packet export of a 430 s trace,
Gorilla Firefox playing a 1080p60 H.264 video on Intel Iris Xe, 2026-10-22; engines-iris-xe.json
is the same machine's counter engine list. The full trace said: 16,803 VideoDecode packets, steady
60 per second. Nothing here elevates, records, downloads or installs.
"""
import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1] / "kits" / "gpudecode"
TOOL = KIT / "gpudecode.py"
DATA = KIT / "testdata"
pytestmark = pytest.mark.skipif(not TOOL.is_file(), reason="kits/gpudecode missing")

spec = importlib.util.spec_from_file_location("gpudecode", TOOL)
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)

PACKETS = DATA / "packets-iris-xe.csv"
ENGINES = DATA / "engines-iris-xe.json"


def run(*args):
    r = subprocess.run([sys.executable, "-I", str(TOOL), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, r.stdout + r.stderr


def engines():
    return json.loads(ENGINES.read_text(encoding="utf-8"))


def write_without(tmp_path, ane):
    """The fixture with every packet on one node removed: what a software decode looks like."""
    dst = tmp_path / "packets.csv"
    with open(PACKETS, encoding="utf-8", newline="") as f, open(dst, "w", encoding="utf-8", newline="") as g:
        r, w = csv.reader(f), csv.writer(g, lineterminator="\n")
        head = next(r)
        w.writerow(head)
        i = head.index("A/N/E")
        for row in r:
            if row[i] != ane:
                w.writerow(row)
    return dst


def test_real_trace_says_hardware_decode():
    code, outp = run("verdict", PACKETS, "--engines", ENGINES)
    assert code == 0, outp
    assert "VERDICT: HARDWARE DECODE: firefox.exe (11308)" in outp
    assert "VideoDecode engine (WPA node 2)" in outp
    assert "steady 60 per second" in outp


def test_rows_name_every_engine_firefox_used():
    result, code = gd.judge(gd.read_packets(PACKETS), engines())
    by_node = {(r["process"], r["node"]): r for r in result["rows"]}
    assert by_node[("firefox.exe (11308)", 1)]["engine"] == "3D"
    assert by_node[("firefox.exe (11308)", 2)]["engine"] == "VideoDecode"
    assert by_node[("firefox.exe (11308)", 4)]["engine"] == "VideoProcessing"
    assert by_node[("firefox.exe (11308)", 2)]["packets"] == 179
    # one decode and one video-processing pass per frame, three 3D passes
    assert by_node[("firefox.exe (11308)", 2)]["per_second"] == 60
    assert by_node[("firefox.exe (11308)", 4)]["per_second"] == 60
    assert by_node[("firefox.exe (11308)", 1)]["per_second"] == 180


def test_rate_is_the_steady_median_not_a_burst():
    # start-up trickle, three steady seconds, one catch-up burst: the frame rate is 60
    packets = []
    for sec, n in [(10, 5), (11, 60), (12, 60), (13, 60), (14, 120)]:
        packets += [{"adapter": "Intel(R) Iris(R) Xe Graphics", "process": "firefox.exe (1)", "node": 2,
                     "count": 1, "gpu_us": 100.0, "start": sec + i / (n + 1)} for i in range(n)]
    result, code = gd.judge(packets, engines())
    assert code == 0
    assert result["rows"][0]["per_second"] == 60
    assert result["rows"][0]["busy_seconds"] == 5


def test_software_renderer_is_never_a_video_engine():
    result, _ = gd.judge(gd.read_packets(PACKETS), engines())
    soft = [r for r in result["rows"] if r["process"] == "firefox.exe (2588)"]
    assert soft and soft[0]["engine"] == "software (CPU)"


def test_no_decode_packets_is_no_hardware_decode(tmp_path):
    code, outp = run("verdict", write_without(tmp_path, "2/2/1"), "--engines", ENGINES)
    assert code == 3, outp
    assert "VERDICT: NO HARDWARE DECODE" in outp


def test_wpa_nodes_count_from_one(tmp_path):
    # Shift the map by one (engine 2 = VideoDecode): node 2 is then Copy, and the verdict must not
    # call it a decode. Counting nodes from 0 would make this pass wrongly.
    e = engines()
    luid = next(l for l, m in e["gpus"].items() if "VideoDecode" in m.values())
    e["gpus"][luid] = {"0": "3D", "1": "Copy", "2": "VideoDecode", "3": "VideoProcessing"}
    result, code = gd.judge(gd.read_packets(PACKETS), e)
    node2 = [r for r in result["rows"] if r["node"] == 2 and "11308" in r["process"]]
    assert node2[0]["engine"] == "Copy"
    assert code == 3


def test_two_video_gpus_are_unknown_not_guessed():
    e = engines()
    luid = next(l for l, m in e["gpus"].items() if "VideoDecode" in m.values())
    e["gpus"]["0x00000000_0x0000AAAA"] = dict(e["gpus"][luid])
    result, code = gd.judge(gd.read_packets(PACKETS), e)
    assert code == 3
    assert result["verdict"].startswith("UNKNOWN")


def test_process_filter():
    result, code = gd.judge(gd.read_packets(PACKETS), engines(), process="chrome")
    assert code == 3 and not result["rows"]


def test_engine_instances_parse_and_typed_wins():
    lines = [
        # typed first, blank after: the blank must not overwrite the name
        r"\GPU Engine(pid_2_luid_0x00000000_0x0000F180_phys_0_eng_1_engtype_VideoDecode)\Utilization Percentage",
        r"\GPU Engine(pid_1_luid_0x00000000_0x0000F180_phys_0_eng_1_engtype_)\Utilization Percentage",
        r"\GPU Engine(pid_3_luid_0x00000000_0x0000F180_phys_0_eng_0_engtype_3D)\Utilization Percentage",
        r"\GPU Engine(pid_3_luid_0x00000000_0x0000F559_phys_0_eng_0_engtype_3D)\Utilization Percentage",
        "not a counter",
    ]
    gpus = gd.parse_engine_instances(lines)
    assert gpus["0x00000000_0x0000F180"] == {1: "VideoDecode", 0: "3D"}
    assert list(gd.video_gpus(gpus)) == ["0x00000000_0x0000F180"]


def test_live_counters_summary():
    text = "\n".join([
        '"(PDH-CSV 4.0)","\\\\PC\\GPU Engine(pid_42_luid_0x0_0x1_phys_0_eng_1_engtype_VideoDecode)\\Utilization Percentage",'
        '"\\\\PC\\GPU Engine(pid_42_luid_0x0_0x1_phys_0_eng_0_engtype_3D)\\Utilization Percentage",'
        '"\\\\PC\\GPU Engine(pid_7_luid_0x0_0x1_phys_0_eng_0_engtype_3D)\\Utilization Percentage"',
        '"10/22/2026 00:00:01.000","4.0","10.0","1.0"',
        '"10/22/2026 00:00:02.000","6.0","20.0","3.0"',
        '"10/22/2026 00:00:03.000"," ","","2.0"',
    ])
    series = gd.parse_typeperf_csv(text)
    rows = gd.summarize_live(series, {42: "firefox.exe", 7: "dwm.exe"}, "firefox")
    dec = [r for r in rows if r["engine"] == "VideoDecode"][0]
    assert dec == {"process": "firefox.exe", "pid": 42, "engine": "VideoDecode", "avg": 5.0, "peak": 6.0}
    assert all(r["process"] == "firefox.exe" for r in rows)


def test_basic_display_adapter_is_a_problem():
    f = gd.adapter_findings([{"Name": "Microsoft Basic Display Adapter", "Status": "OK"},
                             {"Name": "Intel(R) HD Graphics 4000", "Status": "OK"}])
    assert len(f) == 1 and "NO hardware video decoding" in f[0]
    assert gd.adapter_findings([{"Name": "Intel(R) HD Graphics 4000", "Status": "Error"}])


def test_installer_signature_must_be_microsofts():
    ok, _ = gd.signature_ok({"Status": 0, "SignerCertificate": {
        "Subject": "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"}})
    assert ok
    assert not gd.signature_ok({"Status": 1, "SignerCertificate": {"Subject": "O=Microsoft Corporation"}})[0]
    assert not gd.signature_ok({"Status": "Valid", "SignerCertificate": {"Subject": "CN=Evil, O=Evil Ltd"}})[0]
    assert not gd.signature_ok({})[0]


def test_installer_installs_only_the_toolkit_with_ceip_off():
    args = gd.adk_install_args("x.log")
    assert args[args.index("/features") + 1] == "OptionId.WindowsPerformanceToolkit"
    assert args[args.index("/ceip") + 1] == "off"
    assert "/quiet" in args and "/norestart" in args
    assert sum(a.startswith("OptionId.") for a in args) == 1


def test_trace_script_never_cancels_someone_elses_trace(tmp_path):
    s = gd.trace_script(tmp_path / "gpu.etl", 30, tmp_path / "done")
    assert "-cancel" not in s
    assert "WPR is recording" in s and "BUSY" in s
    assert "-start GPU -start Video -filemode" in s
    assert "Start-Sleep -Seconds 30" in s
    assert s.index("-status") < s.index("-start GPU")


def test_trace_refuses_without_yes_and_bad_lengths(tmp_path):
    code, outp = run("trace", "--seconds", "10", "--out", tmp_path / "t")
    assert code == 2 and "--yes" in outp
    assert not (tmp_path / "t").exists()
    assert run("trace", "--seconds", "600", "--yes", "--out", tmp_path / "t")[0] == 2


def test_adk_install_refuses_without_yes():
    code, outp = run("adk", "install")
    assert code == 2 and "refused" in outp


def test_explain_is_the_safe_procedure():
    code, outp = run("explain")
    assert code == 0
    assert "H.264" in outp and "Basic Display" in outp and "HARDWARE DECODE" in outp
    assert "Never convert the .etl with tracerpt" in outp


def test_profile_exports_every_packet_ungrouped():
    p = (KIT / "gpu-packets.wpaProfile").read_text(encoding="utf-8-sig")
    assert 'KeyColumnCount="0"' in p and 'KeyColumnCount="7"' not in p
    assert 'Name="A/N/E"' in p and 'Name="Start Execution"' in p
    assert "<FileReferences />" in p    # no trace path baked in


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_doctor_runs_and_answers():
    code, outp = run("doctor")
    assert code in (0, 3), outp
    assert "VERDICT:" in outp and "wpr.exe" in outp
