from pathlib import Path
from wattnot import flow


def test_offline_key_falls_back_to_throwaway(tmp_path, monkeypatch):
    monkeypatch.setattr(flow, "TEST_KEY", tmp_path / "missing.pem")
    k = flow._offline_key(tmp_path)
    assert k == tmp_path / "flow-offline-key.pem" and k.exists()
    assert oct(k.stat().st_mode & 0o777) == "0o600"
    assert flow._offline_key(tmp_path).read_bytes() == k.read_bytes()


def test_offline_key_prefers_local(tmp_path, monkeypatch):
    local = tmp_path / "local.pem"; local.write_text("x")
    monkeypatch.setattr(flow, "TEST_KEY", local)
    assert flow._offline_key(tmp_path) == local
