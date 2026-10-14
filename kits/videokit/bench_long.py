"""bench_long - prove videokit on a long, large, variable-frame-rate video: time and peak memory.

Usage: python -I bench_long.py SOURCE.mp4 PLAN.txt WORKDIR [--loops 9] [--width 1080]

1. builds WORKDIR/long.mp4: SOURCE looped --loops times (timestamps kept, so it stays VFR) and
   scaled to --width (1080 -> 1080x1920 for a portrait phone clip);
2. runs analyse, draft and render (PLAN's segments repeated at loops 1, 4 and 8) under a monitor
   that samples the whole process tree's memory every 0.5 s;
3. checks the result with flow --strict and shakecompare --strict;
4. writes WORKDIR/bench.json and prints a summary.
Needs psutil (pip install psutil). Takes tens of minutes; it is a proof run, not a unit test.
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
VK = HERE / "videokit.py"


def monitored(cmd, env=None):
    """Run cmd; return (exit code, seconds, peak RSS of the process tree in MB, last output lines)."""
    import psutil
    t0 = time.time()
    p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", env=env)
    proc, peak = psutil.Process(p.pid), 0
    lines = []
    import threading

    def reader():
        for line in p.stdout:
            lines.append(line.rstrip())
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    while p.poll() is None:
        try:
            rss = proc.memory_info().rss + sum(c.memory_info().rss for c in proc.children(recursive=True))
            peak = max(peak, rss)
        except psutil.Error:
            pass
        time.sleep(0.5)
    th.join(5)
    return p.returncode, round(time.time() - t0, 1), round(peak / 2 ** 20), lines[-25:]


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("plan")
    ap.add_argument("work")
    ap.add_argument("--loops", type=int, default=9)
    ap.add_argument("--width", type=int, default=1080)
    a = ap.parse_args()
    work = pathlib.Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    src = pathlib.Path(a.source).resolve()
    long = work / "long.mp4"
    res = {"source": str(src), "loops": a.loops, "width": a.width}
    if not long.is_file():
        lst = work / "loop.txt"
        lst.write_text("".join(f"file '{src.as_posix()}'\n" for _ in range(a.loops)), encoding="utf-8")
        t0 = time.time()
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-fps_mode", "passthrough", "-vf", f"scale={a.width}:-2:flags=lanczos", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "22", "-c:a", "aac", str(long)], check=True)
        res["build_s"] = round(time.time() - t0, 1)
    info = json.loads(subprocess.run([sys.executable, "-I", str(VK), "probe", str(long), "--json"],
                                     capture_output=True, text=True).stdout)
    res["long"] = {k: info[k] for k in ("duration", "width", "height", "fps", "avg_fps", "vfr")}
    one = probe_duration(src)
    # plan: the owner's segments at loops 1, 4 and 8
    base = [l for l in pathlib.Path(a.plan).read_text(encoding="utf-8").splitlines()
            if l.split("#", 1)[0].strip()]
    out = []
    for k in (1, 4, 8):
        if k >= a.loops:
            continue
        off = k * one
        for l in base:
            p = l.split("#", 1)[0].split()
            p[1], p[2] = f"{float(p[1]) + off:.2f}", f"{float(p[2]) + off:.2f}"
            out.append(" ".join(p))
    plan = work / "plan_long.txt"
    plan.write_text("\n".join(out) + "\n", encoding="utf-8")
    py = [sys.executable, "-I", str(VK)]
    steps = [("analyse", py + ["analyse", long]),
             ("draft", py + ["draft", long, work / "draft_long.txt"]),
             ("render", py + ["render", long, plan, work / "long_edit.mp4"]),
             ("flow", py + ["flow", work / "long_edit.mp4", "--strict"]),
             ("shakecompare", py + ["shakecompare", long, plan, work / "long_edit.mp4", "--strict"])]
    for name, cmd in steps:
        rc, secs, peak, tail = monitored(cmd)
        res[name] = {"exit": rc, "seconds": secs, "peak_mb": peak, "tail": tail}
        print(f"{name:13s} exit {rc}  {secs:8.1f} s  peak {peak:6d} MB", flush=True)
        (work / "bench.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    kept = sum(float(l.split()[2]) - float(l.split()[1]) if l.split()[0] == "keep" else float(l.split()[3])
               for l in out)
    res["kept_seconds"] = round(kept, 1)
    (work / "bench.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("analyse", "draft", "render", "flow", "shakecompare")}))
    return 0 if all(res[n]["exit"] == 0 for n, _ in steps) else 1


if __name__ == "__main__":
    sys.exit(main())
