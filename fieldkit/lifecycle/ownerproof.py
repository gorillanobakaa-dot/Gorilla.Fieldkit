"""I-06: `--force` on an owner-only refusal, proven as the owner, so the owner can run it from anywhere (a real terminal,
SSH, the chat's `!` line) and a model still cannot (owner, 2026-11-11: "skip is for the owner at a real terminal"
refused the owner's own `!` command).

The proof is the owner's signing key (fieldkit mandate keygen): its private key is encrypted with a passphrase only
the owner knows; a model can fake a terminal, not the passphrase. The passphrase is asked where a model cannot read
or answer it, and never travels through argv, the environment, a file or stdout:

  1. the controlling terminal (/dev/tty, or the Windows console) when there is one;
  2. else a desktop dialog (zenity / kdialog on Linux, a PowerShell credential box on Windows, osascript on macOS)
     that shows the exact action being forced.

The key must unlock with it AND its public key must be pinned as trusted (mandate.trusted_keys, a store this OS user
cannot write). The action text is signed; the signature and the key fingerprint go into the caller's journal.
Fail closed: no key, no way to ask, a wrong passphrase, an untrusted key -> refused.

    prove(action) -> {"ok", "key", "sig", "at", "why"}
    owner_or_proof(action, force, terminal_why) -> (ok, how): a real terminal as before, or --force with the proof
"""
import getpass
import os
import shutil
import subprocess
import sys
import time


def _tty_passphrase(prompt):
    if sys.platform == "win32":
        try:
            import msvcrt  # noqa: F401  - a console exists
            if not sys.stdin or not sys.stdin.isatty():
                return None
            return getpass.getpass(prompt)
        except Exception:                                    # noqa: BLE001
            return None
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None
    os.close(fd)
    try:
        return getpass.getpass(prompt)                      # getpass reads /dev/tty itself, echo off
    except (EOFError, OSError, getpass.GetPassWarning):
        return None


def _dialog_passphrase(action):
    text = f"Fieldkit: the owner forces an owner-only action.\n\n{action[:300]}\n\nOwner signing-key passphrase:"
    if sys.platform.startswith("linux") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        for cmd in (["zenity", "--password", "--title", "Fieldkit owner proof"],
                    ["kdialog", "--password", text, "--title", "Fieldkit owner proof"]):
            if shutil.which(cmd[0]):
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                return r.stdout.rstrip("\n") if r.returncode == 0 and r.stdout.strip() else None
    if sys.platform == "win32" and shutil.which("powershell"):
        ps = ("$c = Get-Credential -UserName 'owner' -Message " + "'" + text.replace("'", "''")[:900] + "'; "
              "if ($c) { [Runtime.InteropServices.Marshal]::PtrToStringBSTR("
              "[Runtime.InteropServices.Marshal]::SecureStringToBSTR($c.Password)) }")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=300)
        return r.stdout.rstrip("\r\n") if r.returncode == 0 and r.stdout.strip() else None
    if sys.platform == "darwin" and shutil.which("osascript"):
        r = subprocess.run(["osascript", "-e", f'text returned of (display dialog "{text[:300]!s}" default answer "" '
                            'with hidden answer)'], capture_output=True, text=True, timeout=300)
        return r.stdout.rstrip("\n") if r.returncode == 0 and r.stdout.strip() else None
    return None


def ask_passphrase(action):
    """-> the passphrase, or None when there is no terminal and no desktop to ask on. FIELDKIT_OWNERPROOF_ASK=off
    (the test suite sets it) asks nobody: no dialog ever opens on the owner's desktop from a test or a mutation run
    (Zero order M-1d6f4489, 2026-11-11). Setting it can only refuse, never prove."""
    if os.environ.get("FIELDKIT_OWNERPROOF_ASK", "").lower() == "off":
        return None
    return _tty_passphrase(f"Owner signing-key passphrase to force: {action[:120]}\n> ") or _dialog_passphrase(action)


def prove(action, ask=None, key_paths=None, trusted=None):
    """-> {"ok", "key", "sig", "at", "why"}. ask(action) -> passphrase (tests); key_paths() -> (priv, pub) and
    trusted() -> {fingerprint: raw} default to the mandate's owner key and guarded trust store."""
    from ..mandate import mandate as M
    out = {"ok": False, "key": None, "sig": None, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "why": ""}
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        return dict(out, why="the cryptography package is missing: the owner cannot be proven (fail closed)")
    priv_p, _pub = (key_paths or M._key_paths)()
    if not priv_p.exists():
        return dict(out, why="no owner signing key on this computer: fieldkit mandate keygen (once, at a terminal)")
    pw = (ask or ask_passphrase)(action)
    if not pw:
        return dict(out, why="no terminal and no desktop dialog to ask the owner's passphrase on: refused")
    try:
        k = serialization.load_pem_private_key(priv_p.read_bytes(), password=pw.encode())
    except (ValueError, TypeError):
        return dict(out, why="wrong passphrase: refused")
    raw = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    fp = M.fingerprint(raw)
    try:
        pinned = (trusted or M.trusted_keys)()
    except Exception:                                        # noqa: BLE001 - an unreadable store trusts nothing
        pinned = {}
    if pinned.get(fp) != raw:
        return dict(out, key=fp, why=f"the key {fp} is not pinned as the owner's (fieldkit mandate trust): refused")
    sig = k.sign(f"fieldkit-force\n{out['at']}\n{action}".encode("utf-8")).hex()
    return dict(out, ok=True, key=fp, sig=sig, why=f"proven by the owner's key {fp}")


def owner_or_proof(action, force=False, terminal_why=None, ask=None, **kw):
    """-> (ok, how). terminal_why: "" when this process is a person's terminal (the old rule, unchanged), else the reason
    not. Without a terminal, --force asks for the owner's proof; a model has neither."""
    if terminal_why is None:
        from ..buildh import task as _task
        terminal_why = _task.terminal_refusal()
    if not terminal_why:
        return True, "the owner at a real terminal"
    if not force:
        return False, f"{terminal_why}; the owner can add --force and prove it with the signing-key passphrase"
    p = prove(action, ask=ask, **kw)
    return (True, p["why"] + f" (sig {p['sig'][:16]})") if p["ok"] else (False, f"--force refused: {p['why']}")
