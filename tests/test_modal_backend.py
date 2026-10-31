"""Offline unit tests for the real ModalBackend and the glimmer_live cost plan.

No network, no modal SDK, no GPU: the completions/energy transports are injected fakes and the
modal app's estimate() cost math is called as a pure function. Nothing here deploys or spends.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wattnot.headline import ModalBackend
from wattnot.runner.workload import WorkloadItem

REPO = Path(__file__).resolve().parents[1]
GLIMMER = REPO / "modal" / "glimmer_live.py"


def _item(item_id="q1", prompt="hello", max_tokens=32):
    return WorkloadItem(id=item_id, prompt=prompt, max_tokens=max_tokens)


# ---- client ---------------------------------------------------------------------------------

def test_client_parses_completion_with_token_counts():
    captured = {}

    def fake_post(url, body, timeout_s):
        captured["url"], captured["body"] = url, body
        return {"choices": [{"text": "an answer"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7}}

    be = ModalBackend(base_url="https://glimmer.modal.run/v1", transport=fake_post)
    comp = be.client(None).complete(_item("q1", "a question", 64))

    assert comp.item_id == "q1"
    assert comp.prompt_tokens == 11
    assert comp.completion_tokens == 7
    assert comp.text == "an answer"
    assert comp.latency_s > 0
    assert captured["url"].endswith("/completions")
    assert captured["body"]["model"] and captured["body"]["temperature"] == 0
    assert captured["body"]["max_tokens"] == 64


def test_client_latency_is_measured_from_clock():
    # inject a monotonic-ish clock so latency is deterministic and positive
    ticks = iter([100.0, 100.5])
    be = ModalBackend(base_url="http://x/v1", transport=lambda *a: {"usage": {}, "choices": []})
    client = be.client(None)
    client._clock = lambda: next(ticks)
    comp = client.complete(_item())
    assert comp.latency_s == pytest.approx(0.5)
    assert comp.prompt_tokens == 0 and comp.completion_tokens == 0


# ---- meter ----------------------------------------------------------------------------------

def test_meter_window_measured_when_counter_advances():
    # counter advances by 7_200_000 mJ == 2.0 Wh over a 10 s window
    energies = iter([1_000_000, 1_000_000 + 7_200_000])
    clocks = iter([0.0, 10.0])  # start t0 (then reads energy), stop reads t1

    def fake_get(url, timeout_s):
        return {"energy_mj": next(energies)}

    be = ModalBackend(base_url="http://x/v1", energy_transport=fake_get)
    meter = be.meter(None)
    meter._clock = lambda: next(clocks)
    meter.start("baseline-rep0")
    win = meter.stop()

    assert win.label == "MEASURED"
    assert win.method == "counter"
    assert win.source == "nvml"
    assert win.wh == pytest.approx(2.0)
    assert win.seconds == pytest.approx(10.0)
    # mean_w = wh * 3600 / seconds = 2.0 * 3600 / 10 = 720 W
    assert win.mean_w == pytest.approx(720.0)
    assert win.csv_path.endswith("baseline-rep0.csv")


def test_meter_window_estimate_when_counter_flat():
    def fake_get(url, timeout_s):
        return {"energy_mj": 5_000_000}  # never moves

    be = ModalBackend(base_url="http://x/v1", energy_transport=fake_get)
    win = _run_window(be)
    assert win.label == "ESTIMATE"
    assert win.method == "trapezoid"
    assert win.wh == 0.0


def _run_window(be):
    meter = be.meter(None)
    meter.start("w")
    return meter.stop()


def test_meter_idle_returns_watts_from_counter_delta():
    # energy jumps 300_000 mJ == 300 J over ~1 s -> ~300 W
    energies = iter([0, 300_000])
    clocks = iter([0.0, 1.0])

    def fake_get(url, timeout_s):
        return {"energy_mj": next(energies)}

    be = ModalBackend(base_url="http://x/v1", energy_transport=fake_get)
    meter = be.meter(None)
    meter._clock = lambda: next(clocks)
    watts = meter.idle(seconds=0)  # seconds=0 keeps the sleep at ~0
    assert watts == pytest.approx(300.0, rel=1e-6)


# ---- identity fields + outputs --------------------------------------------------------------

def test_backend_identity_fields():
    be = ModalBackend(base_url="http://x/v1")
    assert be.provider == "modal"
    assert be.driver == "vllm"
    assert be.mode == "container"
    assert be.meter_name == "nvml"
    assert "Modal" in be.gpu


def test_outputs_all_ok():
    be = ModalBackend(base_url="http://x/v1")
    items = [_item("a"), _item("b")]
    assert be.outputs(items) == {"a": "ok", "b": "ok"}
    assert be.outputs(items, params=None) == {"a": "ok", "b": "ok"}


def test_missing_url_raises_clear_error_not_notimplemented():
    be = ModalBackend(base_url=None)
    with pytest.raises(RuntimeError) as ei:
        be.client(None)
    assert "base URL" in str(ei.value)


# ---- the glimmer_live modal app: it parses, and its estimate stays <= $5 --------------------

def test_glimmer_live_parses():
    ast.parse(GLIMMER.read_text(encoding="utf-8"))


def _load_glimmer():
    import importlib.util
    spec = importlib.util.spec_from_file_location("glimmer_live", GLIMMER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_glimmer_estimate_default_plan_within_budget():
    mod = _load_glimmer()
    plan = mod.estimate_plan()  # default A100-80GB
    assert plan["gpu"] == "A100-80GB"
    # prefetch: 0.50 $/hr * 25 min / 60 = 0.2083; bench: 2.75 $/hr * 20 min / 60 = 0.9167
    assert plan["prefetch"]["usd"] == pytest.approx(0.2083, abs=1e-3)
    assert plan["bench"]["usd"] == pytest.approx(0.9167, abs=1e-3)
    assert plan["total_usd"] == pytest.approx(1.125, abs=1e-3)
    assert plan["total_usd"] <= 5.0
    assert plan["within_budget"] is True


def test_glimmer_estimate_h100_also_within_budget():
    mod = _load_glimmer()
    plan = mod.estimate_plan("H100")
    # bench: 4.50 $/hr * 20 min / 60 = 1.50 ; + prefetch 0.2083 = 1.7083
    assert plan["total_usd"] == pytest.approx(1.7083, abs=1e-3)
    assert plan["total_usd"] <= 5.0


def test_glimmer_estimate_hard_stop_refuses_over_budget():
    mod = _load_glimmer()
    # a wildly long bench would blow the cap -> SystemExit
    with pytest.raises(SystemExit):
        mod.estimate_hard_stop("H100", bench_min=100.0)


def test_preflight_refuses_unservable_custom_architecture():
    mod = _load_glimmer()
    # The real Muse Glimmer 30B config: a custom multimodal architecture vLLM cannot load.
    glimmer = {"architectures": ["MuseGlimmerForConditionalGeneration"],
               "model_type": "muse_glimmer", "image_token_id": 200092,
               "vision_config": {"model_type": "muse_glimmer_vision"}}
    pf = mod.preflight_servable(glimmer)
    assert pf["ok"] is False and pf["multimodal"] is True
    assert "muse_glimmer" in pf["model_type"]


def test_preflight_accepts_supported_text_model():
    mod = _load_glimmer()
    pf = mod.preflight_servable({"architectures": ["LlamaForCausalLM"], "model_type": "llama"})
    assert pf["ok"] is True and pf["multimodal"] is False


def test_glimmer_measure_computes_measured_joules_and_tokens_per_s():
    mod = _load_glimmer()

    class _Gen:
        def __init__(self, text, n_tok):
            self.text = text
            self.token_ids = list(range(n_tok))

    class _Out:
        def __init__(self, n_tok, n_prompt):
            self.outputs = [_Gen("x", n_tok)]
            self.prompt_token_ids = list(range(n_prompt))

    class _LLM:
        def generate(self, prompts, sampling):
            return [_Out(n_tok=10, n_prompt=5)]

    # energy counter advances 2000 mJ == 2.0 J per request
    energies = iter([0, 2000, 2000, 4000])

    class _Nvml:
        def total_energy_mj(self, h):
            return next(energies)

    clocks = iter([0.0, 0.5, 0.5, 1.0])  # 0.5 s latency per request
    prompts = [{"id": "p0", "prompt": "a", "max_tokens": 16, "prompt_source": "SYNTHETIC"},
               {"id": "p1", "prompt": "b", "max_tokens": 16, "prompt_source": "SYNTHETIC"}]
    res = mod.measure(_LLM(), object(), prompts, _Nvml(), handle=0,
                      clock=lambda: next(clocks))
    assert res["n_requests"] == 2
    assert res["mean_j_per_request"] == pytest.approx(2.0)
    # 20 completion tokens over 1.0 s total latency = 20 tok/s
    assert res["tokens_per_s"] == pytest.approx(20.0)
    assert res["energy_label"] == "MEASURED"
    assert res["energy_method"] == "counter"
    assert all(r["j_measured"] == pytest.approx(2.0) for r in res["per_request"])


def test_glimmer_build_prompts_deterministic_and_labelled():
    mod = _load_glimmer()
    prompts = mod.build_prompts(5)
    assert len(prompts) == 5
    assert all(set(p) >= {"id", "prompt", "max_tokens", "prompt_source"} for p in prompts)
    assert all(p["prompt_source"] in ("replay-v1", "SYNTHETIC") for p in prompts)
