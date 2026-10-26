"""Trial runner tests: fake OpenAI-compatible server, fake meter, no GPU."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from wattnot.events.emitter import Emitter
from wattnot.meter.base import Window
from wattnot.runner import OpenAIClient, TrialParams, percentile, run_trial, server_args
from wattnot.runner.vllm_server import Completion
from wattnot.runner.workload import Workload, WorkloadItem, load_muse_code_set, load_replay_set

RUN_ID = "run_20261027_000000_abcdef"


def _workload(n=4):
    items = tuple(WorkloadItem(f"p{i}", f"prompt {i}", 32) for i in range(n))
    return Workload("fake-v1", "replay", items, "v1")


class ScriptedClient:
    """Latency = 0.1 * (index of item + 1); tokens = 10 * (index + 1)."""

    def __init__(self):
        self.calls: list[str] = []

    def complete(self, item):
        self.calls.append(item.id)
        k = int(item.id[1:]) + 1
        return Completion(item.id, 0.1 * k, 10 * k, 5)


class FakeMeter:
    def __init__(self, wh=2.0):
        self.wh = wh
        self.labels: list[str] = []
        self.open = False

    def start(self, label):
        assert not self.open
        self.open = True
        self.labels.append(label)

    def stop(self):
        self.open = False
        return Window(self.wh, 10.0, self.wh * 360, f"{self.labels[-1]}.csv",
                      f"{len(self.labels):064x}", "nvml", "MEASURED", "counter")

    def idle(self, seconds=60):
        return 70.0


def test_percentile_matches_linear_interpolation():
    data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile(data, 50) == pytest.approx(5.5)
    assert percentile(data, 99) == pytest.approx(9.91)
    assert percentile([3.0], 95) == 3.0
    with pytest.raises(ValueError):
        percentile([], 50)


def test_warmup_discarded_and_five_reps():
    client, meter = ScriptedClient(), FakeMeter()
    res = run_trial(TrialParams(), _workload(4), meter, client, trial_id="base")
    assert len(client.calls) == 20 + 5 * 4
    assert meter.labels == [f"base-rep{i}" for i in range(1, 6)]
    assert len(res.reps) == 5 and res.warmup_discarded == 20
    assert all(r.requests == 4 for r in res.reps)
    assert res.wh_per_request == pytest.approx(0.5)
    assert res.wh_per_1k_tokens == pytest.approx(10.0 / 500 * 1000)
    lats = [0.1, 0.2, 0.3, 0.4] * 5
    assert res.p99_s == pytest.approx(percentile(lats, 99))
    assert res.p50_s == pytest.approx(0.25)


def test_event_order(tmp_path):
    with Emitter(RUN_ID, root=tmp_path) as em:
        run_trial(TrialParams(quantization="fp8"), _workload(2), FakeMeter(), ScriptedClient(),
                  reps=3, emit=em, trial_id="fp8")
    log = tmp_path / "runs" / RUN_ID / "events.jsonl"
    events = [json.loads(x) for x in log.read_text().splitlines()]
    assert [e["type"] for e in events] == (
        ["step.started"] + ["step.progress"] * 3 + ["trial.measured", "step.finished"])
    assert [e["seq"] for e in events] == list(range(6))
    assert events[4]["data"]["params"]["quantization"] == "fp8"


def test_error_event_and_meter_closed(tmp_path):
    class Boom(ScriptedClient):
        def complete(self, item):
            if len(self.calls) == 21:
                raise RuntimeError("server died")
            return super().complete(item)

    meter = FakeMeter()
    with Emitter(RUN_ID, root=tmp_path) as em, pytest.raises(RuntimeError):
        run_trial(TrialParams(), _workload(2), meter, Boom(), emit=em)
    assert not meter.open
    last = json.loads((tmp_path / "runs" / RUN_ID / "events.jsonl").read_text().splitlines()[-1])
    assert last["type"] == "error"


def test_params_validation_and_server_args():
    with pytest.raises(ValueError):
        TrialParams(quantization="int3")
    args = server_args("m", TrialParams(quantization="fp8", enable_prefix_caching=False))
    assert args[:3] == ["vllm", "serve", "m"]
    assert "--quantization" in args and "fp8" in args
    assert "--no-enable-prefix-caching" in args


def test_committed_workloads_run():
    for wl in (load_replay_set(), load_muse_code_set()):
        res = run_trial(TrialParams(), wl, FakeMeter(), _EchoClient(), reps=1, warmup=0)
        assert res.reps[0].requests == len(wl)


class _EchoClient:
    def complete(self, item):
        return Completion(item.id, 0.01, 8, 4)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/completions" and body["temperature"] == 0
        out = json.dumps({"choices": [{"text": "ok"}],
                          "usage": {"prompt_tokens": 3, "completion_tokens": body["max_tokens"]}})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out.encode())

    def log_message(self, *a):
        pass


def test_openai_client_against_fake_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        ticks = iter([1.0, 1.25])
        url = f"http://127.0.0.1:{srv.server_port}/v1"
        client = OpenAIClient(url, "m", clock=lambda: next(ticks))
        c = client.complete(WorkloadItem("a", "hi", 7))
        assert c.completion_tokens == 7 and c.prompt_tokens == 3
        assert c.latency_s == pytest.approx(0.25)
    finally:
        srv.shutdown()


def test_default_model_is_meta_open_weights():
    from wattnot.runner.vllm_server import DEFAULT_MODEL
    assert DEFAULT_MODEL == "meta-llama/Llama-3.1-8B-Instruct"
    args = server_args()
    assert DEFAULT_MODEL in args
    assert OpenAIClient("http://x/v1").model == DEFAULT_MODEL
