"""Probe tests. Hardware is the in-memory NVML fake; real devices stay untouched."""

from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from tests.fixtures.nvml_fake import DEFAULT_POWER_LIMIT_MW, PERMISSION_DENIED, FakeNVML

from wattnot.hostinfo import cuda_version_string, detect_provider, detect_vllm_version
from wattnot.probe import (
    NvmlUnavailable,
    load_nvml,
    main,
    open_nvml,
    probe,
    run_started_fields,
)

ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime(2026, 10, 27, 5, 0, 0, tzinfo=UTC)


class ManualClock:
    """Clock that moves only when the probe asks to sleep."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.t += seconds


def _run(fake: FakeNVML, **kwargs):
    clock = kwargs.pop("clock", None)
    if clock is None:
        clock = ManualClock()
    sleep = kwargs.pop("sleep", None) or clock.sleep
    return probe(
        fake,
        clock=clock,
        sleep=sleep,
        now=kwargs.pop("now", lambda: FIXED_NOW),
        sample_window_s=kwargs.pop("sample_window_s", 0.5),
        sample_period_s=kwargs.pop("sample_period_s", 0.1),
        environ=kwargs.pop("environ", {}),
        hostname=kwargs.pop("hostname", "devbox"),
        vllm_version=kwargs.pop("vllm_version", None),
        **kwargs,
    )


def test_capabilities_contract_and_sample_rate():
    fake = FakeNVML(allow_cap_write=True, allow_clock_lock=False)
    caps = _run(fake, vllm_version="0.6.3")

    assert set(caps) == {
        "host",
        "nvml_read",
        "energy_counter",
        "read_hz_achieved",
        "power_cap_write",
        "clock_lock",
        "mode",
        "probed_at",
    }
    assert set(caps["host"]) == {"provider", "gpu", "driver", "cuda", "vllm"}
    assert caps["host"]["provider"] == "local"
    assert caps["host"]["gpu"] == "H100 80GB"
    assert caps["host"]["driver"] == "550.90.07"
    assert caps["host"]["cuda"] == "12.4"
    assert caps["host"]["vllm"] == "0.6.3"
    assert caps["nvml_read"] is True
    assert caps["energy_counter"] is True
    assert caps["read_hz_achieved"] == 10.0
    assert caps["probed_at"] == "2026-10-27T05:00:00Z"
    assert caps["mode"] == "root"


def test_cap_write_allowed_restores_previous_limit():
    fake = FakeNVML(allow_cap_write=True, allow_clock_lock=False)
    original = fake.current_limit_mw
    caps = _run(fake)

    assert caps["power_cap_write"]["ok"] is True
    assert "error" not in caps["power_cap_write"]
    assert caps["power_cap_write"]["min_w"] == 200
    assert caps["power_cap_write"]["max_w"] == 700
    assert caps["power_cap_write"]["default_w"] == 700
    assert caps["mode"] == "root"
    assert fake.current_limit_mw == original
    assert fake.power_history
    assert fake.power_history[-1] == original
    assert any(value != original for value in fake.power_history)
    assert all(fake.min_limit_mw <= value <= fake.max_limit_mw for value in fake.power_history)
    # Restored to the limit we found, which is not the factory default.
    assert original != fake.default_limit_mw
    assert caps["clock_lock"] == {"ok": False, "error": PERMISSION_DENIED}
    assert fake.locked is None


def test_cap_write_denied_keeps_limit_and_selects_container_mode():
    fake = FakeNVML(allow_cap_write=False, allow_clock_lock=False)
    original = fake.current_limit_mw
    caps = _run(fake)

    assert caps["power_cap_write"]["ok"] is False
    assert caps["power_cap_write"]["error"] == PERMISSION_DENIED
    assert caps["power_cap_write"]["min_w"] == 200
    assert caps["mode"] == "container"
    assert fake.power_history == []
    assert fake.current_limit_mw == original
    assert fake.reset_calls == 0


def test_no_write_does_not_change_cap_or_clocks():
    fake = FakeNVML(allow_cap_write=True, allow_clock_lock=True)
    original = fake.current_limit_mw
    caps = _run(fake, write=False)

    assert caps["mode"] == "container"
    assert caps["power_cap_write"]["ok"] is False
    assert caps["power_cap_write"]["error"] == "write disabled"
    assert caps["power_cap_write"]["min_w"] == 200
    assert caps["clock_lock"] == {"ok": False, "error": "write disabled"}
    assert fake.power_history == []
    assert fake.current_limit_mw == original
    assert fake.lock_calls == []
    assert fake.reset_calls == 0
    assert fake.locked is None


def test_clock_lock_allowed_is_reset():
    fake = FakeNVML(allow_cap_write=True, allow_clock_lock=True)
    caps = _run(fake)

    assert caps["clock_lock"] == {"ok": True}
    assert fake.locked is None
    assert fake.reset_calls >= 1
    assert fake.lock_calls
    assert caps["mode"] == "root"


def test_cap_and_clocks_restored_when_lock_raises():
    fake = FakeNVML(allow_cap_write=True, allow_clock_lock=True, raise_after_lock=True)
    original = fake.current_limit_mw
    previous = signal.getsignal(signal.SIGTERM)
    with pytest.raises(RuntimeError, match="boom after lock"):
        _run(fake)
    assert fake.current_limit_mw == original
    assert fake.power_history[-1] == original
    assert fake.locked is None
    assert fake.reset_calls >= 1
    assert signal.getsignal(signal.SIGTERM) == previous


def test_cap_restored_when_write_raises():
    fake = FakeNVML(allow_cap_write=True, raise_after_set=True)
    original = fake.current_limit_mw
    previous = signal.getsignal(signal.SIGTERM)
    with pytest.raises(RuntimeError, match="boom after write"):
        _run(fake)
    assert fake.current_limit_mw == original
    assert fake.power_history[0] != original
    assert fake.power_history[-1] == original
    assert signal.getsignal(signal.SIGTERM) == previous


def test_cap_restored_on_sigterm(tmp_path):
    journal = tmp_path / "limits.txt"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(ROOT / "src"), env.get("PYTHONPATH", "")]
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from tests.test_probe import _sigterm_child; _sigterm_child(sys.argv[1])",
            str(journal),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    detail = f"code={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert proc.returncode in {-signal.SIGTERM, 128 + signal.SIGTERM}, detail
    lines = [int(line) for line in journal.read_text(encoding="utf-8").split() if line]
    original = DEFAULT_POWER_LIMIT_MW
    assert lines, detail
    assert lines[-1] == original
    assert any(value != original for value in lines)


def _sigterm_child(journal: str) -> None:
    """Subprocess entry: change the fake cap, then die on SIGTERM."""
    path = Path(journal)
    fake = FakeNVML(allow_cap_write=True, allow_clock_lock=True, journal_path=path)
    original = fake.current_limit_mw
    real_set = fake.set_power_limit_mw

    def wrapped(handle: object, limit_mw: int) -> None:
        real_set(handle, limit_mw)
        if limit_mw != original:
            os.kill(os.getpid(), signal.SIGTERM)

    fake.set_power_limit_mw = wrapped  # type: ignore[method-assign]
    clock = ManualClock()
    probe(
        fake,
        write=True,
        clock=clock,
        sleep=clock.sleep,
        now=lambda: FIXED_NOW,
        sample_window_s=0.5,
        sample_period_s=0.1,
        environ={},
        hostname="devbox",
        vllm_version=None,
    )


def test_energy_counter_unsupported_still_reads():
    fake = FakeNVML(energy_supported=False, allow_cap_write=False)
    caps = _run(fake)
    assert caps["nvml_read"] is True
    assert caps["energy_counter"] is False
    assert caps["mode"] == "container"


def test_core_read_failure_is_reported():
    fake = FakeNVML(fail_core_reads=True, allow_cap_write=False)
    caps = _run(fake)
    assert caps["nvml_read"] is False
    assert caps["energy_counter"] is False


def test_sample_window_stays_short():
    clock = ManualClock()
    fake = FakeNVML(allow_cap_write=False)
    _run(fake, clock=clock)
    assert clock.t <= 1.0


def test_no_gpu_exits_2(tmp_path):
    out = tmp_path / "capabilities.json"
    fake = FakeNVML(device_count=0)
    code = main(["--out", str(out)], backend=fake)
    assert code == 2
    assert not out.exists()
    assert fake.power_history == []
    assert fake.shutdown_called is True


def test_main_without_backend_exits_2_and_does_not_open_nvml(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise AssertionError("real NVML load")

    monkeypatch.setattr(ctypes, "CDLL", boom)
    out = tmp_path / "capabilities.json"
    assert main(["--out", str(out)]) == 2
    assert not out.exists()


def test_open_nvml_refuses_while_pytest_is_running():
    with pytest.raises(NvmlUnavailable):
        open_nvml()


def test_load_nvml_missing_path_does_not_search(tmp_path):
    missing = tmp_path / "libnvidia-ml.so.1"
    with pytest.raises(NvmlUnavailable):
        load_nvml(str(missing))


def test_probe_with_fake_does_not_load_a_library(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("CDLL")

    monkeypatch.setattr(ctypes, "CDLL", boom)
    caps = _run(FakeNVML(allow_cap_write=True))
    assert caps["nvml_read"] is True


def test_probe_does_not_spawn_processes(monkeypatch):
    import subprocess as subprocess_mod

    def boom(*_args, **_kwargs):
        raise AssertionError("probe spawned a process")

    monkeypatch.setattr(subprocess_mod, "run", boom)
    monkeypatch.setattr(subprocess_mod, "Popen", boom)
    monkeypatch.setattr(subprocess_mod, "check_call", boom)
    caps = _run(FakeNVML(allow_cap_write=True, allow_clock_lock=True))
    assert caps["clock_lock"]["ok"] is True


def test_main_writes_capabilities_json(tmp_path, capsys):
    out = tmp_path / "capabilities.json"
    fake = FakeNVML(allow_cap_write=False)
    code = main(["--out", str(out), "--no-write"], backend=fake)
    assert code == 0
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert on_disk == printed
    assert on_disk["mode"] == "container"
    assert on_disk["power_cap_write"]["ok"] is False
    assert fake.power_history == []
    assert fake.shutdown_called is True


def test_main_exit_0_when_cap_write_is_denied(tmp_path):
    out = tmp_path / "capabilities.json"
    fake = FakeNVML(allow_cap_write=False, allow_clock_lock=False)
    assert main(["--out", str(out)], backend=fake) == 0
    caps = json.loads(out.read_text(encoding="utf-8"))
    assert caps["mode"] == "container"
    assert caps["power_cap_write"]["error"] == PERMISSION_DENIED


def test_run_started_fields_carry_capabilities():
    fake = FakeNVML(allow_cap_write=True, allow_clock_lock=False)
    caps = _run(fake, vllm_version=None)
    fields = run_started_fields(caps)
    assert fields["mode"] == "root"
    assert fields["host"] == {
        "provider": "local",
        "gpu": "H100 80GB",
        "driver": "550.90.07",
        "cuda": "12.4",
    }
    assert fields["capabilities"]["host"]["vllm"] is None
    assert fields["capabilities"]["mode"] == "root"


@pytest.mark.parametrize(
    ("env", "hostname", "expected"),
    [
        ({}, "workstation", "local"),
        ({"MODAL_TASK_ID": "ta-123"}, "workstation", "modal"),
        ({"MODAL_IS_REMOTE": "1"}, "workstation", "modal"),
        ({"MODAL_IMAGE_ID": "im-123"}, "workstation", "modal"),
        ({"MODAL_TOKEN_ID": "token"}, "workstation", "local"),
        ({"MODAL_ENVIRONMENT": "main"}, "workstation", "local"),
        ({"BT_MODEL_ID": "model-1"}, "workstation", "baseten"),
        ({"BT_MODEL_DEPLOYMENT_ID": "dep-1"}, "workstation", "baseten"),
        ({"BASETEN_API_KEY": "secret"}, "workstation", "local"),
        ({}, "box.jarvislabs.ai", "jarvislabs"),
        ({}, "gpu.e2enetworks.com", "e2e"),
        ({"WATTNOT_PROVIDER": "e2e"}, "workstation", "e2e"),
        ({"WATTNOT_PROVIDER": "modal", "BT_MODEL_ID": "model-1"}, "workstation", "modal"),
        ({"WATTNOT_PROVIDER": "nope"}, "box.jarvislabs.ai", "jarvislabs"),
    ],
)
def test_provider_detection(env, hostname, expected):
    assert detect_provider(env, hostname) == expected


@pytest.mark.parametrize(
    ("coded", "text"),
    [(12040, "12.4"), (11020, "11.2"), (12000, "12.0")],
)
def test_cuda_version_string(coded, text):
    assert cuda_version_string(coded) == text


def test_vllm_version_from_metadata(monkeypatch):
    monkeypatch.setattr("wattnot.hostinfo.package_version", lambda _name: "0.6.3")
    assert detect_vllm_version() == "0.6.3"

    def missing(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr("wattnot.hostinfo.package_version", missing)
    assert detect_vllm_version() is None
