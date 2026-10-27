"""Host capability probe.

Checks whether this machine can read NVML power, energy, clocks and temperature,
whether a power cap can be written, and whether GPU clocks can be locked.
Any cap or lock this probe sets is put back before it returns, including when
the write raises or the process receives SIGTERM.

    python -m wattnot.probe [--out capabilities.json] [--no-write]

Exit 0 means the probe ran, even when the cap write is blocked.
Exit 2 means there is no GPU or NVML is missing.

The returned object is what `run.started.data.capabilities` stores.
`run_started_fields` copies mode and host onto that event as well.

Tests pass an in-memory backend. `open_nvml` refuses to load the real
library while pytest is running, so the suite cannot change a device.
"""

from __future__ import annotations

import argparse
import atexit
import ctypes
import json
import os
import signal
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from wattnot.hostinfo import cuda_version_string, detect_provider, detect_vllm_version

# NVML clock type ids. SM is what `nvidia-smi -lgc` locks; we call
# nvmlDeviceSetGpuLockedClocks directly and do not spawn that command.
CLOCK_SM = 1
CLOCK_MEM = 2

_AUTO = object()
_NVML_ERROR_NAMES = {
    1: "Uninitialized",
    2: "Invalid Argument",
    3: "Not Supported",
    4: "Insufficient Permissions",
    6: "Not Found",
    9: "Driver Not Loaded",
    12: "NVML Shared Library Not Found",
    999: "Unknown Error",
}


class NvmlError(Exception):
    """An NVML call failed (permission, unsupported, bad argument)."""


class NvmlUnavailable(Exception):
    """NVML is missing, blocked, or failed to initialize. Nothing was changed."""


class NoGpu(Exception):
    """NVML is up but no device can be opened."""


def run_started_fields(caps: dict) -> dict:
    """Fields this probe contributes to a `run.started` event's data.

    The caller adds model and workload. `capabilities` is the full probe report.
    The event's host object follows the run schema and leaves vLLM inside
    `capabilities.host`.
    """
    host = caps["host"]
    return {
        "mode": caps["mode"],
        "host": {
            "provider": host["provider"],
            "gpu": host["gpu"],
            "driver": host["driver"],
            "cuda": host["cuda"],
        },
        "capabilities": caps,
    }


def probe(
    nvml,
    *,
    write: bool = True,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], datetime] | None = None,
    sample_window_s: float = 0.5,
    sample_period_s: float = 0.1,
    environ: Mapping[str, str] | None = None,
    hostname: str | None = None,
    vllm_version: object = _AUTO,
) -> dict:
    """Run the capability check against `nvml` and restore anything it changes.

    `write=False` is the `--no-write` path: reads only, mode stays container.
    """
    tick = clock or time.perf_counter
    pause = sleep or time.sleep
    try:
        count = nvml.device_count()
    except NvmlError as exc:
        raise NoGpu(str(exc)) from exc
    if count < 1:
        raise NoGpu("no GPU")
    try:
        handle = nvml.device_handle(0)
    except NvmlError as exc:
        raise NoGpu(str(exc)) from exc

    try:
        saved_cap = nvml.power_limit_mw(handle)
    except NvmlError:
        saved_cap = None

    guard = _RestoreGuard(nvml, handle, saved_cap)
    guard.arm()
    try:
        return _collect(
            nvml,
            handle,
            guard,
            saved_cap=saved_cap,
            write=write,
            clock=tick,
            sleep=pause,
            now=now,
            sample_window_s=sample_window_s,
            sample_period_s=sample_period_s,
            environ=environ,
            hostname=hostname,
            vllm_version=vllm_version,
        )
    finally:
        guard.close()


def _collect(
    nvml,
    handle,
    guard: _RestoreGuard,
    *,
    saved_cap: int | None,
    write: bool,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    now: Callable[[], datetime] | None,
    sample_window_s: float,
    sample_period_s: float,
    environ: Mapping[str, str] | None,
    hostname: str | None,
    vllm_version: object,
) -> dict:
    nvml_read, energy_ok, hz = _measure_reads(
        nvml,
        handle,
        clock=clock,
        sleep=sleep,
        window_s=sample_window_s,
        period_s=sample_period_s,
    )
    power = _check_power_cap(nvml, handle, guard, saved_cap=saved_cap, write=write)
    clocks = _check_clock_lock(nvml, handle, guard, write=write)
    version = detect_vllm_version() if vllm_version is _AUTO else vllm_version
    caps = {
        "host": {
            "provider": detect_provider(environ, hostname),
            "gpu": _read_text(lambda: nvml.device_name(handle), "unknown"),
            "driver": _read_text(nvml.driver_version, "unknown"),
            "cuda": _read_cuda(nvml),
            "vllm": version,
        },
        "nvml_read": nvml_read,
        "energy_counter": bool(energy_ok and nvml_read),
        "read_hz_achieved": hz,
        "power_cap_write": power,
        "clock_lock": clocks,
        "mode": "root" if power["ok"] else "container",
        "probed_at": _format_now(now),
    }
    return caps


def _measure_reads(
    nvml,
    handle,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    window_s: float,
    period_s: float,
) -> tuple[bool, bool, float]:
    """Sample power, clocks, temperature and energy. Return read flags and Hz."""
    start = clock()
    deadline = start + window_s
    samples = 0
    core_ok = True
    energy_ok = True
    if period_s > 0:
        max_samples = max(1, int(window_s / period_s) + 2)
    else:
        max_samples = 1

    while samples < max_samples:
        now = clock()
        if samples > 0 and now >= deadline:
            break
        if not _core_sample(nvml, handle):
            core_ok = False
        if not _energy_sample(nvml, handle):
            energy_ok = False
        samples += 1
        delay = (start + samples * period_s) - clock()
        if delay > 0:
            before = clock()
            sleep(delay)
            if clock() <= before:
                break
        if clock() >= deadline:
            break

    elapsed = clock() - start
    if elapsed <= 0 or samples == 0:
        hz = 0.0
    else:
        hz = float(round(samples / elapsed, 1))
    return core_ok, energy_ok, hz


def _core_sample(nvml, handle) -> bool:
    try:
        nvml.power_usage_mw(handle)
        nvml.clock_mhz(handle, CLOCK_SM)
        nvml.clock_mhz(handle, CLOCK_MEM)
        nvml.temperature_c(handle)
    except NvmlError:
        return False
    return True


def _energy_sample(nvml, handle) -> bool:
    try:
        nvml.total_energy_mj(handle)
    except NvmlError:
        return False
    return True


def _check_power_cap(
    nvml,
    handle,
    guard: _RestoreGuard,
    *,
    saved_cap: int | None,
    write: bool,
) -> dict:
    try:
        min_mw, max_mw = nvml.power_limit_constraints_mw(handle)
        default_mw = nvml.power_default_limit_mw(handle)
    except NvmlError as exc:
        return {
            "ok": False,
            "min_w": None,
            "max_w": None,
            "default_w": None,
            "error": str(exc),
        }

    result: dict = {
        "ok": False,
        "min_w": _watts(min_mw),
        "max_w": _watts(max_mw),
        "default_w": _watts(default_mw),
    }
    if not write:
        result["error"] = "write disabled"
        return result
    if saved_cap is None:
        result["error"] = "power limit unreadable"
        return result

    trial = _trial_limit_mw(saved_cap, min_mw, max_mw)
    # Mark dirty before the write so SIGTERM during the call still restores.
    guard.mark_cap_dirty()
    ok = False
    error: str | None = None
    try:
        try:
            nvml.set_power_limit_mw(handle, trial)
            readback = nvml.power_limit_mw(handle)
            ok = readback == trial
            if not ok:
                error = "power cap readback mismatch"
        except NvmlError as exc:
            ok = False
            error = str(exc)
    finally:
        guard.restore_cap()

    try:
        restored = nvml.power_limit_mw(handle)
    except NvmlError as exc:
        ok = False
        error = str(exc)
    else:
        if restored != saved_cap:
            ok = False
            error = "failed to restore power cap"
    result["ok"] = ok
    if not ok:
        result["error"] = error or "power cap write failed"
    return result


def _check_clock_lock(nvml, handle, guard: _RestoreGuard, *, write: bool) -> dict:
    if not write:
        return {"ok": False, "error": "write disabled"}
    try:
        sm = nvml.clock_mhz(handle, CLOCK_SM)
    except NvmlError as exc:
        return {"ok": False, "error": str(exc)}

    guard.mark_clocks_dirty()
    ok = False
    error: str | None = None
    try:
        nvml.set_locked_clocks(handle, sm, sm)
        ok = True
    except NvmlError as exc:
        ok = False
        error = str(exc)
    finally:
        guard.restore_clocks()
    if not ok:
        return {"ok": False, "error": error or "clock lock failed"}
    return {"ok": True}


def _trial_limit_mw(current: int, min_mw: int, max_mw: int) -> int:
    """A limit inside the allowed range, one watt away from `current` when possible."""
    step = 1000
    lowered = current - step
    if min_mw <= lowered <= max_mw and lowered != current:
        return lowered
    raised = current + step
    if min_mw <= raised <= max_mw and raised != current:
        return raised
    if min_mw <= current <= max_mw:
        return current
    return min(max(current, min_mw), max_mw)


def _watts(mw: int) -> int | float:
    if mw % 1000 == 0:
        return mw // 1000
    return mw / 1000


def _read_text(fn: Callable[[], object], default: str) -> str:
    try:
        value = fn()
    except NvmlError:
        return default
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _read_cuda(nvml) -> str:
    try:
        return cuda_version_string(int(nvml.cuda_driver_version()))
    except NvmlError:
        return "unknown"
    except (TypeError, ValueError):
        return "unknown"


def _format_now(now: Callable[[], datetime] | None) -> str:
    moment = now() if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _RestoreGuard:
    """Puts back a power cap and a clock lock on the way out.

    Python does not run `finally` when SIGTERM kills the process, so the
    handler restores and then re-arms the default terminate action.
    """

    def __init__(self, nvml, handle, saved_cap: int | None) -> None:
        self.nvml = nvml
        self.handle = handle
        self._saved_cap = saved_cap
        self._cap_dirty = False
        self._clocks_dirty = False
        self._in_restore = False
        self._prev_handler = None
        self._signal_installed = False
        self._atexit_cb: Callable[[], None] | None = None

    def arm(self) -> None:
        self._atexit_cb = self.restore
        atexit.register(self._atexit_cb)
        self._prev_handler = signal.getsignal(signal.SIGTERM)
        try:
            signal.signal(signal.SIGTERM, self._on_term)
            self._signal_installed = True
        except ValueError:
            # signal.signal only works on the main thread.
            self._signal_installed = False

    def mark_cap_dirty(self) -> None:
        self._cap_dirty = True

    def mark_clocks_dirty(self) -> None:
        self._clocks_dirty = True

    def restore(self) -> None:
        if self._in_restore:
            return
        self._in_restore = True
        try:
            self.restore_cap()
            self.restore_clocks()
        finally:
            self._in_restore = False

    def restore_cap(self) -> None:
        if not self._cap_dirty or self._saved_cap is None:
            return
        try:
            self.nvml.set_power_limit_mw(self.handle, self._saved_cap)
        except NvmlError:
            return
        try:
            current = self.nvml.power_limit_mw(self.handle)
        except NvmlError:
            return
        if current == self._saved_cap:
            self._cap_dirty = False

    def restore_clocks(self) -> None:
        if not self._clocks_dirty:
            return
        try:
            self.nvml.reset_locked_clocks(self.handle)
        except NvmlError:
            return
        self._clocks_dirty = False

    def close(self) -> None:
        try:
            self.restore()
        finally:
            if self._signal_installed:
                signal.signal(signal.SIGTERM, self._prev_handler)
                self._signal_installed = False
            if self._atexit_cb is not None:
                atexit.unregister(self._atexit_cb)
                self._atexit_cb = None

    def _on_term(self, signum: int, frame) -> None:
        self.restore()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)


def _real_nvml_blocked() -> bool:
    """True when this process must not touch a real GPU."""
    if os.environ.get("WATTNOT_ALLOW_NVML") == "1":
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return "pytest" in sys.modules


def open_nvml() -> NvmlLib:
    """Open the driver library. Refuses while tests are running."""
    if _real_nvml_blocked():
        raise NvmlUnavailable("real NVML is disabled while tests are running")
    return load_nvml(None)


def load_nvml(path: str | None) -> NvmlLib:
    """Load NVML from `path`, or from the usual driver library names.

    An explicit path that is not a file raises before any library search, so a
    bad path cannot fall through onto a real driver.
    """
    if path is not None:
        if not os.path.isfile(path):
            raise NvmlUnavailable(f"NVML library not found at {path}")
        candidates = [path]
    else:
        candidates = ["libnvidia-ml.so.1", "libnvidia-ml.so"]

    errors: list[str] = []
    for candidate in candidates:
        try:
            lib = ctypes.CDLL(candidate)
        except OSError as exc:
            errors.append(str(exc))
            continue
        try:
            return NvmlLib(lib)
        except NvmlError as exc:
            raise NvmlUnavailable(str(exc)) from exc
    detail = "; ".join(errors) if errors else "no candidates"
    raise NvmlUnavailable(f"NVML library not found: {detail}")


class NvmlLib:
    """Thin ctypes binding over libnvidia-ml. Talks only to the local driver."""

    def __init__(self, lib: ctypes.CDLL) -> None:
        self._lib = lib
        self._handles: list[ctypes.c_void_p] = []
        self._init_driver()

    def shutdown(self) -> None:
        fn = getattr(self._lib, "nvmlShutdown", None)
        if fn is None:
            return
        fn.argtypes = []
        fn.restype = ctypes.c_int
        fn()

    def device_count(self) -> int:
        count = ctypes.c_uint()
        fn = self._fn(
            "nvmlDeviceGetCount_v2",
            [ctypes.POINTER(ctypes.c_uint)],
            ctypes.c_int,
            "nvmlDeviceGetCount",
        )
        self._check(fn(ctypes.byref(count)))
        return int(count.value)

    def device_handle(self, index: int):
        handle = ctypes.c_void_p()
        fn = self._fn(
            "nvmlDeviceGetHandleByIndex_v2",
            [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.c_int,
            "nvmlDeviceGetHandleByIndex",
        )
        self._check(fn(ctypes.c_uint(index), ctypes.byref(handle)))
        self._handles.append(handle)
        return handle

    def device_name(self, handle) -> str:
        buf = ctypes.create_string_buffer(96)
        fn = self._fn(
            "nvmlDeviceGetName",
            [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint],
            ctypes.c_int,
        )
        self._check(fn(handle, buf, len(buf)))
        return buf.value.decode()

    def driver_version(self) -> str:
        buf = ctypes.create_string_buffer(80)
        fn = self._fn(
            "nvmlSystemGetDriverVersion",
            [ctypes.c_char_p, ctypes.c_uint],
            ctypes.c_int,
        )
        self._check(fn(buf, len(buf)))
        return buf.value.decode()

    def cuda_driver_version(self) -> int:
        version = ctypes.c_int()
        fn = self._fn(
            "nvmlSystemGetCudaDriverVersion_v2",
            [ctypes.POINTER(ctypes.c_int)],
            ctypes.c_int,
            "nvmlSystemGetCudaDriverVersion",
        )
        self._check(fn(ctypes.byref(version)))
        return int(version.value)

    def power_usage_mw(self, handle) -> int:
        power = ctypes.c_uint()
        fn = self._fn(
            "nvmlDeviceGetPowerUsage",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
            ctypes.c_int,
        )
        self._check(fn(handle, ctypes.byref(power)))
        return int(power.value)

    def total_energy_mj(self, handle) -> int:
        energy = ctypes.c_ulonglong()
        fn = self._fn(
            "nvmlDeviceGetTotalEnergyConsumption",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulonglong)],
            ctypes.c_int,
        )
        self._check(fn(handle, ctypes.byref(energy)))
        return int(energy.value)

    def clock_mhz(self, handle, clock_type: int) -> int:
        mhz = ctypes.c_uint()
        fn = self._fn(
            "nvmlDeviceGetClockInfo",
            [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)],
            ctypes.c_int,
        )
        self._check(fn(handle, ctypes.c_int(clock_type), ctypes.byref(mhz)))
        return int(mhz.value)

    def temperature_c(self, handle) -> int:
        temp = ctypes.c_uint()
        fn = self._fn(
            "nvmlDeviceGetTemperature",
            [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)],
            ctypes.c_int,
        )
        # NVML_TEMPERATURE_GPU == 0.
        self._check(fn(handle, ctypes.c_int(0), ctypes.byref(temp)))
        return int(temp.value)

    def power_limit_constraints_mw(self, handle) -> tuple[int, int]:
        low = ctypes.c_uint()
        high = ctypes.c_uint()
        fn = self._fn(
            "nvmlDeviceGetPowerManagementLimitConstraints",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)],
            ctypes.c_int,
        )
        self._check(fn(handle, ctypes.byref(low), ctypes.byref(high)))
        return int(low.value), int(high.value)

    def power_limit_mw(self, handle) -> int:
        limit = ctypes.c_uint()
        fn = self._fn(
            "nvmlDeviceGetPowerManagementLimit",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
            ctypes.c_int,
        )
        self._check(fn(handle, ctypes.byref(limit)))
        return int(limit.value)

    def power_default_limit_mw(self, handle) -> int:
        limit = ctypes.c_uint()
        fn = self._fn(
            "nvmlDeviceGetPowerManagementDefaultLimit",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
            ctypes.c_int,
        )
        self._check(fn(handle, ctypes.byref(limit)))
        return int(limit.value)

    def set_power_limit_mw(self, handle, limit_mw: int) -> None:
        fn = self._fn(
            "nvmlDeviceSetPowerManagementLimit",
            [ctypes.c_void_p, ctypes.c_uint],
            ctypes.c_int,
        )
        self._check(fn(handle, ctypes.c_uint(limit_mw)))

    def set_locked_clocks(self, handle, min_mhz: int, max_mhz: int) -> None:
        fn = self._fn(
            "nvmlDeviceSetGpuLockedClocks",
            [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint],
            ctypes.c_int,
        )
        self._check(fn(handle, ctypes.c_uint(min_mhz), ctypes.c_uint(max_mhz)))

    def reset_locked_clocks(self, handle) -> None:
        fn = self._fn(
            "nvmlDeviceResetGpuLockedClocks",
            [ctypes.c_void_p],
            ctypes.c_int,
        )
        self._check(fn(handle))

    def _init_driver(self) -> None:
        fn = getattr(self._lib, "nvmlInit_v2", None) or getattr(self._lib, "nvmlInit", None)
        if fn is None:
            raise NvmlError("Not Supported")
        fn.argtypes = []
        fn.restype = ctypes.c_int
        self._check(fn())

    def _check(self, code: int) -> None:
        if code == 0:
            return
        raise NvmlError(self._error_text(code))

    def _error_text(self, code: int) -> str:
        fn = getattr(self._lib, "nvmlErrorString", None)
        if fn is not None:
            fn.argtypes = [ctypes.c_int]
            fn.restype = ctypes.c_char_p
            raw = fn(code)
            if raw:
                return raw.decode()
        return _NVML_ERROR_NAMES.get(code, f"NVML error {code}")

    def _fn(self, name: str, argtypes, restype, fallback: str | None = None):
        fn = getattr(self._lib, name, None)
        if fn is None and fallback is not None:
            fn = getattr(self._lib, fallback, None)
        if fn is None:
            raise NvmlError("Not Supported")
        fn.argtypes = argtypes
        fn.restype = restype
        return fn


def main(argv: Sequence[str] | None = None, *, backend=None) -> int:
    """CLI entry. `backend` is for tests; the real driver is opened otherwise."""
    from wattnot.approvals import NotApproved

    parser = argparse_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if backend is None:
        try:
            backend = open_nvml()
        except NvmlUnavailable:
            print("probe: no GPU or NVML is missing", file=sys.stderr)
            return 2

    code = 0
    caps = None
    session = None
    try:
        try:
            if not args.no_write:  # the write test changes the power cap: ask first
                session = _open_session(backend, args)
            caps = probe(backend, write=not args.no_write)
        except NoGpu:
            print("probe: no GPU or NVML is missing", file=sys.stderr)
            code = 2
        except NotApproved as exc:
            print(f"probe: not approved, nothing was changed: {exc}", file=sys.stderr)
            code = 50
        finally:
            if session is not None:
                session.__exit__(None, None, None)
        if caps is not None:
            payload = json.dumps(caps, indent=2) + "\n"
            if args.out:
                Path(args.out).write_text(payload, encoding="utf-8")
            sys.stdout.write(payload)
    finally:
        try:
            backend.shutdown()
        except Exception:
            # Shutdown must not hide the probe result or skip process exit.
            pass
    return code


def _open_session(backend, args):
    """Ask for permission to run the cap write test (it lowers the cap, then puts it back)."""
    from wattnot.approvals import ApprovalGate, Approvals

    try:
        lo, hi = backend.power_limit_constraints_mw(backend.device_handle(0))
        cap = {"min": lo // 1000, "max": hi // 1000}
    except Exception:  # noqa: BLE001 - no GPU: probe() reports that itself
        cap = {"min": 0, "max": 0}
    gate = ApprovalGate(
        Approvals(args.proposals_dir),
        mode="pre_approved" if args.pre_approved else "ask", name=args.pre_approved or "",
        wait_s=args.approval_wait_s)
    session = gate.session(
        title="Probe: test writing the GPU power cap, then put it back",
        bounds={"power_cap_w": cap, "gpus": [0]}, source={"tool": "wattnot.probe"},
        ttl_s=1800, reason="check that the cap can be written and restored")
    return session.__enter__()


def argparse_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m wattnot.probe",
        description="Check which GPU power and clock controls this host allows.",
    )
    parser.add_argument("--out", metavar="PATH", help="write capabilities JSON to this path")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="read-only check; do not change power caps or clocks",
    )
    parser.add_argument(
        "--pre-approved", metavar="NAME", default=None,
        help="a person approved the cap write test in advance; NAME goes in the audit trail "
             "(without it the test waits for `wattnot proposals approve`)")
    parser.add_argument("--approval-wait-s", type=float, default=600.0,
                        help="how long to wait for an approval, then exit 50 (default 600)")
    parser.add_argument("--proposals-dir", default=None,
                        help="where proposals and the audit trail live "
                             "(default $WATTNOT_PROPOSALS_DIR or ./proposals)")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
