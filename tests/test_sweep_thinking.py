"""The sweep workload keeps thinking ON at the model's default effort, and stays concurrent (D).

The reasoning_effort-low / enable_thinking knobs are for the vision/spoken/probe CHECKS only (they
grade message.content and don't need the thinking). The power-cap sweep measures the model doing
real work, so it must NOT suppress reasoning — otherwise the energy/quality numbers describe a
different workload than the one shipped.
"""

from __future__ import annotations

import io
import json
import time

from wattnot.proof.client import ChatClient


def _capture_body(monkeypatch) -> dict:
    captured = {}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return _Resp(json.dumps({"choices": [{"message": {"content": "42"},
                                              "finish_reason": "stop"}],
                                 "usage": {"completion_tokens": 3}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ChatClient("http://x/v1", "m").ask("2+2?", 64)
    return captured["body"]


def test_sweep_client_does_not_suppress_reasoning(monkeypatch):
    body = _capture_body(monkeypatch)
    # no reasoning_effort, no enable_thinking: the sweep keeps thinking at the default effort
    assert "reasoning_effort" not in body
    assert "chat_template_kwargs" not in body
    assert body["temperature"] == 0 and body["max_tokens"] == 64


def test_sweep_requests_run_concurrently():
    # the rig fans requests out across a thread pool, so a cap's block does not serialise them
    import threading

    from wattnot.proof.rig import Rig

    class _Nvml:
        def __init__(self):
            self.limit_mw, self.energy_mj, self.lock = 700_000, 0, threading.Lock()

        def power_limit_constraints_mw(self, h): return 200_000, 700_000
        def power_default_limit_mw(self, h): return 700_000
        def power_limit_mw(self, h): return self.limit_mw
        def set_power_limit_mw(self, h, mw): self.limit_mw = mw
        def total_energy_mj(self, h): return self.energy_mj
        def power_usage_mw(self, h): return 400_000
        def temperature_c(self, h): return 55
        def clock_mhz(self, h, kind): return 1500

    from wattnot.proof.client import Reply
    from wattnot.runner.workload import WorkloadItem

    nvml = _Nvml()
    inflight, peak = {"n": 0}, {"n": 0}
    lock = threading.Lock()

    class _SlowClient:
        def ask(self, prompt, max_tokens):
            with lock:
                inflight["n"] += 1
                peak["n"] = max(peak["n"], inflight["n"])
            time.sleep(0.05)
            with lock:
                inflight["n"] -= 1
                nvml.energy_mj += 1000
            return Reply(0.05, 5, "ok", "stop")

    items = [WorkloadItem(id=f"q{i}", prompt=f"p{i}", max_tokens=16) for i in range(8)]
    rig = Rig(nvml, object(), _SlowClient(), items, concurrency=8, repeat=1, settle_s=0,
              warmup=0, sleep=lambda s: None)
    rig.block(500)
    assert peak["n"] > 1   # more than one request was in flight at once
