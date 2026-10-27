"""Muse Spark picks the next action; the controller keeps it safe.

Each step we send Muse Spark a short summary: the current cap, the driver
range, baseline and current p99, Wh per request, and the last few gate
results. It answers with JSON: one action (a cap in watts, one vLLM knob,
or stop) and a one-line reason. Only these numbers go
out: no prompts, no model outputs, no host names.

Safety stays in our code, not in the model's answer:

* the cap is clamped to the driver's min/max and to one step from now,
* knob names and values must be on a fixed allow-list,
* the gate still runs after every change and reverts on a fail.

If the call fails, times out or returns something we can't use, a
deterministic policy picks instead, and the decision says `fallback`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field

MODEL = "muse-spark-1.3"
KNOBS: dict[str, tuple] = {
    "max_num_seqs": (64, 128, 256, 512),
    "enable_prefix_caching": (True, False),
    "enable_chunked_prefill": (True, False),
    "quantization": ("none", "fp8"),
    "gpu_mem_util": (0.8, 0.85, 0.9, 0.95),
}
MAX_REASON = 200

CHOICE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["kind", "power_cap_w", "knob", "value", "reason"],
    "properties": {
        "kind": {"enum": ["cap", "knob", "stop"]},
        "power_cap_w": {"type": ["integer", "null"]},
        "knob": {"enum": [*KNOBS, None]},
        "value": {"type": ["string", "number", "boolean", "null"]},
        "reason": {"type": "string", "maxLength": MAX_REASON},
    },
}

SYSTEM = (
    "You tune one GPU serving an LLM to use less energy per request. Pick ONE next action: "
    "lower or raise the power cap (kind=cap, power_cap_w), change one vLLM knob (kind=knob), "
    "or stop. Keep p99 inside the budget and never repeat a step that failed a gate. "
    "Reason: one short line quoting the number that drove the choice. JSON only."
)


@dataclass(frozen=True)
class Choice:
    kind: str  # cap | knob | stop
    power_cap_w: int | None = None
    knob: str | None = None
    value: object = None


@dataclass(frozen=True)
class Decision:
    choice: Choice
    reason: str
    model: str  # muse-spark-1.3 or fallback
    tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0})
    clamped: bool = False

    def event_data(self) -> dict:
        return {"choice": asdict(self.choice), "reason": self.reason, "model": self.model,
                "tokens": self.tokens, "clamped": self.clamped}

    def line(self) -> str:
        who = "Muse Spark" if self.model != "fallback" else "Fallback policy"
        c = self.choice
        what = (f"{c.power_cap_w} W" if c.kind == "cap"
                else f"{c.knob}={c.value}" if c.kind == "knob" else "to stop")
        return f"{who} chose {what} because {self.reason}"


def summarize(*, cap_w: int, min_w: int, max_w: int, step_w: int, base_p99_s: float,
              p99_s: float, slack: float, wh_per_req: float, base_wh_per_req: float,
              gates: Sequence[Mapping], knobs: Mapping[str, object],
              physical: Mapping | None = None) -> dict:
    """The only thing Muse Spark sees. Small on purpose (about 150 tokens).

    `physical` is the rack photo audit for this GPU (numbers only): units seen, the per-node
    ceiling. `range_w` is already clamped to that ceiling, so a cap above it is never chosen.
    """

    budget = base_p99_s * (1 + slack)
    out = {
        "cap_w": cap_w, "range_w": [min_w, max_w], "step_w": step_w,
        "p99_ms": round(p99_s * 1000), "p99_budget_ms": round(budget * 1000),
        "p99_headroom_pct": round(100 * (budget - p99_s) / budget, 1),
        "wh_per_req": round(wh_per_req, 5),
        "wh_saved_pct": round(100 * (base_wh_per_req - wh_per_req) / base_wh_per_req, 1),
        "last_gates": [{"cap_w": g.get("cap_w"), "knob": g.get("knob"),
                        "passed": g["passed"], "failed": g.get("failed", [])}
                       for g in list(gates)[-3:]],
        "knobs": dict(knobs),
        "allowed_knobs": {k: list(v) for k, v in KNOBS.items()},
    }
    if physical:
        out["physical"] = dict(physical)
    return out


def fallback_policy(s: Mapping) -> Decision:
    """Deterministic: step the cap down while there's headroom, back off after a fail."""

    lo, hi = s["range_w"]
    last = s["last_gates"][-1] if s["last_gates"] else None
    if last and not last["passed"]:
        if last.get("cap_w") is not None:
            return Decision(Choice("stop"), f"last cap {last['cap_w']} W failed "
                            f"{', '.join(last['failed']) or 'a gate'}; keeping the last pass",
                            "fallback")
    if s["p99_headroom_pct"] >= 10 and s["cap_w"] - s["step_w"] >= lo:
        cap = s["cap_w"] - s["step_w"]
        return Decision(Choice("cap", power_cap_w=cap),
                        f"p99 has {s['p99_headroom_pct']}% headroom", "fallback")
    if s["knobs"].get("enable_prefix_caching") is False:
        return Decision(Choice("knob", knob="enable_prefix_caching", value=True),
                        "cap is near the p99 budget; try prefix caching", "fallback")
    return Decision(Choice("stop"), f"p99 headroom only {s['p99_headroom_pct']}%", "fallback")


Transport = Callable[[dict], dict]


class SparkClient:
    """Chat Completions call with a strict JSON schema and a small token cap.

    Every call goes through the spend guard (`wattnot.meta`): it is logged in the ledger
    under `purpose` and refused once the $20 cap would be passed. A refusal is an
    exception, so callers take their normal fallback path.
    """

    def __init__(self, *, key: str | None = None, base: str | None = None,
                 model: str = MODEL, max_tokens: int = 600, timeout_s: float = 30,
                 purpose: str = "planner", ledger=None) -> None:
        from wattnot.meta.client import load_key

        self.key = key or load_key()
        self.base = base or os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.purpose = purpose
        self.ledger = ledger

    def request_body(self, summary: Mapping) -> dict:
        return {"model": self.model, "reasoning_effort": "minimal", "max_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": json.dumps(summary,
                                                                    separators=(",", ":"))}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "NextAction", "schema": CHOICE_SCHEMA, "strict": True}}}

    def __call__(self, body: dict) -> dict:
        from wattnot.meta.client import GuardedClient

        if not self.key:
            raise RuntimeError("META_API_KEY is not set")
        return GuardedClient(key=self.key, base=self.base, ledger=self.ledger,
                             timeout_s=self.timeout_s)(body, purpose=self.purpose)


class DecisionMaker:
    """Ask Muse Spark, check the answer, clamp it, fall back if needed, emit `decision`."""

    def __init__(self, transport: Transport | None, *, model: str = MODEL, emit=None,
                 client: SparkClient | None = None) -> None:
        self.transport = transport
        self.client = client or SparkClient(model=model)
        self.model = model
        self.emit = emit

    def decide(self, summary: Mapping, *, step_id: str | None = None) -> Decision:
        decision = self._ask(summary) if self.transport is not None else None
        if decision is None:
            decision = fallback_policy(summary)
        decision = _make_safe(decision, summary)
        if self.emit is not None:
            self.emit.emit("decision", decision.event_data(), step_id=step_id)
        return decision

    def _ask(self, summary: Mapping) -> Decision | None:
        try:
            raw = self.transport(self.client.request_body(summary))
            content = raw["choices"][0]["message"]["content"]
            data = json.loads(content)
            usage = raw.get("usage") or {}
            tokens = {"input": int(usage.get("prompt_tokens", 0)),
                      "output": int(usage.get("completion_tokens", 0))}
            return _parse(data, raw.get("model", self.model), tokens)
        except Exception:  # any failure means the fixed policy decides
            return None


def _parse(data: Mapping, model: str, tokens: dict) -> Decision | None:
    kind = data.get("kind")
    reason = " ".join(str(data.get("reason", "")).split())[:MAX_REASON]
    if not reason:
        return None
    if kind == "cap" and isinstance(data.get("power_cap_w"), int):
        return Decision(Choice("cap", power_cap_w=data["power_cap_w"]), reason, model, tokens)
    if kind == "knob" and data.get("knob") in KNOBS and data.get("value") in KNOBS[data["knob"]]:
        return Decision(Choice("knob", knob=data["knob"], value=data["value"]), reason, model,
                        tokens)
    if kind == "stop":
        return Decision(Choice("stop"), reason, model, tokens)
    return None


def _make_safe(d: Decision, s: Mapping) -> Decision:
    """Clamp a cap to the driver range and to one step from the current cap."""

    if d.choice.kind != "cap":
        return d
    lo, hi = s["range_w"]
    cur, step = s["cap_w"], s["step_w"]
    want = d.choice.power_cap_w
    safe = max(lo, min(hi, want, cur + step))
    safe = max(safe, cur - step)
    if safe == want:
        return d
    return Decision(Choice("cap", power_cap_w=safe), d.reason, d.model, d.tokens, clamped=True)
