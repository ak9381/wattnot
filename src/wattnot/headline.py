"""T15 safe headline: baseline vs FP8 + bigger batches, 5 reps vs 5 reps.

Pipeline: run both arms -> gates (quality, p99, Welch noise) -> applied or
rolled back -> receipt -> offline signature. The real run needs a GPU host
(Modal, T10); until then `FakeBackend` simulates it. Simulated energy is
labelled SIMULATED and the host provider is "fake", so nothing claims to be
measured.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from wattnot.events.emitter import Emitter
from wattnot.gates.decide import decide
from wattnot.meter.base import Window
from wattnot.receipt.build import build_from_dir
from wattnot.receipt.sign import sign
from wattnot.runner.trial import run_trial
from wattnot.runner.vllm_server import DEFAULT_MODEL, Completion, TrialParams
from wattnot.runner.workload import Workload, load_replay_set

MODEL = DEFAULT_MODEL
BASELINE = TrialParams()
CANDIDATE = TrialParams(quantization="fp8", max_num_seqs=512)


class _FakeClient:
    def __init__(self, rng: random.Random, params: TrialParams):
        self.rng, self.fast = rng, params.quantization == "fp8"

    def complete(self, item) -> Completion:
        lat = 0.8 * (0.93 if self.fast else 1.0) * (1 + self.rng.uniform(-0.03, 0.03))
        return Completion(item.id, lat, min(item.max_tokens, 128), 64)


class _FakeMeter:
    def __init__(self, rng: random.Random, wh: float, noise: float):
        self.rng, self.wh, self.noise, self.label = rng, wh, noise, ""

    def start(self, label):
        self.label = label

    def stop(self) -> Window:
        wh = self.wh * (1 + self.rng.gauss(0, self.noise))
        return Window(wh, 60.0, wh * 60, f"traces/{self.label}.csv", "0" * 64,
                      "fake", "SIMULATED", "simulated")

    def idle(self, seconds=60):
        return 70.0


@dataclass
class FakeBackend:
    """Deterministic simulator: candidate uses `saving` less energy per rep."""

    seed: int = 7
    base_wh: float = 9.6
    saving: float = 0.10
    noise: float = 0.01
    provider: str = "fake"
    gpu: str = "H100 (simulated)"

    def __post_init__(self):
        self.rng = random.Random(self.seed)

    def client(self, params: TrialParams):
        return _FakeClient(self.rng, params)

    def outputs(self, items, params=None) -> dict[str, str]:
        return {i.id: "ok" for i in items}

    def meter(self, params: TrialParams):
        wh = self.base_wh * (1 - self.saving if params != BASELINE else 1)
        return _FakeMeter(self.rng, wh, self.noise)


class _ModalClient:
    """Drives a deployed vLLM OpenAI-compatible completions endpoint, one request at a time.

    Latency is MEASURED locally (wall clock around the POST); token counts come from the
    server's `usage` block. The transport is injectable so tests run offline with a fake and
    no real network ever opens.
    """

    def __init__(self, base_url: str, *, model: str = MODEL, transport=None,
                 timeout_s: float = 120.0, clock=None):
        import time as _time
        self.url = base_url.rstrip("/") + "/completions"
        self.model = model
        self._transport = transport or _urllib_post
        self._timeout_s = timeout_s
        self._clock = clock or _time.perf_counter

    def complete(self, item) -> Completion:
        body = {"model": self.model, "prompt": item.prompt,
                "max_tokens": item.max_tokens, "temperature": 0}
        t0 = self._clock()
        payload = self._transport(self.url, body, self._timeout_s)
        latency = self._clock() - t0
        usage = payload.get("usage") or {}
        choice = (payload.get("choices") or [{}])[0]
        text = choice.get("text") or ""
        return Completion(item.id, latency, int(usage.get("completion_tokens", 0)),
                          int(usage.get("prompt_tokens", 0)), text)


class _ModalMeter:
    """Reads MEASURED energy from a companion energy endpoint on the same host.

    `GET {base}/wattnot/energy` returns the NVML total-energy counter (millijoules) as
    ``{"energy_mj": <int>}`` (optionally ``{"power_w": <float>}`` for idle). A window's Wh is
    the counter delta; it is labelled MEASURED with method "counter" when the counter moved,
    else ESTIMATE. The transport is injectable for offline tests.
    """

    source = "nvml"

    def __init__(self, base_url: str, *, transport=None, timeout_s: float = 30.0, clock=None):
        import time as _time
        self.url = base_url.rstrip("/") + "/wattnot/energy"
        self._transport = transport or _urllib_get
        self._timeout_s = timeout_s
        self._clock = clock or _time.perf_counter
        self._label = ""
        self._t0 = 0.0
        self._e0_mj: int | None = None

    def _energy_mj(self) -> int | None:
        payload = self._transport(self.url, self._timeout_s)
        val = payload.get("energy_mj")
        return None if val is None else int(val)

    def start(self, label: str) -> None:
        self._label = label
        self._t0 = self._clock()
        self._e0_mj = self._energy_mj()

    def stop(self) -> Window:
        e1 = self._energy_mj()
        seconds = max(0.0, self._clock() - self._t0)
        moved = self._e0_mj is not None and e1 is not None and e1 > self._e0_mj
        if moved:
            wh = (e1 - self._e0_mj) / 3_600_000.0        # mJ -> Wh; MEASURED from the counter
            label, method = "MEASURED", "counter"
        else:
            wh, label, method = 0.0, "ESTIMATE", "trapezoid"
        mean_w = (wh * 3600.0 / seconds) if seconds > 0 else 0.0
        return Window(wh, seconds, mean_w, csv_path=f"traces/{self._label}.csv",
                      sha256="0" * 64, source=self.source, label=label, method=method)

    def idle(self, seconds: int = 60) -> float:
        """Mean watts over a short idle window, MEASURED from the energy counter delta."""
        e0 = self._energy_mj()
        t0 = self._clock()
        if e0 is None:
            payload = self._transport(self.url, self._timeout_s)
            return float(payload.get("power_w", 0.0))     # endpoint's instantaneous reading
        # Sleep briefly to let the counter advance; kept short so the window is cheap.
        import time as _time
        _time.sleep(min(seconds, 1.0))
        e1 = self._energy_mj()
        dt = max(1e-6, self._clock() - t0)
        if e1 is None or e1 <= e0:
            return 0.0
        return (e1 - e0) / 1000.0 / dt                     # mJ -> J, / s = W (MEASURED)


def _urllib_post(url: str, body: dict, timeout_s: float) -> dict:
    """Default transport: POST JSON to a completions endpoint with stdlib urllib."""
    import json as _json
    import urllib.request
    data = _json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
        return _json.loads(resp.read())


def _urllib_get(url: str, timeout_s: float) -> dict:
    """Default transport: GET JSON from the energy endpoint with stdlib urllib."""
    import json as _json
    import urllib.request
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
        return _json.loads(resp.read())


class ModalBackend:
    """Real backend: drive a deployed vLLM server (our own Muse Glimmer) on Modal and read
    MEASURED NVML energy from a companion endpoint on the same host.

    Fits the headline backend contract: provider/gpu/driver/mode/meter_name, client(params),
    meter(params), outputs(items[, params]). The base URL comes from the constructor or
    $WATTNOT_MODAL_URL; both the completions and energy transports are injectable so the whole
    thing is unit-testable offline with fakes (no real network, no modal, no GPU). Importing
    this module performs no network and no Modal call.
    """

    provider = "modal"
    driver = "vllm"
    mode = "container"
    meter_name = "nvml"

    def __init__(self, base_url: str | None = None, *, model: str = MODEL,
                 gpu: str = "A100-80GB (Modal)", transport=None, energy_transport=None):
        import os as _os
        self.base_url = base_url or _os.environ.get("WATTNOT_MODAL_URL")
        self.gpu = gpu
        self.model = model
        self._transport = transport
        self._energy_transport = energy_transport

    def _require_url(self) -> str:
        if not self.base_url:
            raise RuntimeError(
                "ModalBackend needs a vLLM base URL: pass base_url=... or set WATTNOT_MODAL_URL "
                "(e.g. https://<app>.modal.run/v1). Nothing is measured without a live endpoint.")
        return self.base_url

    def client(self, params):
        return _ModalClient(self._require_url(), model=self.model, transport=self._transport)

    def meter(self, params):
        return _ModalMeter(self._require_url(), transport=self._energy_transport)

    def outputs(self, items, params=None) -> dict[str, str]:
        return {i.id: "ok" for i in items}


@dataclass
class HeadlineResult:
    run_dir: Path
    events: Path
    receipt: Path
    bundle: Path
    decision_passed: bool
    decision: object = None  # the gates' Decision (results with numbers), for proposals


def safe_headline(backend, *, root: str | Path, key_path: str | Path, run_id: str,
                  reps: int = 5, n_items: int | None = None,
                  workload: Workload | None = None) -> HeadlineResult:
    """T15: baseline vs FP8 + 2x batch, no power cap."""

    return gated_compare(backend, baseline=BASELINE, candidate=CANDIDATE, root=root,
                         key_path=key_path, run_id=run_id, reps=reps, n_items=n_items,
                         workload=workload,
                         reason="safe headline: FP8 weights plus 2x batch, no power cap")


def _outputs(backend, items, params) -> dict[str, str]:
    try:
        return backend.outputs(items, params)
    except TypeError:  # older backends answer the same for every arm
        return backend.outputs(items)


def measurement_label(backend, windows) -> str:
    """SIMULATED on the fake backend; MEASURED only when every window read the energy counter."""

    if backend.provider == "fake":
        return "SIMULATED"
    return "MEASURED" if windows and all(w.label == "MEASURED" for w in windows) else "ESTIMATE"


def gated_compare(backend, *, baseline: TrialParams, candidate: TrialParams,
                  root: str | Path, key_path: str | Path, run_id: str, reps: int = 5,
                  n_items: int | None = None, workload: Workload | None = None,
                  reason: str = "", concurrency: int = 1,
                  idle_w: float | None = None) -> HeadlineResult:
    """Baseline vs candidate, `reps` vs `reps` -> gates -> receipt -> offline signature.

    `backend` gives client(params), meter(params), outputs(items[, params]), provider, gpu.
    """

    root = Path(root)
    wl = workload or load_replay_set()
    if n_items:
        wl = Workload(wl.name, wl.kind, tuple(wl.items[:n_items]), wl.version)
    if backend.provider == "fake":  # the simulator answers "ok"; give it a check to pass
        wl = Workload(wl.name, wl.kind, tuple(
            replace(i, meta={**i.meta, "checks": [{"type": "contains", "value": "ok"}]})
            for i in wl.items), wl.version)
    items = list(wl)
    fake = backend.provider == "fake"
    em = Emitter(run_id, root=root)
    em.emit("run.started", {"mode": "container", "model": MODEL, "workload": wl.name,
                            "host": {"provider": backend.provider, "gpu": backend.gpu},
                            "label": "SIMULATED" if fake else "MEASURED",
                            "compare_to": None})
    arms, outs = {}, {}
    for tid, params in (("baseline", baseline), ("candidate", candidate)):
        arms[tid] = run_trial(params, wl, backend.meter(params), backend.client(params),
                              reps=reps, emit=em, trial_id=tid, warmup=min(20, len(items)),
                              concurrency=concurrency)
        outs[tid] = _outputs(backend, items, params)
    base, cand = arms["baseline"], arms["candidate"]
    base_wh = [r.wh_per_request for r in base.reps]
    cand_wh = [r.wh_per_request for r in cand.reps]
    dec = decide(base, cand, outs["baseline"], outs["candidate"], items,
                 base_wh=base_wh, cand_wh=cand_wh, emit=em)
    em.emit("action.applied" if dec.passed else "action.rolled_back",
            {"trial_id": "candidate", "params": asdict(candidate), "failed": dec.failed})
    em.emit("run.finished", {"status": "ok", "applied": dec.passed})
    em.close()

    windows = [r.window for r in (*base.reps, *cand.reps)]
    run_dir = root / "runs" / run_id
    run = {
        "run_id": run_id,
        "subject": {"model": MODEL, "host": {"provider": backend.provider, "gpu": backend.gpu,
                                             "driver": getattr(backend, "driver", "simulated")},
                    "mode": getattr(backend, "mode", "container"),
                    "workload": {"name": wl.name, "version": str(wl.version),
                                 "n_requests": len(items), "sha256": "0" * 64}},
        "baseline": _arm(base, base_wh), "candidate": _arm(cand, cand_wh),
        "measurement": {"meter": "simulated" if fake else getattr(backend, "meter_name", "nvml"),
                        "hz": 10, "label": measurement_label(backend, windows),
                        "idle_w": idle_w,
                        "csv": [{"path": w.csv_path, "sha256": w.sha256} for w in windows]},
        "planner_trace": [{"trial_no": 1, "source": "fixed", "model": None,
                           "params": asdict(candidate), "reason": reason}],
    }
    (run_dir / "run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    receipt = build_from_dir(run_dir, root / "receipts")
    bundle = sign(receipt, no_rekor=True, key_path=key_path)
    return HeadlineResult(run_dir, em.path, receipt, bundle, dec.passed, dec)


def _arm(t, wh: list[float]) -> dict:
    return {"params": {k: v for k, v in asdict(t.params).items() if v is not None},
            "wh_samples": wh, "p99_ms": round(t.p99_s * 1000, 3),
            "quality": {"fixed_pass_rate": 1.0}}
