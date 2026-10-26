"""vLLM server launch args and a small OpenAI-compatible client.

`server_args` turns a `TrialParams` into the `vllm serve` command line, so a
trial is "restart vLLM with these flags". Power cap and SM clock are not vLLM
flags; the power-cap controller (M4) applies them before the server starts.

`OpenAIClient` sends one completion and times it. It speaks the plain
`/v1/completions` API, so it works against vLLM, Baseten or a test fake.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from wattnot.runner.workload import WorkloadItem

QUANTIZATIONS = {"none", "fp8", "awq", "gptq", "bitsandbytes"}


@dataclass(frozen=True)
class TrialParams:
    max_num_seqs: int = 256
    enable_chunked_prefill: bool = True
    enable_prefix_caching: bool = True
    block_size: int = 16
    quantization: str = "none"
    gpu_mem_util: float = 0.9
    power_cap_w: int | None = None
    sm_clock_mhz: int | None = None

    def __post_init__(self) -> None:
        if self.max_num_seqs < 1:
            raise ValueError("max_num_seqs must be >= 1")
        if self.block_size not in (8, 16, 32):
            raise ValueError("block_size must be 8, 16 or 32")
        if self.quantization not in QUANTIZATIONS:
            raise ValueError(f"quantization must be one of {sorted(QUANTIZATIONS)}")
        if not 0 < self.gpu_mem_util <= 1:
            raise ValueError("gpu_mem_util must be in (0, 1]")


def server_args(model: str, params: TrialParams, *, port: int = 8000) -> list[str]:
    """The `vllm serve` argv for one trial."""

    args = [
        "vllm", "serve", model,
        "--port", str(port),
        "--max-num-seqs", str(params.max_num_seqs),
        "--block-size", str(params.block_size),
        "--gpu-memory-utilization", f"{params.gpu_mem_util:g}",
    ]
    args.append("--enable-chunked-prefill" if params.enable_chunked_prefill
                else "--no-enable-chunked-prefill")
    args.append("--enable-prefix-caching" if params.enable_prefix_caching
                else "--no-enable-prefix-caching")
    if params.quantization != "none":
        args += ["--quantization", params.quantization]
    return args


@dataclass(frozen=True)
class Completion:
    item_id: str
    latency_s: float
    completion_tokens: int
    prompt_tokens: int


class Client(Protocol):
    def complete(self, item: WorkloadItem) -> Completion: ...


class OpenAIClient:
    """Blocking `/v1/completions` client. Temperature 0 so reps are comparable."""

    def __init__(self, base_url: str, model: str, *, timeout_s: float = 120.0,
                 clock: Callable[[], float] = time.perf_counter,
                 api_key: str | None = None) -> None:
        self.url = base_url.rstrip("/") + "/completions"
        self.model = model
        self.timeout_s = timeout_s
        self._clock = clock
        self._api_key = api_key

    def complete(self, item: WorkloadItem) -> Completion:
        body = json.dumps({"model": self.model, "prompt": item.prompt,
                           "max_tokens": item.max_tokens, "temperature": 0}).encode()
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        t0 = self._clock()
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
            payload = json.loads(resp.read())
        latency = self._clock() - t0
        usage = payload.get("usage") or {}
        return Completion(item.id, latency, int(usage.get("completion_tokens", 0)),
                          int(usage.get("prompt_tokens", 0)))
