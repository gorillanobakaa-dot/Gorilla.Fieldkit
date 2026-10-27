# Version: 1.0.0 · updated 26-10-26-23-37
"""fieldkit mandate receive (2026-10-26, owner approved: "yeah"): a renewed mandate from the other computer is
installed ONLY when its seal verifies against the owner key already pinned here; anything else is refused; the old
pair goes to the trash, never deleted. `mandate share` asks the far listener to run it (link verb mandate-receive)."""
import json
from datetime import date

import pytest

from fieldkit.mandate import mandate as M
from test_mandate import MANDATE, _guarded_store

DAY = date(2026, 10, 23)


@pytest.fixture
def here(tmp_path, monkeypatch):
    """This computer: the owner key pinned, one mandate installed (release.yaml, approved)."""
    _guarded_store(monkeypatch, tmp_path / "guarded")
    monkeypatch.setattr(M, "home", lambda: tmp_path / "home")
    pub, _fp = M.keygen("correct horse battery")
    assert M.trust_raw(bytes.fromhex(pub.read_text().strip()))[0]
    f = tmp_path / "home" / "mandates" / "release.yaml"
    f.parent.mkdir(parents=True)
    f.write_text(MANDATE)
    assert M.approve(f, "correct horse battery", True)[0] == 0
    return f


def _received(tmp_path, text, approve=True, monkeypatch=None, n=3):
    """A pair as the link leaves it: ~/.cache/fieldkit-scratch/link-*/NAME-N.yaml and NAME-M.seal (two numbers)."""
    d = tmp_path / "scratch" / "link-abc"
    d.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "far" / "release.yaml"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text)
    if approve:
        assert M.approve(src, "correct horse battery", True)[0] == 0
    y, s = d / f"release-{n}.yaml", d / f"release-{n + 1}.seal"
    y.write_bytes(src.read_bytes())
    if approve:
        s.write_bytes(M._seal_path(src).read_bytes())
    return y, s


def _renewed():
    return MANDATE.replace("expires: 2026-10-30", "expires: 2026-11-06")


def test_a_valid_pair_is_installed_and_the_old_pair_goes_to_the_trash(here, tmp_path):
    old_yaml, old_seal = here.read_bytes(), M._seal_path(here).read_bytes()
    y, s = _received(tmp_path, _renewed())
    code, msg = M.receive(y, s, today=DAY, trash=tmp_path / "trash")
    assert code == 0 and msg.startswith("INSTALLED"), msg
    assert here.read_bytes() == y.read_bytes() and M._seal_path(here).read_bytes() == s.read_bytes()
    assert M.verify(here, DAY) == (True, M.verify(here, DAY)[1]) and "2026-11-06" in M.verify(here, DAY)[1]
    binned = list((tmp_path / "trash").glob("mandate-release-*"))
    assert len(binned) == 1
    assert (binned[0] / "release.yaml").read_bytes() == old_yaml
    assert (binned[0] / "release.seal").read_bytes() == old_seal
    assert M.read_log(here)[-1]["what"] == "received"


def test_a_new_project_is_installed_under_its_project_name(here, tmp_path):
    y, s = _received(tmp_path, MANDATE.replace("project: gorilla-opencode-mac-release", "project: other"))
    code, msg = M.receive(y, s, today=DAY, trash=tmp_path / "trash")
    assert code == 0, msg
    assert (M.home() / "mandates" / "other.yaml").is_file() and M.verify(M.home() / "mandates" / "other.yaml", DAY)[0]
    assert not (tmp_path / "trash").exists()


def test_a_changed_byte_is_refused_and_nothing_moves(here, tmp_path):
    before = here.read_bytes()
    y, s = _received(tmp_path, _renewed())
    y.write_text(y.read_text().replace("feat/*", "*"))
    code, msg = M.receive(y, s, today=DAY, trash=tmp_path / "trash")
    assert code == 3 and "changed after its approval" in msg
    assert here.read_bytes() == before and not (tmp_path / "trash").exists()


def test_a_bad_signature_is_refused(here, tmp_path):
    y, s = _received(tmp_path, _renewed())
    seal = json.loads(s.read_text())
    seal["sig"] = ("00" if seal["sig"][:2] != "00" else "11") + seal["sig"][2:]
    s.write_text(json.dumps(seal))
    code, msg = M.receive(y, s, today=DAY, trash=tmp_path / "trash")
    assert code == 3 and "signature is not valid" in msg
    assert "2026-10-30" in here.read_text()


def test_a_key_not_pinned_here_is_refused(here, tmp_path, monkeypatch):
    """The far side signed with a key this computer never pinned: refused, whatever the seal says."""
    y, s = _received(tmp_path, _renewed())
    monkeypatch.setattr(M, "trusted_keys", lambda: {})
    code, msg = M.receive(y, s, today=DAY, trash=tmp_path / "trash")
    assert code == 3 and "not trusted here" in msg
    assert "2026-10-30" in here.read_text() and not (tmp_path / "trash").exists()


def test_no_seal_or_the_wrong_kind_of_file_is_refused(here, tmp_path):
    y, s = _received(tmp_path, _renewed())
    assert M.receive(y, tmp_path / "missing.seal", today=DAY)[0] == 2
    assert M.receive(s, y, today=DAY)[0] == 2


def test_an_expired_pair_is_refused(here, tmp_path):
    y, s = _received(tmp_path, _renewed())
    code, msg = M.receive(y, s, today=date(2026, 12, 1), trash=tmp_path / "trash")
    assert code == 3 and "expired" in msg


def test_an_older_approval_never_replaces_a_newer_one(here, tmp_path, monkeypatch):
    y, s = _received(tmp_path, _renewed())
    monkeypatch.setattr(M, "_approved_at", lambda p: "2026-10-25 00:00:00" if str(p) == str(s) else "2026-10-26 00:00:00")
    code, msg = M.receive(y, s, today=DAY, trash=tmp_path / "trash")
    assert code == 3 and "older approval" in msg
    assert "2026-10-30" in here.read_text()


def test_the_same_pair_twice_is_already_installed(here, tmp_path):
    y, s = _received(tmp_path, _renewed())
    assert M.receive(y, s, today=DAY, trash=tmp_path / "trash")[0] == 0
    code, msg = M.receive(y, s, today=DAY, trash=tmp_path / "trash")
    assert code == 0 and msg.startswith("ALREADY INSTALLED")
    assert len(list((tmp_path / "trash").glob("mandate-release-*"))) == 1


def test_the_command_line_receive(here, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(M, "verify", lambda p, today=None, _v=M.verify: _v(p, DAY))
    monkeypatch.setattr(M.Path, "home", lambda: tmp_path)
    y, s = _received(tmp_path, _renewed())
    assert M.main(["receive", str(y), str(s)]) == 0
    assert "INSTALLED" in capsys.readouterr().out
    assert list((tmp_path / "Agents.Work.Trash").glob("mandate-release-*"))
    assert M.main(["receive", str(y)]) == 2


def test_share_asks_the_far_listener_to_install_and_reports_it(here):
    puts, calls = [], []

    def put(f):
        puts.append(f.name)
        return {"ok": True, "saved": [f"/far/link-x/{f.stem}-2{f.suffix}"]}

    def call(req):
        calls.append(req)
        return {"ok": True, "message": "INSTALLED /far/release.yaml"}
    ok, sent = M.share(here, put=put, call=call)
    assert ok and puts[:2] == ["release.yaml", "release.seal"]
    assert calls == [{"verb": "mandate-receive", "yaml": "/far/link-x/release-2.yaml", "seal": "/far/link-x/release-2.seal"}]
    assert sent[-1] == "installed: INSTALLED /far/release.yaml"


def test_share_with_a_refusal_there_is_not_a_success(here):
    put = lambda f: {"ok": True, "saved": [f"/far/{f.name}"]}       # noqa: E731
    ok, sent = M.share(here, put=put, call=lambda req: {"ok": False, "error": "REFUSED: not trusted here"})
    assert not ok and "NOT installed: REFUSED" in sent[-1]


def test_share_to_an_older_listener_leaves_a_note_with_the_command(here):
    calls = []

    def call(req):
        calls.append(req)
        return {"ok": False, "error": "unknown verb 'mandate-receive'"} if req["verb"] != "message" else {"ok": True}
    ok, sent = M.share(here, put=lambda f: {"ok": True, "saved": [f"/far/{f.name}"]}, call=call)
    assert not ok and calls[-1]["verb"] == "message"
    assert "fieldkit mandate receive /far/release.yaml /far/release.seal" in calls[-1]["text"]


def test_a_failed_put_asks_nothing(here):
    calls = []
    ok, sent = M.share(here, put=lambda f: {"ok": False, "error": "no peer"}, call=lambda r: calls.append(r) or {})
    assert not ok and not calls
    ok, sent = M.share(here, put=lambda f: {"ok": True}, call=lambda r: calls.append(r) or {"ok": True})
    assert not ok and not calls                     # sent, but nothing says where it landed: nothing to install


def test_the_listener_verb_runs_receive_on_the_cable_only(monkeypatch):
    from fieldkit.peers import link
    k = b"k" * 32
    monkeypatch.setattr(link, "_log", lambda text: None)
    got = []
    monkeypatch.setattr(M, "receive", lambda y, s: got.append((y, s)) or (0, "INSTALLED x"))
    r = link.handle(link.sign({"verb": "mandate-receive", "yaml": "a.yaml", "seal": "a.seal"}, k), link.Verifier(k))
    assert r == {"ok": True, "message": "INSTALLED x"} and got == [("a.yaml", "a.seal")]
    monkeypatch.setattr(M, "receive", lambda y, s: (3, "REFUSED: no"))
    r = link.handle(link.sign({"verb": "mandate-receive", "yaml": "a.yaml", "seal": "a.seal"}, k), link.Verifier(k))
    assert not r["ok"] and r["error"] == "REFUSED: no"
    r = link.handle(link.sign({"verb": "mandate-receive"}, k), link.Verifier(k), via="lan")
    assert not r["ok"] and "cable only" in r["error"]
