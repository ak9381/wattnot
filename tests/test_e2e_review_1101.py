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


def test_long_plan_needs_the_glimmer_h100_setting(tmp_path, monkeypatch):
    # llama has no long plan: refused rather than silently running the old full plan
    with pytest.raises(SystemExit):
        driver.main(["--dry-run", "--plan", "long", "--model", "llama", "--out", str(tmp_path)])
    # glimmer (the default) is the same Glimmer 30B on the H100: it runs the glimmer-test plan
    seen = {}
    monkeypatch.setattr(driver, "dry_run", lambda out, **kw: seen.update(kw) or
                        {"blocks": {}, "stopped": "finished", "sim": {}})
    assert driver.main(["--dry-run", "--plan", "long", "--out", str(tmp_path)]) == 0
    assert seen["model"] == "glimmer-test" and seen["plan_name"] == "long"


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


def test_library_default_is_watch(monkeypatch, tmp_path):
    from wattnot import settings
    monkeypatch.delenv("WATTNOT_MODE", raising=False)
    monkeypatch.setenv("WATTNOT_CONFIG", str(tmp_path / "none.json"))
    assert settings.watch_only()


def test_sweep_keeps_cap_only_with_act(monkeypatch, tmp_path):
    from types import SimpleNamespace as NS

    from wattnot.proof.orchestrate import _watch_only_keep
    monkeypatch.delenv("WATTNOT_MODE", raising=False)
    monkeypatch.setenv("WATTNOT_CONFIG", str(tmp_path / "none.json"))
    assert _watch_only_keep(NS(mode="live", act=False))
    assert not _watch_only_keep(NS(mode="live", act=True))
    assert not _watch_only_keep(NS(mode="rehearsal", act=False))


def test_long_run_vm_path_acts():
    from pathlib import Path
    assert "WATTNOT_MODE=act" in driver.host_inner(["python", "-V"])
    assert "--act" in driver.sweep_live_argv(Path("o"), Path("l"), "g", sweep_budget_s=60, reps=2, warmup=4, warmup_s=5, grid="375")
