"""Every Modal-failure lesson has a $0 check on the Baseten and H100 paths (wattnot.launch_check)."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from wattnot import launch_check as L
from wattnot.longrun import driver

PROVIDERS = ("baseten", "h100")


def _ok(r):
    assert r["ok"], r
    assert r["where"] and r["lesson"]


# 1 block time vs kill limit
def test_h100_public_blocks_fit_with_headroom():
    _ok(L.check_block_time_h100())


def test_h100_estimator_catches_the_modal_agentwaste_config():
    b = driver.Block("p1-agentwaste", "x", 15 / 60, None,
                     live=["python", "scripts/agentwaste_gpu_ab.py", "--reps", "2", "--max-tokens", "2048"])
    assert L.h100_block_worst_s(b) > L.HEADROOM * b.hours * 3600 * driver.OVERRUN_FACTOR


def test_public_plan_applies_the_agentwaste_trim():
    bs = {b.id: b for b in driver.plan_glimmer_public(Path("/tmp/x"))}
    aw = bs["p1-agentwaste"].live
    assert aw[-6:] == list(driver.PUBLIC_AGENTWASTE_TRIM)
    assert bs["p1-langchain"].hours * 60 == driver.PUBLIC_BLOCK_MIN["p1-langchain"]
    assert bs["t2-fp8"].hours * 60 == driver.PUBLIC_BLOCK_MIN["t2-fp8"]


def test_baseten_block_time():
    _ok(L.check_block_time_baseten())


def test_modal_block_time_flags_the_tight_caps():
    assert not L.check_block_time_modal()["ok"]   # langchain 898 s vs 900 s cap in r04-try1


# 2 tool calling
@pytest.mark.parametrize("p", PROVIDERS)
def test_tool_calling(p):
    _ok(L.check_tool_calling(p))


# 3 /v1 doubling
@pytest.mark.parametrize("p", PROVIDERS)
def test_v1(p):
    _ok(L.check_v1(p))


def test_langchain_trailing_slash_regression():
    lp = L._load_script("scripts/langchain_pass.py", "_t_lp")
    assert lp.upstream_base("http://h:8000/v1/") == "http://h:8000"
    assert lp.upstream_base("http://h:8000/v1") == "http://h:8000"
    assert lp.upstream_base("http://h:8000") == "http://h:8000"


# 4 reasoning_effort
def test_reasoning_effort():
    _ok(L.check_reasoning_effort())


# 5 imports
@pytest.mark.parametrize("p", PROVIDERS)
def test_imports(p):
    _ok(L.check_imports(p))


def test_imports_catches_a_missing_module(monkeypatch):
    monkeypatch.setitem(L.IMPORTS, "runner", ["optuna", "no_such_module_xyz"])
    r = L.check_imports("baseten")
    assert not r["ok"] and "no_such_module_xyz" in r["detail"]


def test_h100_setup_installs_and_checks_langchain():
    sh = (L.ROOT / "scripts" / "h100_vm_setup.sh").read_text()
    assert "langchain-openai==1.7.3" in sh and "IMPORT_CHECK" in sh and "import check failed" in sh


# 6 recorder
def test_recorder_dashboard_starts_and_guards_the_runner():
    _ok(L.check_dashboard(True))


def test_recorder_catches_a_broken_dash_python(monkeypatch):
    monkeypatch.setenv("WN_DASH_PY", "/bin/false")
    assert not L.check_dashboard(True)["ok"]


# 7 eye gauge, 8 hearing
def test_eye_gauge():
    _ok(L.check_eye())


def test_hearing():
    _ok(L.check_hearing())


# 9 MOCK labels
@pytest.mark.parametrize("p", PROVIDERS)
def test_mock_labels(p):
    _ok(L.check_mock(p))


# 10 FP8, 11 context saver
@pytest.mark.parametrize("p", PROVIDERS)
def test_fp8_and_context_saver(p):
    _ok(L.check_fp8(p))
    _ok(L.check_context_saver(p))


def test_fp8_catches_a_too_short_block(monkeypatch):
    real = driver.plan_glimmer_public
    monkeypatch.setattr(driver, "plan_glimmer_public",
                        lambda out: [replace(b, hours=20 / 60) if b.id == "t2-fp8" else b for b in real(out)])
    assert not L.check_fp8("h100")["ok"]


# 12 certificate, 13 disk
def test_certificate_noise_declines_with_exit_0():
    _ok(L.check_certificate())


@pytest.mark.parametrize("p", PROVIDERS)
def test_disk(p):
    _ok(L.check_disk(p))


def test_disk_floor(monkeypatch):
    monkeypatch.setattr(L, "MIN_DISK_GB", 10 ** 9)
    assert not L.check_disk("baseten")["ok"]


# the single command
@pytest.mark.parametrize("p", PROVIDERS)
def test_one_command_passes(p):
    rep = L.run(p, dash=False)
    assert rep["ok"], [c for c in rep["checks"] if not c["ok"]]
    assert rep["cost_usd"] == 0.0 and len(rep["checks"]) == 13
