import json

from wattnot import settings
from wattnot.gates.latency import p99_gate


class _L:
    def __init__(self, p):
        self.p99_s = p


def test_default_is_ten_percent(monkeypatch, tmp_path):
    monkeypatch.delenv("WATTNOT_MAX_P99_INCREASE", raising=False)
    monkeypatch.setenv("WATTNOT_CONFIG", str(tmp_path / "none.json"))
    assert settings.max_p99_increase() == 0.10


def test_config_and_env(monkeypatch, tmp_path):
    cfg = tmp_path / "wattnot.json"
    cfg.write_text(json.dumps({"max_p99_increase": "5%"}))
    monkeypatch.setenv("WATTNOT_CONFIG", str(cfg))
    monkeypatch.delenv("WATTNOT_MAX_P99_INCREASE", raising=False)
    assert settings.max_p99_increase() == 0.05
    monkeypatch.setenv("WATTNOT_MAX_P99_INCREASE", "0.2")
    assert settings.max_p99_increase() == 0.2
    assert settings.max_p99_increase(0.15) == 0.15  # --p99-slack alias wins
    assert settings.receipt_block()["max_p99_increase_pct"] == 20.0


def test_gate_reads_setting(monkeypatch):
    monkeypatch.setenv("WATTNOT_MAX_P99_INCREASE", "0.05")
    assert not p99_gate(_L(1.0), _L(1.08)).passed
    monkeypatch.setenv("WATTNOT_MAX_P99_INCREASE", "0.10")
    assert p99_gate(_L(1.0), _L(1.08)).passed


def test_cloud_flag_defaults_to_setting():
    from wattnot.cloud import cli
    src = open(cli.__file__).read()
    assert '"--p99-slack", type=float, default=None' in src


def test_setup_screen_shows_both(monkeypatch):
    monkeypatch.delenv("WATTNOT_MAX_P99_INCREASE", raising=False)
    from wattnot.dash import configure
    o = configure.options({})
    ids = [x["id"] for x in (o if isinstance(o, list) else o.get("options", o.get("items", [])))]
    assert "p99_slack_pct" in ids and "auto_approve_max_cap_drop_pct" in ids
