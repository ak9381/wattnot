"""Regression tests for the Nov 1 end-to-end review fixes."""

import json

import pytest

from wattnot import verify
from wattnot.longrun import driver


def test_synthetic_is_a_known_label():
    assert "SYNTHETIC" in verify.KNOWN_LABELS


def test_long_plan_defaults_to_its_booking():
    assert driver._default_hours("glimmer-test", "long") == driver.LONG_BOOKING_H
    assert driver._default_hours("glimmer-test", "retest") == 3.5


def test_long_plan_needs_glimmer_test():
    with pytest.raises(SystemExit):
        driver.main(["--dry-run", "--plan", "long", "--out", "x"])


def test_init_keeps_watch_when_config_has_no_mode(tmp_path, monkeypatch):
    from wattnot import init, preflight
    monkeypatch.setattr(preflight, "run", lambda: {"features": []})
    p = tmp_path / "c.json"
    p.write_text("{}")
    assert init.init(p)["mode"] == "watch"
    p.write_text("not json")
    with pytest.raises(SystemExit):
        init.init(p)
    assert json.loads("{}") == {}
