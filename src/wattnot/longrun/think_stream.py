"""Parser-agnostic streaming thinking-budget enforcer, with a startup self-probe.

Why this exists
---------------
`thinking_budget.BudgetChat` enforces a per-tier thinking budget with the classic
"stop on the close marker, else force the answer" trick (s1-style budget forcing; see
`thinking_budget` for the prior-art note). That trick reads one non-streaming reply and
decides "did the model close its thought?" from the close marker in `content` and from
`finish_reason`.

That breaks on a reasoning model served with vLLM's ``--reasoning-parser`` (see
``scripts/glimmer_serve.sh``). A working parser routes the chain of thought into a separate
``reasoning_content`` field and *consumes* the close marker, so it never appears in
``content``. The marker-based ``stop`` then never fires: every thinking phase runs to
``max_tokens`` and reports ``finish_reason == "length"``, which the old code reads as
"ran out of budget -> force close" for *every* item -- a forced-close storm that both
wastes joules and mislabels healthy, naturally-finished thoughts as cut off.

What this module does
---------------------
1. A **startup self-probe** (`probe_mode`) sends one tiny streaming request and watches the
   delta channels to decide the transport mode, recorded in the receipt:
     - ``parser-split``  reasoning arrives in the ``reasoning_content``/``reasoning`` delta
                         field; the close marker is hidden (the risky case above).
     - ``raw-marker``    everything arrives in ``content`` with ``<think>...</think>`` visible
                         (the historical assumption; `BudgetChat`'s world).
     - ``no-think``      no reasoning channel and no markers (a non-reasoning model).
2. A **streaming enforcer** (`StreamBudgetChat`) that counts *reasoning* tokens as they
   stream -- from the right channel for the detected mode -- and cuts the stream the instant
   the reasoning budget is reached, regardless of whether the close marker is ever surfaced.
   It then forces the answer exactly like `BudgetChat` (``continue_final_message``), so the
   gate, the ``cut``/``forced`` bookkeeping and the `Reply` contract are unchanged.
3. A **fallback**: if the probe is not confident, or streaming is turned off
   (``WATTNOT_THINK_STREAM=0``), callers keep using the raw-marker `BudgetChat`.

What is new (vs. the prior art named in `thinking_budget`)
----------------------------------------------------------
s1 forces the budget inside a *local* decode loop over raw token ids; L1 and ThinkPrune move
the budget into *training* (RL on length / pruned traces); TALE puts a token budget in the
*prompt* and trusts the model to self-limit. None of them handle a *hosted* server whose
reasoning parser splits the thinking onto a side channel and hides the close marker -- which
is exactly the deployment here. Counting the budget off the OpenAI ``reasoning_content``
delta stream at the transport layer, auto-selecting that behaviour with a self-probe, and
writing the chosen mode into the receipt, is the new part. The token budget itself is not
ours and we do not claim it.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field

from wattnot.proof.client import THINK_CLOSE, THINK_OPEN, Reply, strip_reasoning

MODE_SPLIT = "parser-split"
MODE_MARKER = "raw-marker"
MODE_NOTHINK = "no-think"
MODES = (MODE_SPLIT, MODE_MARKER, MODE_NOTHINK)

# Live token counting off a stream is an estimate: vLLM streams roughly a token per chunk,
# but we cut on an accumulated-character count so the budget holds even when a server batches
# several tokens into one delta. ~4 chars/token matches the 3-4 chars/token used elsewhere in
# the harness (grade._est_tokens). Override with WATTNOT_CHARS_PER_TOKEN if a tokenizer differs.
CHARS_PER_TOKEN = max(1, int(os.environ.get("WATTNOT_CHARS_PER_TOKEN", "4")))

ENABLED = os.environ.get("WATTNOT_THINK_STREAM", "1") != "0"


def _reasoning_delta(delta: dict) -> str:
    return delta.get("reasoning_content") or delta.get("reasoning") or ""


# ---- phase A: stream the thought, count reasoning tokens, cut at budget -----------------------

@dataclass
class PhaseA:
    thought: str          # the chain of thought seen before we stopped (never includes markers)
    answer: str           # any final answer that streamed after thinking finished naturally
    reasoning_tokens: int  # estimated thinking tokens (see CHARS_PER_TOKEN)
    tokens: int           # estimated total completion tokens for phase A
    thinking_done: bool   # the model closed its thought / started answering / the stream ended
    budget_hit: bool      # we cut the stream because the reasoning budget was reached
    finish: str | None    # the server's finish_reason, if the stream reached it


def consume_thinking(chunks, *, mode: str, budget: int, open_: str = THINK_OPEN,
                     close: str = THINK_CLOSE, chars_per_token: int = CHARS_PER_TOKEN) -> PhaseA:
    """Walk a streaming chat completion (an iterable of OpenAI chunk dicts) and stop as soon as
    the reasoning budget is reached or the thought finishes. Pure and side-effect free so the
    tests can drive it with hand-written fake streams."""
    thought_parts: list[str] = []
    answer_parts: list[str] = []
    buf = ""                       # raw content accumulator, for raw-marker mode
    reasoning_chars = 0
    answer_chars = 0
    thinking_done = mode == MODE_NOTHINK
    budget_hit = False
    finish: str | None = None
    usage_tokens = 0

    for chunk in chunks:
        ch = (chunk.get("choices") or [{}])[0]
        delta = ch.get("delta") or {}
        fr = ch.get("finish_reason")
        usage = chunk.get("usage") or {}
        if usage.get("completion_tokens"):
            usage_tokens = int(usage["completion_tokens"])
        rc = _reasoning_delta(delta)
        content = delta.get("content") or ""

        if mode == MODE_SPLIT:
            if rc and not thinking_done:
                thought_parts.append(rc)
                reasoning_chars += len(rc)
            if content:
                # the first content byte in parser-split mode means the thought is closed
                thinking_done = True
                answer_parts.append(content)
                answer_chars += len(content)
        elif mode == MODE_MARKER:
            if content:
                buf += content
            if not thinking_done:
                if close in buf:
                    head, _, tail = buf.partition(close)
                    thought_parts = [head.replace(open_, "")]
                    reasoning_chars = len(thought_parts[0])
                    answer_parts = [tail] if tail else []
                    answer_chars = len(tail)
                    thinking_done = True
                else:
                    reasoning_chars = len(buf.replace(open_, ""))
            elif content:
                answer_parts.append(content)
                answer_chars += len(content)
        else:  # MODE_NOTHINK: there is no thought, everything is the answer
            if content:
                answer_parts.append(content)
                answer_chars += len(content)

        if fr:
            finish = fr
            thinking_done = True
            break
        if not thinking_done and budget and reasoning_chars // chars_per_token >= budget:
            budget_hit = True
            break

    if mode == MODE_MARKER and not thought_parts:
        # the budget was hit (or the stream ended) before the close marker: the whole buffer so
        # far is the thought we carry into the forced-answer phase.
        thought_parts = [buf.replace(open_, "")]
    reasoning_tokens = reasoning_chars // chars_per_token
    tokens = usage_tokens or (reasoning_tokens + answer_chars // chars_per_token)
    return PhaseA("".join(thought_parts), "".join(answer_parts), reasoning_tokens, tokens,
                  thinking_done, budget_hit, finish)


# ---- the streaming budget chat ----------------------------------------------------------------

class StreamBudgetChat:
    """Drop-in replacement for `thinking_budget.BudgetChat` that enforces the budget from the
    stream instead of from one non-streaming reply, so it survives a hidden close marker.

    `stream_post(body) -> iterable[chunk]` runs the streaming phase A; `post(body) -> dict` runs
    the non-streaming forced-answer phase B (identical to `BudgetChat`, so the same mock server
    serves both). `cut`/`forced` keep the same meaning and feed the same gate."""

    def __init__(self, url: str, model: str, budget: int, *, mode: str = MODE_SPLIT,
                 answer_cap: int = 512, timeout_s: float = 300, post=None, stream_post=None,
                 chars_per_token: int = CHARS_PER_TOKEN) -> None:
        self.url, self.model, self.budget, self.answer_cap = url, model, budget, answer_cap
        self.mode, self.timeout_s, self.chars_per_token = mode, timeout_s, chars_per_token
        self.post = post or self._post
        self.stream_post = stream_post or self._stream_post
        self.cut: dict[str, int] = {}
        self.forced: dict[str, int] = {}
        self.context_limit = 8192

    def _post(self, body: dict) -> dict:  # pragma: no cover - needs a server
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:  # noqa: S310
            return json.loads(r.read())

    def _stream_post(self, body: dict):  # pragma: no cover - needs a server
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:  # noqa: S310
            for raw in r:
                line = raw.decode().strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue

    def __call__(self, messages: list[dict], max_tokens: int) -> Reply:
        key = next((m["content"] for m in messages if m.get("role") == "user"), "")
        t0 = time.perf_counter()
        base = {"model": self.model, "temperature": 0, "seed": 0, "messages": messages}
        try:
            chunks = self.stream_post({**base, "max_tokens": self.budget, "stream": True,
                                       "skip_special_tokens": False,
                                       "stream_options": {"include_usage": True}})
            a = consume_thinking(chunks, mode=self.mode, budget=self.budget,
                                 chars_per_token=self.chars_per_token)
            toks = a.tokens

            if self.mode == MODE_NOTHINK:
                # no thinking phase: the content is the answer; a length finish means it was cut
                if a.finish == "length":
                    self.cut[key] = self.cut.get(key, 0) + 1
                return Reply(time.perf_counter() - t0, toks, strip_reasoning(a.answer),
                             a.finish or "stop")

            if a.thinking_done and not a.budget_hit and a.answer.strip():
                # the model closed its thought and answered inside the budget: nothing to force
                return Reply(time.perf_counter() - t0, toks, strip_reasoning(a.answer),
                             a.finish or "stop")

            if a.budget_hit:
                self.forced[key] = self.forced.get(key, 0) + 1
            thought = a.thought or a.answer
            prefill = f"{THINK_OPEN}{thought}{THINK_CLOSE}\n"
            b = self.post({**base, "messages": messages + [{"role": "assistant",
                                                            "content": prefill}],
                           "continue_final_message": True, "add_generation_prompt": False,
                           "max_tokens": self.answer_cap})
            cb = b["choices"][0]
            toks += int(b.get("usage", {}).get("completion_tokens", 0))
            if cb.get("finish_reason") == "length":
                self.cut[key] = self.cut.get(key, 0) + 1
            text = strip_reasoning(cb["message"].get("content") or "")
            return Reply(time.perf_counter() - t0, toks, text, cb.get("finish_reason") or "stop")
        except Exception:  # noqa: BLE001 - a failed item is recorded, not hidden (as BudgetChat)
            return Reply(time.perf_counter() - t0, 0, "", "error")


# ---- the startup self-probe -------------------------------------------------------------------

@dataclass
class ThinkProbe:
    mode: str
    confident: bool
    saw_reasoning_field: bool
    saw_markers: bool
    saw_content: bool
    note: str = ""
    sample: list[str] = field(default_factory=list)

    def as_receipt(self) -> dict:
        return {"mode": self.mode, "confident": self.confident,
                "saw_reasoning_field": self.saw_reasoning_field, "saw_markers": self.saw_markers,
                "note": self.note}


def classify_chunks(chunks, *, open_: str = THINK_OPEN, close: str = THINK_CLOSE) -> ThinkProbe:
    """Decide the transport mode from a probe stream. Reasoning channel wins (that is the risky
    hidden-marker case); then visible markers; else no-think. Pure, for the tests."""
    saw_reasoning = saw_markers = saw_content = False
    content = ""
    labels: list[str] = []
    for chunk in chunks:
        delta = ((chunk.get("choices") or [{}])[0].get("delta")) or {}
        if _reasoning_delta(delta):
            saw_reasoning = True
            labels.append("reasoning")
        c = delta.get("content") or ""
        if c:
            saw_content = True
            content += c
            labels.append("content")
    if open_ in content or close in content:
        saw_markers = True
    if saw_reasoning:
        return ThinkProbe(MODE_SPLIT, True, True, saw_markers, saw_content,
                          "reasoning delta field present; close marker hidden by the parser",
                          labels[:8])
    if saw_markers:
        return ThinkProbe(MODE_MARKER, True, False, True, saw_content,
                          "thinking visible in content with markers", labels[:8])
    if saw_content:
        return ThinkProbe(MODE_NOTHINK, True, False, False, True,
                          "no reasoning channel and no markers", labels[:8])
    return ThinkProbe(MODE_MARKER, False, False, False, False,
                      "empty probe; defaulting to raw-marker fallback", labels[:8])


def probe_mode(stream_post, model: str, *, prompt: str = "Think briefly, then answer: what is "
               "2+2? Put the answer on its own line.", limit: int = 64) -> ThinkProbe:
    """Run one short streaming request and classify the transport mode. Never raises: a failed
    probe falls back to raw-marker (the historical behaviour)."""
    body = {"model": model, "temperature": 0, "seed": 0, "max_tokens": 48, "stream": True,
            "skip_special_tokens": False, "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": prompt}]}
    try:
        collected = []
        for i, chunk in enumerate(stream_post(body)):
            collected.append(chunk)
            if i + 1 >= limit:
                break
        return classify_chunks(collected)
    except Exception as e:  # noqa: BLE001 - the probe never blocks a run
        return ThinkProbe(MODE_MARKER, False, False, False, False,
                          f"probe failed ({type(e).__name__}); raw-marker fallback")
