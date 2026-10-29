import pytest

from wattnot.meta.ledger import CAP_USD, SOFT_USD, SpendLedger, caps_from_env


def test_defaults_are_approved_100_80(monkeypatch, tmp_path):
    monkeypatch.delenv("WATTNOT_META_CAP_USD", raising=False)
    monkeypatch.delenv("WATTNOT_META_SOFT_USD", raising=False)
    assert (CAP_USD, SOFT_USD) == (100.0, 80.0)
    lg = SpendLedger(tmp_path / "l.json")
    assert (lg.cap_usd, lg.soft_usd) == (100.0, 80.0)


def test_env_can_tighten(monkeypatch, tmp_path):
    monkeypatch.setenv("WATTNOT_META_CAP_USD", "30")
    monkeypatch.delenv("WATTNOT_META_SOFT_USD", raising=False)
    assert caps_from_env() == (30.0, 24.0)
    lg = SpendLedger(tmp_path / "l.json")
    assert (lg.cap_usd, lg.soft_usd) == (30.0, 24.0)


def test_env_cannot_loosen(monkeypatch):
    monkeypatch.setenv("WATTNOT_META_CAP_USD", "150")
    with pytest.raises(ValueError):
        caps_from_env()


def test_soft_above_hard_refused(monkeypatch):
    monkeypatch.setenv("WATTNOT_META_CAP_USD", "20")
    monkeypatch.setenv("WATTNOT_META_SOFT_USD", "25")
    with pytest.raises(ValueError):
        caps_from_env()


def test_explicit_args_still_win(monkeypatch, tmp_path):
    monkeypatch.setenv("WATTNOT_META_CAP_USD", "30")
    lg = SpendLedger(tmp_path / "l.json", cap_usd=10, soft_usd=5)
    assert (lg.cap_usd, lg.soft_usd) == (10, 5)
