"""Gorilla UI checks: what a person sees must be readable and must work, proved by the harness before the person does.

Born 2026-10-04. The Gorilla.Satellite toolbar menu reached the maintainer in build 23 with two defects no check saw:
  1. cyan text on the system's light-grey menu (contrast 1.25:1): the Gorilla theme forces EVERY menu to the system
     colours with !important (master-redirect.css, "NATIVE CONTEXT MENU & POPUP RESTORATION"), while a menu owned
     by a toolbar button inherits the toolbar's cyan text;
  2. its opening handler threw (`button.ownerGlobal` is undefined: this Firefox tree's WebIDL has no
     Node.ownerGlobal), so items meant to be hidden showed and its commands failed.
The rules, the reasons and the fixes are written down for people in the owner's repo, docs/GORILLA-THEME-AND-UI.md.

Two layers:
  lint(workdir)        STATIC, before anything is compiled (the build gate runs it): the lines Gorilla ADDED to the
                       pristine Firefox (git diff root..HEAD), checked for mistakes that are certain:
                         UI-API      a chrome-only DOM shortcut that this tree's WebIDL does not define (ownerGlobal)
                         UI-MENU     a menu Gorilla creates without a scoped !important colour rule in Gorilla CSS
  probe_rows(install)  RUNTIME, on a throwaway copy of the installed build (post-install row "ui"): probe ui-contrast
                       opens every menu a toolbar button owns and the Gorilla controls in Settings, and reports
                         - the contrast of every visible item's text against what is really painted behind it
                           (WCAG 2: at least 4.5:1; native menus are judged against the system Menu colour),
                         - every script error raised while each surface opened,
                         - surfaces that opened with nothing visible.

    fieldkit build-harness ui-check <task> [--static] [--install-dir D]
"""
import re
import subprocess
from pathlib import Path

MIN_CONTRAST = 4.5
# script errors that are known and explained; each needs the reason and the decision behind it
KNOWN_ERRORS = {
}
# chrome-only DOM shortcuts some Firefox versions have and others do not: name -> where its absence bites
API_SHORTCUTS = {"ownerGlobal": "Node.ownerGlobal (use ownerDocument.defaultView)"}
LINE = re.compile(r"^UI\|(\w+)\|(.*)$")
POPUP_CREATE = re.compile(r"""createXULElement\(\s*["'](menupopup|panel)["']|<(menupopup|panel)[\s>]""")
SCOPED_MENU_RULE = re.compile(r"menupopup[^{}]*\{[^}]*?background-color:[^;]*!important", re.S)


def _git(workdir, *args):
    return subprocess.run(["git", "-C", str(workdir), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout


def added_lines(workdir, exts=(".mjs", ".js", ".css", ".xhtml", ".ftl", ".inc.xhtml")):
    """{path: [(line number, text)]} of every line Gorilla added to the pristine tree (the root commit)."""
    root = _git(workdir, "rev-list", "--max-parents=0", "HEAD").split()
    if not root:
        return {}
    out, path, n = {}, None, 0
    for line in _git(workdir, "diff", "-U0", "--no-color", root[0], "HEAD", "--",
                     *[f"*{e}" for e in exts], ":!**/test/**", ":!**/tests/**").splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            n = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            out.setdefault(path, []).append((n, line[1:]))
            n += 1
    return out


def _webidl_has(workdir, name):
    return bool(_git(workdir, "grep", "-l", "-w", name, "--", "dom/webidl", "dom/chrome-webidl").strip())


def lint(workdir, added=None):
    """-> [{"rule", "ok", "evidence", "where": [...]}]: the certain mistakes, from the lines Gorilla added."""
    added = added_lines(workdir) if added is None else added
    rows = []
    for name, what in API_SHORTCUTS.items():
        if _webidl_has(workdir, name):
            continue
        pat = re.compile(r"\." + name + r"\b")
        hits = [f"{p}:{n}" for p, ls in added.items() if not p.endswith(".css")
                for n, t in ls if pat.search(t) and not t.lstrip().startswith(("//", "*", "#"))]
        rows.append({"rule": "UI-API", "ok": not hits, "where": hits,
                     "evidence": f"no Gorilla code uses {what}, which this tree does not define" if not hits else
                     f"{len(hits)} use(s) of .{name}, undefined in this tree ({what}): {', '.join(hits[:4])}"})
    creates = [f"{p}:{n}" for p, ls in added.items() if not p.endswith((".css", ".ftl")) for n, t in ls
               if POPUP_CREATE.search(t)]
    css = "\n".join(t for p, ls in added.items() if p.endswith(".css") for _, t in ls)
    styled = bool(SCOPED_MENU_RULE.search(css))
    rows.append({"rule": "UI-MENU", "ok": not creates or styled, "where": creates,
                 "evidence": ("no menu created by Gorilla code" if not creates else
                              f"{len(creates)} Gorilla menu(s), and Gorilla CSS has a scoped menupopup colour rule with "
                              "!important" if styled else
                              f"{len(creates)} Gorilla menu(s) ({', '.join(creates[:3])}) but no Gorilla CSS rule gives a "
                              "menupopup its background with !important: the theme forces every menu to the system "
                              "colours, so the menu shows the toolbar's cyan text on system grey "
                              "(docs/GORILLA-THEME-AND-UI.md, Menus)")})
    return rows


def gate_rows(workdir):
    """Rows for compile.gate."""
    return [(f"UI rule {r['rule']}: Gorilla code follows the theme and the tree's API", r["ok"], r["evidence"])
            for r in lint(workdir)]


def parse(lines):
    """Probe ui-contrast output -> {"items": [...], "errors": [...], "surfaces": [...]}."""
    res = {"items": [], "errors": [], "surfaces": []}
    for raw in lines:
        m = LINE.match(raw.strip())
        if not m:
            continue
        kind, rest = m.group(1), m.group(2).split("|")
        if kind in ("menu", "settings") and len(rest) >= 5:
            try:
                ratio = float(rest[2])
            except ValueError:
                continue
            res["items"].append({"surface": f"{kind} {rest[0]}", "label": rest[1], "contrast": ratio,
                                 "text": rest[3], "background": rest[4]})
        elif kind == "error" and len(rest) >= 2:
            res["errors"].append({"surface": rest[0], "message": "|".join(rest[1:])})
        elif kind == "surface" and len(rest) >= 3:
            res["surfaces"].append({"surface": rest[0], "visible": int(rest[1]), "hidden": int(rest[2])})
    return res


def judge(res):
    """-> rows (check, ok, evidence) from parsed probe output."""
    rows = []
    low = [i for i in res["items"] if i["contrast"] < MIN_CONTRAST]
    worst = min(res["items"], key=lambda i: i["contrast"]) if res["items"] else None
    rows.append({"check": f"UI: every menu item and Gorilla Settings control is readable (contrast at least {MIN_CONTRAST}:1)",
                 "ok": bool(res["items"]) and not low,
                 "evidence": ("no item measured: the probe did not reach any menu" if not res["items"] else
                              f"{len(res['items'])} item(s), lowest {worst['contrast']}:1 ({worst['surface']}: "
                              f"{worst['label'][:40]})" if not low else
                              f"{len(low)} unreadable item(s): " + "; ".join(
                                  f"{i['surface']} '{i['label'][:30]}' {i['contrast']}:1 ({i['text']} on {i['background']})"
                                  for i in low[:4])),
                 "bad": [f"{i['surface']} | {i['label']} | {i['contrast']}" for i in low]})
    unknown = [e for e in res["errors"] if not any(k in e["message"] for k in KNOWN_ERRORS)]
    rows.append({"check": "UI: no script error while menus and Settings open",
                 "ok": not unknown,
                 "evidence": ("none" + (f" (known and explained: {len(res['errors'])})" if res["errors"] else "")
                              if not unknown else
                              f"{len(unknown)} error(s): " + "; ".join(f"{e['surface']}: {e['message'][:120]}" for e in unknown[:3])),
                 "bad": [f"{e['surface']} | {e['message']}" for e in unknown]})
    empty = [s for s in res["surfaces"] if s["visible"] == 0 and s["hidden"] > 0]
    rows.append({"check": "UI: no menu opens empty",
                 "ok": not empty,
                 "evidence": "every surface showed items" if not empty else
                 "; ".join(f"{s['surface']}: 0 visible, {s['hidden']} hidden" for s in empty)})
    return rows


def probe_rows(install_dir, say=print):
    """Run probe ui-contrast on a throwaway copy of `install_dir` -> rows."""
    from . import probe
    r = probe.run(install_dir, "ui-contrast", wait=20, timeout=240, say=lambda m: None)
    if not r["done"]:
        return [{"check": "UI: the ui-contrast probe ran", "ok": False,
                 "evidence": "the probe did not finish: " + "; ".join(r["lines"][-3:])[:200]}]
    return judge(parse(r["lines"]))


def rows(workdir, install_dir, say=print):
    """Post-install row set "ui": the static rules and the runtime probe."""
    out = [{"check": f"UI rule {r['rule']}", "ok": r["ok"], "evidence": r["evidence"]} for r in lint(workdir)]
    return out + probe_rows(install_dir, say=say)
