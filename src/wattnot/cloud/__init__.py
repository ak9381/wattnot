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
KEY_ENV = "BASETEN_API_KEY"
# Inference key lookup order. On 29 Oct 2026 BASETEN_METRICS_KEY answered inference (200) and
# BASETEN_API_KEY returned 401, so the working key goes first and the old one is the fallback.
INFERENCE_KEY_ENVS = ("BASETEN_METRICS_KEY", "BASETEN_API_KEY")
# A Spark call whose max_tokens is all spent on reasoning comes back with content None: that is
# a cut-off answer and is scored as a fail. Live proof: max_tokens 64 -> 61 reasoning, no answer.
JUDGE_MAX_TOKENS = 512  # judge at low effort; 64 was not enough to leave room for a verdict


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
