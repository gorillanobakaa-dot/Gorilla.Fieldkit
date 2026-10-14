"""VideoKit (kits/videokit/videokit.py), run as a user would: subprocess, a real synthetic video.

The test video is made by ffmpeg itself: 2 s of a moving test pattern, 3 s frozen, 2 s moving,
dark, with a tone - so every command has something real to measure, cut and correct.
"""
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "kits" / "videokit" / "videokit.py"
pytestmark = [pytest.mark.skipif(not TOOL.is_file(), reason="kits/videokit missing"),
              pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed (videokit doctor)")]


def run(*args, cwd=None):
    r = subprocess.run([sys.executable, "-I", str(TOOL), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300, cwd=cwd)
    return r.returncode, r.stdout + r.stderr


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    # fps=30 before tpad is load-bearing, not decoration. After trim+setpts the
    # frame rate does not survive on every ffmpeg build, and tpad then pads
    # nothing: on Debian 13 (ffmpeg 7.1.5) this clip came out 3.97s instead of
    # ~7s, the frozen stretch was simply absent, and 20 tests in this file
    # failed. Measured 2026-10-13: with fps=30 the padded segment is 91 frames
    # and 3.03s, as intended. Stating the rate makes the fixture deterministic
    # on every build rather than relying on one that happens to propagate it.
    d = tmp_path_factory.mktemp("vk")
    out = d / "clip.mp4"
    graph = ("[0:v]trim=0:2,setpts=PTS-STARTPTS[a];[0:v]trim=2:2.04,setpts=PTS-STARTPTS,fps=30,tpad=stop_mode=clone:"
             "stop_duration=3[b];[0:v]trim=2:4,setpts=PTS-STARTPTS[c];[a][b][c]concat=n=3:v=1,"
             "eq=brightness=-0.25,format=yuv420p[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=240x426:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=7", "-filter_complex", graph,
                    "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)],
                   check=True, capture_output=True)
    return out


def test_doctor_reports_and_passes_here():
    rc, out = run("doctor")
    assert rc == 0 and "ffmpeg filter vidstabdetect" in out and "MISSING" not in out


def test_probe(clip):
    rc, out = run("probe", clip, "--json")
    p = json.loads(out)
    assert rc == 0 and p["width"] == 240 and p["audio"] is True and 6.5 < p["duration"] < 7.5


def test_frames_named_by_time(clip, tmp_path):
    rc, _ = run("frames", clip, tmp_path / "f", "--every", "2", "--width", "120")
    assert rc == 0
    assert sorted(p.name for p in (tmp_path / "f").iterdir())[:2] == ["t0000.0.jpg", "t0002.0.jpg"]


def test_measure_sees_the_frozen_part_and_is_deterministic(clip, tmp_path):
    assert run("measure", clip, tmp_path / "m1.csv", "--fps", "4")[0] == 0
    assert run("measure", clip, tmp_path / "m2.csv", "--fps", "4")[0] == 0
    assert (tmp_path / "m1.csv").read_bytes() == (tmp_path / "m2.csv").read_bytes()
    rows = list(csv.DictReader(open(tmp_path / "m1.csv")))
    frozen = [float(r["motion"]) for r in rows if 2.5 <= float(r["t"]) < 4.5]
    moving = [float(r["motion"]) for r in rows if 0.5 <= float(r["t"]) < 1.75]
    assert max(frozen) < 0.5 < min(moving)


def test_suggest_speeds_up_the_idle_run(clip, tmp_path):
    run("measure", clip, tmp_path / "m.csv", "--fps", "4")
    rc, out = run("suggest", tmp_path / "m.csv", tmp_path / "plan.txt", "--idle", "0.5", "--min-idle", "1.5",
                  "--keep-idle", "0.5", "--idle-speed", "8")
    plan = (tmp_path / "plan.txt").read_text()
    assert rc == 0 and "speed" in plan and "keep" in plan
    rc, out = run("suggest", tmp_path / "m.csv", tmp_path / "cut.txt", "--idle", "0.5", "--min-idle", "1.5",
                  "--idle-speed", "0")
    assert "# cut" in (tmp_path / "cut.txt").read_text()


def test_render_cuts_speeds_brightens_and_rejects_bad_plans(clip, tmp_path):
    plan = tmp_path / "plan.txt"
    plan.write_text("keep 0 2  # moving\nspeed 2 5 6\nkeep 5 7\n", encoding="utf-8")
    out = tmp_path / "out.mp4"
    rc, text = run("render", clip, plan, out)
    assert rc == 0, text
    p = json.loads(run("probe", out, "--json")[1])
    assert 4.0 < p["duration"] < 5.0 and p["audio"]  # 2 + 0.5 + 2
    m1, m2 = tmp_path / "src.csv", tmp_path / "out.csv"
    run("measure", clip, m1)
    run("measure", out, m2)
    luma = lambda f: sum(float(r["luma"]) for r in csv.DictReader(open(f))) / sum(1 for _ in csv.DictReader(open(f)))
    assert luma(m2) > luma(m1) + 5  # the dark clip was brightened
    for bad in ("keep 3 1\n", "speed 0 2 40\n", "jump 0 1\n", "# nothing\n", "keep 0 99\n"):
        plan.write_text(bad, encoding="utf-8")
        assert run("render", clip, plan, tmp_path / "x.mp4", "--no-stab")[0] != 0, bad


@pytest.fixture(scope="module")
def sway(tmp_path_factory):
    """8 s of a test pattern seen through a window that sways slowly (period 2 s, +-12 px),
    like a hand holding a phone. Played 8x faster the sway becomes violent shake."""
    d = tmp_path_factory.mktemp("sway")
    out, still = d / "sway.mp4", d / "pattern.png"
    # a STATIC pattern: testsrc2 animates by itself and that would be measured as movement (found 2026-10-12)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=400x600:rate=1:duration=1",
                    "-frames:v", "1", str(still)], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", "8", "-r", "30", "-i", str(still),
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=8", "-vf",
                    "crop=320:480:x='40+12*sin(2*PI*t/2)':y='60+6*cos(2*PI*t/2)',format=yuv420p",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)], check=True, capture_output=True)
    return out


def test_shake_measures_real_movement_and_zero_for_a_still(sway, tmp_path):
    rc, out = run("shake", sway, "--out", tmp_path / "s.csv")
    assert rc == 0
    rows = list(csv.DictReader(open(tmp_path / "s.csv")))
    mean_shift = sum(float(r["shift"]) for r in rows[1:]) / (len(rows) - 1)
    assert 0.6 < mean_shift < 1.1, mean_shift  # calculated 0.73 px/frame at the 240-px measuring width
    plan = tmp_path / "p.txt"
    plan.write_text("hold 0 8 2\n", encoding="utf-8")
    assert run("render", sway, plan, tmp_path / "still.mp4", "--no-stab")[0] == 0
    rc, out = run("shake", tmp_path / "still.mp4")
    assert "shift/frame 0.00 px" in out


def test_shakecompare_catches_sped_up_sway_and_passes_holds(sway, tmp_path):
    fast, held = tmp_path / "fast.txt", tmp_path / "held.txt"
    fast.write_text("keep 0 2\nspeed 2 8 8\n", encoding="utf-8")
    held.write_text("keep 0 2\nhold 2 8 1\n", encoding="utf-8")
    assert run("render", sway, fast, tmp_path / "fast.mp4", "--no-stab")[0] == 0
    assert run("render", sway, held, tmp_path / "held.mp4")[0] == 0
    rc, out = run("shakecompare", sway, fast, tmp_path / "fast.mp4", "--strict")
    assert rc == 1 and "!" in out.split("\n")[3], out  # the speed row is flagged
    rc, out = run("shakecompare", sway, held, tmp_path / "held.mp4", "--strict")
    assert rc == 0, out
    log = (tmp_path / "held.mp4.render.txt").read_text()
    assert "still at" in log and "#win 1 hold" in log and "jitter plain" in log


@pytest.fixture(scope="module")
def two_shots(tmp_path_factory):
    """4 s: colour bars for 2 s, then a hard cut to a different static pattern for 2 s."""
    d = tmp_path_factory.mktemp("shots")
    out = d / "shots.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "smptebars=size=240x426:rate=30:duration=2",
                    "-f", "lavfi", "-i", "testsrc=size=240x426:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=500:duration=4", "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1,noise=alls=6:allf=t,format=yuv420p[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)], check=True, capture_output=True)
    return out


def test_flow_finds_freezes_from_holds(sway, tmp_path):
    plan = tmp_path / "p.txt"
    plan.write_text("keep 0 2\nhold 2 8 1\nkeep 6 8\n", encoding="utf-8")
    assert run("render", sway, plan, tmp_path / "held.mp4", "--no-stab")[0] == 0
    rc, out = run("flow", tmp_path / "held.mp4", "--strict")
    assert rc == 1 and "freeze" in out


def test_best_picks_real_footage_no_freeze(sway, tmp_path):
    plan = tmp_path / "p.txt"
    plan.write_text("keep 0 2\nbest 2 8 1\nkeep 6 8\n", encoding="utf-8")
    rc, out = run("render", sway, plan, tmp_path / "best.mp4", "--no-stab")
    assert rc == 0, out
    log = (tmp_path / "best.mp4.render.txt").read_text()
    assert "steadiest 1s starts at" in log and "#win 1 best" in log
    rc, out = run("flow", tmp_path / "best.mp4", "--strict")
    assert rc == 0 and "0 freeze" in out, out
    with pytest.raises(AssertionError):  # best longer than its range is refused
        plan.write_text("best 2 2.5 1\n", encoding="utf-8")
        assert run("render", sway, plan, tmp_path / "x.mp4")[0] == 0


def test_flow_hard_cut_at_join_vs_inside_a_shot(two_shots, tmp_path):
    rc, out = run("flow", two_shots)
    assert "hard cut  at   2.0" in out  # the source has its own cut at 2 s
    joins = tmp_path / "joins.txt"
    joins.write_text("keep 0 1.5\nkeep 2.5 4\n", encoding="utf-8")
    assert run("render", two_shots, joins, tmp_path / "hard.mp4", "--no-stab", "--xfade", "0")[0] == 0
    rc, out = run("flow", tmp_path / "hard.mp4", "--strict")
    assert rc == 1 and "at a join" in out, out
    assert run("render", two_shots, joins, tmp_path / "soft.mp4", "--no-stab")[0] == 0  # default 0.3 s dissolve
    rc, out = run("flow", tmp_path / "soft.mp4", "--strict")
    assert rc == 0, out
    inside = tmp_path / "inside.txt"
    inside.write_text("keep 0 1\nkeep 1.5 3.5\n", encoding="utf-8")  # second shot contains the source's own cut
    assert run("render", two_shots, inside, tmp_path / "inside.mp4", "--no-stab")[0] == 0
    rc, out = run("flow", tmp_path / "inside.mp4", "--strict")
    assert rc == 0 and "inside a shot" in out, out


def _shift(out):
    return float(out.split("shift/frame ")[1].split(" px")[0])


def test_lock_tripod_removes_the_sway_and_gimbal_smooths_it(sway, tmp_path):
    base = _shift(run("shake", sway)[1])
    rc, out = run("lock", sway, tmp_path / "tri.mp4", "--mode", "tripod")
    assert rc == 0, out
    tri = _shift(run("shake", tmp_path / "tri.mp4")[1])
    assert tri < base * 0.35, (base, tri)  # tripod holds the pattern still
    rc, out = run("lock", sway, tmp_path / "gim.mp4", "--mode", "gimbal", "--follow", "0.25")
    assert rc == 0 and "jitter" in out, out
    jin, jout = (float(x) for x in out.split("jitter ")[1].split(" px")[0].split(" -> "))
    assert jout < jin * 0.6, out
    assert json.loads(run("probe", tmp_path / "gim.mp4", "--json")[1])["audio"] is True


def test_render_auto_tries_gimbal_and_logs_every_candidate(sway, tmp_path):
    plan = tmp_path / "p.txt"
    plan.write_text("keep 0 3\nkeep 4 7\n", encoding="utf-8")
    rc, out = run("render", sway, plan, tmp_path / "o.mp4")
    assert rc == 0, out
    log = (tmp_path / "o.mp4.render.txt").read_text()
    assert "gimbal 0.25" in log and "tripod" in log and "jitter plain" in log
    # dissolves are timed on picture length, so no join snaps. (A tripod-locked STATIC test pattern is
    # correctly reported as frozen - every frame identical; real footage keeps light and noise changes.)
    assert "0 hard cut" in run("flow", tmp_path / "o.mp4")[1]


def test_doctor_requires_opencv():
    rc, out = run("doctor")
    assert "opencv-python" in out


def _ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True, capture_output=True)


def test_vfr_timestamps_are_real_not_frame_count(tmp_path):
    """1 s at 30 fps then 2 s at 15 fps: frame 59 is at ~2.93 s, not 59/30 = 1.97 s (the old drift)."""
    a, b, v = tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "vfr.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=size=240x426:rate=30:duration=1", "-c:v", "libx264", a)
    _ff("-f", "lavfi", "-i", "testsrc2=size=240x426:rate=15:duration=2", "-c:v", "libx264", b)
    (tmp_path / "l.txt").write_text(f"file '{a.as_posix()}'\nfile '{b.as_posix()}'\n", encoding="utf-8")
    _ff("-f", "concat", "-safe", "0", "-i", tmp_path / "l.txt", "-fps_mode", "passthrough", "-c:v", "libx264", v)
    rc, out = run("analyse", v)
    assert rc == 0
    last = float(out.split(" over ")[1].split(" s")[0])
    assert 2.8 < last < 3.0, out
    assert json.loads(run("probe", v, "--json")[1])["vfr"] is True


def test_analysis_is_cached(clip):
    import time
    run("analyse", clip)
    t0 = time.time()
    rc, _ = run("analyse", clip)
    assert rc == 0 and time.time() - t0 < 5


def test_hdr_is_detected_and_tone_mapped(tmp_path):
    v = tmp_path / "hlg.mp4"
    # libx264 drops -color_trc; the tags must go through -x264-params (found 2026-10-12)
    _ff("-f", "lavfi", "-i", "testsrc2=size=240x426:rate=30:duration=2", "-pix_fmt", "yuv420p10le",
        "-c:v", "libx264", "-x264-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc", v)
    p = json.loads(run("probe", v, "--json")[1])
    assert p["hdr"] and p["bit10"]
    plan = tmp_path / "p.txt"
    plan.write_text("keep 0 1.5\n", encoding="utf-8")
    rc, out = run("render", v, plan, tmp_path / "o.mp4", "--no-stab")
    assert rc == 0 and "HDR tone-mapped" in (tmp_path / "o.mp4.render.txt").read_text(), out


def test_rotation_metadata_swaps_reported_size(tmp_path):
    src, v = tmp_path / "s.mp4", tmp_path / "rot.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=1", "-c:v", "libx264", src)
    _ff("-display_rotation", "90", "-i", src, "-c", "copy", v)
    p = json.loads(run("probe", v, "--json")[1])
    assert abs(p["rotation"]) == 90 and (p["width"], p["height"]) == (240, 320), p


def test_distortion_guard_measures_bending():
    import importlib.util
    import numpy as np
    spec = importlib.util.spec_from_file_location("vk", TOOL)
    vk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vk)
    assert vk.distortion([np.eye(3)], 320, 480) == (0.0, 0.0)
    tilt = np.array([[1, 0.05, 0], [0, 1, 0], [0.0008, 0, 1]])
    ang, area = vk.distortion([tilt], 320, 480)
    assert ang > 3.0  # would be rejected by render's default --max-bend 3


def test_events_and_draft_find_a_change_and_a_scroll(tmp_path):
    """A still pattern; at 3 s a box appears (change); 6-11 s a panel scrolls at 180 px/s like a real page
    scroll (continuous); then still. (At 70 px/s it read only as 'change': too gentle to be a scroll.)"""
    pat, panel, v = tmp_path / "pat.png", tmp_path / "panel.png", tmp_path / "ev.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=size=320x480:rate=1:duration=1", "-frames:v", "1", pat)
    # the box must be inside the analysed area (the outer 10% is ignored)
    # random texture: test patterns are mostly vertical stripes and look unchanged when scrolled vertically
    _ff("-f", "lavfi", "-i", "nullsrc=s=160x1200:d=1,geq=lum='random(1)*255':cb=128:cr=128",
        "-frames:v", "1", panel)
    _ff("-loop", "1", "-t", "15", "-r", "30", "-i", pat, "-loop", "1", "-t", "15", "-r", "30", "-i", panel,
        "-filter_complex",
        "[1:v]crop=160:200:0:'if(between(t,6,11),(t-6)*180,if(gt(t,11),900,0))'[p];"
        "[0:v][p]overlay=80:140,drawbox=x=130:y=360:w=60:h=40:color=red:t=fill:enable='gte(t,3)',"
        "noise=alls=3:allf=t,format=yuv420p", "-c:v", "libx264", v)
    rc, out = run("events", v)
    assert rc == 0
    ev = [l.split() for l in out.splitlines() if l.strip().endswith("floor")]
    times = {round(float(x[0][:-1])): x[1] for x in ev}
    assert any(2.5 <= t <= 3.5 and k == "change" for t, k in times.items()), out
    assert sum(1 for t, k in times.items() if 6 <= t <= 11 and k == "continuous") >= 3, out
    rc, out = run("draft", v, tmp_path / "d.txt")
    d = (tmp_path / "d.txt").read_text()
    assert rc == 0 and "continuous change" in d and "screen content changes" in d, d
    assert run("render", v, tmp_path / "d.txt", tmp_path / "o.mp4", "--no-stab")[0] == 0  # the draft renders


def test_doctor_reports_the_video(clip):
    rc, out = run("doctor", "--video", clip)
    assert "video clip.mp4" in out and "opencv features" in out


def test_many_segments_join_in_batches_with_the_same_timeline(sway, tmp_path):
    """12 pieces > JOIN_BATCH (8): joined in batches, yet the length is exactly the planned one and no
    join snaps. (One 45-input chain at 1080p peaked at 3.5 GB; batches bound that.)"""
    plan = tmp_path / "p.txt"
    plan.write_text("".join(f"keep {i * 0.6:.1f} {i * 0.6 + 0.5:.1f}\n" for i in range(12)), encoding="utf-8")
    rc, out = run("render", sway, plan, tmp_path / "o.mp4", "--no-stab")
    assert rc == 0, out
    wins = [l.split() for l in (tmp_path / "o.mp4.render.txt").read_text().splitlines() if l.startswith("#win")]
    planned_end = float(wins[-1][6])
    real = json.loads(run("probe", tmp_path / "o.mp4", "--json")[1])["duration"]
    assert abs(real - planned_end) < 0.1, (real, planned_end)
    rc, out = run("flow", tmp_path / "o.mp4")
    assert "0 counted against the edit" in out, out


def test_render_without_stabilisation_or_audio(clip, tmp_path):
    plan = tmp_path / "p.txt"
    plan.write_text("keep 0 1\n", encoding="utf-8")
    rc, text = run("render", clip, plan, tmp_path / "o.mp4", "--no-stab", "--audio", "drop", "--sharpen", "0",
                   "--brighten", "0", "--gamma", "1")
    assert rc == 0, text
    assert json.loads(run("probe", tmp_path / "o.mp4", "--json")[1])["audio"] is False
