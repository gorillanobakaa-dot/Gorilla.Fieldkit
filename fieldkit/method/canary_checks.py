# Version: 1.0.0 · updated 26-10-19
"""Canaries for checks that have no one-file command line: each plants its input in a temporary folder, calls the
check's public function, and answers as a command does (exit 3 when the check flagged it, 0 when it passed it).

    python -m fieldkit.method.canary_checks NAME canary|control

Registered in fieldkit/method/canaries.yaml and run by `fieldkit canary run`. The forms come from the owner of each
check (the orchestrator session, 2026-10-19): every plant must come out failing, every control passing. A check that
raises on its plant (decisions.Unreadable) counts as "not passed" too: it did not sign the input.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

FLAGGED, PASSED = 3, 0


def _git(w, *a):
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t", *a], check=True,
                   capture_output=True)


UNAVAILABLE = 4     # the check needs the owner's theme spec (gorilla-patchset/decisions/THEME.yaml) and it is not here


def _theme_spec():
    from ..buildh import themecheck
    try:
        return themecheck.load()
    except FileNotFoundError as e:
        print(f"unavailable: {e}")
        return None


def _static(side, d, plant):
    """`plant` added in a SECOND commit on top of a pristine root: static() judges only lines added after the root
    (the root is the upstream release), so a plant in the root would pass for the wrong reason."""
    from ..buildh import themecheck
    spec = _theme_spec()
    if spec is None:
        return UNAVAILABLE
    w = d / "tree"
    w.mkdir()
    (w / "a.css").write_text("x{color:#00FFFF}\n", encoding="utf-8")
    _git(w, "init", "-q")
    _git(w, "add", "-A")
    _git(w, "commit", "-qm", "root")
    added = plant if side == "canary" else "menupopup{background-color:#000000}\n"
    (w / "a.css").write_text("x{color:#00FFFF}\n" + added, encoding="utf-8")
    _git(w, "commit", "-qam", "second")
    return PASSED if themecheck.static_row(w, spec)["ok"] else FLAGGED


def themecheck_static(side, d):
    """A colour outside the theme spec: the static check must name it."""
    return _static(side, d, "menupopup{background-color:#123456}\n")


def themecheck_static_white_popup(side, d):
    """White (a text-only colour) as a popup background: the static check must name it. Blind until themecheck 1.1.0
    (2026-10-19: found by the canary above, which then had to plant an off-palette colour instead)."""
    return _static(side, d, "menupopup{background-color:#FFFFFF}\n")


def themecheck_rendered(side, d):
    """A rendered popup with cyan text on white: contrast 1.25, under every floor."""
    from ..buildh import themecheck
    if side == "canary":
        item = {"surface": "canary", "label": "x", "contrast": 1.25, "text": (0, 255, 255),
                "background": (255, 255, 255), "hover": (255, 255, 255), "clipped": False}
    spec = _theme_spec()
    if spec is None:
        return UNAVAILABLE
    if side == "control":
        # the spec's own first pair and hover, well above the floor: what a correct popup looks like
        text, background = spec["pairs"][0]
        item = {"surface": "canary", "label": "x", "contrast": spec["floor"] + 10, "text": tuple(text),
                "background": tuple(background), "hover": tuple(spec["hover"]), "clipped": False}
    rows = themecheck.judge_popups([item], spec)
    return FLAGGED if any(not r["ok"] for r in rows) else PASSED


def _claims_owner(root, extra_patch):
    o = root / "owner"
    (o / "config").mkdir(parents=True)
    groups = {"08.Look": {"status": "enabled"}, "21.X": {"status": "enabled"}}
    (o / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "patches", "groups": groups}))
    nf = o / "patches" / "08.Look" / "NEW_FILES" / "b"
    nf.mkdir(parents=True)
    (nf / "kept.txt").write_bytes(b"kept\n")
    (o / "patches" / "21.X").mkdir()
    if extra_patch:
        (o / "patches" / "21.X" / "002-x.patch").write_text(
            "# canary\n\ndiff --git a/b/kept.txt b/b/kept.txt\n--- a/b/kept.txt\n+++ b/b/kept.txt\n@@ -1 +1,2 @@\n kept\n"
            "+GORILLA-CANARY-NOT-IN-TREE\n", encoding="utf-8")
    return o


def _claims_tree(root, files):
    w = root / "tree"
    (w / "b").mkdir(parents=True)
    for name, data in files.items():
        (w / "b" / name).write_bytes(data)
    _git(w, "init", "-q")
    _git(w, "add", "-A")
    _git(w, "commit", "-qm", "built")
    return w


def claims_new_file(side, d):
    """A NEW_FILES entry the tree does not have and nothing explains: MISSING, a failure."""
    from ..buildh import claims
    o = _claims_owner(d, extra_patch=False)
    w = _claims_tree(d, {"x.txt": b"x\n"} if side == "canary" else {"kept.txt": b"kept\n"})
    res = claims.audit_patches(o, w, [])
    return FLAGGED if any(x["fail"] for x in res["new_files"]) else PASSED


def claims_hunk(side, d):
    """A patch whose added line is not in the tree: its item fails."""
    from ..buildh import claims
    o = _claims_owner(d, extra_patch=True)
    body = b"kept\n" if side == "canary" else b"kept\nGORILLA-CANARY-NOT-IN-TREE\n"
    w = _claims_tree(d, {"kept.txt": body})
    res = claims.audit_patches(o, w, [])
    return FLAGGED if any(x.get("fail") for x in res["patches"]) else PASSED


def decisions_tree_contains(side, d):
    """tree_contains on text the file does not hold must not pass; Unreadable (no such file) is also not a pass."""
    from ..buildh import decisions
    w = d / "tree"
    w.mkdir()
    (w / "a.txt").write_text("GORILLA-CANARY-PRESENT\n" if side == "control" else "something else\n", encoding="utf-8")
    want = "GORILLA-CANARY-PRESENT"
    try:
        ok, _ = decisions._check("tree_contains", {"path": "a.txt", "text": want},
                                 {"owner": None, "workdir": str(w), "install": None})
    except decisions.Unreadable:
        return FLAGGED
    return PASSED if ok else FLAGGED


def _ev(**kw):
    e = {"scenario": "startup-idle", "mode": "direct", "sensor": "necko-http", "kind": "dest", "value": "x", "port": None}
    e.update(kw)
    return e


ALL_SENSORS = [_ev(sensor=s, kind="file", value="prefs.js")
               for s in ("necko-http", "necko-dns", "mitm", "sockets", "process-tree", "filesystem")]


def _judge(extra):
    from ..leakgate import gate
    return gate.judge(ALL_SENSORS + extra, {"entries": []}, {}, ["C:/build"], [], 3, True, False)


def leakgate_canary(side, d):
    """A canary form posted by the browser: CANARY_POLICY fails and the event is listed."""
    extra = [_ev(kind="canary", value="form", sensor="mitm", detail="POST https://x.example/")] if side == "canary" else []
    fail, lists = _judge(extra)
    return FLAGGED if fail.get("CANARY_POLICY") and lists.get("UNEXPECTED_CANARIES") else PASSED


def leakgate_telemetry(side, d):
    """A telemetry destination reached: it lands in UNEXPECTED_TELEMETRY."""
    host = "incoming.telemetry.mozilla.org"  # telemetry-ok: canary fixture for the leak-gate judge
    _, lists = _judge([_ev(value=host)] if side == "canary" else [])
    return FLAGGED if lists.get("UNEXPECTED_TELEMETRY") else PASSED


def runner_offline(side, d):
    """The canary runner's offline guard (fieldkit/core/offline.py). Without the guard this never tries a lookup: it
    answers "passed" on both sides, so the runner shows BLIND and nothing leaves the machine. With it, a non-local
    name is refused (flagged) and localhost still resolves (passed)."""
    import socket
    from ..core import offline
    if not offline.installed():
        return PASSED
    name = "fieldkit-canary-probe.invalid" if side == "canary" else "localhost"
    try:
        socket.getaddrinfo(name, 443)
    except socket.gaierror as e:
        return FLAGGED if "offline" in str(e) else 1
    return PASSED


CHECKS = {"runner-offline": runner_offline,
          "themecheck-static": themecheck_static, "themecheck-static-white-popup": themecheck_static_white_popup, "themecheck-rendered": themecheck_rendered,
          "claims-new-file": claims_new_file, "claims-hunk": claims_hunk,
          "decisions-tree-contains": decisions_tree_contains,
          "leakgate-canary": leakgate_canary, "leakgate-telemetry": leakgate_telemetry}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in CHECKS or argv[1] not in ("canary", "control"):
        print(f"usage: python -m fieldkit.method.canary_checks {{{'|'.join(CHECKS)}}} canary|control", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="fkcanarycheck-") as d:
        rc = CHECKS[argv[0]](argv[1], Path(d))
    print({FLAGGED: "flagged", PASSED: "passed"}.get(rc, "unavailable"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
