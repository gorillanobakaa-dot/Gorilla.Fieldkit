"""catchup - bring another machine's AI session up to date with this one: a machine catch-up handover.

Built 2026-10-12: a month of work happened on a Windows laptop while the owner's Debian machine (and its
Claude) was away. GitHub carries the pushed code; it deliberately does NOT carry the context that makes a
session useful - memory files, instruction files (CLAUDE.md, GEMINI.md, AGENTS.md), private tools, the local
tool registry, internal notes and earlier handovers - nor work that was never pushed. This kit gathers all of
that into one folder, checks it for secrets, zips it and copies it to a stick, verified. Hand-carried only:
internal documentation never goes to GitHub.

  catchup.py doctor                                   git, Python, free space at the destination
  catchup.py repos ROOT... [--since DATE] [--fetch] [--json]
        every git repo under ROOTs: remote (any token stripped), branch, ahead/behind its upstream,
        uncommitted and untracked counts, last commit, and the commits since DATE. --fetch asks the
        remotes first (network); without it the numbers are as of the last fetch.
  catchup.py collect --out DIR [--config FILE] [--since DATE] [--fetch]
        builds DIR: repos.md/json, per repo the work GitHub does not have (uncommitted diff, unpushed
        commits as patches, small untracked files), and copies of the configured sets (memory,
        instructions, private tools, local registry, notes). Writes MANIFEST.json, README-FIRST.txt and
        HANDOFF-SKELETON.md (per-repo facts; the narrative is written by the agent into HANDOFF.md).
  catchup.py check DIR                                secrets (tokens, keys, credentials in URLs) fail it,
        exit 3; personal data (paths, emails) is only counted - this is a private, hand-carried bundle.
  catchup.py public-check PATH... [--no-terms] [--history] [--github ACCOUNT]   before anything goes PUBLIC: Romanian text (Romanian-only
        letters, or two common Romanian words on a line) and the owner's private terms (names, places,
        background; from fieldkit.local.json privacy.terms) in what git would publish. Binary files are
        skipped; a line carrying "privacy-scan: allow" is skipped. --history adds every commit on every ref
        (added lines and messages); --github adds what GitHub shows outside git (descriptions, release pages,
        issues; needs `gh`, network). Exit 3 on findings; judge third-party
        lists (block lists, author lists) by eye.
  catchup.py patch REPO --out DIR [--message FILE]   staged (uncommitted) work as a binary-safe patch + its base
        commit + how to apply; PROVED by applying it to the base in a scratch worktree (no match: nothing kept).
  catchup.py manifest DIR                             refresh MANIFEST.json after adding something by hand.
  catchup.py verify ZIP|DIR [--json]                  the receiver's check: the zip against its .sha256, every
        file against MANIFEST.json (missing, different, or not listed); exit 3 on any. Stdlib only.
  catchup.py ask "QUESTION" [--to linux] | answer ID "ANSWER" | questions [--all]   the question ledger:
        open questions ride in every bundle (QUESTIONS-OPEN.md) until answered, oldest first, with their age.
  catchup.py pack DIR DEST [--replace]                           DIR -> DEST/<DIR name>.zip (+ .sha256 + README-FIRST.txt
        beside it), written as .part, renamed when complete, read back: SHA-256 and every member's CRC
        must check. Nothing already at DEST is overwritten (a different file of the same name gets _2).

CONFIG (JSON, private - keep it in Fieldkit's local/): {"roots": [...], "repo_exclude": [globs],
  "sets": {"name": [paths...]}, "exclude": [globs], "max_file_mb": 50, "max_untracked_mb": 20}
Paths may use ~ and ${HOME}/${DOCUMENTS}. Without a config, sensible defaults for this machine are used.

Exit codes: 0 ok, 1 failure, 2 usage, 3 secrets (check) or identifying text (public-check) found.
"""
import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile

TOKEN_IN_URL = re.compile(r"(https?://)[^/@\s]+@")
SKIP_NAMES = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", ".mypy_cache"}
SECRETS = [
    ("github-token", r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    ("github-fine-token", r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    ("openai-key", r"(?<![\w.-])sk-(proj-)?[A-Za-z0-9_-]{20,}\b"),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("nvidia-key", r"\bnvapi-[A-Za-z0-9_-]{30,}\b"),
    ("private-key", r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ("url-credentials", r"https?://[^/\s@\"'<>]+:[^/\s@\"'<>]+@[^/\s]+"),
    ("url-token", r"https?://(ghp|gho|github_pat)_[^/\s@]+@"),
]
PERSONAL = [("user-path", r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+|/home/[a-z_][a-z0-9_-]*"),
            ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")]
ALLOW_MARK = "privacy-scan: allow"
FIXTURE = re.compile(r"FAKE|EXAMPLE|DUMMY|PLACEHOLDER", re.I)


def git(repo, *args, check=False):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(args)} in {repo}: {r.stderr.strip()[:300]}")
    return r.stdout.strip() if r.returncode == 0 else ""


def expand(p):
    home = str(pathlib.Path.home())
    docs = os.environ.get("DOCUMENTS") or str(pathlib.Path(home) / "Documents")
    return pathlib.Path(os.path.expanduser(str(p).replace("${HOME}", home).replace("${DOCUMENTS}", docs)))


def default_config():
    h = pathlib.Path.home()
    claude_proj = h / ".claude" / "projects"
    return {
        "roots": [str(h / "Documents"), str(h / ".gemini" / "antigravity" / "scratch")],
        "repo_exclude": ["*/Build.Work/*", "*/Vault.Do.Not.Delete/*", "*/src-*", "*/repo-backups/*",
                         "*/Claude playground/*", "*/snapshot-*/*", "*Fieldkit-review*",
                         "*/Gorilla.Opencode.Builds/*", "*/Documents/Codex/*", "*/Gorilla.firefox/src"],
        "sets": {
            "memory": [str(d / "memory") for d in sorted(claude_proj.glob("*")) if (d / "memory").is_dir()],
            "instructions": [str(h / ".claude" / "CLAUDE.md"), str(h / ".gemini" / "GEMINI.md"),
                             str(h / ".codex" / "AGENTS.md")],
        },
        "exclude": ["*.pyc", "*/state/*", "*/__pycache__/*"],
        "max_file_mb": 50,
        "max_untracked_mb": 20,
    }


def load_config(path):
    cfg = default_config()
    if path:
        user = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        for k, v in user.items():
            if k == "sets":
                cfg["sets"].update(v)
            else:
                cfg[k] = v
    return cfg


def excluded(path, globs):
    s = str(path).replace("\\", "/")
    return any(fnmatch.fnmatch(s, g) or fnmatch.fnmatch(s + "/", g) for g in globs)


# -- repos -------------------------------------------------------------------------------
def find_repos(roots, exclude, depth=4):
    found = []
    for root in roots:
        root = expand(root)
        if not root.is_dir():
            continue
        for dirpath, dirnames, _files in os.walk(root):
            d = pathlib.Path(dirpath)
            if len(d.relative_to(root).parts) > depth or excluded(d, exclude):
                dirnames[:] = []
                continue
            if (d / ".git").exists():
                found.append(d)
                dirnames[:] = [n for n in dirnames if n not in SKIP_NAMES]  # nested repos are still found
            dirnames[:] = [n for n in dirnames if n not in SKIP_NAMES]
    return sorted(set(found), key=lambda p: str(p).lower())


def repo_state(repo, since=None, fetch=False):
    if fetch:
        git(repo, "fetch", "--quiet", "--all")
    remote = TOKEN_IN_URL.sub(r"\1", git(repo, "remote", "get-url", "origin"))
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    ahead = behind = None
    if upstream:
        lr = git(repo, "rev-list", "--left-right", "--count", f"{upstream}...HEAD").split()
        if len(lr) == 2:
            behind, ahead = int(lr[0]), int(lr[1])
    status = git(repo, "status", "--porcelain").splitlines()
    since_log = git(repo, "log", f"--since={since}", "--format=%cs %h %s") if since else ""
    return {"path": str(repo), "remote": remote, "branch": branch, "upstream": upstream or None,
            "ahead": ahead, "behind": behind,
            "modified": sum(1 for s in status if not s.startswith("??")),
            "untracked": sum(1 for s in status if s.startswith("??")),
            "last_commit": git(repo, "log", "-1", "--format=%cs %h %s"),
            "commits_since": since_log.splitlines()[:200]}


def repos_markdown(states, since):
    out = [f"# Git repositories on this machine{' - commits since ' + since if since else ''}", "",
           "| repo | remote | branch | ahead/behind upstream | changed / untracked | last commit |",
           "|---|---|---|---|---|---|"]
    for s in states:
        ab = "-" if s["upstream"] is None else f"{s['ahead']} ahead / {s['behind']} behind"
        out.append(f"| `{pathlib.Path(s['path']).name}` | {s['remote'] or '(none)'} | {s['branch']} | {ab} | "
                   f"{s['modified']} / {s['untracked']} | {s['last_commit']} |")
    for s in states:
        if s["commits_since"]:
            out += ["", f"## {pathlib.Path(s['path']).name}  ({s['path']})", ""]
            out += [f"- {c}" for c in s["commits_since"]]
    return "\n".join(out) + "\n"


def cmd_repos(a):
    cfg = load_config(a.config)
    states = [repo_state(r, a.since, a.fetch) for r in find_repos(a.roots or cfg["roots"], cfg["repo_exclude"])]
    print(json.dumps(states, indent=1) if a.json else repos_markdown(states, a.since))
    return 0


# -- collect -----------------------------------------------------------------------------
def copy_tree(src, dst, exclude, max_bytes, log):
    src = expand(src)
    if not src.exists():
        log.append(f"missing (skipped): {src}")
        return 0
    n = 0
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return 1
    for dirpath, dirnames, files in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES and not excluded(pathlib.Path(dirpath) / d, exclude)]
        for f in files:
            p = pathlib.Path(dirpath) / f
            if excluded(p, exclude):
                continue
            size = p.stat().st_size
            if size > max_bytes:
                log.append(f"too big, listed not copied ({size / 2**20:.1f} MB): {p}")
                continue
            t = dst / p.relative_to(src)
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, t)
            n += 1
    return n


def safe_name(path):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(path).replace(str(pathlib.Path.home()), "~")).strip("_")[:120]


def unpushed_work(repo, dst, max_untracked, exclude, log):
    """What GitHub does not have: the uncommitted diff, commits not on the upstream (as patches), and small
    untracked files. Returns a short summary."""
    dst.mkdir(parents=True, exist_ok=True)
    out = []
    diff = git(repo, "diff", "HEAD", "--binary")
    if diff:
        (dst / "uncommitted.diff").write_text(diff + "\n", encoding="utf-8")
        out.append(f"uncommitted.diff ({len(diff.splitlines())} lines)")
    up = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    remote = git(repo, "remote", "get-url", "origin")
    if not remote or not remote.startswith(("http", "git@", "ssh:")):
        # no GitHub copy at all: carry the whole repository, every branch and its history, as one bundle file
        # (on the other side: git clone NAME.bundle NAME)
        b = dst / f"{pathlib.Path(repo).name}.bundle"
        r = subprocess.run(["git", "-C", str(repo), "bundle", "create", str(b), "--all"], capture_output=True,
                           text=True)
        if r.returncode == 0 and b.is_file():
            out.append(f"whole repository as {b.name} ({b.stat().st_size / 2**20:.1f} MB): not on GitHub")
        else:
            log.append(f"bundle failed for {repo}: {r.stderr.strip()[:200]}")
    if up:
        n = git(repo, "rev-list", "--count", f"{up}..HEAD")
        if n and int(n) > 0:
            pd = dst / "unpushed-commits"
            pd.mkdir(exist_ok=True)
            git(repo, "format-patch", "--quiet", "-o", str(pd), f"{up}..HEAD")
            out.append(f"{n} unpushed commit(s) as patches")
    untracked = git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    total, copied, listed = 0, 0, []
    for rel in untracked:
        p = pathlib.Path(repo) / rel
        if not p.is_file() or excluded(p, exclude) or any(part in SKIP_NAMES for part in p.parts):
            continue
        size = p.stat().st_size
        if total + size > max_untracked:
            listed.append(rel)
            continue
        t = dst / "untracked" / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, t)
        total += size
        copied += 1
    if copied:
        out.append(f"{copied} untracked file(s), {total / 2**20:.1f} MB")
    if listed:
        (dst / "untracked-not-copied.txt").write_text("\n".join(listed) + "\n", encoding="utf-8")
        out.append(f"{len(listed)} untracked file(s) over the size budget, listed only")
    if not out:
        shutil.rmtree(dst, ignore_errors=True)
    return out


def manifest(root):
    rows = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "MANIFEST.json":
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            rows.append({"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size, "sha256": h})
    return rows


def cmd_collect(a):
    cfg = load_config(a.config)
    out = pathlib.Path(a.out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty: choose a new folder (nothing is overwritten)")
    out.mkdir(parents=True, exist_ok=True)
    log = [f"catchup collect {dt.datetime.now():%Y-%m-%d %H:%M} on {os.environ.get('COMPUTERNAME') or os.uname().nodename}"
           if hasattr(os, "uname") or os.environ.get("COMPUTERNAME") else "catchup collect"]
    since = a.since or (dt.date.today() - dt.timedelta(days=31)).isoformat()
    repos = find_repos(cfg["roots"], cfg["repo_exclude"])
    states = [repo_state(r, since, a.fetch) for r in repos]
    (out / "repos.md").write_text(repos_markdown(states, since), encoding="utf-8")
    (out / "repos.json").write_text(json.dumps(states, indent=1), encoding="utf-8")
    work = {}
    for r, s in zip(repos, states):
        summary = unpushed_work(r, out / "unpushed-work" / safe_name(r), cfg["max_untracked_mb"] * 2**20,
                                cfg["exclude"], log)
        if summary:
            work[s["path"]] = summary
    counts = {}
    for name, paths in cfg["sets"].items():
        n = 0
        for p in paths:
            n += copy_tree(p, out / name / safe_name(p), cfg["exclude"], cfg["max_file_mb"] * 2**20, log)
        counts[name] = n
    # the clone hooks that honor the owner-wide mandate are private to each clone, so they ride in every bundle
    # (2026-10-26, owner: "fa permanent"): the other computer runs `fieldkit mandate install-hooks` from them
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
        from fieldkit.mandate import upkeep
        upkeep.stick(out / "mandate-hooks")
        counts["mandate-hooks"] = sum(1 for f in (out / "mandate-hooks").rglob("*") if f.is_file())
    except Exception as e:  # noqa: BLE001 - no Fieldkit beside this kit: the bundle goes without them, said so
        log.append(f"mandate-hooks not added: {type(e).__name__}: {e}")
    (out / "collect.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    # skeleton: facts per repo; the agent writes the narrative into HANDOFF.md
    sk = ["# HANDOFF (skeleton generated by catchup; the agent writes HANDOFF.md from it)", "",
          f"Period: {since} to {dt.date.today()}.", "",
          "For each project: what it is (plain words), what changed in the period, where it stands now,",
          "what is open, traps, and what the receiving side must do first. Facts below are generated.", ""]
    for s in states:
        if s["commits_since"] or s["path"] in work:
            sk += [f"## {pathlib.Path(s['path']).name}", f"- path here: `{s['path']}`", f"- remote: {s['remote']}",
                   f"- branch {s['branch']}; " + ("no upstream" if s["upstream"] is None else
                                                  f"{s['ahead']} ahead / {s['behind']} behind {s['upstream']}"),
                   f"- {len(s['commits_since'])} commit(s) in the period; latest: {s['last_commit']}"]
            if s["path"] in work:
                sk.append(f"- NOT on GitHub (in unpushed-work/{safe_name(s['path'])}): " + "; ".join(work[s["path"]]))
            sk += ["- what changed: TODO", "- state now: TODO", "- open items / traps: TODO", ""]
    (out / "HANDOFF-SKELETON.md").write_text("\n".join(sk) + "\n", encoding="utf-8")
    # every open question to the other machine rides in every bundle until it is answered (2026-10-19)
    questions = [r for r in load_ledger(ledger_path(getattr(a, "ledger", ""))) if r["status"] == "open"]
    (out / "QUESTIONS-OPEN.md").write_text(open_questions_md(questions), encoding="utf-8")
    readme = ["README FIRST - machine catch-up handover", "",
              f"Made {dt.datetime.now():%Y-%m-%d %H:%M} by catchup (Fieldkit kits/catchup). Period {since} to {dt.date.today()}.",
              "PRIVATE: internal documentation. Never commit or upload any of it. Hand-carried only.", "",
              "Reading order:",
              "  1. HANDOFF.md            what happened, project by project, and what to do first",
              "  2. repos.md              every repository: where it is, ahead/behind, commits in the period",
              "  3. memory/               the other machine's memory files (MEMORY.md is each folder's index)",
              "  4. instructions/         CLAUDE.md / GEMINI.md / AGENTS.md as they are on the other machine",
              "  5. unpushed-work/        work GitHub does not have: diffs, patches, untracked files",
              "  6. the other folders     private tools, local registry, notes, earlier handovers",
              "  mandate-hooks/           the clone hooks that honor the owner-wide mandate: README.txt inside", "",
              "  7. QUESTIONS-OPEN.md     questions this machine is waiting on: answer each in your report", "",
              "Check the copy before reading: python3 catchup.py verify <the .zip>   (or <the unpacked folder>):",
              "the zip's SHA-256 against its .sha256 file, and every file against MANIFEST.json.", "",
              f"Open questions in this bundle: {len(questions)}",
              "Copied: " + ", ".join(f"{k} {v} file(s)" for k, v in counts.items()),
              f"Repositories: {len(states)}; with work not on GitHub: {len(work)}"]
    (out / "README-FIRST.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")
    (out / "MANIFEST.json").write_text(json.dumps(manifest(out), indent=1), encoding="utf-8")
    print("\n".join(readme))
    return 0


# -- check -------------------------------------------------------------------------------
def scan(root):
    secret_rx = [(k, re.compile(r)) for k, r in SECRETS]
    personal_rx = [(k, re.compile(r)) for k, r in PERSONAL]
    secrets, personal = [], {}
    for p in sorted(pathlib.Path(root).rglob("*")):
        if not p.is_file() or p.stat().st_size > 20 * 2**20:
            continue
        try:
            text = p.read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARK in line:
                continue
            for k, rx in secret_rx:
                for m in rx.finditer(line):
                    v = m.group(0)
                    if FIXTURE.search(v):
                        continue  # a planted fake in a test sandbox; a random real key never spells these
                    secrets.append({"file": str(p.relative_to(root)), "line": n, "kind": k,
                                    "masked": v[:4] + "…" + v[-2:]})
            for k, rx in personal_rx:
                if rx.search(line):
                    personal[k] = personal.get(k, 0) + 1
    return secrets, personal


def cmd_check(a):
    secrets, personal = scan(a.dir)
    for s in secrets:
        print(f"SECRET  {s['kind']:<18} {s['file']}:{s['line']}  {s['masked']}")
    print(f"{len(secrets)} secret(s); personal data (fine in a private bundle): "
          + (", ".join(f"{k} {v}" for k, v in personal.items()) or "none"))
    return 3 if secrets else 0


# -- public-check: nothing identifying may reach a PUBLIC repository --------------------------
RO_LETTERS = re.compile("[ăĂșȘțȚşŞţŢ]")  # Romanian-only (â/î are also French)  privacy-scan: allow
RO_WORDS = re.compile(r"\b(pentru|este|sunt|acest|aceasta|această|ghid|porneste|pornește|opreste|"  # privacy-scan: allow
                      r"oprește|anulare|imprimarea|multumesc|mulțumesc|foarte|trebuie|fisier|fișier|"  # privacy-scan: allow
                      r"dupa|după|inainte|înainte|acum|toate|daca|dacă|lucru|salut|nimic|doar|unde|"  # privacy-scan: allow
                      r"bine|frate|adica|adică|deci|vreau|poti|poți|facem|nu-i|asta|aici|acolo)\b",  # privacy-scan: allow
                      re.I)  # two distinct words on one line count; one alone does not


def public_findings(paths, terms=()):
    """Romanian text, and the owner's private terms (names, places, background), in files that would be
    published. paths: files or git repositories (only files git would publish are read)."""
    out = []
    for root in paths:
        root = pathlib.Path(root)
        if (root / ".git").exists():
            listed = git(root, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
            files = [root / f for f in listed]
        elif root.is_file():
            files = [root]
        else:
            files = [p for p in root.rglob("*") if p.is_file() and not any(s in p.parts for s in SKIP_NAMES)]
        for p in files:
            try:
                if not p.is_file() or p.stat().st_size > 5 * 2**20:
                    continue
                raw = p.read_bytes()
                if bytes(1) in raw[:8192]:
                    continue  # binary: pictures, icons, resources
                text = raw.decode("utf-8", "replace")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                out += [{"file": str(p), "line": n, **f} for f in line_findings(line, terms)]
    return out


def line_findings(line, terms):
    if ALLOW_MARK in line:
        return []
    found = []
    words = {w.lower() for w in RO_WORDS.findall(line)}
    if RO_LETTERS.search(line) or len(words) >= 2:
        found.append({"kind": "romanian", "excerpt": line.strip()[:70]})
    low = line.lower()
    found += [{"kind": "private-term", "excerpt": t[:2] + "…"} for t in terms if t.lower() in low]
    return found


def history_findings(repo, terms):
    """Every commit on every ref (what a push of any branch publishes): added lines and commit messages."""
    p = subprocess.Popen(["git", "-C", str(repo), "log", "--all", "-p", "--no-color", "--no-ext-diff",
                          "--format=@@commit %h%n%B"], stdout=subprocess.PIPE)
    out, commit, path = [], "", ""
    for raw in p.stdout:
        line = raw.decode("utf-8", "replace").rstrip("\n")
        if line.startswith("@@commit "):
            commit, path = line[9:], "(message)"
            continue
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
            continue
        if line.startswith(("--- ", "diff --git", "index ", "@@ ", "Binary files")):
            continue
        if path != "(message)" and not line.startswith("+"):
            continue  # context and removed lines were published by an earlier commit already
        out += [{"file": f"{repo} {commit} {path}", "line": 0, **f} for f in line_findings(line, terms)]
    p.wait()
    return out


def pages_findings(account, terms):
    """What GitHub shows outside git: repository descriptions, release pages, issues and their bodies."""
    def gh(*args):
        r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.stdout if r.returncode == 0 else ""
    out = []
    repos = json.loads(gh("repo", "list", account, "--limit", "500", "--json", "name,description") or "[]")
    for r in repos:
        full = f"{account}/{r['name']}"
        texts = [("description", r.get("description") or "")]
        for t in json.loads(gh("release", "list", "-R", full, "--limit", "500", "--json", "tagName") or "[]"):
            v = json.loads(gh("release", "view", t["tagName"], "-R", full, "--json", "name,body") or "{}")
            texts.append((f"release {t['tagName']}", f"{v.get('name', '')}\n{v.get('body', '')}"))
        for i in json.loads(gh("issue", "list", "-R", full, "--state", "all", "--limit", "500",
                               "--json", "number,title,body") or "[]"):
            texts.append((f"issue #{i['number']}", f"{i['title']}\n{i.get('body') or ''}"))
        for where, text in texts:
            for n, line in enumerate(text.splitlines(), 1):
                out += [{"file": f"{full} {where}", "line": n, **f} for f in line_findings(line, terms)]
    return out, len(repos)


def private_terms():
    """The owner's private word list from Fieldkit's fieldkit.local.json (never committed)."""
    for c in (pathlib.Path(__file__).resolve().parents[2] / "fieldkit.local.json",
              expand("${DOCUMENTS}/Scripts/Fieldkit/fieldkit.local.json")):
        if c.is_file():
            j = json.loads(c.read_text(encoding="utf-8"))
            return [t for t in j.get("privacy", {}).get("terms", []) if t and len(t) > 2]
    return []


def cmd_public_check(a):
    terms = [] if a.no_terms else private_terms()
    found = public_findings(a.paths, terms)
    if a.history:
        for p in a.paths:
            if (pathlib.Path(p) / ".git").exists():
                found += history_findings(p, terms)
    if a.github:
        if not shutil.which("gh"):
            raise SystemExit("--github needs the GitHub CLI `gh`, signed in (Debian: apt install gh)")
        pages, n = pages_findings(a.github, terms)
        print(f"GitHub pages: {n} repositories of {a.github} read (descriptions, releases, issues)")
        found += pages
    for f in found:
        print(f"{f['kind']:<13} {f['file']}:{f['line']}  {f['excerpt']}")
    print(f"{len(found)} finding(s): {sum(f['kind'] == 'romanian' for f in found)} Romanian, "
          f"{sum(f['kind'] == 'private-term' for f in found)} private term(s). Third-party lists (block lists,"
          f" public-suffix data) can match by chance: judge each line.")
    return 3 if found else 0


# -- patch: carry staged (uncommitted) work, proved to reapply ---------------------------------
def cmd_patch(a):
    """Write REPO's staged changes as a binary-safe patch with its base commit, a commit message and how to apply
    it, then PROVE it: apply it to the base commit in a throwaway worktree and compare every touched file with the
    working copy. A patch that does not reproduce the work is deleted (fail closed)."""
    repo, out = pathlib.Path(a.repo), pathlib.Path(a.out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty (nothing is overwritten)")
    out.mkdir(parents=True, exist_ok=True)
    diff = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--binary"], capture_output=True).stdout
    if not diff.strip():
        raise SystemExit(f"nothing staged in {repo}")
    (out / "staged.diff").write_bytes(diff)
    base = git(repo, "rev-parse", "HEAD", check=True)
    (out / "BASE-COMMIT.txt").write_bytes((base + "\n").encode())
    if a.message:
        shutil.copy2(a.message, out / "COMMIT-MESSAGE.txt")
    files = git(repo, "diff", "--cached", "--name-only", "--no-renames").splitlines()
    (out / "HOW-TO-APPLY.txt").write_bytes((
        f"Staged work from {repo.name} (branch {git(repo, 'rev-parse', '--abbrev-ref', 'HEAD')}), not committed.\n"
        "In a clean clone, ONLY on the owner's word:\n"
        "  git checkout -b NAME $(cat BASE-COMMIT.txt)\n"
        "  git apply --index --whitespace=nowarn staged.diff\n"
        "  (run the repository's tests)\n"
        "  git commit -F COMMIT-MESSAGE.txt\n"
        "Do NOT push: the owner says yes first.\n").encode())
    wt = pathlib.Path(a.scratch or (out.parent / f".{out.name}-proof"))
    try:
        git(repo, "worktree", "add", "-q", "--detach", str(wt), base, check=True)
        r = subprocess.run(["git", "-C", str(wt), "apply", "--index", "--whitespace=nowarn", str(out / "staged.diff")],
                           capture_output=True, text=True)
        bad = [] if r.returncode == 0 else [f"git apply: {r.stderr.strip()[:300]}"]
        for f in files if not bad else []:
            mine, theirs = repo / f, wt / f
            if mine.exists() != theirs.exists() or (mine.is_file() and mine.read_bytes() != theirs.read_bytes()):
                bad.append(f)
    finally:
        git(repo, "worktree", "remove", "--force", str(wt))
    if bad:
        shutil.rmtree(out, ignore_errors=True)
        raise SystemExit(f"the patch does not reproduce the work ({len(bad)}): {bad[:5]}; nothing kept")
    print(f"{out}: {len(files)} file(s), base {base[:12]}; applied to the base in a scratch worktree, every file "
          f"identical to the working copy")
    return 0


def cmd_manifest(a):
    """Refresh MANIFEST.json after something was added by hand (a HANDOFF.md, a patch)."""
    root = pathlib.Path(a.dir)
    rows = manifest(root)
    (root / "MANIFEST.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"MANIFEST.json: {len(rows)} file(s)")
    return 0


# -- pack ----------------------------------------------------------------------------------
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def free_name(dest, name):
    p = dest / name
    i = 2
    while p.exists():
        p = dest / f"{pathlib.Path(name).stem}_{i}{pathlib.Path(name).suffix}"
        i += 1
    return p


def cmd_pack(a):
    src, dest = pathlib.Path(a.dir), pathlib.Path(a.dest)
    if not (src / "README-FIRST.txt").is_file():
        raise SystemExit(f"{src} has no README-FIRST.txt: run collect first")
    secrets, _ = scan(src)
    if secrets and not a.allow_secrets:
        raise SystemExit(f"{len(secrets)} secret(s) in {src}: run check, remove them, then pack (fail closed)")
    dest.mkdir(parents=True, exist_ok=True)
    need = sum(p.stat().st_size for p in src.rglob("*") if p.is_file())
    if shutil.disk_usage(dest).free < need * 1.1:
        raise SystemExit(f"not enough room at {dest}: need about {need / 2**20:.0f} MB")
    if a.replace:
        # the previous pack of THIS bundle only: its zip, its .sha256 and the README beside them
        for n in (src.name + ".zip", src.name + ".zip.sha256", "README-FIRST.txt"):
            if (dest / n).is_file():
                (dest / n).unlink()
    final = free_name(dest, src.name + ".zip")
    part = final.with_suffix(".zip.part")
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, (pathlib.Path(src.name) / p.relative_to(src)).as_posix())
    local_sha = sha256(part)
    with zipfile.ZipFile(part) as z:
        bad = z.testzip()
    if bad:
        part.unlink()
        raise SystemExit(f"zip check failed at member {bad}; nothing written")
    os.replace(part, final)
    if sha256(final) != local_sha:
        raise SystemExit(f"read-back SHA-256 differs for {final}")
    # LF on every system: a CRLF line makes Debian's `sha256sum -c` look for "NAME\r" and fail
    (final.parent / (final.name + ".sha256")).write_bytes(f"{local_sha}  {final.name}\n".encode("utf-8"))
    rf = free_name(dest, "README-FIRST.txt")
    shutil.copy2(src / "README-FIRST.txt", rf)
    print(f"wrote {final} ({final.stat().st_size / 2**20:.1f} MB), sha256 {local_sha[:16]}..., every member checked;"
          f" {rf.name} beside it")
    return 0


# -- verify (the receiver's side) -------------------------------------------------------------
def _members(zf):
    """zip member name -> path relative to the bundle folder (the zip holds <bundle>/<path>)."""
    out = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        parts = info.filename.split("/", 1)
        out[parts[1] if len(parts) == 2 else parts[0]] = info
    return out


def verify(target):
    """-> {"ok", "problems": [...], "checked": n} for a packed bundle (ZIP, with ZIP.sha256 beside it) or an unpacked
    bundle folder. Every MANIFEST.json entry must be there with its SHA-256; a file the manifest does not list is
    reported too (something added after packing). Stdlib only: it runs on the receiving machine as it is.
    2026-10-19: the receiver was told to paste an inline Python heredoc to do this (HANDOFF-261012 step 4)."""
    target = pathlib.Path(target)
    problems, checked = [], 0
    if target.is_file() and target.suffix == ".zip":
        side = target.with_name(target.name + ".sha256")
        if not side.is_file():
            problems.append(f"no {side.name} beside the zip: the bundle cannot be checked against what was packed")
        else:
            want = side.read_text(encoding="utf-8").split()[0].lower()
            got = sha256(target)
            if want != got:
                problems.append(f"{target.name}: SHA-256 {got[:16]}... but {side.name} says {want[:16]}... "
                                "(a partial or changed copy)")
        try:
            zf = zipfile.ZipFile(target)
        except zipfile.BadZipFile as e:
            return {"ok": False, "problems": problems + [f"not a readable zip: {e}"], "checked": 0}
        with zf:
            bad = zf.testzip()
            if bad:
                problems.append(f"zip member {bad} is damaged (CRC)")
            members = _members(zf)
            if "MANIFEST.json" not in members:
                return {"ok": False, "problems": problems + ["no MANIFEST.json in the bundle"], "checked": 0}
            rows = json.loads(zf.read(members["MANIFEST.json"]).decode("utf-8"))
            for r in rows:
                info = members.get(r["path"])
                if info is None:
                    problems.append(f"missing: {r['path']}")
                    continue
                data = zf.read(info)
                checked += 1
                if hashlib.sha256(data).hexdigest() != r["sha256"]:
                    problems.append(f"differs: {r['path']}")
            listed = {r["path"] for r in rows} | {"MANIFEST.json"}
            problems += [f"not in the manifest: {p}" for p in sorted(set(members) - listed)]
    elif target.is_dir():
        mf = target / "MANIFEST.json"
        if not mf.is_file():
            return {"ok": False, "problems": ["no MANIFEST.json in the folder"], "checked": 0}
        rows = json.loads(mf.read_text(encoding="utf-8"))
        for r in rows:
            p = target / r["path"]
            if not p.is_file():
                problems.append(f"missing: {r['path']}")
                continue
            checked += 1
            if sha256(p) != r["sha256"]:
                problems.append(f"differs: {r['path']}")
        listed = {r["path"] for r in rows} | {"MANIFEST.json"}
        present = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
        problems += [f"not in the manifest: {p}" for p in sorted(present - listed)]
    else:
        return {"ok": False, "problems": [f"{target}: neither a .zip nor a bundle folder"], "checked": 0}
    return {"ok": not problems, "problems": problems, "checked": checked}


def cmd_verify(a):
    r = verify(a.target)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        for p in r["problems"]:
            print("  " + p)
        print(f"VERIFY {'OK' if r['ok'] else 'FAILED'}: {r['checked']} file(s) checked against MANIFEST.json"
              + ("" if r["ok"] else f", {len(r['problems'])} problem(s): do not use this copy"))
    return 0 if r["ok"] else 3


# -- the question ledger -----------------------------------------------------------------------
def ledger_path(given=""):
    """The ledger of questions to the other machine: --ledger, CATCHUP_LEDGER, else Fieldkit's local/ (private,
    git-ignored) when this kit sits inside Fieldkit, else ~/.catchup-questions.json."""
    if given:
        return pathlib.Path(given)
    if os.environ.get("CATCHUP_LEDGER"):
        return pathlib.Path(os.environ["CATCHUP_LEDGER"])
    fk = pathlib.Path(__file__).resolve().parents[2]
    if (fk / "fieldkit").is_dir():
        return fk / "local" / "catchup-questions.json"
    return pathlib.Path.home() / ".catchup-questions.json"


def load_ledger(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def save_ledger(path, rows):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def ask(path, text, to, today=None):
    """Add a question; -> its id (Q-YYYYMMDD-N). 2026-10-19: questions to the Linux machine were repeated by hand
    from handover to handover ("may still be unanswered") and never closed."""
    rows = load_ledger(path)
    day = (today or dt.date.today()).strftime("%Y%m%d")
    n = 1 + sum(1 for r in rows if r["id"].startswith(f"Q-{day}-"))
    qid = f"Q-{day}-{n}"
    rows.append({"id": qid, "asked": (today or dt.date.today()).isoformat(), "to": to, "question": text,
                 "status": "open", "answer": None, "answered": None})
    save_ledger(path, rows)
    return qid


def answer(path, qid, text, today=None):
    rows = load_ledger(path)
    for r in rows:
        if r["id"] == qid:
            if r["status"] == "answered":
                raise SystemExit(f"{qid} is already answered ({r['answered']}): {r['answer']}")
            r.update(status="answered", answer=text, answered=(today or dt.date.today()).isoformat())
            save_ledger(path, rows)
            return r
    raise SystemExit(f"no question {qid} in {path}")


def open_questions_md(rows, today=None):
    """The open questions as a page carried in every bundle until each is answered, oldest first."""
    today = today or dt.date.today()
    rows = sorted((r for r in rows if r["status"] == "open"), key=lambda r: r["asked"])
    if not rows:
        return "# Open questions\n\nNone.\n"
    out = ["# Open questions (answer each; the sender closes them with `catchup.py answer ID TEXT`)", ""]
    for r in rows:
        age = (today - dt.date.fromisoformat(r["asked"])).days
        out.append(f"- **{r['id']}** (to {r['to']}, asked {r['asked']}, {age} day(s) open): {r['question']}")
    return "\n".join(out) + "\n"


def cmd_ask(a):
    qid = ask(ledger_path(a.ledger), a.text, a.to)
    print(f"{qid} recorded; it goes into every bundle (QUESTIONS-OPEN.md) until it is answered")
    return 0


def cmd_answer(a):
    r = answer(ledger_path(a.ledger), a.id, a.text)
    print(f"{r['id']} answered {r['answered']}")
    return 0


def cmd_questions(a):
    rows = load_ledger(ledger_path(a.ledger))
    if not a.all:
        rows = [r for r in rows if r["status"] == "open"]
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        for r in rows:
            print(f"{r['id']}  {r['status']:<8} to {r['to']:<8} {r['asked']}  {r['question'][:90]}"
                  + (f"\n    -> {r['answer']}" if r["answer"] else ""))
        print(f"{sum(1 for r in rows if r['status'] == 'open')} open")
    return 0


# -- doctor ------------------------------------------------------------------------------
def cmd_doctor(a):
    ok = True
    gitv = shutil.which("git")
    print(f"{'ok ' if gitv else 'MISSING'}  git        {gitv or 'install git (Debian: apt install git; Windows: winget install Git.Git)'}")
    ok &= bool(gitv)
    print(f"{'ok ' if sys.version_info >= (3, 9) else 'MISSING'}  python>=3.9 {sys.version.split()[0]}")
    ok &= sys.version_info >= (3, 9)
    if a.dest:
        d = pathlib.Path(a.dest)
        if d.exists():
            print(f"ok   destination {d}: {shutil.disk_usage(d).free / 2**30:.1f} GB free")
        else:
            print(f"MISSING  destination {d} (plug the stick in / create the folder)")
            ok = False
    print("Debian reads exFAT and FAT32 sticks natively (kernel 5.4+); unzip with `unzip` or Python's zipfile.")
    return 0 if ok else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # commit subjects can hold any character (a BOM broke cp1252)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor")
    p.add_argument("--dest", default="")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("repos")
    p.add_argument("roots", nargs="*")
    p.add_argument("--since", default="")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--config", default="")
    p.set_defaults(fn=cmd_repos)
    p = sub.add_parser("collect")
    p.add_argument("--out", required=True)
    p.add_argument("--config", default="")
    p.add_argument("--since", default="")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--ledger", default="", help="the question ledger whose open questions go into the bundle")
    p.set_defaults(fn=cmd_collect)
    p = sub.add_parser("check")
    p.add_argument("dir")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("public-check")
    p.add_argument("paths", nargs="+", help="repositories (only what git would publish is read), folders or files")
    p.add_argument("--no-terms", action="store_true", help="Romanian only, without the private word list")
    p.add_argument("--history", action="store_true", help="also every commit on every ref: added lines and messages")
    p.add_argument("--github", default="", metavar="ACCOUNT",
                   help="also the account's repository descriptions, release pages and issues (network, gh)")
    p.set_defaults(fn=cmd_public_check)
    p = sub.add_parser("pack")
    p.add_argument("dir")
    p.add_argument("dest")
    p.add_argument("--allow-secrets", action="store_true", help="pack even with secrets found (owner's call only)")
    p.add_argument("--replace", action="store_true",
                   help="replace this bundle's previous zip, .sha256 and README at DEST (nothing else is touched)")
    p.set_defaults(fn=cmd_pack)
    p = sub.add_parser("patch")
    p.add_argument("repo")
    p.add_argument("--out", required=True)
    p.add_argument("--message", default="", help="a commit message file to carry with it")
    p.add_argument("--scratch", default="", help="where the proof worktree goes (default: beside --out)")
    p.set_defaults(fn=cmd_patch)
    p = sub.add_parser("manifest")
    p.add_argument("dir")
    p.set_defaults(fn=cmd_manifest)
    p = sub.add_parser("verify", help="the receiver's check: zip SHA-256 vs its .sha256, every file vs MANIFEST.json")
    p.add_argument("target", help="the bundle .zip (its .sha256 beside it) or the unpacked bundle folder")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_verify)
    for name, fn in (("ask", cmd_ask), ("answer", cmd_answer), ("questions", cmd_questions)):
        p = sub.add_parser(name)
        p.add_argument("--ledger", default="", help="the question ledger (default: Fieldkit local/catchup-questions.json)")
        if name == "ask":
            p.add_argument("text")
            p.add_argument("--to", default="linux", help="which machine is asked (default linux)")
        elif name == "answer":
            p.add_argument("id")
            p.add_argument("text")
        else:
            p.add_argument("--all", action="store_true", help="answered ones too")
            p.add_argument("--json", action="store_true")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
