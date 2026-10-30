"""Answerers for the eye test, plus a time-budgeted runner.

An *answerer* takes one ``EyeTestItem`` and returns the served vision model's
short answer. Two are provided:

* ``OfflineAnswerer`` returns the known ground truth. It performs no I/O and
  makes no network call, so it is what ``--fake`` dry runs and tests use. It
  is NOT a measurement — it only exercises the plumbing.
* ``ServedVisionAnswerer`` asks the served vision model (Glimmer, or Spark via
  the Meta Model API) over the OpenAI-compatible ``/chat/completions`` route
  with the image inlined as a data URI. It only touches the network when
  called, so importing this module is side-effect free and tests never hit it.

``answer_eye_test`` runs an answerer over the set with an optional wall-clock
budget, so the eye test fits inside the 2-hour Glimmer test window.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

from wattnot.vision.assets import EyeTest, EyeTestItem, ground_truth

Answerer = Callable[[EyeTestItem], str]

# The served vision model name used when none is passed. Meta open weights; the
# eye test can also route to Spark vision through the Meta Model API.
DEFAULT_VISION_MODEL = "meta-models/Muse-Glimmer-30B"
_INSTRUCTION = "Answer with only the value, no explanation."


class OfflineAnswerer:
    """Returns ground truth. No I/O; for dry runs and tests only (not measured)."""

    def __init__(self, eye_test: EyeTest):
        self._truth = ground_truth(eye_test.items)

    def __call__(self, item: EyeTestItem) -> str:
        return self._truth.get(item.id, "")


def _data_uri(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in ("jpg", "jpeg") else suffix
    return f"data:image/{mime};base64,{b64}"


class ServedVisionAnswerer:
    """Ask the served vision model one question per image. Live only.

    ``client`` is any callable ``(body, purpose=...) -> response dict`` — pass
    ``wattnot.meta.client.GuardedClient`` for Spark vision, or a thin wrapper
    around the local vLLM server for Glimmer. A tiny built-in opener is used
    only if no client is supplied, so nothing is imported that would hit the
    network at import time.
    """

    def __init__(self, base_dir: Path, *, model: str = DEFAULT_VISION_MODEL,
                 client: Callable[..., Mapping] | None = None,
                 base_url: str | None = None, timeout_s: float = 60) -> None:
        self.base_dir = Path(base_dir)
        self.model = model
        self.client = client
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_s = timeout_s

    def _body(self, item: EyeTestItem) -> dict:
        return {
            "model": self.model,
            "max_completion_tokens": 32,
            "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": f"{item.question} {_INSTRUCTION}"},
                {"type": "image_url",
                 "image_url": {"url": _data_uri(item.path(self.base_dir))}},
            ]}],
        }

    def _call(self, body: dict) -> Mapping:
        if self.client is not None:
            return self.client(body, purpose="eye-test")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:  # noqa: S310
            return json.loads(r.read() or b"{}")

    def __call__(self, item: EyeTestItem) -> str:
        try:
            data = self._call(self._body(item))
        except (OSError, ValueError):  # HTTP error, refused, timeout, bad JSON: a miss, not a crash
            return ""
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError):
            return ""


def answer_eye_test(eye_test: EyeTest, answerer: Answerer, *, budget_s: float | None = None,
                    clock: Callable[[], float] = time.monotonic) -> tuple[dict[str, str], dict]:
    """Run ``answerer`` over the set, stopping early if ``budget_s`` is spent.

    Returns ``(answers, meta)``. ``meta`` records how many images were answered,
    whether the budget was hit, and the seconds spent — images left unanswered
    by a budget cut are simply absent, which the gate scores as a miss.
    """

    answers: dict[str, str] = {}
    t0 = clock()
    budget_hit = False
    for item in eye_test.items:
        if budget_s is not None and clock() - t0 >= budget_s:
            budget_hit = True
            break
        answers[item.id] = answerer(item)
    meta = {
        "answered": len(answers),
        "total": len(eye_test),
        "budget_s": budget_s,
        "budget_hit": budget_hit,
        "elapsed_s": round(clock() - t0, 6),
    }
    return answers, meta


__all__ = [
    "Answerer",
    "DEFAULT_VISION_MODEL",
    "OfflineAnswerer",
    "ServedVisionAnswerer",
    "answer_eye_test",
]
