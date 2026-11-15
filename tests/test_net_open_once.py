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
