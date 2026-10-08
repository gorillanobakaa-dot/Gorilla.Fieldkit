"""Dispositions: for every network-capable source file in the audit inventory, what stops it from reaching the
network, with the evidence the harness can check. Categories:

  cut-in-source       the file carries a GORILLA PHYSICAL LOCK (the code that would send returns first)
  not-shipped         a JS module absent from both packaged omni.ja archives of the build
  not-built           C++/Rust/tooling the build configuration never compiles (evidence names the flag or excision)
  dead-remote-settings  its only network API is a Remote Settings client, and Remote Settings has no server
                      (services/settings/Utils.sys.mjs PHYSICAL LOCK)
  dead-caller-cut     its only caller is cut in source (evidence names the caller)
  local-only          it only reads resource:, chrome:, file:, blob: or moz-extension: URLs
  user-initiated      the network action happens only when the user asks for it (evidence says how)
  not-a-sender        it names a host or touches the network layer but sends nothing itself (evidence says why)
  OWNER-DECISION      a trade-off only the owner can make; never approved by a blanket approval
  OPEN                nothing stops it yet; never approvable - it gets cut

A model writes these; the owner approves them, at a real terminal only (`approve_from`, which checks the terminal
itself and takes no argument that stands in for it). A chat route (`approve_from_chat`) that skipped the terminal
check was removed with the unused `build()`.

Proposals (`propose`, `build-harness leakgate-proposal`): reviewers (models or people) write result files, one row
per item they checked; `propose` merges them into proposed-dispositions-<label>.json in the task's leak-gate folder,
the one place `leakgate-dispositions` reads, and writes the owner's review document beside it. It never approves:
every entry carries "approval": null, a row that brings an approval of its own is refused, and an existing proposal
file is never overwritten (the owner may already have approved from it). Born 2026-10-04 (build 26: 150 rows from
four reviewers merged by a throwaway script with the run, the build number and two corrections typed in).
"""
import io
import zipfile
from pathlib import Path

REASONS = {
    # not built
    "toolkit/crashreporter/": ("not-built", "mozconfig --disable-crashreporter: the crash reporter, Breakpad and crash submission are not compiled"),
    "toolkit/crashreporter/google-breakpad/src/tools/": ("not-built", "Breakpad developer tools, never part of a Firefox build"),
    "toolkit/components/telemetry/pingsender/": ("not-built", "pingsender excised from DIRS and from package-manifest.in (build 11)"),
    "toolkit/mozapps/extensions/internal/AddonTestUtils.sys.mjs": ("not-built", "test helper, --disable-tests"),
    "services/sync/tps/": ("not-built", "TPS test harness, --disable-tests"),
    "toolkit/components/translations/bergamot-translator/upload-bergamot.py": ("not-built", "maintainer upload script, not part of the build"),
    "toolkit/components/ml/vendor/": ("not-shipped", "toolkit/components/ml/jar.mn emptied (build 6): nothing under chrome://global/content/ml/ is packaged"),
    "toolkit/components/ml/content/": ("not-shipped", "toolkit/components/ml/jar.mn emptied (build 6)"),
    "toolkit/components/ml/actors/": ("not-built", "actors excised from toolkit/components/ml/moz.build DIRS (build 6)"),
    "toolkit/mozapps/update/": ("not-built", "mozconfig --disable-updater: no update service is built"),
    # dead: caller cut / no lists / not initialised
    "toolkit/components/captivedetect/CaptiveDetect.sys.mjs": ("dead-caller-cut", "its only user, CaptivePortalService::Start, returns before starting (netwerk/base/CaptivePortalService.cpp PHYSICAL LOCK)"),
    "dom/push/PushServiceWebSocket.sys.mjs": ("dead-caller-cut", "PushService._changeStateConnectionEnabledEvent forces enabled=false (dom/push/PushService.sys.mjs PHYSICAL LOCK)"),
    "toolkit/mozapps/extensions/internal/ProductAddonChecker.sys.mjs": ("dead-caller-cut", "its callers, GMPInstallManager and the system add-on updater, are cut (PHYSICAL LOCK)"),
    "toolkit/components/url-classifier/UrlClassifierHashCompleter.sys.mjs": ("dead-caller-cut", "gethash only runs for a URL matching a downloaded list prefix; list downloads are cut (nsUrlClassifierStreamUpdater.cpp PHYSICAL LOCK)"),
    "toolkit/components/url-classifier/nsUrlClassifierDBService.cpp": ("dead-caller-cut", "network use is gethash/update through the stream updater, cut in source"),
    "toolkit/components/url-classifier/nsUrlClassifierUtils.cpp": ("dead-caller-cut", "channel classification helpers, no request of its own; list downloads cut"),
    "toolkit/components/glean/src/init/viaduct_uploader.rs": ("dead-caller-cut", "Glean's uploader; FOG::InitializeFOG returns before Glean starts (13.TELEMETRY.KILL)"),
    "toolkit/components/glean/bindings/jog/JOG.cpp": ("local-only", "reads the local JOG metric definitions file"),
    # local
    "browser/extensions/newtab/lib/NewTabGleanUtils.sys.mjs": ("local-only", "fetches a resource:// metrics definition"),
    "browser/extensions/newtab/lib/RemoteRenderer.sys.mjs": ("local-only", "fetches BUNDLED_SCRIPT_URI / BUNDLED_STYLE_URI (packaged); its Remote Settings client has no server"),
    "browser/extensions/newtab/lib/Screenshots.sys.mjs": ("local-only", "fetches file:// thumbnails"),
    "browser/extensions/newtab/lib/cache.worker.js": ("local-only", "XHR to the new tab's own cached page"),
    "toolkit/mozapps/extensions/LightweightThemeManager.sys.mjs": ("local-only", "fetches the manifest of a packaged theme"),
    "toolkit/components/backgroundtasks/BackgroundTasksUtils.sys.mjs": ("local-only", "fetches file: URIs of experiment opt-in data; no background task is scheduled in this build"),
    "browser/components/urlbar/UrlbarUtils.sys.mjs": ("local-only", "fetches blob: and moz-extension: engine icons only"),
    "toolkit/components/translations/bergamot-translator/bergamot-translator.js": ("dead-caller-cut", "the engine is never assembled (TranslationsParent.getTranslationsEnginePayload PHYSICAL LOCK)"),
    "toolkit/components/translations/cld2/cld-worker.js": ("local-only", "language detection worker loading its packaged data"),
    "services/settings/": ("dead-remote-settings", "Remote Settings: Utils.SERVER_URL is the dummy URL and shouldSkipRemoteActivity is true (PHYSICAL LOCK)"),
    # user-initiated
    "toolkit/mozapps/extensions/internal/XPIInstall.sys.mjs": ("user-initiated", "an install the user starts; every non-system install is rejected in source (07.TOOLKIT 'API LOBOTOMY')"),
    "toolkit/components/search/OpenSearchLoader.sys.mjs": ("user-initiated", "only when the user adds a search engine from a page"),
    "toolkit/components/search/SearchUtils.sys.mjs": ("user-initiated", "engine icon/description loads for an engine the user adds"),
    "netwerk/dns/": ("user-initiated", "DNS over HTTPS only when the user enables it (network.trr.mode); no region rollout (Remote Settings/Nimbus dead)"),
    "remote/shared/": ("not-a-sender", "WebDriver network observers; Marionette and the Remote Agent are physically locked (09.REMOTE)"),
    "remote/shared/webdriver/Session.sys.mjs": ("not-a-sender", "WebDriver session; Marionette and the Remote Agent are physically locked (09.REMOTE)"),
    "services/sync/modules/": ("dead-caller-cut", "every Sync request goes through resource.sys.mjs _doRequest, which throws (PHYSICAL LOCK)"),
    "toolkit/components/normandy/": ("dead-caller-cut", "Normandy.init returns first (toolkit/components/normandy/Normandy.sys.mjs PHYSICAL LOCK): no recipe runner, no API call"),
    "toolkit/components/nimbus/lib/RemoteSettingsExperimentLoader.sys.mjs": ("local-only", "fetches resource://nimbus/ schemas; its Remote Settings clients have no server"),
    # owner decisions
    "security/manager/ssl/nsNSSCallbacks.cpp": ("OWNER-DECISION", "OCSP: on a TLS visit Firefox may ask the certificate's CA whether it is revoked, telling the CA which site was visited. CRLite cannot replace it here (its data comes from Remote Settings, which is dead). Off = privacy, no revocation checking; on = revocation, CA sees visits. security.OCSP.enabled is a pref decision."),
    "security/manager/ssl/nsNSSIOLayer.cpp": ("not-a-sender", "the TLS socket layer every HTTPS connection uses"),
    "security/manager/ssl/TLSClientAuthCertSelection.cpp": ("not-a-sender", "client-certificate selection during a TLS handshake"),
}


def shipped_names(install_or_zip):
    """Basenames of every file in both omni.ja archives of a build (dir or zip)."""
    names = set()
    p = Path(install_or_zip)
    if p.is_dir():
        read = lambda rel: (p / rel).read_bytes()
    else:
        z = zipfile.ZipFile(p)
        top = z.namelist()[0].split("/")[0]
        read = lambda rel: z.read(f"{top}/{rel}")
    for ja in ("omni.ja", "browser/omni.ja"):
        with zipfile.ZipFile(io.BytesIO(read(ja))) as o:
            names.update(Path(n).name for n in o.namelist())
    return names


def classify(rel, info, shipped):
    if info.get("physical_lock"):
        return "cut-in-source", "GORILLA PHYSICAL LOCK in the file: the sending code returns first"
    best = None
    for prefix, (cat, why) in REASONS.items():
        if rel == prefix or rel.startswith(prefix):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, cat, why)
    if best:
        return best[1], best[2]
    if set(info.get("apis", [])) <= {"RemoteSettings("}:
        return "dead-remote-settings", "its only network API is a Remote Settings client, and Remote Settings has no server (PHYSICAL LOCK)"
    if rel.endswith((".mjs", ".js")) and Path(rel).name not in shipped:
        return "not-shipped", "absent from both packaged omni.ja archives of the build"
    return "OPEN", "nothing in the tree stops this file from sending yet"


NEVER_BLANKET = ("OPEN", "OWNER-DECISION")


def approve_from(disp_path, proposed_path, named=(), say=print):
    """Owner only, at a real terminal: the proposals of `proposed_path` ({key: entry}, written by a model, approval
    null) become approved entries of dispositions.json. OPEN is never approved (it gets cut); OWNER-DECISION only
    when its key is named on the command line. Every entry is printed before anything is written.
    -> {"approved": [keys], "left": [(key, why)]}"""
    import collections
    import json
    import time
    from ..buildh import task
    if not task.owner_terminal():
        raise task.Refused("approval is the owner's, at a real terminal; an agent's shell has none")
    disp_path, proposed_path = Path(disp_path), Path(proposed_path)
    proposed = json.loads(proposed_path.read_text(encoding="utf-8"))
    data = json.loads(disp_path.read_text(encoding="utf-8")) if disp_path.is_file() else {}
    take, left = {}, []
    for k, e in proposed.items():
        d = e.get("disposition")
        if d == "OPEN" or (e.get("proposal") or {}).get("needs_fix"):
            left.append((k, "OPEN / needs a fix: never approved, it gets cut"))
        elif d == "OWNER-DECISION" and k not in named:
            left.append((k, "an owner decision: approved only when named on the command line"))
        else:
            take[k] = e
    for d, n in sorted(collections.Counter(e["disposition"] for e in take.values()).items()):
        say(f"  {n:4d}  {d}")
    for k, e in sorted(take.items()):
        say(f"{k}\n    {e['disposition']}: {(e.get('evidence') or '')[:300]}")
    at = time.strftime("%Y-%m-%d %H:%M:%S")
    for k, e in take.items():
        data[k] = {**e, "approval": {"by": "owner", "at": at, "how": "terminal", "from": proposed_path.name}}
    disp_path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return {"approved": sorted(take), "left": left}


# ---------------------------------------------------------------------------------------------- proposals
KINDS = ("cut-in-source", "not-shipped", "not-built", "dead-remote-settings", "dead-caller-cut", "local-only",
         "user-initiated", "not-a-sender", "OWNER-DECISION", "OPEN")
CATEGORIES = (("source", "Source files (SOURCE_POLICY)"), ("host-vendor", "Vendor hosts in omni.ja (BINARY_POLICY)"),
              ("crate", "Rust crates new since release N-1 (DEPENDENCY_POLICY)"),
              ("host-new", "Embedded hosts new since release N-1 (BINARY_POLICY)"),
              ("binary", "Executables and imports (BINARY_POLICY)"))
COMPONENT = {"host-vendor": "Embedded vendor host (omni.ja)", "host-new": "Embedded host new since release N-1",
             "crate": "Rust crate new since release N-1", "binary": "Executable / DLL"}


class Invalid(ValueError):
    pass


def _key(row):
    return row.get("key") or ("host:" + row["host"] if row.get("host") else None)


def category(row):
    k = _key(row) or ""
    if k.startswith("host:"):
        return "host-new" if row.get("set") == "new-since-N-1" else "host-vendor"
    if k.startswith("crate:"):
        return "crate"
    if k.startswith(("exe:", "pe:")):
        return "binary"
    return "source"


def merge(result_rows, override_rows=()):
    """Reviewer rows -> {key: row}, checked. Each row: key (or host), what, reachable, disposition, reason, evidence.
    Refused (Invalid, listing every problem, nothing written): an unknown kind, no evidence or reason, a key two
    reviewers both wrote, an override for a key nobody wrote, any row carrying an approval."""
    rows, why = {}, []
    for r in result_rows:
        k = _key(r)
        if not k:
            why.append(f"a row with neither key nor host: {str(r)[:80]}")
        elif k in rows:
            why.append(f"{k}: written twice (each item is decided once; correct it with an override file)")
        else:
            rows[k] = r
    for r in override_rows:
        k = _key(r)
        if k not in rows:
            why.append(f"{k}: an override for an item no result file has")
        else:
            rows[k] = {**rows[k], **r}
    for k, r in rows.items():
        if r.get("approval"):
            why.append(f"{k}: carries an approval; approval is the owner's, at a real terminal, never a reviewer's")
        if r.get("disposition") not in KINDS:
            why.append(f"{k}: unknown disposition {r.get('disposition')!r} (one of {', '.join(KINDS)})")
        if not r.get("evidence"):
            why.append(f"{k}: no evidence (path:line the owner can open)")
        if not r.get("reason"):
            why.append(f"{k}: no reason")
    if why:
        raise Invalid("; ".join(why[:40]) + (f"; ... {len(why) - 40} more" if len(why) > 40 else ""))
    return rows


def entries(rows, inventory, run_name, build=None, by="model, not approved", date=None):
    """{key: row} -> {key: entry} in dispositions.json's own shape, approval null on every one."""
    import time
    date = date or time.strftime("%Y-%m-%d")
    out, missing = {}, []
    for k, r in rows.items():
        cat = category(r)
        if cat == "source":
            inv = inventory.get(k)
            if inv is None:
                missing.append(k)
                continue
            comp, apis = inv.get("component"), inv.get("apis", [])
        else:
            comp, apis = COMPONENT[cat], []
        ev = r["evidence"] if isinstance(r["evidence"], list) else [str(r["evidence"])]
        out[k] = {"component": comp, "apis": apis, "disposition": r["disposition"],
                  "evidence": r["reason"] + " | " + "; ".join(ev), "approval": None,
                  "proposal": {"by": by, "date": date, "build": build, "run": run_name, "what": r.get("what"),
                               "reachable": r.get("reachable"), "needs_fix": r["disposition"] == "OPEN"}}
    if missing:
        raise Invalid(f"source item(s) not in the run's source inventory (a typo, or another run): {missing[:10]}")
    return out


def _esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def review_lines(rows, task_id, run_name, json_path, legend=None, notes=None, date=None):
    """The owner's review document: counts, the NEEDS FIX items first, then every item by category with its evidence."""
    import collections
    import time
    date = date or time.strftime("%Y-%m-%d")
    cats = {k: category(r) for k, r in rows.items()}
    L = ["# Leak gate: proposed dispositions for the owner to decide", "",
         f"Prepared {date} by a model from run `{run_name}` of task `{task_id}`. **Nothing here is approved.** Every entry "
         "in the proposed file carries `\"approval\": null`. Under D-157-10 only the maintainer approves, at a real "
         f"terminal: `fieldkit build-harness leakgate-dispositions {task_id}` (OPEN is never approved; an OWNER-DECISION "
         "only when its key is named on that command line).", ""]
    if legend:
        L += ["Paths: " + "; ".join(f"`{k}` = `{v}`" for k, v in legend.items()) + ".", ""]
    L += [f"Proposed file: `{json_path}` (same shape as `leakgate/dispositions.json`).", "", "## Counts", "",
          "| category | " + " | ".join(KINDS) + " | total |", "|---|" + "---|" * (len(KINDS) + 1)]
    for cat, title in CATEGORIES:
        c = collections.Counter(r["disposition"] for k, r in rows.items() if cats[k] == cat)
        if c:
            L.append(f"| {title.split(' (')[0]} | " + " | ".join(str(c.get(x) or "") for x in KINDS) + f" | {sum(c.values())} |")
    L.append("")
    if any("pref lock" in str(r.get("reason", "")).lower() for r in rows.values()):
        L += ["Reasons that say *Pref lock, not a source cut* rest on a locked pref and not on a PHYSICAL LOCK: weaker than "
              "a source cut, since someone who edits the omni.ja or the profile's prefs could reopen them.", ""]
    opens = [k for k, r in sorted(rows.items()) if r["disposition"] == "OPEN"]
    L += [f"## 1. NEEDS FIX ({len(opens)}) - not approvable; these get cut", ""]
    if opens:
        L += ["| item | what it is | reachable? | why | evidence |", "|---|---|---|---|---|"]
        for k in opens:
            r = rows[k]
            ev = r["evidence"] if isinstance(r["evidence"], list) else [r["evidence"]]
            L.append(f"| `{_esc(k)}` | {_esc(r.get('what', ''))} | {_esc(r.get('reachable', ''))} | {_esc(r['reason'])} | "
                     + "<br>".join(f"`{_esc(e)}`" for e in ev) + " |")
    else:
        L.append("None.")
    L.append("")
    if notes:
        L += [notes.rstrip(), ""]
    n = 2
    for cat, title in CATEGORIES:
        keys = sorted((k for k in rows if cats[k] == cat), key=lambda k: (rows[k]["disposition"] != "OPEN", k))
        if not keys:
            continue
        L += [f"## {n}. {title} - {len(keys)}", "", "| item | what it is | reachable? | proposed disposition | reason | evidence path:line |",
              "|---|---|---|---|---|---|"]
        n += 1
        for k in keys:
            r = rows[k]
            ev = r["evidence"] if isinstance(r["evidence"], list) else [r["evidence"]]
            disp = "**OPEN (NEEDS FIX)**" if r["disposition"] == "OPEN" else r["disposition"]
            L.append(f"| `{_esc(k)}` | {_esc(r.get('what', ''))} | {_esc(r.get('reachable', ''))} | {disp} | {_esc(r['reason'])} | "
                     + "<br>".join(f"`{_esc(e)}`" for e in ev) + " |")
        L.append("")
    return L


def propose(task_id, run_dir, result_files, override_files=(), label=None, notes_file=None, legend=None,
            review_path=None, by="model, not approved", date=None):
    """Reviewer result files -> the proposal and its review document. The proposal goes to the task's leak-gate
    folder (the run's parent), where `leakgate-dispositions` reads it, and nowhere else; it is never overwritten.
    -> {"json", "review", "entries", "counts", "open"}"""
    import collections
    import json
    run_dir = Path(run_dir)
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))          # noqa: E731
    rows = merge([r for f in result_files for r in load(f)], [r for f in override_files for r in load(f)])
    inv_path = run_dir / "source-inventory.json"
    inventory = load(inv_path).get("inventory", {}) if inv_path.is_file() else {}
    build = None
    if (run_dir / "test-results.json").is_file():
        build = load(run_dir / "test-results.json").get("BUILD")
    label = label or run_dir.name
    if not label.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise Invalid(f"label {label!r}: letters, digits, '-', '_' and '.' only")
    out = run_dir.parent / f"proposed-dispositions-{label}.json"
    if out.exists():
        raise Invalid(f"{out} exists: a proposal is never overwritten (the owner may have approved from it); choose another label")
    ents = entries(rows, inventory, run_dir.name, build=build, by=by, date=date)
    review = Path(review_path) if review_path else run_dir.parent / f"REVIEW-{label}-dispositions.md"
    if review.exists():
        raise Invalid(f"{review} exists: not overwritten; choose another label or review= path")
    notes = Path(notes_file).read_text(encoding="utf-8") if notes_file else None
    text = "\n".join(review_lines(rows, task_id, run_dir.name, out, legend=legend, notes=notes, date=date)) + "\n"
    out.write_text(json.dumps(ents, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    review.write_text(text, encoding="utf-8", newline="\n")
    counts = {cat: dict(collections.Counter(rows[k]["disposition"] for k in rows if category(rows[k]) == cat))
              for cat, _ in CATEGORIES}
    return {"json": str(out), "review": str(review), "entries": len(ents), "counts": counts,
            "open": sorted(k for k, r in rows.items() if r["disposition"] == "OPEN")}
