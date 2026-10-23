# Version: 1.0.0 · updated 26-10-23-04-20
"""A project mandate: the owner's ONE approval for a whole project, valid on both computers, checkable by each.

    fieldkit mandate keygen                       the owner's signing key, once per owner (passphrase-protected)
    fieldkit mandate trust PUBFILE                the other computer pins the owner's PUBLIC key (once, typed yes)
    fieldkit mandate approve FILE.yaml            shows what the mandate allows (every door it opens), typed yes and
                                                  the passphrase: writes FILE.seal (Ed25519 over its sha256)
    fieldkit mandate verify FILE.yaml             seal, hash, trusted key, expiry, stop: in force or why not
    fieldkit mandate allows FILE.yaml ACTION [k=v ...]   allow | ask | deny, and the clause that decided
    fieldkit mandate stop FILE.yaml               the owner's stop: nothing is allowed by it any more
    fieldkit mandate share FILE.yaml              the mandate, its seal and the public key to the other computer
    fieldkit mandate status                       every mandate here and whether it is in force

Owner, 2026-10-23, after a day as the router between 16 sessions on two computers: "aprobarile se dau o singura data
pentru un proiect si ar trebui sa fie valabile si pentru cealalta sesiune ... doua sesiuni lucrand la acelasi proiect
sub acelasi mandat using the same SOPs." Plan: local/PROJECT-MANDATE-PLAN.md.

Why a signing key and not the cable's key (the Windows side's review, 2026-10-23): anything that can read link.key
could mint a seal; a seal must prove THE OWNER'S TERMINAL. The private key is encrypted with a passphrase only the
owner knows (a model can fake a terminal, not the passphrase); the other computer holds only the public key, so it
can verify and never sign. Fail closed: no seal, a changed byte, an untrusted key, an expired or stopped mandate,
an unreadable file -> nothing is allowed by the mandate ("ask": the old behaviour, the owner is asked).
"""
import fnmatch
import getpass
import hashlib
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import yaml

REQUIRED = ("project", "goal", "expires", "repos", "never")
ACTIONS = ("push", "merge_main", "install", "mac", "release")


def home():
    return Path(os.environ.get("FIELDKIT_MANDATE_HOME") or (Path.home() / ".fieldkit" / "mandate"))


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
    if len(passphrase) < 8:
        raise ValueError("the passphrase needs at least 8 characters")
    k = Ed25519PrivateKey.generate()
    priv_p.parent.mkdir(parents=True, exist_ok=True)
    priv_p.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.BestAvailableEncryption(passphrase.encode())))
    os.chmod(priv_p, 0o600)
    raw = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    pub_p.write_text(raw.hex() + "\n", encoding="utf-8")
    trust_raw(raw)                                      # this computer trusts its own owner key
    return pub_p, fingerprint(raw)


def trusted_dir():
    return home() / "trusted"


def trust_raw(raw: bytes):
    d = trusted_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{fingerprint(raw)}.pub").write_text(raw.hex() + "\n", encoding="utf-8")
    return fingerprint(raw)


def trusted_keys():
    d = trusted_dir()
    out = {}
    for p in d.glob("*.pub") if d.is_dir() else []:
        try:
            out[p.stem] = bytes.fromhex(p.read_text(encoding="utf-8").strip())
        except ValueError:
            continue
    return out


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
    for repo, r in (m.get("repos") or {}).items():
        if r.get("push"):
            out.append(f"sessions may PUSH, fast-forward only, these branches of {repo}: {', '.join(r['push'])}")
        mm = r.get("merge_to_main")
        if mm:
            out.append(f"sessions may MERGE into {repo} main ({mm.get('how', 'fast-forward')}) when: "
                       f"{', '.join(mm.get('when', []))}")
    if m.get("install"):
        out.append(f"sessions may INSTALL software up to {m['install'].get('budget_gb', 0)} GB, each logged")
    for name in (m.get("mac") or {}).values() if isinstance(m.get("mac"), dict) else []:
        out.append(f"sessions have full read/write access to the Mac '{name}'")
    out.append("release (tags, published assets): " + str(m.get("release", "ask")))
    out.append("never: " + ", ".join(m.get("never") or []))
    out.append(f"ends: {m['expires']}")
    return out


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


def verify(path, today=None):
    """-> (in_force: bool, why). Fail closed on every doubt."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        m = load(path)
    except (OSError, ValueError, yaml.YAMLError) as e:
        return False, f"unreadable mandate: {e}"
    if stop_path(path).exists():
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


def allows(path, action, today=None, **p):
    """-> ("allow" | "ask" | "deny", why). Only a verified mandate allows anything."""
    ok, why = verify(path, today)
    if not ok:
        return "ask", f"no mandate in force ({why}): the owner is asked"
    m = load(path)
    never = set(m.get("never") or [])
    if action == "push":
        if str(p.get("force", "")).lower() in ("1", "true", "yes"):
            return ("deny", "never: force_push") if "force_push" in never else \
                ("ask", "a force push is not something a mandate grants")
        r = (m.get("repos") or {}).get(p.get("repo", ""))
        if not r:
            return "ask", f"the repository {p.get('repo')} is not in the mandate"
        if any(fnmatch.fnmatch(p.get("branch", ""), pat) for pat in r.get("push") or []):
            return "allow", f"repos.{p['repo']}.push matches {p.get('branch')}"
        return "ask", f"{p.get('branch')} is not a branch the mandate lets sessions push"
    if action == "merge_main":
        r = (m.get("repos") or {}).get(p.get("repo", ""))
        mm = (r or {}).get("merge_to_main")
        if not mm:
            return "ask", f"merging into {p.get('repo')} main is not in the mandate"
        missing = [w for w in mm.get("when", []) if not p.get(w)]
        # the other machine's review must name the exact commit it tested (the Windows side's review point 5)
        if "other_machine_reviewed" in mm.get("when", []) and p.get("other_machine_reviewed") != p.get("commit"):
            missing.append("other_machine_reviewed (the reviewer's note must name this commit sha)")
        if missing:
            return "ask", "not yet proven: " + ", ".join(dict.fromkeys(missing))
        return "allow", f"repos.{p['repo']}.merge_to_main: every condition proven for {p.get('commit')}"
    if action == "install":
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
    if action == "release":
        return ("allow", "release: allow") if str(m.get("release", "ask")) == "allow" else ("ask", "release: ask")
    return "ask", f"'{action}' is not something a mandate grants ({', '.join(ACTIONS)})"


def stop(path):
    stop_path(path).write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n", encoding="utf-8")
    log(path, "stopped", "by the owner")
    return stop_path(path)


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


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit mandate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["keygen", "trust", "approve", "verify", "allows", "stop", "share", "status"])
    ap.add_argument("rest", nargs="*")
    a = ap.parse_args(argv)
    r = a.rest
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
        print(f"KEY MADE: {pub}  fingerprint {fp}. The other computer: fieldkit mandate trust {pub.name} (after share)")
        return 0
    if a.action == "trust":
        if not r:
            print("trust PUBFILE")
            return 2
        raw = bytes.fromhex(Path(r[0]).read_text(encoding="utf-8").strip())
        print(f"owner key fingerprint {fingerprint(raw)}: compare it with the screen of the computer that made it")
        if not _typed_yes("Trust this key for mandates on this computer? Type yes: "):
            print("not trusted")
            return 1
        print(f"TRUSTED {trust_raw(raw)}")
        return 0
    if a.action == "status":
        d = home() / "mandates"
        for p in sorted(d.glob("*.yaml")) if d.is_dir() else []:
            ok, why = verify(p)
            print(f"  {'IN FORCE' if ok else 'not in force':12} {p.stem}: {why}")
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
        verdict, why = allows(path, r[1], **params)
        print(f"{verdict.upper()}  {why}")
        return {"allow": 0, "ask": 3, "deny": 4}[verdict]
    if a.action == "stop":
        print(f"STOPPED: {stop(path)}")
        return 0
    if a.action == "share":
        from ..peers import link
        sent = []
        for f in (path, _seal_path(path), _key_paths()[1]):
            if f.is_file():
                res = link.put(f)
                sent.append(f"{f.name}: {'ok' if res.get('ok') else res.get('error')}")
        print("SHARED  " + "; ".join(sent))
        return 0
    return 2
