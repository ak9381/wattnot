"""Cloud mode: tune the request, not the GPU, when the model runs on a hosted API.

On a hosted endpoint such as Baseten's Model API we cannot change power caps or
touch the experts. What we can change is the request: how hard the model
reasons, how long its answer may be, how the prompt is laid out. Every change
is gated like the rest of WattNot (quality replay plus p99) and written to a
receipt with MEASURED tokens, USD and latency, and an ESTIMATE for energy.
"""

from __future__ import annotations

PROVIDER = "baseten"
MODEL = "meta-models/muse-spark-open-1026"  # the hackathon slug; the docs print it without -1026
BASE_URL = "https://inference.baseten.co/v1"
MGMT_URL = "https://api.baseten.co"
KEY_ENV = "BASETEN_API_KEY"

EFFORTS = ("low", "medium", "high", "xhigh")  # none/minimal are mapped to low by the API
REFERENCE = "xhigh"  # the API default, so the baseline is what an untuned caller pays
