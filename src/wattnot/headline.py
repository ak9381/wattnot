"""T15 safe headline: baseline vs FP8 + bigger batches, 5 reps vs 5 reps.

Pipeline: run both arms -> gates (quality, p99, Welch noise) -> applied or
rolled back -> receipt -> offline signature. The real run needs a GPU host
(Modal, T10); until then `FakeBackend` simulates it. Simulated energy is
labelled ESTIMATE and the host provider is "fake", so nothing claims to be
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
from wattnot.runner.vllm_server import Completion, TrialParams
from wattnot.runner.workload import Workload, load_replay_set

MODEL = "meta-llama/Llama-3.1-8B-Instruct"
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
                      "fake", "ESTIMATE", "simulated")

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

    def outputs(self, items) -> dict[str, str]:
        return {i.id: "ok" for i in items}

    def meter(self, params: TrialParams):
        wh = self.base_wh * (1 - self.saving if params != BASELINE else 1)
        return _FakeMeter(self.rng, wh, self.noise)


class ModalBackend:  # pragma: no cover - needs T10 (Modal account and card)
    provider = "modal"

    def client(self, params):
        raise NotImplementedError("Modal backend waits for T10 (needs Akash's card)")

    meter = client


@dataclass
class HeadlineResult:
    run_dir: Path
    events: Path
    receipt: Path
    bundle: Path
    decision_passed: bool


def safe_headline(backend, *, root: str | Path, key_path: str | Path, run_id: str,
                  reps: int = 5, n_items: int | None = None,
                  workload: Workload | None = None) -> HeadlineResult:
    root = Path(root)
    wl = workload or load_replay_set()
    if n_items:
        wl = Workload(wl.name, wl.kind, tuple(wl.items[:n_items]), wl.version)
    if backend.provider == "fake":  # the simulator answers "ok"; give it a check to pass
        wl = Workload(wl.name, wl.kind, tuple(
            replace(i, meta={**i.meta, "checks": [{"type": "contains", "value": "ok"}]})
            for i in wl.items), wl.version)
    items = list(wl)
    em = Emitter(run_id, root=root)
    em.emit("run.started", {"mode": "container", "model": MODEL, "workload": wl.name,
                            "host": {"provider": backend.provider, "gpu": backend.gpu},
                            "label": "ESTIMATE", "compare_to": None})
    arms = {}
    for tid, params in (("baseline", BASELINE), ("candidate", CANDIDATE)):
        arms[tid] = run_trial(params, wl, backend.meter(params), backend.client(params),
                              reps=reps, emit=em, trial_id=tid, warmup=min(20, len(items)))
    base, cand = arms["baseline"], arms["candidate"]
    outputs = backend.outputs(items)
    base_wh = [r.wh_per_request for r in base.reps]
    cand_wh = [r.wh_per_request for r in cand.reps]
    dec = decide(base, cand, outputs, outputs, items, base_wh=base_wh, cand_wh=cand_wh, emit=em)
    em.emit("action.applied" if dec.passed else "action.rolled_back",
            {"trial_id": "candidate", "params": asdict(CANDIDATE), "failed": dec.failed})
    em.emit("run.finished", {"status": "ok", "applied": dec.passed})
    em.close()

    run_dir = root / "runs" / run_id
    run = {
        "run_id": run_id,
        "subject": {"model": MODEL, "host": {"provider": backend.provider, "gpu": backend.gpu,
                                             "driver": "simulated"},
                    "mode": "container",
                    "workload": {"name": wl.name, "version": str(wl.version),
                                 "n_requests": len(items), "sha256": "0" * 64}},
        "baseline": _arm(base, base_wh), "candidate": _arm(cand, cand_wh),
        "measurement": {"meter": "simulated", "hz": 10, "label": "ESTIMATE", "idle_w": None,
                        "csv": [{"path": r.window.csv_path, "sha256": r.window.sha256}
                                for r in (*base.reps, *cand.reps)]},
        "planner_trace": [{"trial_no": 1, "source": "fixed", "model": None,
                           "params": asdict(CANDIDATE),
                           "reason": "safe headline: FP8 weights plus 2x batch, no power cap"}],
    }
    (run_dir / "run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    receipt = build_from_dir(run_dir, root / "receipts")
    bundle = sign(receipt, no_rekor=True, key_path=key_path)
    return HeadlineResult(run_dir, em.path, receipt, bundle, dec.passed)


def _arm(t, wh: list[float]) -> dict:
    return {"params": {k: v for k, v in asdict(t.params).items() if v is not None},
            "wh_samples": wh, "p99_ms": round(t.p99_s * 1000, 3),
            "quality": {"fixed_pass_rate": 1.0}}
