"""I-06: --force on an owner-only refusal works only with the owner's signing-key passphrase and a pinned key."""
import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from fieldkit.lifecycle import ownerproof  # noqa: E402
from fieldkit.mandate import mandate as M  # noqa: E402


@pytest.fixture
def key(tmp_path):
    k = Ed25519PrivateKey.generate()
    priv = tmp_path / "owner.key"
    priv.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.BestAvailableEncryption(b"right horse")))
    raw = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"paths": lambda: (priv, tmp_path / "owner.pub"), "raw": raw, "fp": M.fingerprint(raw)}


def test_the_right_passphrase_and_a_pinned_key_prove_the_owner(key):
    p = ownerproof.prove("skip x", ask=lambda a: "right horse", key_paths=key["paths"],
                         trusted=lambda: {key["fp"]: key["raw"]})
    assert p["ok"] and p["key"] == key["fp"] and p["sig"]


def test_a_wrong_passphrase_is_refused(key):
    p = ownerproof.prove("skip x", ask=lambda a: "guess", key_paths=key["paths"], trusted=lambda: {key["fp"]: key["raw"]})
    assert not p["ok"] and "wrong passphrase" in p["why"]


def test_a_key_not_pinned_as_the_owners_is_refused(key):
    p = ownerproof.prove("skip x", ask=lambda a: "right horse", key_paths=key["paths"], trusted=lambda: {})
    assert not p["ok"] and "not pinned" in p["why"]


def test_no_way_to_ask_is_refused(key):
    p = ownerproof.prove("skip x", ask=lambda a: None, key_paths=key["paths"], trusted=lambda: {key["fp"]: key["raw"]})
    assert not p["ok"] and "no terminal" in p["why"]


def test_no_key_is_refused(tmp_path):
    p = ownerproof.prove("x", ask=lambda a: "y", key_paths=lambda: (tmp_path / "none", tmp_path / "none.pub"))
    assert not p["ok"] and "no owner signing key" in p["why"]


def test_owner_or_proof_terminal_as_before_force_needs_the_proof(key):
    assert ownerproof.owner_or_proof("a", False, "")[0] is True
    ok, why = ownerproof.owner_or_proof("a", False, "an agent's shell")
    assert not ok and "--force" in why
    ok, why = ownerproof.owner_or_proof("a", True, "an agent's shell", ask=lambda a: "guess", key_paths=key["paths"],
                                        trusted=lambda: {key["fp"]: key["raw"]})
    assert not ok and "wrong passphrase" in why
    ok, why = ownerproof.owner_or_proof("a", True, "an agent's shell", ask=lambda a: "right horse",
                                        key_paths=key["paths"], trusted=lambda: {key["fp"]: key["raw"]})
    assert ok and "proven" in why


def test_under_tests_no_terminal_prompt_or_dialog_is_ever_opened(key, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a real passphrase prompt or dialog was opened")
    monkeypatch.setattr(ownerproof, "_tty_passphrase", boom)
    monkeypatch.setattr(ownerproof, "_dialog_passphrase", boom)
    monkeypatch.setattr(ownerproof.subprocess, "run", boom)
    monkeypatch.setattr(ownerproof.getpass, "getpass", boom)
    p = ownerproof.prove("skip x", key_paths=key["paths"], trusted=lambda: {key["fp"]: key["raw"]})
    assert not p["ok"] and "no terminal" in p["why"]
    ok, why = ownerproof.owner_or_proof("skip x", True, "an agent's shell", key_paths=key["paths"],
                                        trusted=lambda: {key["fp"]: key["raw"]})
    assert not ok
