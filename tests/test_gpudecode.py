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


def test_a_trace_that_lost_its_gpu_data_is_not_a_no():
    # 2026-10-22: a -filemode trace of 30 s playback held 2 packets (Explorer, DWM). That is a broken
    # recording, and must never read as "no hardware decoding".
    packets = [{"adapter": "Intel(R) Iris(R) Xe Graphics", "process": p, "node": 1, "count": 1,
                "gpu_us": 500.0, "start": 27.9} for p in ("explorer.exe (4932)", "dwm.exe (1960)")]
    result, code = gd.judge(packets, engines())
    assert code == 1
    assert result["verdict"].startswith("INCOMPLETE TRACE: only 2 GPU packets")


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
    assert "-start GPU -start Video" in s
    # -filemode lost the GPU packets (2 left in a 30 s playback trace, 2026-10-22)
    assert "-filemode" not in s
    assert "Start-Sleep -Seconds 30" in s
    assert s.index("-status") < s.index("-start GPU")


def test_trace_refuses_without_yes_and_bad_lengths(tmp_path):
    code, outp = run("trace", "--seconds", "10", "--out", tmp_path / "t")
    assert code == 2 and "--yes" in outp
    assert not (tmp_path / "t").exists()
    assert run("trace", "--seconds", "600", "--yes", "--out", tmp_path / "t")[0] == 2


def test_read_refuses_a_missing_trace(tmp_path):
    code, outp = run("read", tmp_path / "nothing.etl")
    assert code == 1 and "no such trace" in outp


def test_manual_command_is_the_one_proven_by_hand():
    # 2026-10-22: this exact line, pasted into an admin PowerShell, recorded 3,220 VideoDecode packets
    cmd = gd.manual_command(r"C:\x\gpu-30s.etl", 30)
    assert cmd.startswith("cd $HOME; ")
    assert r"C:\Windows\System32\wpr.exe -start GPU -start Video; Start-Sleep -Seconds 30; " in cmd
    assert cmd.endswith(r'C:\Windows\System32\wpr.exe -stop "C:\x\gpu-30s.etl"')
    assert "-filemode" not in cmd and "&&" not in cmd


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


# ── macOS backend (kits/gpudecode/gpudecode_mac.py), from REAL logs ─────────────────────────────────────────────
# testdata/mac-air-log-hw.txt and -sw.txt: `gpudecode.py mac-ab macair` on 2026-10-25, MacBook Air 4,1, macOS 10.13.6,
# Gorilla 158 for the Air, 1080p60 H.264, 30 s each. On: 21 of 1,800 dropped, Apple's decoder driver in the rdd
# process. Off: 1,505 of 1,511 dropped, no process with the driver.
mspec = importlib.util.spec_from_file_location("gpudecode_mac", KIT / "gpudecode_mac.py")
gm = importlib.util.module_from_spec(mspec)
mspec.loader.exec_module(gm)
HW = (DATA / "mac-air-log-hw.txt").read_text(encoding="utf-8")
SW = (DATA / "mac-air-log-sw.txt").read_text(encoding="utf-8")


def test_mac_real_ab_is_hardware_decode_in_the_rdd_process():
    r = gm.judge(gm.parse_log(HW), gm.parse_log(SW))
    assert r["verdict"].startswith("HARDWARE DECODE"), r["verdict"]
    assert [p["role"] for p in r["hw"]["decoder_procs"]] == ["rdd"]
    assert r["hw"]["decoder_procs"][0]["drivers"]["AppleIntelSNBVA"] == 6
    assert (r["hw"]["total"], r["hw"]["dropped"], r["sw"]["total"], r["sw"]["dropped"]) == (1800, 21, 1511, 1505)
    assert "DTrace" in r["not_done"]


def test_mac_videotoolbox_alone_is_not_the_decoder():
    """VideoToolbox is the API, mapped in every browser process; only the Intel/GVA driver counts."""
    hw = gm.parse_log(HW)
    hw["procs"] = [dict(p, drivers={"VideoToolbox": 4}) for p in hw["procs"]]
    assert gm.judge(hw, gm.parse_log(SW))["verdict"].startswith("NO HARDWARE DECODE")


def test_mac_driver_with_decoding_off_is_incomplete_never_a_yes():
    assert gm.judge(gm.parse_log(HW), gm.parse_log(HW))["verdict"].startswith("INCOMPLETE: the decoder driver")


def test_mac_a_run_without_frames_is_incomplete_never_a_no():
    sw = gm.parse_log(SW)
    sw["result"] = None
    assert gm.judge(gm.parse_log(HW), sw)["verdict"].startswith("INCOMPLETE: a run reported no frames")
    hw = gm.parse_log(HW)
    hw["procs"] = []
    hw["result"] = {"total": 0, "dropped": 0}
    assert gm.judge(hw, gm.parse_log(SW))["verdict"].startswith("INCOMPLETE")


def test_mac_verdict_command_reads_saved_logs(tmp_path):
    (tmp_path / "log-hw.txt").write_text(HW, encoding="utf-8")
    (tmp_path / "log-sw.txt").write_text(SW, encoding="utf-8")
    code, text = run("mac-verdict", str(tmp_path))
    assert code == 0 and "VERDICT: HARDWARE DECODE" in text
    assert json.loads((tmp_path / "verdict.json").read_text())["hw"]["dropped"] == 21


def test_mac_ssh_retries_only_a_lost_connection():
    calls = []

    class R:
        def __init__(self, rc):
            self.returncode, self.stdout, self.stderr = rc, "", ""

    def fake(argv, **kw):
        calls.append(argv)
        return R(255 if len(calls) < 2 else 0)
    assert gm.ssh("m", "true", run=fake, sleep=lambda s: None)[0] == 0 and len(calls) == 2
    calls.clear()
    assert gm.ssh("m", "false", run=lambda a, **k: calls.append(a) or R(1), sleep=lambda s: None)[0] == 1
    assert len(calls) == 1


def test_mac_run_script_measures_before_vmmap_and_stops_only_its_own():
    s = gm.RUN
    assert s.index('RESULT:') < s.index("vmmap $p")      # vmmap suspends the process: never during the measurement
    assert "BEFORE=" in s and "comm -13" in s and "killall" not in s and "pkill" not in s
    assert '-profile "$P"' in s                          # a fresh test profile, never the person's own
