"""The own-GPU (H100 long-run driver) provider: config validation and dry-run command building
only. No paid launch is ever started by these tests."""
from __future__ import annotations

import pytest

from wattnot.dash import launch as L


def test_own_gpu_defaults_to_a_free_dry_run():
    cfg = L.norm_config({"provider": "own_gpu"})
    assert cfg["own_gpu"]["dry_run"] is True
    assert cfg["own_gpu"]["plan"] == L.OWN_GPU_DEFAULT_PLAN
    assert cfg["own_gpu"]["key_path"] == "~/.ssh/e2e_h100"


def test_own_gpu_unknown_plan_is_rejected():
    with pytest.raises(ValueError):
        L.norm_config({"provider": "own_gpu", "own_gpu": {"plan": "quick"}})


def test_own_gpu_live_without_host_is_rejected():
    with pytest.raises(ValueError):
        L.norm_config({"provider": "own_gpu", "own_gpu": {"dry_run": False}})


def test_own_gpu_live_with_host_is_accepted():
    cfg = L.norm_config({"provider": "own_gpu",
                         "own_gpu": {"dry_run": False, "host": "203.0.113.10", "plan": "long"}})
    assert cfg["own_gpu"]["host"] == "203.0.113.10" and cfg["own_gpu"]["plan"] == "long"


def test_own_gpu_empty_key_path_falls_back_to_the_default():
    cfg = L.norm_config({"provider": "own_gpu", "own_gpu": {"key_path": ""}})
    assert cfg["own_gpu"]["key_path"] == "~/.ssh/e2e_h100"


# --------------------------------------------------------------------------- command building


def test_own_gpu_dry_run_command_never_touches_a_real_vm():
    cfg = L.norm_config({"provider": "own_gpu", "own_gpu": {"plan": "max"}})
    cmd, env, label = L.command(cfg, "mock")
    assert "--dry-run" in cmd and "--live" not in cmd
    assert cmd[:3] == [L.PY, "-m", "wattnot.longrun.driver"]
    assert cmd[cmd.index("--model") + 1] == "glimmer"
    assert cmd[cmd.index("--plan") + 1] == "max"
    assert cmd[cmd.index("--out") + 1] == "{job}/longrun"
    assert label == "MOCK"
    assert "--host" not in cmd and "--key" not in cmd  # no VM / key details in a free dry run


def test_own_gpu_live_command_without_the_go_env_is_refused(monkeypatch):
    monkeypatch.delenv(L.OWN_GPU_GO_ENV, raising=False)
    cfg = L.norm_config({"provider": "own_gpu", "own_gpu": {"dry_run": False, "host": "203.0.113.10"}})
    with pytest.raises(PermissionError):
        L.command(cfg, "live")


def test_own_gpu_live_command_with_the_go_env_builds_the_real_launch(monkeypatch):
    monkeypatch.setenv(L.OWN_GPU_GO_ENV, "1")
    cfg = L.norm_config({"provider": "own_gpu",
                         "own_gpu": {"dry_run": False, "host": "203.0.113.10",
                                     "key_path": "/home/x/.ssh/id_h100", "plan": "retest"}})
    cmd, env, label = L.command(cfg, "live")
    assert "--live" in cmd and "--dry-run" not in cmd
    assert cmd[cmd.index("--host") + 1] == "203.0.113.10"
    assert cmd[cmd.index("--key") + 1] == "/home/x/.ssh/id_h100"
    assert label == "MEASURED"


def test_own_gpu_mock_mode_always_stays_dry_run_even_if_configured_live(monkeypatch):
    # mode="mock" is the generic free path: it must never build a --live command, no matter what
    # the stored config says.
    monkeypatch.setenv(L.OWN_GPU_GO_ENV, "1")
    cfg = L.norm_config({"provider": "own_gpu", "own_gpu": {"dry_run": False, "host": "203.0.113.10"}})
    cmd, env, label = L.command(cfg, "mock")
    assert "--dry-run" in cmd and "--live" not in cmd
    assert label == "MOCK"


def test_own_gpu_preview_shows_command_and_checks_without_starting_anything(monkeypatch):
    monkeypatch.delenv(L.OWN_GPU_GO_ENV, raising=False)
    cfg = L.norm_config({"provider": "own_gpu", "own_gpu": {"plan": "retest"}})
    prev = L.own_gpu_preview(cfg)
    assert prev["dry_run"] is True
    assert "--dry-run" in prev["command"] and "--live" not in prev["command"]
    by_id = {c["id"]: c for c in prev["checks"]}
    assert by_id["plan"]["state"] == "pass"
    assert by_id["go"]["state"] == "warn"  # WATTNOT_OWN_GPU_GO not set


def test_own_gpu_provider_is_registered_with_a_runner():
    from wattnot import eligibility as E
    assert E.PROVIDERS["own_gpu"]["runner"] == "own_gpu"


def test_own_gpu_checklist_names_the_driver_module():
    cfg = L.norm_config({"provider": "own_gpu"})
    item = L.check_runner(cfg)
    assert item["state"] == "pass"
    assert item["reason"] == "wattnot.longrun.driver"
