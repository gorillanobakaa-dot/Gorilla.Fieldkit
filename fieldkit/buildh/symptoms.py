# Version: 1.0.0 · updated 26-10-15-17-30
"""What you SEE -> the tool that diagnoses it. The first command for any visible problem in a Gorilla build.

Born 2026-10-15. The owner: "a narrative is not exactly a deterministic tool ... none of the models was trained on
Firefox compiling, troubleshooting and hardening the way our projects work, so the next session does blind steps,
trial and error and reinvents the wheel." A model that is handed a screenshot does not read a document first; it
starts guessing. This registry turns the description of the symptom into the command that was already built and
proven for it, and says what it proves, where to look, and what the fix was the last time. The session-start brief
(fieldkit/migrate/cli.py) prints the one line that points here, so the next model meets it before it guesses.

    fieldkit build-harness symptom "<what you see, any words, English or Romanian>"
    fieldkit build-harness symptom --all          every recorded symptom

Every entry is DATA checked by tests: its command must be a real build-harness action, its words must match its own
example, and two entries may not claim the same example. A symptom that is not here: diagnose it, build the tool,
then add the entry here (tests/test_buildh_symptoms.py shows the shape). The registry grows with every fault.
"""
import re
import sys

SYMPTOMS = [
    {
        "id": "dialog-text-cut",
        "seen": "A page's dialog (alert, confirm, prompt, 'Leave page?') shows its message cut, as a thin strip or "
                "half a line; the buttons look fine.",
        "example": "the Leave page dialog on web.whatsapp.com shows the message truncated",
        "words": [r"dialog|alert|confirm|prompt|leave page|stay on page|pop-?up|fereastr", r"cut|truncat|clip|squeez|half|not visible|nu se vede|t[aă]iat|trunchi|nu este prezent|not present|missing"],
        "action": "clip-check",
        "run": "build-harness clip-check <task>",
        "proves": "which dialogs cut text (OURS vs stock of the same version, repeated), and WHY: what moved after "
                  "the height was measured, the transition running then, and the CSS rule (sheet:line) behind it",
        "look": ["toolkit/modules/SubDialog.sys.mjs resizeVertically (measures once)",
                 "browser/themes/shared/master-redirect.css (global transition rule)",
                 "toolkit/components/prompts/content/commonDialog.css (:root[subdialog] dialog padding)"],
        "last_fix": "T-158-10-A (2026-10-15): transition-property: none inside :root[subdialog]",
        "then": "try a fix without a build: build-harness clip-check <task> try=<path in the build>=<edited tree file>",
    },
    {
        "id": "launcher-old-name-or-icon",
        "seen": "The app grid, the dock or the menu shows an old version number in the name, or two different "
                "Gorilla icons (grid vs dock vs taskbar).",
        "example": "the app grid says Gorilla Unleashed 155 and the icon differs from the one on the taskbar",
        "words": [r"icon|iconi|launcher|app grid|\bgrid\b|dock|taskbar|bara de|desktop file", r"\bold\b|155|154|differ|diferit|wrong|blurry|vechi|\balt[aă]\b|not the same|nu e la fel"],
        "action": "install",
        "run": "{python} -c \"from fieldkit.build import deb_install as d; print(d.icon_shadows('gorilla-unleashed'));"
               " print(d.stale_shell_note([d.DESKTOP, d.PERSONAL], d.shell_started()))\"",
        "proves": "personal icon files that shadow the package's (~/.local/share/icons, ~/.icons, wrong-size ones "
                  "marked), personal launchers that shadow the system one, and a GNOME Shell older than the menu "
                  "entry (it keeps the old name and picture until log out and in)",
        "look": ["~/.local/share/applications then /usr/share/applications (the menu entry wins in that order)",
                 "~/.local/share/icons, ~/.icons, then /usr/share/icons/hicolor/<size>/apps",
                 "gsettings get org.gnome.shell app-picker-layout (the grid's app id)"],
        "last_fix": "2026-10-15: nine July icon files quarantined to Agents.Work.Trash, personal icon cache rebuilt; "
                    "the grid's '155' is the running shell's memory: log out and in",
        "then": "the install stage checks this by itself after every install (deb_install.verify_install)",
    },
    {
        "id": "video-frames-dropped",
        "seen": "Video stutters or drops frames (often at 60 fps) more than stock Firefox on the same machine.",
        "example": "youtube 1080p60 drops frames and stutters",
        "words": [r"frame|video|fps|youtube|cadre", r"drop|stutter|sacad|\blag|jank|pierd|not smooth"],
        "action": "pref-bisect",
        "run": "build-harness pref-bisect <task>",
        "proves": "which of our graphics prefs costs frames, by halving with an isolated, repeated, confirmed "
                  "measurement against the pristine defaults",
        "look": ["modules/libpref/init/StaticPrefList.yaml", "browser/app/profile/firefox.js",
                 "build-harness decode-test <task> (is decoding on the GPU at all)"],
        "last_fix": "2026-10-15: inside the wobble until confirmed (handover item 6)",
        "then": "measure isolated (hands off, browsers closed); noise = INCONCLUSIVE",
    },
    {
        "id": "video-not-hardware-decoded",
        "seen": "Video uses a lot of CPU or the laptop heats up while playing; hardware decoding may be off.",
        "example": "video playback uses high cpu, is hardware decoding working",
        "words": [r"video|play|youtube|decod", r"cpu|heat|\bhot\b|fan\b|ventilat|[iî]nc[aă]lz|procesor|hardware|gpu|va-?api"],
        "action": "decode-test",
        "run": "build-harness decode-test <task>",
        "proves": "whether the installed browser decodes on the GPU's video engine (local clip, frame counters, "
                  "MOZ_LOG), D-158-06",
        "look": ["widget/gtk/GfxInfo.cpp (blocklist short-circuit)", "gfx/thebes/gfxPlatformGtk.cpp"],
        "last_fix": "D-158-06 / D-158-07 (2026-10-15)",
        "then": "",
    },
    {
        "id": "labels-blank-after-removal",
        "seen": "Menu items, buttons or settings show an empty label after texts (.ftl) were removed or moved.",
        "example": "a context menu item shows a blank line with no text",
        "words": [r"menu|label|button|item|setting|etichet|meniu", r"blank|empty|\bgol|f[aă]r[aă] text|no text|missing text|missing label"],
        "action": "fluent-check",
        "run": "build-harness fluent-check <task>",
        "proves": "every .ftl change parses and every removed message id is no longer used by visible UI",
        "look": ["git grep the message id outside *.ftl", "the <link rel=\"localization\"> lines of the window"],
        "last_fix": "D-158-08 (2026-10-15): remove the UI that uses a text BEFORE the text",
        "then": "",
    },
    {
        "id": "build-error",
        "seen": "The build or packaging stage failed.",
        "example": "the build failed with a compiler error",
        "words": [r"build|compil|mach|\blink|package|pachet", r"fail|error|eroare|e[sș]u|broken|stops"],
        "action": "companions",
        "run": "build-harness companions <task>  (then: build-harness api-drift <task>)",
        "proves": "a ported change missing its other half (companions), or an upstream API that moved (api-drift); "
                  "the pipeline log's failure summary already names the first compiler error and any known fix",
        "look": ["the pipeline log's failure summary", "build/api-drift/firefox.yaml"],
        "last_fix": "",
        "then": "every new build error becomes a tool + signature + test + lesson (owner priority 2026-10-15)",
    },
    {
        "id": "pipeline-wrong-source-tree",
        "seen": "The firefox-linux pipeline's preflight fails on things the work tree has (must-keep files missing, "
                "fonts not listed, 'no objdir yet (clean build)') right after a build that worked.",
        "example": "preflight fails must-keep-tree and package-fonts and says no objdir yet",
        "words": [r"preflight|pipeline|must-keep|package-fonts", r"fail|no objdir|missing|not carry|e[sș]u"],
        "action": "preflight",
        "run": "fieldkit pipeline run firefox-linux --from preflight --var src=<the task's work tree>",
        "proves": "the pipeline's default src is {work}/firefox-{version} under Build.Work: the PRISTINE Mozilla "
                  "tree. The Gorilla work tree (task firefox-158-linux) is ~/Documents/FIREFOX.WORK/work/firefox-158.0; "
                  "with --var src= pointing there, preflight passes and the build uses the existing objdir",
        "look": ["fieldkit/build/pipelines/firefox-linux.yaml (vars: work, src)",
                 "the task's workdir: build-harness migrate sitrep <task>, or intents/port-*/STATE.md",
                 "git -C <src> log --oneline -1: a pristine tree has only Mozilla's commit"],
        "last_fix": "2026-10-15: preflight refused the pristine tree (correctly); re-run with --var src=<work tree>",
        "then": "",
    },
    {
        "id": "decision-not-held",
        "seen": "Something the owner decided against is back (AI feature, new-tab tiles, translations, a theme), or "
                "a decision may not hold in this build.",
        "example": "the AI sidebar or smart window is still in the browser",
        "words": [r"\bai\b|smart ?window|smartbar|sidebar|top ?sites|sponsor|translat|decision|decizie|register|registru", r"still|back|again|[iî]nc[aă]|present|apare|violat|hold"],
        "action": "decisions",
        "run": "build-harness decisions <task>",
        "proves": "every recorded product decision checked on the tree and the install: ENFORCED, VIOLATED, UNCHECKABLE",
        "look": ["gorilla-firefox/decisions/PRODUCT-DECISIONS.yaml"],
        "last_fix": "",
        "then": "quote the decision id; never re-argue a settled decision",
    },
]


def match(text):
    """-> the entries whose word groups ALL occur in `text`, in any order (best first: most distinct words hit).
    A group is one regex of alternatives: 'what' (dialog, icon, video) and 'how it is wrong' (cut, old, drops)."""
    t = " ".join(str(text).lower().split())
    scored = []
    for e in SYMPTOMS:
        found = [{m.group(0) for m in re.finditer(g, t)} for g in e["words"]]
        if all(found):
            scored.append((sum(len(f) for f in found), e))
    return [e for _, e in sorted(scored, key=lambda x: -x[0])]


def command(run, self_cmd="fieldkit", python=None):
    """The entry's command with the full path of its program (directives 12b: it runs from any folder)."""
    if run.startswith("build-harness"):
        return f"{self_cmd} {run}"
    if run.startswith("fieldkit "):
        return self_cmd + run[len("fieldkit"):]
    return run.replace("{python}", python or sys.executable)


def lines(entries, self_cmd="fieldkit", python=None):
    out = []
    for e in entries:
        out += [f"SYMPTOM {e['id']}: {e['seen']}",
                f"  RUN FIRST: {command(e['run'], self_cmd, python)}",
                f"  it proves: {e['proves']}"]
        out += [f"  look at: {x}" for x in e["look"]]
        if e["last_fix"]:
            out.append(f"  last time: {e['last_fix']}")
        if e["then"]:
            out.append(f"  then: {e['then']}")
    return out
