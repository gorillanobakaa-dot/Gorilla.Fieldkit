# Version: 1.0.0 · updated 26-10-16-00-55
"""What to do on THIS operating system, for a Firefox port: the steps differ between Linux and Windows, and a model
does not know which of them apply until something fails.

Born 2026-10-16. The owner: "this should be a deterministic tool that tells the AI exactly that: for cases like these,
on Windows you have to do this ... for similar situations on Linux you have to do this and this and this." Every line
below is computed from the machine and the repositories (which system, which build script exists, whether the
push hook is installed, whether the tree has unrecorded edits, which rebase groups the policy has), not read from a
document. The session-start brief prints the lines that need an action.

    fieldkit build-harness platform-steps <task>          this machine's steps, then the other system's for contrast
"""
import subprocess
from pathlib import Path

from . import firefox, task

#: the fixed part: the same command means the same thing everywhere; only the build and install differ
COMMON = [
    ("record", "every source edit: build-harness record <task> kind=privacy|port <files> --note '<why>' (never git commit by "
               "hand: a direct commit reached no patch until 2026-10-15)"),
    ("check-change", "after each record: build-harness check-change <task> (export, rebase-set, replay; the set must give the "
                     "tree, same git tree hash)"),
    ("decisions", "build-harness decisions <task>: every recorded product decision on the tree and the install"),
    ("symptom", "anything visible goes wrong: build-harness symptom \"<what you see>\" names the tool already built"),
    ("push", "before a push: fieldkit privacy push-check <repo> --range <remote>..HEAD (the pre-push hook runs it)"),
]
BUILD = {
    "linux": [("build", "fieldkit pipeline run firefox-linux: preflight, bootstrap, build, package (.deb), install; the source "
                        "tree comes from fieldkit.local.json firefox.src"),
              ("post-install", "build-harness post-install <task>, then build-harness probe <task> js=install-surface "
                               "(uBlock active, Google only, add refused) and clip-check <task>"),
              ("platform-group", "rebase-set writes 23.REBASE.<major>.linux for the files only Linux parts touch (01.MEDIA, "
                                 "GTK GfxInfo, the Linux font list); check-change runs it")],
    "windows": [("build", "build-harness build-run <task>: it drives harness/gorilla_build.py build and package of the "
                          "Gorilla.firefox repository (the Windows build script; not in Fieldkit and not on GitHub)"),
                ("post-install", "build-harness post-install <task>, then build-harness probe <task> js=install-surface"),
                ("platform-group", "the policy picks the Windows patches by itself (01.MEDIA and the Linux-only patches are "
                                   "left out: platforms, exclude_on). A Windows-only edit, recorded and checked, makes "
                                   "rebase-set write 23.REBASE.<major>.windows; nothing to do by hand")],
}


def _hook_installed(repo):
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-path", "hooks/pre-push"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    p = Path(r.stdout.strip())
    p = p if p.is_absolute() else Path(repo) / p
    return p.is_file() and "fieldkit privacy" in p.read_text(encoding="utf-8", errors="replace")


def checks(t, platform=None):
    """-> [{"id", "state": OK|DO|BLOCKED, "say", "run"}] computed for this machine."""
    from .ownercheck import _owner_root, patchset_owner
    plat = platform or firefox.this_platform()
    out = []
    owner = patchset_owner(t)
    if plat == "windows":
        ok = _owner_root(t) is not None
        out.append({"id": "windows-build-script", "state": "OK" if ok else "BLOCKED",
                    "say": "harness/gorilla_build.py found: build-run can build and package" if ok else
                    "harness/gorilla_build.py is not beside the task's harness root: build-run cannot build. It is the "
                    "Windows build script of the private Gorilla.firefox repository; ask the owner where it is",
                    "run": "build-harness build-run <task>"})
    if owner:
        h = _hook_installed(owner)
        out.append({"id": "push-hook", "state": "OK" if h else "DO",
                    "say": "the patch-set repository checks every push for private data" if h else
                    "the patch-set repository has no pre-push privacy hook: a push is not checked",
                    "run": f"fieldkit privacy install-hook {owner}"})
        major = (t.get("meta", {}).get("upstream", {}).get("version") or "").split(".")[0]
        if major:
            try:
                _, groups = firefox._policy(owner, plat)
            except (OSError, ValueError, KeyError):
                groups = {}
            shared = f"23.REBASE.{major}" in groups
            out.append({"id": "patch-set-on-release", "state": "OK" if shared else "DO",
                        "say": (f"the set is carried onto {major} (23.REBASE.{major}); pristine {major} + the set is proven "
                                "by replay at every check-change") if shared else
                        f"the set has not been carried onto {major}: old patches may fail on pristine {major}",
                        "run": "build-harness rebase-set <task> write=1"})
    wd = t.get("workdir")
    if wd and Path(wd, ".git").exists():
        dirty = [x for x in task.changed_files(t)]
        out.append({"id": "unrecorded-edits", "state": "DO" if dirty else "OK",
                    "say": f"{len(dirty)} edited file(s) not recorded (they reach no patch): {dirty[:3]}" if dirty else
                    "no unrecorded edit in the tree",
                    "run": "build-harness record <task> kind=privacy|port <files> --note '<why>'"})
    return out


def lines(t, platform=None, self_cmd="fieldkit", only_actions=False):
    plat = platform or firefox.this_platform()
    other = "windows" if plat == "linux" else "linux"
    tid = t.get("id", "<task>")
    fix = lambda s: s.replace("<task>", tid).replace("build-harness ", f"{self_cmd} build-harness ") \
        .replace("fieldkit privacy", f"{self_cmd} privacy").replace("fieldkit pipeline", f"{self_cmd} pipeline")
    rows = checks(t, plat)
    if only_actions:
        return [f"  {r['state']} on {plat}: {r['say']}. RUN: {fix(r['run'])}" for r in rows if r["state"] != "OK"]
    out = [f"THIS MACHINE IS {plat.upper()}. Checked now:"]
    out += [f"  {r['state']:<8}{r['say']}" + (f"\n          RUN: {fix(r['run'])}" if r["state"] != "OK" else "") for r in rows]
    out.append(f"ON {plat.upper()} (here):")
    out += [f"  {k}: {fix(v)}" for k, v in BUILD[plat] + COMMON]
    out.append(f"ON {other.upper()} (the other machine), for contrast:")
    out += [f"  {k}: {fix(v)}" for k, v in BUILD[other]]
    return out
