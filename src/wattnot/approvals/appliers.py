"""Appliers: the only code that actually changes something, and how to undo it.

An applier has two methods:

    apply(change) -> token      do it, return what is needed to undo it (plain JSON)
    rollback(token)             put things back

`Approvals.apply` is the only caller, and only for a proposal a human (or the explicit
pre-approved flag, or a policy the human enabled) has approved. Three are built in:

* `PowerCapApplier`   sets a GPU power limit and remembers the old one.
* `ConfigFileApplier` writes `active/<kind>.json` (vLLM settings, routing policy, adaptive
                      top-k setting) and keeps
                      the previous file for rollback. WattNot does not restart a running
                      server; whatever serves traffic reads this file.
* `CallbackApplier`   wraps two functions, for tests and for callers with their own apply.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from wattnot.approvals import model as m
from wattnot.approvals.store import Store


class ApplyError(RuntimeError):
    """The change could not be applied (or could not be confirmed)."""


class Applier(Protocol):
    def apply(self, proposal: Mapping) -> dict: ...

    def rollback(self, token: Mapping, proposal: Mapping) -> None: ...


class CallbackApplier:
    def __init__(self, apply: Callable[[Mapping], dict | None],
                 rollback: Callable[[Mapping], None]) -> None:
        self._apply, self._rollback = apply, rollback

    def apply(self, proposal: Mapping) -> dict:
        return dict(self._apply(proposal) or {})

    def rollback(self, token: Mapping, proposal: Mapping) -> None:
        self._rollback(token)


class PowerCapApplier:
    """`get_cap_w(gpu) -> int` and `set_cap_w(gpu, watts) -> int` are the only hardware access.

    For a real GPU build it with `from_guard` or `from_nvml`. The new limit is read back; if
    it is not what was asked for the old limit is restored and the apply fails.
    """

    def __init__(self, get_cap_w: Callable[[int], int],
                 set_cap_w: Callable[[int, int], int]) -> None:
        self.get, self.set = get_cap_w, set_cap_w

    @staticmethod
    def from_nvml(nvml) -> PowerCapApplier:
        from wattnot.control.nvml_ctl import CapGuard

        guards: dict[int, CapGuard] = {}

        def g(i: int) -> CapGuard:
            if i not in guards:
                guards[i] = CapGuard(nvml, nvml.device_handle(i), handle_sigterm=False)
            return guards[i]
        return PowerCapApplier(lambda i: g(i).cap_w(), lambda i, w: g(i).set_cap_w(w))

    def apply(self, proposal: Mapping) -> dict:
        ch = proposal["change"]
        gpu = int(ch.get("gpu", 0))
        want = ch["after"].get("power_cap_w")
        if not isinstance(want, int | float) or want <= 0:
            raise ApplyError("the proposal has no power_cap_w to set")
        before = int(self.get(gpu))
        got = int(self.set(gpu, int(want)))
        read = int(self.get(gpu))
        if read != int(want) or got != int(want):
            self.set(gpu, before)
            raise ApplyError(f"asked for {int(want)} W, the card reports {read} W; "
                             f"put it back to {before} W")
        return {"gpu": gpu, "before_w": before, "after_w": read}

    def rollback(self, token: Mapping, proposal: Mapping) -> None:
        gpu, before = int(token["gpu"]), int(token["before_w"])
        self.set(gpu, before)
        if int(self.get(gpu)) != before:
            raise ApplyError(f"rollback: card did not return to {before} W")


class ConfigFileApplier:
    """Writes the approved settings to `<root>/active/<kind>.json`."""

    def __init__(self, store: Store, kind: str) -> None:
        self.store, self.kind = store, kind
        self.rel = f"active/{kind}.json"

    @property
    def path(self) -> Path:
        return self.store.root / self.rel

    def _current(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def apply(self, proposal: Mapping) -> dict:
        before = self._current()
        doc = {"format": f"wattnot.active-{self.kind}/1", "proposal_id": proposal["id"],
               "content_sha256": proposal["content_sha256"], "label": proposal["label"],
               "config": proposal["change"]["after"]}
        if self.kind == m.VLLM_CONFIG:
            doc["vllm_args"] = _vllm_args(proposal["change"]["after"])
        self.store.write_text(self.rel, json.dumps(doc, indent=2) + "\n")
        return {"file": self.rel, "before": before}

    def rollback(self, token: Mapping, proposal: Mapping) -> None:
        before = token.get("before")
        if before is None:
            self.path.unlink(missing_ok=True)
        else:
            self.store.write_text(self.rel, json.dumps(before, indent=2) + "\n")


def _vllm_args(cfg: Mapping) -> list[str] | None:
    """The `vllm serve` flags for these settings, so a person can see exactly what runs."""
    from wattnot.runner.vllm_server import TrialParams, server_args

    fields = {k: cfg[k] for k in ("max_num_seqs", "enable_chunked_prefill",
                                  "enable_prefix_caching", "block_size", "quantization",
                                  "gpu_mem_util") if k in cfg}
    try:
        return server_args(str(cfg.get("model", "MODEL")), TrialParams(**fields))[3:]
    except (TypeError, ValueError):
        return None


def standard(store: Store) -> dict[str, Applier]:
    """The appliers that need no hardware handle."""
    return {m.VLLM_CONFIG: ConfigFileApplier(store, m.VLLM_CONFIG),
            m.CLOUD_POLICY: ConfigFileApplier(store, m.CLOUD_POLICY),
            m.MOE_ROUTING: ConfigFileApplier(store, m.MOE_ROUTING)}


class _FlexRestore:
    """A flex shed is run by the session that owns the GPU. Rolling one back only means making
    sure the card is back at the cap it had before the event."""

    def __init__(self, set_cap_w: Callable[[int, int], int]) -> None:
        self.set = set_cap_w

    def apply(self, proposal: Mapping) -> dict:
        raise ApplyError("a flex shed is run by `wattnot flex run`, not applied from here")

    def rollback(self, token: Mapping, proposal: Mapping) -> None:
        if token.get("restored_cap_w") is not None:
            self.set(int(token.get("gpu", 0)), int(token["restored_cap_w"]))


def nvml_appliers(nvml) -> dict[str, Applier]:
    """Appliers for a GPU host: power cap set / rollback, flex rollback."""
    cap = PowerCapApplier.from_nvml(nvml)
    return {m.POWER_CAP: cap, m.FLEX_SHED: _FlexRestore(cap.set)}


class NvidiaSmiApplier:
    """Set a GPU power limit with the `nvidia-smi` command (for kind power_cap).

    `run` is `subprocess.run` unless a test passes a fake. The new limit is read
    back; if it is not what was asked for the old limit is restored and the
    apply fails. Never construct this in replay or demo code: use
    `MockPowerCapApplier` there.
    """

    def __init__(self, run: Callable = subprocess.run) -> None:
        self._run = run

    def _query(self, gpu: int) -> int:
        try:
            out = self._run(
                ["nvidia-smi", "-i", str(gpu), "--query-gpu=power.limit",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            raise ApplyError(f"could not read the power limit on GPU {gpu}: {e}") from e
        try:
            return int(float(str(out.stdout).strip().split()[0]))
        except (ValueError, IndexError) as e:
            raise ApplyError(f"could not parse the power limit on GPU {gpu}: {e}") from e

    def _set(self, gpu: int, watts: int) -> None:
        try:
            self._run(["nvidia-smi", "-i", str(gpu), "-pl", str(int(watts))],
                      capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            raise ApplyError(f"could not set the power limit on GPU {gpu}: {e}") from e

    def apply(self, proposal: Mapping) -> dict:
        ch = proposal["change"]
        gpu = int(ch.get("gpu", 0))
        want = ch["after"].get("power_cap_w")
        if not isinstance(want, int | float) or want <= 0:
            raise ApplyError("the proposal has no power_cap_w to set")
        before = self._query(gpu)
        self._set(gpu, int(want))
        read = self._query(gpu)
        if read != int(want):
            self._set(gpu, before)
            raise ApplyError(f"asked for {int(want)} W, the card reports {read} W; "
                             f"put it back to {before} W")
        return {"gpu": gpu, "before_w": before, "after_w": read}

    def rollback(self, token: Mapping, proposal: Mapping) -> None:
        gpu, before = int(token["gpu"]), int(token["before_w"])
        self._set(gpu, before)
        if self._query(gpu) != before:
            raise ApplyError(f"rollback: card did not return to {before} W")


class MockPowerCapApplier:
    """An in-memory power cap for demos and tests (MOCK). Same token shape as the
    real appliers, starts at the stock value you pass in, never touches hardware."""

    def __init__(self, stock_w: int = 700) -> None:
        self.caps: dict[int, int] = {}
        self.stock_w = int(stock_w)

    def _read(self, gpu: int) -> int:
        return self.caps.get(gpu, self.stock_w)

    def apply(self, proposal: Mapping) -> dict:
        ch = proposal["change"]
        gpu = int(ch.get("gpu", 0))
        want = ch["after"].get("power_cap_w")
        if not isinstance(want, int | float) or want <= 0:
            raise ApplyError("the proposal has no power_cap_w to set")
        before = self._read(gpu)
        self.caps[gpu] = int(want)
        return {"gpu": gpu, "before_w": before, "after_w": int(want)}

    def rollback(self, token: Mapping, proposal: Mapping) -> None:
        self.caps[int(token["gpu"])] = int(token["before_w"])


def demo_appliers(stock_w: int = 700) -> dict[str, Applier]:
    """Appliers for demos: a mock cap, a flex rollback that restores nothing.

    The caller merges this with `standard(store)` for the config-file kinds."""
    return {m.POWER_CAP: MockPowerCapApplier(stock_w),
            m.FLEX_SHED: _FlexRestore(lambda gpu, watts: int(watts))}
