"""migrate consistency is never served stale (2026-10-08: a 3 October cache kept a LOST-LAYER brief open for builds
that carried the file): the cache is reused only for the installed build and tree it was measured on."""
from fieldkit.migrate import consistency, measure


class M:
    def __init__(self, rec, build="B2", tree="T2", install="C:/inst"):
        self.rec, self._b, self._t, self.install = rec, build, tree, install
        self.owner, self.t, self.tid, self.put_calls = "owner", {"workdir": "w"}, "task", []

    def cache(self, name):
        return self.rec

    def build_id(self):
        return self._b

    def tree(self):
        return self._t

    def put(self, name, data):
        self.put_calls.append(name)
        self.rec = {"build_id": self._b, "tree": self._t, "data": data}
        return self.rec


def test_a_cache_for_this_build_and_tree_is_reused(monkeypatch):
    monkeypatch.setattr(consistency, "evaluate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("measured")))
    m = M({"build_id": "B2", "tree": "T2", "data": {"items": []}})
    assert measure.fresh_consistency(m, say=lambda s: None)["data"] == {"items": []} and not m.put_calls


def test_a_cache_for_another_build_is_measured_again(monkeypatch):
    monkeypatch.setattr(consistency, "evaluate", lambda *a, **k: {"items": [{"id": "CONS-002", "closed": True}]})
    m = M({"build_id": "B1", "tree": "T2", "data": {"items": [{"id": "CONS-002", "closed": False}]}})
    rec = measure.fresh_consistency(m, say=lambda s: None)
    assert m.put_calls == ["consistency"] and rec["data"]["items"][0]["closed"] is True


def test_without_an_install_the_cache_is_returned_as_is(monkeypatch):
    m = M({"build_id": "B1", "tree": "T1", "data": {"items": []}}, install=None)
    assert measure.fresh_consistency(m, say=lambda s: None)["build_id"] == "B1"
