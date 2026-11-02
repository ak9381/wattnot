"""Cloud mode: tune the request, not the GPU, when the model runs on a hosted API.

On a hosted endpoint such as Baseten's Model API we cannot change power caps or
touch the experts. What we can change is the request: how hard the model
reasons, how long its answer may be, how the prompt is laid out. Every change
is gated like the rest of WattNot (quality replay plus p99) and written to a
receipt with MEASURED tokens, USD and latency, and an ESTIMATE for energy.
"""

from __future__ import annotations

PROVIDER = "baseten"
MODEL = "meta-models/muse-spark-open"  # verified live 29 Oct 2026 (HTTP 200); the old -1026 slug no longer answers
BASE_URL = "https://inference.baseten.co/v1"
MGMT_URL = "https://api.baseten.co"
# The env var inference actually reads (the paid workspace key). The old free hackathon key
# BASETEN_API_KEY is dead (401, 1 Nov 2026) and is never used for inference; it is only ever
# refused (wattnot.cloud.transport.read_key) so a run can never silently stand on it.
KEY_ENV = "BASETEN_METRICS_KEY"
DEAD_KEY_ENV = "BASETEN_API_KEY"   # the retired free hackathon key: refused, never read
# Inference key lookup order. On 29 Oct 2026 BASETEN_METRICS_KEY answered inference (200) and
# BASETEN_API_KEY returned 401, so the working key goes first and the old one is the fallback.
# 1 Nov 2026: inference uses ONLY the hackathon key (BASETEN_API_KEY). BASETEN_METRICS_KEY belongs
# to a personal workspace and billed it; it must never be used for inference (pinned by
# tests/test_baseten_key_lock.py). It may still read usage/prices (free management calls).
# 1 Nov 2026 (later): the hackathon key still 401s; Akash moved Baseten to his paid workspace.
# Inference now uses ONLY BASETEN_METRICS_KEY, no fallback (pinned by tests/test_baseten_key_lock.py).
INFERENCE_KEY_ENVS = ("BASETEN_METRICS_KEY",)
# A Spark call whose max_tokens is all spent on reasoning comes back with content None: that is
# a cut-off answer and is scored as a fail. Live proof: max_tokens 64 -> 61 reasoning, no answer.
JUDGE_MAX_TOKENS = 1500  # judge at low effort; pinned to the batch judge (wattnot.gates.judge). 512 still spent the whole budget reasoning on some replies


def inference_key(env=None) -> tuple[str, str]:
    """(env var name, key) for the first set inference key, or ("", ""). Never logged."""
    from wattnot.cloud.transport import read_env  # the one place the environment is read
    for name in INFERENCE_KEY_ENVS:
        val = read_env(name, env)
        if val:
            return name, val
    return "", ""

EFFORTS = ("low", "medium", "high", "xhigh")  # none/minimal are mapped to low by the API
REFERENCE = "xhigh"  # the API default, so the baseline is what an untuned caller pays

# Models that answer OpenAI-style tool calls on their Baseten Model API endpoint. The agent
# blocks (context saver, agent-waste guard) need this: without tool calling there is nothing
# to dedup or guard, so those blocks are marked skipped instead of pretending to run.
TOOL_CALLING_MODELS = frozenset({"meta-models/muse-spark-open"})


def supports_tool_calling(model: str = MODEL) -> bool:
    """True if `model` serves OpenAI tool calls on Baseten (so agent blocks can run)."""
    return model in TOOL_CALLING_MODELS


def chat_completions_url(base: str = BASE_URL) -> str:
    """`<base>/chat/completions` with exactly one `/v1`, whether or not `base` already ends in it.

    Baseten's inference host is `https://inference.baseten.co/v1`; a base that already carries
    `/v1` (or a trailing slash) must not become `/v1/v1/chat/completions`. Mirrors the same
    normalisation ModalLiveTransport does for its endpoint URL.
    """
    base = (base or BASE_URL).rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"
