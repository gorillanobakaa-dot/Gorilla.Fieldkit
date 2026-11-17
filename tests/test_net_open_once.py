from fieldkit.zero import channels


def test_net_open_goes_to_a_station_once_per_epoch(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path))
    sent = []
    oc = channels.OpenCode(sessions=lambda: [{"name": "a"}],
                           send=lambda n, t, as_name=None: sent.append(n) or {"ok": True})
    net = {"epoch": 5, "zero": {"station": "z"}}
    for _ in range(5):
        oc.push({"kind": "open", "id": "x"}, net)
    assert sent == ["a"]
    oc.push({"kind": "open", "id": "x"}, {"epoch": 6, "zero": {"station": "z"}})
    assert sent == ["a", "a"]
    oc.push({"kind": "msg", "id": "m", "from": "z"}, net)
    oc.push({"kind": "msg", "id": "m2", "from": "z"}, net)
    assert len(sent) == 4


def test_a_message_to_one_station_never_reaches_another_opencode_session():
    # 2026-11-15: NET MESSAGEs addressed to Zero (claude:...) appeared in the owner's OpenCode chat
    from fieldkit.zero import channels as ch
    sent = []
    oc = ch.OpenCode(sessions=lambda: [{"name": "Gorilla.Opencode"}, {"name": "other"}],
                     send=lambda n, t, as_name=None: sent.append(n) or {"ok": True})
    net = {"epoch": 1, "zero": {"station": "claude:c67@host"}}
    e = {"kind": "msg", "id": "M-1", "from": "claude:91@host", "text": "hi", "to": "claude:c67eb841@host"}
    assert oc.push(e, net)[0]["target"] == "-" and sent == []  # a message not about Zero: nothing, not even a copy
    oc.push(dict(e, to="opencode:other@host"), net)
    assert sent == ["other"]
    sent.clear()
    oc.push(dict(e, to="*"), net)
    assert sent == ["Gorilla.Opencode", "other"]


def test_zero_traffic_is_mirrored_as_a_display_only_copy(monkeypatch):
    # owner 2026-11-15: he wants to see Zero's messages in OpenCode, as a CC the model never gets
    from fieldkit.zero import channels as ch
    sent = []
    oc = ch.OpenCode(sessions=lambda: [{"name": "Gorilla.Opencode"}, {"name": "other"}],
                     send=lambda n, t, as_name=None, kind="message": sent.append((n, kind, t)) or {"ok": True})
    net = {"epoch": 1, "zero": {"station": "claude:c67@host"}}
    monkeypatch.setattr(ch, "mirror_on", lambda: True)
    to_zero = {"kind": "msg", "id": "M-1", "from": "claude:91@host", "text": "hi", "to": "claude:c67@host"}
    oc.push(to_zero, net)
    assert [(n, k) for n, k, _ in sent] == [("Gorilla.Opencode", "cc"), ("other", "cc")]
    assert "from claude:91@host to claude:c67@host: hi" in sent[0][2]
    sent.clear()
    oc.push(dict(to_zero, **{"from": "claude:c67@host", "to": "claude:91@host"}), net)  # sent BY Zero
    assert len(sent) == 2 and {k for _, k, _ in sent} == {"cc"}
    sent.clear()
    oc.push(dict(to_zero, **{"from": "claude:1@host", "to": "claude:2@host"}), net)  # neither is Zero
    assert sent == []
    monkeypatch.setattr(ch, "mirror_on", lambda: False)
    oc.push(to_zero, net)
    assert sent == []
    # a message to an OpenCode station is still a real message to that one station only
    monkeypatch.setattr(ch, "mirror_on", lambda: True)
    oc.push(dict(to_zero, to="opencode:other@host"), net)
    assert [(n, k) for n, k, _ in sent] == [("other", "message")]
