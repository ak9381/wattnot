import importlib, json, sys
from pathlib import Path

E2E = Path(__file__).resolve().parents[1] / "scripts" / "e2e"


def _load(monkeypatch, loc=None):
    monkeypatch.syspath_prepend(str(E2E))
    if loc: monkeypatch.setenv("E2E_LOCATION", loc)
    else: monkeypatch.delenv("E2E_LOCATION", raising=False)
    sys.modules.pop("ctl", None)
    return importlib.import_module("ctl")


def test_chennai_uses_its_own_price_and_security_group(monkeypatch):
    c = _load(monkeypatch, "Chennai")
    assert c.P["location"] == "Chennai" and c.PRICE_ID == 7077 and c.SG == 34482 and c.SKU_ID == 525


def test_default_region_is_delhi(monkeypatch, tmp_path):
    c = _load(monkeypatch)
    if not Path(f"{c.D}/vm.json").exists():
        assert c.P["location"] == "Delhi" and c.PRICE_ID == 7087 and c.SG == 34486


def test_unknown_region_is_refused(monkeypatch):
    import pytest
    with pytest.raises(AssertionError):
        _load(monkeypatch, "Mumbai")
