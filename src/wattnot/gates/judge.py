"""Muse Spark as a pairwise quality judge.

For each item the judge sees the question, the baseline answer and the
candidate answer, and says whether the candidate is `same` (equally correct),
`better` or `worse`. All items go in ONE call to keep spend low. The gate
fails when more than `max_worse` of the items are judged worse.

Only public, non-confidential text may be sent (the 30-item set in
data/judge-30.jsonl or the public replay set). Tests use a recorded response.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from wattnot.gates.result import GateResult
from wattnot.planner.spark import MODEL, SparkClient

VERDICTS = ("same", "better", "worse")
JUDGE_SET = Path(__file__).resolve().parents[3] / "data" / "judge-30.jsonl"

SYSTEM = (
    "You grade answers. For each item compare the candidate answer with the baseline answer to "
    "the same question. verdict=same if the candidate is equally correct (wording may differ), "
    "better if more correct, worse if wrong, truncated or less correct. JSON only."
)

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdicts"],
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "verdict"],
        "properties": {"id": {"type": "string"}, "verdict": {"enum": list(VERDICTS)}}}}},
}

Transport = Callable[[dict], dict]


def load_judge_set(path: str | Path = JUDGE_SET) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def request_body(items: Sequence[Mapping], model: str = MODEL, max_tokens: int = 1500) -> dict:
    payload = [{"id": i["id"], "question": i["question"], "baseline": i["baseline"],
                "candidate": i["candidate"]} for i in items]
    return {"model": model, "reasoning_effort": "minimal", "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": json.dumps(payload, separators=(",", ":"))}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "Verdicts", "schema": SCHEMA, "strict": True}}}


def parse_verdicts(raw: Mapping, ids: Sequence[str]) -> dict[str, str]:
    data = json.loads(raw["choices"][0]["message"]["content"])
    out = {v["id"]: v["verdict"] for v in data["verdicts"]
           if v.get("id") in ids and v.get("verdict") in VERDICTS}
    missing = set(ids) - set(out)
    if missing:
        raise ValueError(f"judge skipped {len(missing)} items")
    return out


def judge(base_out: Mapping[str, str], cand_out: Mapping[str, str], items: Sequence[Mapping],
          *, transport: Transport | None = None, model: str = MODEL,
          max_worse: float = 0.05) -> GateResult:
    """items: dicts with id and question. Returns a `judge` GateResult.

    No transport (or a failed call) means the judge could not run: the gate
    fails closed rather than passing an unchecked change.
    """

    rows = [{"id": i["id"], "question": i["question"], "baseline": base_out[i["id"]],
             "candidate": cand_out[i["id"]]} for i in items]
    ids = [r["id"] for r in rows]
    send = transport or SparkClient(model=model)
    try:
        raw = send(request_body(rows, model))
        verdicts = parse_verdicts(raw, ids)
    except Exception as e:  # network, schema, missing items
        return GateResult("judge", False, f"judge unavailable: {type(e).__name__}",
                          {"model": model, "n": len(ids)})
    usage = raw.get("usage") or {}
    worse = sum(v == "worse" for v in verdicts.values())
    rate = worse / len(ids)
    passed = rate <= max_worse
    return GateResult("judge", passed,
                      f"{worse}/{len(ids)} judged worse ({rate:.1%}, limit {max_worse:.0%})",
                      {"model": raw.get("model", model), "n": len(ids), "worse": worse,
                       "same": sum(v == "same" for v in verdicts.values()),
                       "better": sum(v == "better" for v in verdicts.values()),
                       "worse_rate": round(rate, 4), "max_worse": max_worse,
                       "tokens": {"input": int(usage.get("prompt_tokens", 0)),
                                  "output": int(usage.get("completion_tokens", 0))}})


def agreement(items: Sequence[Mapping], verdicts: Mapping[str, str]) -> dict:
    """Judge vs human labels on the agreement set. better counts as not-worse."""

    def bucket(v: str) -> str:
        return "worse" if v == "worse" else "ok"

    hits = sum(bucket(verdicts[i["id"]]) == bucket(i["label"]) for i in items)
    tp = sum(verdicts[i["id"]] == "worse" and i["label"] == "worse" for i in items)
    fn = sum(verdicts[i["id"]] != "worse" and i["label"] == "worse" for i in items)
    fp = sum(verdicts[i["id"]] == "worse" and i["label"] != "worse" for i in items)
    return {"n": len(items), "agreement": round(hits / len(items), 4),
            "worse_caught": tp, "worse_missed": fn, "false_alarms": fp}
