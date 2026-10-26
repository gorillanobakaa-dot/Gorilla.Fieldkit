# Version: 1.6.0 · updated 26-10-26-12-00
"""A project mandate: the owner's ONE approval for a whole project, valid on both computers, checkable by each.

    fieldkit mandate keygen                       the owner's signing key, once per owner (passphrase-protected)
    fieldkit mandate trust PUBFILE                the other computer pins the owner's PUBLIC key (once, typed yes)
    fieldkit mandate approve FILE.yaml            shows what the mandate allows (every door it opens), typed yes and
                                                  the passphrase: writes FILE.seal (Ed25519 over its sha256)
    fieldkit mandate verify FILE.yaml             seal, hash, trusted key, expiry, stop: in force or why not
    fieldkit mandate allows FILE.yaml ACTION [k=v ...]   a PREVIEW: allow | ask | deny and the clause (gates nothing)
    fieldkit mandate stop FILE.yaml               the owner's stop: nothing is allowed by it any more
    fieldkit mandate share FILE.yaml              the mandate, its seal and the public key to the other computer
    fieldkit mandate review-key                   this computer's review key (made once; its public half is carried
                                                  to the other computer)
    fieldkit mandate trust-reviewer PUBFILE       the owner pins the OTHER computer's review key (guarded store)
    fieldkit mandate verdict REPO SHA             the worker's test run by hand (temporary worktree, measured against
                                                  main): printed, nothing sent or signed
    fieldkit mandate job send|status|wait ...     one orchestrator, many workers (jobs.py): a job to the worker, the
                                                  ledger, one blocking wait for a result
    fieldkit mandate boss                         the devices seen and the proposed boss (always on > memory > cable)
    fieldkit mandate ledger [N]                   the project ledger, append-only, from event N
    fieldkit mandate resume                       a restarted boss: open jobs, workers asked to resend missed results
    fieldkit mandate tasks init|list|pass ...     the task list, all failing at first; passing only with evidence
    fieldkit mandate start                        a session's startup check: ledger, mandate, link, the next thing
    fieldkit mandate report [--since N]           the one morning report, from the ledger
    fieldkit mandate ask "..." [--why "..."]      what truly needs the owner, queued for that report (never asked live)
    fieldkit mandate quiet --install              while a mandate is in force no session asks the owner (two hooks)
    fieldkit mandate status                       every mandate here and whether it is in force
    fieldkit mandate install GB WHAT -- CMD ...   an install inside the budget: runs CMD, measures the disk, logs it
    fieldkit mandate gate [--repo .]              a repository's pre-push hook: the push lines on stdin, allowed only
                                                  through fieldkit ship push under a mandate in force (gate.py)
    fieldkit mandate doctor [--json]              in force, days left, keys, the VAIO's key, every covered hook (upkeep.py)
    fieldkit mandate install-hooks [--yes] [--stick DIR]   the gate-aware hook in every covered clone here (upkeep.py)
    fieldkit mandate renew FILE.yaml [--days 7] [--from CHANGED.yaml]   a new period (with --from: the changed
                                                  mandate, new doors marked NEW): typed yes + passphrase (upkeep.py)
    fieldkit mandate prereqs [check | apply [--force] | undo ID]   the mandate's machine prerequisites (machine.py):
                                                  approved once with the mandate, applied at a session start

The OWNER-WIDE mandate (scope: owner-wide, 2026-10-25, owner: "the approval has to come once, at the beginning ...
has to cover all our projects"): one file, `defaults:` (branch patterns, tag patterns, merge after green CI, release
from a tag) under every repository it names; the approve screen adds what it does NOT cover and the threat model.

Callers (M3): `fieldkit ship push` logs a push the mandate covers; `fieldkit ship merge` merges into main ONLY when
a mandate allows it, with every condition proven by the tool; the autopilot hard lines 1 and 4 open for a push or an
owner approval a mandate in force covers (and log it), and judge `fieldkit mac run` on a granted Mac as the Mac's
business; while a mandate is in force, installs go through `fieldkit mandate install`.

Owner, 2026-10-23, after a day as the router between 16 sessions on two computers: "aprobarile se dau o singura data
pentru un proiect si ar trebui sa fie valabile si pentru cealalta sesiune ... doua sesiuni lucrand la acelasi proiect
sub acelasi mandat using the same SOPs." Plan: local/PROJECT-MANDATE-PLAN.md.

Why a signing key and not the cable's key (the Windows side's review, 2026-10-23): anything that can read link.key
could mint a seal; a seal must prove THE OWNER'S TERMINAL. The private key is encrypted with a passphrase only the
owner knows (a model can fake a terminal, not the passphrase); the other computer holds only the public key, so it
can verify and never sign. Fail closed: no seal, a changed byte, an untrusted key, an expired or stopped mandate,
an unreadable file -> nothing is allowed by the mandate ("ask": the old behaviour, the owner is asked).

M1.1 (the Windows review of ba40f28): every write under the mandate home is a hard line (a model could otherwise pin
its own key); FIELDKIT_MANDATE_HOME counts only under pytest; a trusted key must match its file name; a merge's
conditions and a push's fast-forward are found by the tool (proofs.py), never given by the caller; a stop is
permanent for the file it stopped.
"""
import fnmatch
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import yaml

REQUIRED = ("project", "goal", "expires", "repos", "never")
ACTIONS = ("push", "tag", "merge_main", "release_tag", "install", "mac", "release", "approval")
# owner approvals no mandate can grant: the mandate's own key, and the owner's signature on a harness change
NEVER_GRANTED = re.compile(r"\bmandate\b|gorilla_owner_approved", re.I)
# never pushed under a mandate, whatever a pattern says: the base branches move only by `fieldkit ship merge`
BASE_BRANCHES = re.compile(r"^(main|master|windows-port|gh-pages)$")
# a kernel on the owner's machine (2026-10-25): built, pushed and published under a mandate, never installed or booted
KERNEL_BOOT = re.compile(r"\blinux-(image|headers|modules)\b|\bdpkg\b[^\n]*\s-i\b[^\n]*(kernel|linux)|"
                         r"\b(update-grub|grub-(install|mkconfig|reboot|set-default)|update-initramfs|mkinitcpio|"
                         r"dracut|kexec|bootctl|efibootmgr)\b|\bsystemctl\s+reboot\b|(^|[;&|]\s*|\bsudo\s+)reboot\b|"
                         r"\bshutdown\b[^\n]*\s-r\b", re.I)

# What no mandate covers, whatever it says: shown on the approve screen as NOT COVERED, and each one enforced by the
# place named (owner, 2026-10-25). The mandate never lists them as doors; a mandate that tries is still shown these.
NOT_COVERED = (
    ("decision-register approvals (product-decisions, gorilla_decisions.json, record_decisions.py)",
     "hard lines 3 and 4; the gorilla-patchset pre-push hook still wants GORILLA_OWNER_APPROVED for that file"),
    ("leak-gate allowlist entries, dispositions and re-judging", "hard lines 3 and 4"),
    ("changes to the guards, the git hooks, the hard lines and the mandate code or store itself",
     "hard line 3; a hook is installed by your own paste, or by `fieldkit mandate install-hooks --yes` (only the "
     "template merged into Fieldkit main, byte for byte; hard line 4 keeps every unattended run off it)"),
    ("force-push or any history rewrite", "fieldkit ship (never forces), the pre-push gate (fast-forward only)"),
    ("deleting a branch or a tag on GitHub, or moving a tag that exists", "the pre-push gate (no deletes, new tags only)"),
    ("pushing main, master or windows-port directly", "the pre-push gate; main moves only by a pull request merged "
                                                       "after green CI (fieldkit ship merge)"),
    ("real permanent deletes outside the scratch folders", "hard line 2"),
    ("downloading a new third-party tool", "not a door of this mandate: the install budget is 0 unless you write one"),
    ("administrator rights or a UAC prompt on the laptop, beyond the machine prerequisites listed above",
     "the elevate kit needs your click; no mandate clicks it (a listed prerequisite still shows ONE UAC prompt the "
     "first time a session start applies it: Windows lets no program answer its own prompt)"),
    ("installing or booting a kernel on your machine", "KERNEL_BOOT: refused by the install door and the hard lines"),
)


def owner_wide(m):
    return str(m.get("scope", "")) == "owner-wide"


def repo_cfg(m, repo):
    """The repository's block with the mandate's `defaults:` under it -> dict, or None when the mandate does not name
    the repository. `Gorilla.Opencode: {}` (or empty) means the defaults alone."""
    repos = m.get("repos") or {}
    if repo not in repos:
        return None
    out = dict(m.get("defaults") or {})
    out.update(repos.get(repo) or {})
    return out


def home():
    # No environment override (M1.2: `import pytest` satisfied the M1.1 test guard). Tests replace this function.
    # What decides trust is not here at all: guarded.py (owner keys, reviewer keys, stops), where this user cannot write.
    return Path.home() / ".fieldkit" / "mandate"


def _key_paths():
    h = home()
    return h / "owner_signing.pem", h / "owner_signing.pub"   # *.pem: hard line 5 keeps every model off it


def fingerprint(pub_raw: bytes) -> str:
    d = hashlib.sha256(pub_raw).hexdigest()[:16]
    return "-".join(d[i:i + 4] for i in range(0, 16, 4))


# ---- keys -----------------------------------------------------------------------------------------------------------
def keygen(passphrase: str):
    """-> (pub_path, fingerprint). Refuses to replace an existing key (a new key voids every seal)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv_p, pub_p = _key_paths()
    if priv_p.exists():
        raise FileExistsError(f"{priv_p} exists: a second key would void every seal; remove it by hand if meant")
    if len(passphrase) < 12:
        raise ValueError("the passphrase needs at least 12 characters")
    k = Ed25519PrivateKey.generate()
    priv_p.parent.mkdir(parents=True, exist_ok=True)
    priv_p.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.BestAvailableEncryption(passphrase.encode())))
    os.chmod(priv_p, 0o600)
    raw = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    pub_p.write_text(raw.hex() + "\n", encoding="utf-8")
    return pub_p, fingerprint(raw)


def trust_raw(raw: bytes, folder="trusted"):
    """Pin a public key in the guarded store, as the owner (a password prompt) -> (ok, message)."""
    from . import guarded
    if len(raw) != 32:
        return False, "not an Ed25519 public key (32 bytes)"
    return guarded.write(f"{folder}/{fingerprint(raw)}.pub", raw.hex())


def _pinned(folder):
    from . import guarded
    out = {}
    for p in guarded.listing(folder):
        try:
            raw = bytes.fromhex(p.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if fingerprint(raw) == p.stem:                  # the review's point 3: the name alone proves nothing
            out[p.stem] = raw
    return out


def trusted_keys():
    return _pinned("trusted")


def reviewer_keys():
    return _pinned("reviewers")


# ---- this computer's REVIEW key: what makes "APPROVE <sha>" the other computer's word (M1.2, review point B) -----------
def _review_paths():
    return home() / "machine_review.pem", home() / "machine_review.pub"


def review_key():
    """-> (private key, fingerprint). Made once per computer, unencrypted (a reviewing session signs unattended); it
    lives on this computer only, and only the OTHER computer pins its public half, so it proves which computer
    reviewed, never that the owner did."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv_p, pub_p = _review_paths()
    if priv_p.is_file():
        k = serialization.load_pem_private_key(priv_p.read_bytes(), password=None)
    else:
        k = Ed25519PrivateKey.generate()
        priv_p.parent.mkdir(parents=True, exist_ok=True)
        priv_p.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        os.chmod(priv_p, 0o600)
    raw = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if not pub_p.is_file():
        pub_p.write_text(raw.hex() + "\n", encoding="utf-8")
    return k, fingerprint(raw)


def approval_text(repo, commit):
    return f"APPROVE {repo} {commit}"


def sign_review(repo, commit):
    """-> the approval as it travels: {repo, commit, key, sig}. Only a full 40-character sha is ever signed."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        raise ValueError("APPROVE needs the full 40-character commit sha")
    k, fp = review_key()
    return {"repo": repo, "commit": commit, "key": fp, "sig": k.sign(approval_text(repo, commit).encode()).hex()}


# ---- the mandate ----------------------------------------------------------------------------------------------------
def load(path):
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    missing = [k for k in REQUIRED if k not in d]
    if missing:
        raise ValueError(f"the mandate lacks: {', '.join(missing)}")
    return d


def doors(m):
    """Every door the mandate opens, in plain words: what the owner reads before typing yes."""
    out = []
    for repo in (m.get("repos") or {}):
        r = repo_cfg(m, repo)
        if r.get("push"):
            out.append(f"sessions may PUSH, fast-forward only, these branches of {repo}: {', '.join(r['push'])}")
        if r.get("tags"):
            out.append(f"  and push NEW tags {', '.join(r['tags'])} on a commit already on GitHub (a tag is never "
                       f"moved or deleted)")
        if r.get("release") == "from_tag":
            out.append(f"  and publish a GitHub release (gh release create) of such a tag once its CI is green")
        if r.get("kernel"):
            out.append("  kernel repository: build, push the branches above, publish the .deb on a release; NEVER "
                       "install or boot it on your machine (that needs your separate yes)")
        mm = r.get("merge_to_main")
        if mm:
            out.append(f"sessions may MERGE into {repo} main ({mm.get('how', 'fast-forward')}) when: "
                       f"{', '.join(mm.get('when', []))}"
                       + (" (every GitHub check of that exact commit finished green; no CI = no merge)"
                          if "ci_green" in (mm.get("when") or []) else ""))
            if any(str(w).split(":")[0] in ("other_machine_reviewed", "tests_on") for w in mm.get("when") or []):
                # the Windows review of 804f28b: say what the proof is, and what it is not
                out.append("  a worker's RESULT (other_machine_reviewed, tests_on:<system>): a worker ran the full suite "
                           "on that exact commit, on its own OS, measured against main, signed by its key for "
                           "provenance. NO PERSON READS A DIFF BEFORE MAIN under this gate: it proves another OS ran "
                           "the tests and they did not shrink, nothing more. The worker's concerns are reported; "
                           "with ask_when: [worker_concern] a concern stops the merge and comes to you")
    if float((m.get("install") or {}).get("budget_gb", 0) or 0) > 0:
        out.append(f"sessions may INSTALL software up to {m['install'].get('budget_gb', 0)} GB, each logged")
    for name in (m.get("mac") or {}).values() if isinstance(m.get("mac"), dict) else []:
        out.append(f"sessions have full read/write access to the Mac '{name}'")
    for a in m.get("owner_approvals") or []:
        out.append(f"sessions may run, as if you approved it: {a}")
    for role in ("orchestrator", "backup"):
        b = m.get(role)
        if isinstance(b, dict) and b.get("device"):
            out.append(f"the {'BOSS (orchestrator)' if role == 'orchestrator' else 'backup boss'} is {b['device']} "
                       f"(key {b.get('key', '?')}): every other device takes jobs only from it, and asks nobody")
    rel = m.get("release", "ask")
    if isinstance(rel, dict) and isinstance(rel.get("cycle"), dict) and rel["cycle"]:
        out.append("sessions may RELEASE, the whole cycle (build every system, checksums, screenshots, both tracks, "
                   "publish on GitHub, clean up), exactly these: " + "; ".join(
                       f"{p_} {', '.join(map(str, v_ or []))}" for p_, v_ in rel["cycle"].items()))
    elif not owner_wide(m):                            # owner-wide: releases are per repository (release: from_tag)
        out.append("release (tags, published assets): " + str(rel))
    from . import machine
    out += machine.door_lines(m)                       # machine prerequisites: approved here, applied at a start
    out.append("never: " + ", ".join(m.get("never") or []))
    out.append(f"ends: {m['expires']}")
    return out


def not_covered():
    """What stays yours with this mandate in force, and what enforces each: the approve screen's second list."""
    return [f"{what}  [{by}]" for what, by in NOT_COVERED]


def threat_model(path):
    """Who can act through the mandate, where the key is, what is logged, how to stop it: the approve screen's third
    list, with this computer's real paths."""
    from . import guarded
    priv, _pub = _key_paths()
    return [
        "WHO CAN ACT THROUGH IT: any process on a computer that trusts your key, running as your user, that pushes "
        "through fieldkit ship (every Claude, Gemini or OpenCode session, and any program you run). It opens only "
        "the doors above, and only until it ends or you stop it.",
        "WHO CAN MAKE A NEW ONE: whoever holds BOTH your private key file and its passphrase. The passphrase is never "
        "written anywhere by Fieldkit; a model can fake a terminal, not the passphrase.",
        f"KEY STORAGE: private key {priv} (encrypted with the passphrase; hard line 5 keeps models off *.pem); the "
        f"public half pinned in {guarded.root()} (only an administrator / root can write there).",
        f"WHAT IS LOGGED: every push, tag, merge and release a mandate allows, in {_log_path(path)} (append-only by "
        "habit, not by force: your user can edit it), and by GitHub itself (the push and release events, which no "
        "session can edit).",
        "WHAT IT DOES NOT STOP: a program running as your user can still do whatever your user can (edit a hook, "
        "git push --no-verify). The hooks and hard lines stop mistakes and unattended models; GitHub branch "
        "protection is what stops a determined local program.",
        f"HOW TO STOP IT: fieldkit mandate stop {path}  (at once, on this computer; permanent for this file), or "
        f"let it expire on {load(path)['expires']}. On the other computer run the same stop.",
    ]


REF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$")


def _plain_ref(name):
    return bool(REF_NAME.match(name or "")) and ".." not in name and "//" not in name and \
        not name.endswith((".lock", "/"))


def branch_ok(cfg, branch):
    """-> (ok, why): a branch the repository's block lets sessions push (never a base branch, whatever the pattern)."""
    if not _plain_ref(branch):
        return False, f"{branch!r} is not a plain branch name"
    if BASE_BRANCHES.match(branch):
        return False, f"{branch} is a base branch: it moves only by fieldkit ship merge"
    if not any(fnmatch.fnmatchcase(branch, pat) for pat in cfg.get("push") or []):
        return False, f"{branch} is not a branch the mandate lets sessions push ({', '.join(cfg.get('push') or [])})"
    return True, f"{branch} matches {', '.join(cfg.get('push') or [])}"


def tag_ok(cfg, tag):
    if not _plain_ref(tag):
        return False, f"{tag!r} is not a plain tag name"
    if not any(fnmatch.fnmatchcase(tag, pat) for pat in cfg.get("tags") or []):
        return False, f"{tag} is not a tag the mandate lets sessions push ({', '.join(cfg.get('tags') or []) or 'none'})"
    return True, f"{tag} matches {', '.join(cfg.get('tags'))}"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _seal_path(path):
    return Path(path).with_suffix(".seal")


def _payload(sha, m, approved_at, fp):
    return json.dumps({"sha256": sha, "project": m["project"], "expires": str(m["expires"]),
                       "approved_at": approved_at, "key": fp}, sort_keys=True, separators=(",", ":")).encode()


def approve(path, passphrase, confirmed, say=print):
    """confirmed: the owner typed yes after reading doors(). The passphrase unlocks the private key."""
    from cryptography.hazmat.primitives import serialization
    m = load(path)
    if not confirmed:
        return 1, "not approved"
    if stopped(path):
        return 4, "this mandate was stopped: a stop is permanent for it; write a changed mandate and approve that"
    priv_p, pub_p = _key_paths()
    if not priv_p.exists():
        return 2, "no owner signing key here: fieldkit mandate keygen (once)"
    try:
        k = serialization.load_pem_private_key(priv_p.read_bytes(), password=passphrase.encode())
    except (ValueError, TypeError):
        return 3, "wrong passphrase: not approved"
    raw = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    fp = fingerprint(raw)
    at = time.strftime("%Y-%m-%d %H:%M:%S")
    sha = _sha(path)
    sig = k.sign(_payload(sha, m, at, fp))
    _seal_path(path).write_text(json.dumps({"sha256": sha, "project": m["project"], "expires": str(m["expires"]),
                                            "approved_at": at, "key": fp, "sig": sig.hex()}, indent=1),
                                encoding="utf-8")
    log(path, "approved", f"key {fp}")
    return 0, f"APPROVED {m['project']} until {m['expires']} (key {fp})"


def stop_path(path):
    return Path(path).with_suffix(".STOP")


def stopped(path):
    """The .STOP beside it, or its sha256 in the guarded stop list (a deleted .STOP does not lift a stop, and the list
    is where this user cannot write; the review's points 5 and A)."""
    from . import guarded
    if stop_path(path).exists():
        return True
    try:
        sha = _sha(path)
        lst = guarded.read("stopped.txt")
    except OSError:
        return True                                     # unreadable: fail closed
    return any(line.split()[:1] == [sha] for line in lst.splitlines())


def verify(path, today=None):
    """-> (in_force: bool, why). Fail closed on every doubt."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:                                   # the lean set (a Mac from the zip) has no cryptography
        return False, "the cryptography package is not installed here, so no seal can be checked (NOT in force)"
    try:
        m = load(path)
    except (OSError, ValueError, yaml.YAMLError) as e:
        return False, f"unreadable mandate: {e}"
    if stopped(path):
        return False, "STOPPED by the owner"
    sp = _seal_path(path)
    if not sp.is_file():
        return False, "not approved (no seal)"
    try:
        seal = json.loads(sp.read_text(encoding="utf-8"))
    except ValueError:
        return False, "the seal is unreadable"
    if seal.get("sha256") != _sha(path):
        return False, "the mandate changed after its approval"
    raw = trusted_keys().get(seal.get("key", ""))
    if raw is None:
        return False, f"the seal's key {seal.get('key')} is not trusted here (fieldkit mandate trust)"
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(bytes.fromhex(seal["sig"]),
                                                       _payload(seal["sha256"], m, seal["approved_at"], seal["key"]))
    except (InvalidSignature, ValueError, KeyError):
        return False, "the seal's signature is not valid"
    try:
        ends = date.fromisoformat(str(m["expires"]))
    except ValueError:
        return False, f"expires is not a date: {m['expires']}"
    if (today or date.today()) > ends:
        return False, f"expired on {ends}"
    return True, f"in force until {ends} (approved {seal['approved_at']}, key {seal['key']})"


def allows(path, action, today=None, repo_path=None, commit=None, provers=None, **p):
    """-> ("allow" | "ask" | "deny", why). Only a verified mandate allows anything.

    push and merge_main find their facts themselves (proofs.py): the repository and branch from git at `repo_path`,
    the fast-forward from its refs, every `when:` condition of a merge by running its prover on `commit`. Without a
    repo_path they answer "ask": that is the preview, it opens nothing. `provers` replaces proofs.py in tests."""
    from . import proofs
    pv = provers or proofs
    ok, why = verify(path, today)
    if not ok:
        return "ask", f"no mandate in force ({why}): the owner is asked"
    m = load(path)
    never = set(m.get("never") or [])
    if action == "push":
        if str(p.get("force", "")).lower() in ("1", "true", "yes"):
            return ("deny", "never: force_push") if "force_push" in never else \
                ("ask", "a force push is not something a mandate grants")
        if repo_path is None:
            return "ask", "preview only: a push is allowed by the tool that makes it (fieldkit ship push), which " \
                          "reads the repository, branch and fast-forward itself"
        repo, branch = pv.where(repo_path)
        r = repo_cfg(m, repo)
        if r is None:
            return "ask", f"the repository {repo} is not in the mandate"
        ok, why = branch_ok(r, branch)
        if not ok:
            return "ask", why
        ff, ev = pv.fast_forward(repo_path, branch)
        if not ff:
            return "ask", ev
        return "allow", f"repos.{repo}.push matches {branch}; {ev}"
    if action in ("tag", "release_tag"):
        # tag: a NEW tag matching repos.X.tags on a commit GitHub already has (fieldkit ship push --tag);
        # release_tag: also release: from_tag and CI green on the tag's commit (fieldkit ship gh-release)
        tag = str(p.get("tag") or "")
        if repo_path is None or not tag:
            return "ask", "preview only: fieldkit ship push --tag / ship gh-release find the tag's facts themselves"
        repo, _ = pv.where(repo_path)
        r = repo_cfg(m, repo)
        if r is None:
            return "ask", f"the repository {repo} is not in the mandate"
        ok, why = tag_ok(r, tag)
        if not ok:
            return "ask", why
        if action == "tag":
            ok, ev = pv.tag_new_on_pushed_commit(repo_path, tag)
            return ("allow", f"repos.{repo}.tags matches {tag}; {ev}") if ok else ("ask", ev)
        if r.get("release") != "from_tag":
            return "ask", f"repos.{repo}.release is not from_tag: publishing a release is the owner's"
        ok, ev = pv.tag_on_origin(repo_path, tag)
        if not ok:
            return "ask", ev
        green, cev = pv.PROVERS["ci_green"](repo_path, ev.split()[-1], r)
        if not green:
            return "ask", f"{tag}: {cev}"
        return "allow", f"repos.{repo}.release from_tag {tag}: {cev}"
    if action == "merge_main":
        if repo_path is None or not commit:
            return "ask", "preview only: a merge is allowed by fieldkit ship merge, which proves every condition itself"
        repo, _ = pv.where(repo_path)
        r = repo_cfg(m, repo)
        mm = (r or {}).get("merge_to_main")
        if not mm:
            return "ask", f"merging into {repo} main is not in the mandate"
        missing, proven = [], []
        stop_on_concern = "worker_concern" in (m.get("ask_when") or [])
        for w in mm.get("when", []):
            base_, _, arg = str(w).partition(":")             # tests_on:windows runs on a worker of that system
            fn = pv.PROVERS.get(base_)
            ok, ev = (fn(repo_path, commit, r, os_name=arg) if arg else fn(repo_path, commit, r)) if fn else \
                (False, f"no tool proves '{w}'")
            if ok and stop_on_concern and "CONCERNS:" in ev:  # the orchestrator's mandate decides, never the worker
                ok, ev = False, f"ask_when worker_concern: {ev}"
            (proven if ok else missing).append(f"{w}: {ev}")
        if missing:
            return "ask", "not proven: " + "; ".join(missing)
        return "allow", f"repos.{repo}.merge_to_main for {commit[:10]}: " + "; ".join(proven)
    if action == "install":
        if KERNEL_BOOT.search(f"{p.get('what', '')} {p.get('cmd', '')}"):
            return "ask", "installing or booting a kernel on this machine is never inside a mandate: the owner's yes"
        budget = float((m.get("install") or {}).get("budget_gb", 0))
        used = sum(float(e.get("gb", 0)) for e in read_log(path) if e.get("what") == "install")
        want = float(p.get("gb", 0))
        if used + want <= budget:
            return "allow", f"install.budget_gb {budget}: {used:g} used, {want:g} wanted"
        return "ask", f"the install budget ({budget} GB) would be passed: {used:g} used, {want:g} wanted"
    if action == "mac":
        names = (m.get("mac") or {}).values() if isinstance(m.get("mac"), dict) else []
        return ("allow", f"mac access to {p.get('name')}") if p.get("name") in names else \
            ("ask", f"the Mac {p.get('name')} is not in the mandate")
    if action == "approval":
        # p["what"]: the command as the session typed it; granted when an owner_approvals entry appears in it whole
        what = " ".join(str(p.get("what", "")).split())
        if NEVER_GRANTED.search(what):
            return "ask", "the mandate's own key and the owner's signature are never granted by a mandate"
        for a in m.get("owner_approvals") or []:
            if re.search(r"(^|\s)" + re.escape(" ".join(str(a).split())) + r"(\s|$)", what):
                return "allow", f"owner_approvals: {a}"
        return "ask", "this owner approval is not in the mandate's owner_approvals"
    if action == "release":
        # the release door (2026-10-23): release: {cycle: [project, ...]} lets sessions run the whole release cycle
        # (fieldkit release-cycle) of the projects it names, and nothing else
        # (the Windows review, point 4) the owner writes the exact versions: release: {cycle: {project: [vX.Y.Z]}}
        rel = m.get("release", "ask")
        if isinstance(rel, dict):
            proj, ver = p.get("project"), p.get("version")
            cyc = rel.get("cycle") if isinstance(rel.get("cycle"), dict) else {}
            if proj in cyc and ver and ver in [str(v) for v in (cyc.get(proj) or [])]:
                return "allow", f"release.cycle.{proj} lists {ver}"
            return "ask", f"release.cycle does not list {proj} {ver or '(no version)'}: the owner writes the version"
        return "ask", "release: the owner's (only release.cycle with exact versions opens it)"
    return "ask", f"'{action}' is not something a mandate grants ({', '.join(ACTIONS)})"


def share(path, put=None):
    """Send the mandate, its seal and the public keys to the cable peer -> (all sent, ["name: ok | error"])."""
    if put is None:
        from ..peers import link
        put = link.put
    sent, ok = [], True
    for f in (Path(path), _seal_path(path), _key_paths()[1], _review_paths()[1]):
        if f.is_file():
            res = put(f)
            ok = ok and bool(res.get("ok"))
            sent.append(f"{f.name}: {'ok' if res.get('ok') else res.get('error')}")
    return ok and bool(sent), sent


def mandates():
    d = home() / "mandates"
    return sorted(d.glob("*.yaml")) if d.is_dir() else []


def in_force(today=None):
    return [p for p in mandates() if verify(p, today)[0]]


def decide(action, today=None, **kw):
    """Across every mandate here -> (verdict, why, path or None): a deny wins, then the first allow, else ask."""
    asks, allow = [], None
    for p in mandates():
        v, why = allows(p, action, today, **kw)
        if v == "deny":
            return "deny", f"{p.stem}: {why}", p
        if v == "allow" and allow is None:
            allow = ("allow", f"{p.stem}: {why}", p)
        elif v == "ask":
            asks.append(f"{p.stem}: {why}")
    return allow or ("ask", "; ".join(asks) or "no mandate on this computer: the owner is asked", None)


def _disk_used():
    seen, used = set(), 0
    for root in (Path(os.path.abspath(os.sep)), Path.home()):
        try:
            u = shutil.disk_usage(root)
        except OSError:
            continue
        if (u.total, u.free) not in seen:
            seen.add((u.total, u.free))
            used += u.used
    return used


def install(gb, what, cmd, run=subprocess.run, used=_disk_used):
    """An install inside a mandate's budget -> (code, message). Logs the larger of what was declared and what the
    disk measured, so a low declaration does not stretch the budget."""
    v, why, p = decide("install", gb=gb, what=what, cmd=" ".join(cmd))
    if v != "allow":
        return 3, f"NOT INSIDE THE MANDATE ({why}); nothing installed"
    before = used()
    rc = run(cmd).returncode
    grew = max(0.0, (used() - before) / 1e9)
    spent = max(float(gb), grew)
    log(p, "install", what, gb=round(spent, 3), declared=float(gb), measured=round(grew, 3), rc=rc,
        cmd=" ".join(cmd)[:300])
    return (0 if rc == 0 else 1), f"{'INSTALLED' if rc == 0 else 'FAILED'} {what}: {spent:.2f} GB counted ({why})"


def stop(path):
    """The .STOP beside the file at once (anyone may stop; it only closes), then the permanent entry in the guarded
    stop list, as the owner -> (stop file, guarded ok, message)."""
    from . import guarded
    stop_path(path).write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n", encoding="utf-8")
    name = re.sub(r"[^\w.-]", "_", Path(path).name)[:80]
    ok, msg = guarded.write("stopped.txt", f"{_sha(path)} {name} {time.strftime('%Y-%m-%d_%H:%M:%S')}", append=True)
    log(path, "stopped", f"by the owner; guarded list: {msg}")
    return stop_path(path), ok, msg


def _log_path(path):
    return Path(path).with_suffix(".log")


def log(path, what, detail, **extra):
    with open(_log_path(path), "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), what=what, detail=detail, **extra)) + "\n")


def read_log(path):
    p = _log_path(path)
    out = []
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ---- command line -----------------------------------------------------------------------------------------------------
def _typed_yes(prompt, isatty=None):
    if not (isatty if isatty is not None else sys.stdin.isatty()):
        return False
    return input(prompt).strip().lower() == "yes"


def _job_cli(r, argv):
    from . import jobs
    args = list(argv if argv is not None else sys.argv[1:])
    sub = r[0] if r else ""
    if sub == "status":
        print("\n".join(jobs.lines()))
        return 0
    if sub == "wait" and len(r) >= 2:
        secs = int(args[args.index("--seconds") + 1]) if "--seconds" in args else 3600
        e = jobs.wait(r[1], secs)
        print(f"{(e or {}).get('status', 'no such job')}: {(e or {}).get('evidence', '')}")
        for c in (e or {}).get("concerns") or []:
            print(f"  CONCERN: {c.get('text')}  FIX OFFERED: {c.get('fix_offered')}")
        return 0 if e and e.get("status") not in (None, "sent") else 3
    if sub == "send" and len(r) >= 3:
        from . import proofs
        kind = args[args.index("--kind") + 1] if "--kind" in args else "test"
        repo = Path(r[1])
        name = proofs.where(repo)[0] if repo.is_dir() else r[1]
        mp = jobs.mandate_for(name)
        if mp is None:
            print(f"REFUSED: no mandate in force here names {name}: the orchestrator works under the owner's mandate")
            return 3
        jid, what = jobs.send(kind, name, r[2], mp)
        print(f"{what.upper() if what == 'sent' else what}: {jid}")
        return 0 if jid and not what.startswith("not sent") else 3
    if sub == "run" and len(r) >= 5:                      # the worker's detached process (handle_job starts it)
        lock = Path(args[args.index("--lock") + 1]) if "--lock" in args else None
        try:
            res = jobs.run_job(r[1], Path(r[2]), r[3], Path(r[4]))
            print(f"RESULT {res['status']}: {res['evidence'][:200]}")
            return 0
        finally:
            if lock is not None and lock.is_file():
                lock.unlink()                             # one job at a time: the next may start
    print("job send REPO SHA [--kind test] | job status | job wait ID [--seconds S]")
    return 2


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit mandate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["keygen", "trust", "trust-reviewer", "approve", "verify", "allows", "stop",
                                       "share", "status", "install", "review-key", "verdict", "job", "boss",
                                       "ledger", "resume", "tasks", "start", "report", "ask", "quiet", "gate",
                                       "doctor", "install-hooks", "renew", "prereqs"])
    ap.add_argument("rest", nargs="*")
    # the flags after the command (--kind, --seconds, --evidence, --since, --lock, --send) are read from argv by each
    # command: parse_args refused them all, and the worker's own job process (job run ... --lock) died at start
    # (found 2026-10-23 by the first real job)
    a, extra = ap.parse_known_args(argv)
    flag_values = [extra[i + 1] for i, x in enumerate(extra[:-1]) if x.startswith("--") and not extra[i + 1].startswith("-")]
    r = list(a.rest)
    for v in flag_values:                                 # a flag's value is never a positional
        if v in r:
            r.remove(v)
    if a.action == "keygen":
        if not sys.stdin.isatty():
            print("REFUSED: the owner makes the key at a real terminal")
            return 3
        pw = getpass.getpass("Passphrase for the owner signing key (only you know it): ")
        if pw != getpass.getpass("Again: "):
            print("the two passphrases differ: nothing made")
            return 1
        try:
            pub, fp = keygen(pw)
        except (FileExistsError, ValueError) as e:
            print(f"REFUSED: {e}")
            return 2
        print(f"KEY MADE: {pub}  fingerprint {fp}")
        print("Pinning it in the guarded store (your password, once):")
        ok, msg = trust_raw(bytes.fromhex(pub.read_text(encoding="utf-8").strip()))
        print(("TRUSTED " if ok else "NOT TRUSTED YET ") + msg)
        print(f"The other computer: fieldkit mandate trust {pub.name} (after share)")
        return 0 if ok else 3
    if a.action in ("trust", "trust-reviewer"):
        if not r:
            print(f"{a.action} PUBFILE")
            return 2
        raw = bytes.fromhex(Path(r[0]).read_text(encoding="utf-8").strip())
        what = "owner signing key" if a.action == "trust" else "the OTHER computer's review key"
        print(f"{what}, fingerprint {fingerprint(raw)}: compare it with the screen of the computer that made it")
        if a.action == "trust-reviewer" and len(raw) == 32 and fingerprint(raw) == review_key()[1]:
            print("REFUSED: that is THIS computer's own review key; a computer never approves its own work")
            return 2
        if not _typed_yes(f"Pin this {what} on this computer? Type yes: "):
            print("not trusted")
            return 1
        ok, msg = trust_raw(raw, "trusted" if a.action == "trust" else "reviewers")
        print(("TRUSTED " if ok else "NOT TRUSTED ") + msg)
        return 0 if ok else 3
    if a.action == "review-key":
        _k, fp = review_key()
        print(f"REVIEW KEY {fp}: {_review_paths()[1]}. The other computer pins it: fieldkit mandate trust-reviewer "
              f"{_review_paths()[1].name} (the owner, once, after it is carried over)")
        return 0
    # status and install were cut out by mistake on 2026-10-23 (a block replacement that ran to the next marker);
    # restored the same evening, with tests that call them
    if a.action == "status":
        d = home() / "mandates"
        for p in sorted(d.glob("*.yaml")) if d.is_dir() else []:
            ok, why = verify(p)
            print(f"  {'IN FORCE' if ok else 'not in force':12} {p.stem}: {why}")
        from . import upkeep
        w = upkeep.warning()
        if w:
            print("  " + w)
        return 0
    if a.action == "prereqs":                                # machine.py: declared, approved once, applied at a start
        from . import machine
        return machine.main(r, list(argv if argv is not None else sys.argv[1:]))
    if a.action in ("doctor", "install-hooks", "renew"):     # upkeep.py (2026-10-26: made permanent)
        from . import upkeep
        return upkeep.main(a.action, r, list(argv if argv is not None else sys.argv[1:])[1:])
    if a.action == "install":
        if "--" not in (argv if argv is not None else sys.argv):
            print("install GB WHAT -- COMMAND ...")
            return 2
        argv_all = list(argv if argv is not None else sys.argv[1:])
        cmd = argv_all[argv_all.index("--") + 1:]
        try:
            gb = float(r[0])
        except (IndexError, ValueError):
            print("install GB WHAT -- COMMAND ...  (GB: what it will take, a number)")
            return 2
        if len(r) < 2 or not cmd:
            print("install GB WHAT -- COMMAND ...")
            return 2
        code, msg = install(gb, r[1], cmd)
        print(msg)
        return code
    if a.action == "verdict":
        # the worker's test run, by hand: printed here, nothing sent, nothing signed
        if len(r) < 2:
            print("verdict REPO SHA  (REPO: the repository's name or its folder; SHA: 40 characters)")
            return 2
        from . import proofs, verdict as V
        repo = Path(r[0]) if Path(r[0]).is_dir() else V.find_repo(r[0])
        if repo is None:
            print(f"REFUSED: {r[0]} is not a folder nor one of this computer's repositories (sync.repos)")
            return 2
        res = V.verdict(repo, r[1], name=proofs.where(repo)[0], branches=["*"])
        print(f"{res['status'].upper()} {r[1]}: {res['evidence']}")
        for c in res["concerns"]:
            print(f"  CONCERN: {c['text']}  FIX OFFERED: {c['fix_offered']}")
        return 0 if res["status"] == "pass" else 3
    if a.action == "job":
        return _job_cli(r, argv)
    if a.action == "boss":
        from . import boss
        devs = boss.devices()
        print("\n".join(boss.lines(devs, boss.propose(devs))))
        return 0
    if a.action == "ledger":
        from . import jobs
        since = int(r[0]) if r and r[0].isdigit() else 0
        for e in jobs.events(since):
            print(f"  #{e['n']} {e['at']} {e['what']}: " + ", ".join(f"{k}={v}" for k, v in e.items()
                                                                  if k not in ("n", "at", "what")))
        return 0
    if a.action == "gate":                                # a repository's pre-push hook (gate.py)
        from . import gate
        return gate.main(list(argv if argv is not None else sys.argv[1:])[1:])
    if a.action == "quiet":                               # the owner is not asked while a mandate is in force
        from . import quiet
        return quiet.main(list(argv if argv is not None else sys.argv[1:]))
    if a.action == "ask":
        from . import quiet
        args = list(argv if argv is not None else sys.argv[1:])
        why = args[args.index("--why") + 1] if "--why" in args else ""
        if not r:
            print('ask "the question" [--why "..."]')
            return 2
        n = quiet.queue(" ".join(r), why)
        print(f"QUEUED for the owner's morning report (ledger #{n}); carry on with what does not depend on it")
        return 0
    if a.action in ("tasks", "start", "report"):
        from . import project
        args = list(argv if argv is not None else sys.argv[1:])
        opt = lambda flag: args[args.index(flag) + 1] if flag in args else None
        if a.action == "start":
            lines_, _ = project.start()
            print("\n".join(lines_))
            return 0
        if a.action == "report":
            print("\n".join(project.report(int(opt("--since") or 0))))
            return 0
        sub = r[0] if r else "list"
        if sub == "init" and len(r) >= 2:
            added, total = project.init(r[1])
            print(f"TASKS: {len(added)} written, all failing; {total} in the list")
            return 0
        if sub == "pass" and len(r) >= 2:
            ok, why = project.mark_pass(r[1], opt("--evidence"), opt("--screenshot"))
            print(("PASSING " if ok else "REFUSED: ") + why)
            return 0 if ok else 3
        print("\n".join(project.task_lines()))
        return 0
    if a.action == "resume":
        from . import jobs
        res = jobs.resume()
        print(f"OPEN JOBS: {', '.join(res['open']) or 'none'}; last event: #{res['last'].get('n', 0)} "
              f"{res['last'].get('what', '')}" + (f"; worker: {res['asked']}" if res["asked"] else ""))
        return 0
    if not r:
        print(f"{a.action} FILE.yaml ...")
        return 2
    path = Path(r[0])
    if a.action == "approve":
        try:
            m = load(path)
        except (OSError, ValueError) as e:
            print(f"REFUSED: {e}")
            return 2
        print(f"MANDATE {m['project']}: {m['goal']}".strip())
        print("It opens these doors, for every session on every computer that trusts your key:")
        for line in doors(m):
            print(f"  - {line}")
        print("NOT COVERED: these stay yours, whatever the mandate says (and what enforces each):")
        for line in not_covered():
            print(f"  - {line}")
        print("THREAT MODEL:")
        for line in threat_model(path):
            print(f"  - {line}")
        if not _typed_yes("Approve? Type yes: "):
            print("REFUSED: approving needs the owner typing yes at a real terminal" if not sys.stdin.isatty()
                  else "not approved")
            return 3
        code, msg = approve(path, getpass.getpass("Owner key passphrase: "), True)
        print(msg)
        return code
    if a.action == "verify":
        ok, why = verify(path)
        print(("IN FORCE  " if ok else "NOT IN FORCE  ") + why)
        return 0 if ok else 3
    if a.action == "allows":
        if len(r) < 2:
            print("allows FILE.yaml ACTION [key=value ...]")
            return 2
        params = dict(kv.split("=", 1) for kv in r[2:] if "=" in kv)
        from . import proofs
        given = sorted(set(params) & (set(proofs.PROVERS) | {"repo_path", "commit", "provers", "today"}))
        if given:
            print(f"REFUSED: {', '.join(given)} cannot be given; the tool that acts proves them itself")
            return 2
        verdict, why = allows(path, r[1], **params)
        # a preview: it gates nothing (the review's point 4); ship push / ship merge decide with their own proofs
        print(f"PREVIEW {verdict.upper()}  {why}")
        return 0
    if a.action == "stop":
        sp, ok, msg = stop(path)
        print(f"STOPPED: {sp}" + ("" if ok else f"; the permanent entry is NOT written yet ({msg})"))
        return 0 if ok else 3
    if a.action == "share":
        ok, sent = share(path)
        print("SHARED  " + "; ".join(sent))
        return 0
    return 2
