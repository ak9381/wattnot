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
from urllib.parse import urlsplit

from wattnot.runner.workload import WorkloadItem

# Default served model: Meta's open-weights Muse Glimmer 30B (Apache 2.0). Fits one
# H100 80GB in bf16, and FP8 trials make sense on it. Llama 3.1 8B is the comparison
# model. See wattnot.models.
DEFAULT_MODEL = "meta-models/Muse-Glimmer-30B"

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


def server_args(model: str = DEFAULT_MODEL, params: TrialParams | None = None, *,
                port: int = 8000) -> list[str]:
    """The `vllm serve` argv for one trial."""

    params = params or TrialParams()

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
    text: str = ""


class Client(Protocol):
    def complete(self, item: WorkloadItem) -> Completion: ...


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in ("localhost", "::1") or host.startswith("127.")


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _urlopen(req: urllib.request.Request, timeout: float):
    """`urlopen` that bypasses any proxy for loopback targets.

    A local vLLM server must be reached directly; sending it through an
    egress proxy (from `http_proxy`) fails. Remote URLs use the default
    opener, so proxy settings still apply to them.
    """

    if _is_loopback(req.full_url):
        return _NO_PROXY_OPENER.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310


class OpenAIClient:
    """Blocking `/v1/completions` client. Temperature 0 so reps are comparable."""

    def __init__(self, base_url: str, model: str = DEFAULT_MODEL, *, timeout_s: float = 120.0,
                 clock: Callable[[], float] = time.perf_counter,
                 api_key: str | None = None, chat: bool = False) -> None:
        self.chat = chat
        self.url = base_url.rstrip("/") + ("/chat/completions" if chat else "/completions")
        self.model = model
        self.timeout_s = timeout_s
        self._clock = clock
        self._api_key = api_key

    def complete(self, item: WorkloadItem) -> Completion:
        msg = ({"messages": [{"role": "user", "content": item.prompt}]} if self.chat
               else {"prompt": item.prompt})
        body = json.dumps({"model": self.model, **msg,
                           "max_tokens": item.max_tokens, "temperature": 0}).encode()
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        t0 = self._clock()
        with _urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.loads(resp.read())
        latency = self._clock() - t0
        usage = payload.get("usage") or {}
        choice = (payload.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") if self.chat else choice.get("text")
        return Completion(item.id, latency, int(usage.get("completion_tokens", 0)),
                          int(usage.get("prompt_tokens", 0)), text or "")
