"""`fieldkit build-harness netbench ...` (wired in buildh/cli.py).

    netbench [TASK] [--bench B1,B2,B3,B4,B5] [--profile normal|satellite|slow] [--install-dir D] [--label NAME]
                    [--links broadband,starlink,geo,austere] [--repeat N] [--out DIR]
                    [--moz-log cache2:5,nsHttp:5] [--keep-profile] [--no-images]
                    (a MOZ_LOG file per visit and/or every profile, kept beside the result; images off on top of the
                    profile: benches.Bench, report.run_all)
    netbench compare A B         A and B: result files, or labels (the newest file with that label in --out)

Exit codes: 0 every metric measured, 3 something is UNMEASURED (the result says why), 1 the run could not start.
"""
from pathlib import Path

from . import links, report


def run(a, emit, current_id):
    args = list(a.args or [])
    out_dir = getattr(a, "out", None) or None
    if args and args[0] == "compare":
        if len(args) != 3:
            raise SystemExit("netbench compare needs two results: netbench compare A B (files or labels)")
        c = report.compare(args[1], args[2], out_dir)
        emit(c, lambda c: print("\n".join(report.compare_lines(c))))
        return 0
    from ..buildh import install, task
    tid = None
    try:
        tid = current_id(a.task or (args[0] if args else None))
    except task.Refused:
        tid = None
    install_dir = Path(a.install_dir) if getattr(a, "install_dir", None) else install.find_install()
    if not install_dir:
        raise SystemExit("netbench: no install found; pass --install-dir")
    names = report.pick_benches(getattr(a, "bench", None))
    link_list = links.pick_links(getattr(a, "links", None))
    reps = int(getattr(a, "repeat", None) or 3)
    result, jp, yp = report.run_all(install_dir, names, mode=getattr(a, "profile", None) or "normal", link_list=link_list,
                                    reps=reps, label=getattr(a, "label", None), out_dir=out_dir,
                                    say=lambda s: print(s, flush=True), moz_log=getattr(a, "moz_log", None),
                                    keep_profile=bool(getattr(a, "keep_profile", False)),
                                    images=not getattr(a, "no_images", False))
    unmeasured = [f"{b}/{ln}/{g}/{k}" for b, x in result["benches"].items() for ln, gs in x["results"].items()
                  for g, ms in gs.items() for k, m in ms.items() if m["status"] != "MEASURED"]
    emit({k: v for k, v in result.items() if k != "raw"} | {"json": str(jp), "yaml": str(yp)},
         lambda r: print("\n".join(report.lines(result)) + f"\n\nsaved: {jp}\n       {yp}"
                         + (f"\nkept:  {result['evidence']['kept_dir']} ({len(result['evidence']['kept'])} log(s) and "
                            "profile(s))" if result["evidence"]["kept_dir"] else "")
                         + (f"\nUNMEASURED: {len(unmeasured)} metric(s), each with its reason in the result" if unmeasured else "")))
    if tid:
        try:
            t = task.load(tid)
            task.journal(t, "netbench", label=result["label"], build_id=result["build"]["build_id"], json=str(jp),
                         benches=names, unmeasured=unmeasured[:20])
        except (FileNotFoundError, task.Refused, KeyError, OSError):
            pass
    return 3 if unmeasured else 0
