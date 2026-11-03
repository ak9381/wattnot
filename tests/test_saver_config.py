"""Context saver on/off and long-conversation threshold from config, env and CLI."""
import json

import pytest

from wattnot import settings
from wattnot.cli import main
from wattnot.context_saver import ContextSaver


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for k in ("WATTNOT_SAVER", "WATTNOT_SAVER_MIN_TOKENS", "WATTNOT_MODE", "WATTNOT_MAX_P99_INCREASE"):
        monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "wattnot.json"
    monkeypatch.setenv("WATTNOT_CONFIG", str(cfg))
    return cfg


def _mc(saver):
    return next(m for m in saver.middlewares if m.name == "memory_compression")


def test_defaults_on_at_500():
    cfg = settings.load()
    assert cfg["context_saver"] is True and cfg["saver_min_tokens"] == 500
    assert _mc(ContextSaver.from_settings()).min_tokens == 500


def test_config_file(clean_env):
    clean_env.write_text(json.dumps({"saver_min_tokens": 2000}))
    assert _mc(ContextSaver.from_settings()).min_tokens == 2000
    clean_env.write_text(json.dumps({"context_saver": False}))
    assert ContextSaver.from_settings() is None


def test_env_wins(clean_env, monkeypatch):
    clean_env.write_text(json.dumps({"context_saver": True, "saver_min_tokens": 2000}))
    monkeypatch.setenv("WATTNOT_SAVER_MIN_TOKENS", "800")
    assert _mc(ContextSaver.from_settings()).min_tokens == 800
    monkeypatch.setenv("WATTNOT_SAVER", "off")
    assert ContextSaver.from_settings() is None


@pytest.mark.parametrize("bad", [{"saver_min_tokens": -1}, {"saver_min_tokens": "lots"},
                                 {"saver_min_tokens": 1.5}, {"context_saver": "maybe"}])
def test_bad_values_refused(clean_env, bad):
    clean_env.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        settings.load()


def test_threshold_gates_compression():
    msgs = [{"role": "system", "content": "sys"}] + [
        {"role": "user" if i % 2 else "assistant", "content": f"turn {i} " + "word " * 60} for i in range(12)]
    _, acct = ContextSaver(min_tokens=10**6).transform(list(msgs))
    mc = next(s for s in acct["stages"] if s["name"] == "memory_compression")
    assert mc["applied"] is False and mc["gated"] == "below_min_tokens"


def test_cli_sets_and_shows(clean_env, capsys):
    assert main(["saver", "--off", "--min-tokens", "1200"]) == 0
    assert json.loads(clean_env.read_text()) == {"context_saver": False, "saver_min_tokens": 1200}
    assert "Context saver: off" in capsys.readouterr().out
    assert main(["saver", "--on"]) == 0
    out = capsys.readouterr().out
    assert "on" in out and "1200+" in out


def test_request_levers_respect_off(monkeypatch):
    monkeypatch.setenv("WATTNOT_SAVER", "off")
    from wattnot.e2e.fullstack import RequestLevers
    lv = RequestLevers(tools_supported=False)
    assert lv.saver.middlewares == []
