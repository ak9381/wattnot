"""Answerers for the spoken-answer gate, plus a time-budgeted runner.

An *answerer* takes one ``SpokenItem`` and returns the served model's short
answer to that question. Two are provided:

* ``OfflineAnswerer`` returns the known ground truth. It performs no I/O and
  makes no network call, so it is what ``--fake`` dry runs and tests use. It is
  NOT a measurement — it only exercises the plumbing.
* ``ServedSpokenAnswerer`` asks the served model (Glimmer, or Spark via the
  Meta Model API) the *transcribed* question over the OpenAI-compatible
  ``/chat/completions`` route. The recordings are transcribed once by Muse
  Voice Transcribe and cached in ``transcripts.json``; this answerer never
  transcribes, it only reads the cached text. It only touches the network when
  called, so importing this module is side-effect free and tests never hit it.

``answer_spoken_set`` runs an answerer over the set with an optional wall-clock
budget, so the spoken gate fits inside the 2-hour Glimmer test window.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable, Mapping

from wattnot.spokenanswer.assets import SpokenItem, SpokenSet, ground_truth

Answerer = Callable[[SpokenItem], str]

# The served model name used when none is passed. Meta open weights; the spoken
# gate can also route to Spark through the Meta Model API.
DEFAULT_MODEL = "meta-models/Muse-Glimmer-30B"
_INSTRUCTION = "Answer with only the value, no explanation."


class OfflineAnswerer:
    """Returns ground truth. No I/O; for dry runs and tests only (not measured)."""

    def __init__(self, spoken: SpokenSet):
        self._truth = ground_truth(spoken.items)

    def __call__(self, item: SpokenItem) -> str:
        return self._truth.get(item.id, "")


class ServedSpokenAnswerer:
    """Ask the served model the transcribed question, one per recording. Live only.

    ``transcripts`` maps item id -> the cached Muse Voice Transcribe text. If a
    recording has no cached transcript the item's reference question is used.
    ``client`` is any callable ``(body, purpose=...) -> response dict`` — pass
    ``wattnot.meta.client.GuardedClient`` for Spark, or a thin wrapper around
    the local vLLM server for Glimmer. A tiny built-in opener is used only if no
    client is supplied, so nothing hits the network at import time.
    """

    def __init__(self, transcripts: Mapping[str, str], *, model: str = DEFAULT_MODEL,
                 client: Callable[..., Mapping] | None = None,
                 base_url: str | None = None, timeout_s: float = 60) -> None:
        self.transcripts = dict(transcripts)
        self.model = model
        self.client = client
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_s = timeout_s

    def _question(self, item: SpokenItem) -> str:
        return self.transcripts.get(item.id) or item.question

    def _body(self, item: SpokenItem) -> dict:
        return {
            "model": self.model,
            "max_completion_tokens": 32,
            "temperature": 0,
            "messages": [{"role": "user",
                          "content": f"{self._question(item)} {_INSTRUCTION}"}],
        }

    def _call(self, body: dict) -> Mapping:
        if self.client is not None:
            return self.client(body, purpose="spoken-answer")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:  # noqa: S310
            return json.loads(r.read() or b"{}")

    def __call__(self, item: SpokenItem) -> str:
        data = self._call(self._body(item))
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError):
            return ""


def answer_spoken_set(spoken: SpokenSet, answerer: Answerer, *, budget_s: float | None = None,
                      clock: Callable[[], float] = time.monotonic) -> tuple[dict[str, str], dict]:
    """Run ``answerer`` over the set, stopping early if ``budget_s`` is spent.

    Returns ``(answers, meta)``. ``meta`` records how many questions were
    answered, whether the budget was hit, and the seconds spent — recordings
    left unanswered by a budget cut are simply absent, which the gate scores as
    a miss.
    """

    answers: dict[str, str] = {}
    t0 = clock()
    budget_hit = False
    for item in spoken.items:
        if budget_s is not None and clock() - t0 >= budget_s:
            budget_hit = True
            break
        answers[item.id] = answerer(item)
    meta = {
        "answered": len(answers),
        "total": len(spoken),
        "budget_s": budget_s,
        "budget_hit": budget_hit,
        "elapsed_s": round(clock() - t0, 6),
    }
    return answers, meta


__all__ = [
    "Answerer",
    "DEFAULT_MODEL",
    "OfflineAnswerer",
    "ServedSpokenAnswerer",
    "answer_spoken_set",
]
