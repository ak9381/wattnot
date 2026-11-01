"""Muse Spark grades every answer in a matrix round, next to the deterministic check.

The deterministic check (GSM8K final number, HumanEval / our-set tests, agent checks) always
decides `ok`. Spark's verdict is recorded beside it, and a disagreement is logged, never used to
flip a result. Turned on by scripts/e2e/round-matrix.json (`spark_grade_all: true`) for a run that
sets WATTNOT_MATRIX_ROUND (e.g. r01); off otherwise, so other plans spend nothing.

Every call goes through the guarded Meta client, so the $120 hard cap holds: once a call is
refused for budget, grading stops for the rest of the run (spark = None, reason says why).
WATTNOT_META_MOCK=1 gives canned verdicts labelled MOCK, kept out of the real ledger.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "scripts" / "e2e" / "round-matrix.json"
ROUND_ENV = "WATTNOT_MATRIX_ROUND"
ARM_ENV = "WATTNOT_MATRIX_ARM"
RECORD_ENV = "WATTNOT_MATRIX_RECORD"
MAX_ANSWER = 3000
SYSTEM = ("Grade one answer. Reply JSON {\"correct\": bool, \"reason\": str}. correct = the answer "
          "solves the task (final number right, or the code would pass reasonable tests). Ignore style. "
          "reason: one short sentence.")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["correct", "reason"],
          "properties": {"correct": {"type": "boolean"}, "reason": {"type": "string"}}}


def load_config(path: Path | None = None) -> dict:
    try:
        return json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def request_body(model: str, prompt: str, answer: str) -> dict:
    return {"model": model, "reasoning_effort": "minimal", "max_tokens": 300,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": f"TASK:\n{prompt[:2000]}\n\nANSWER:\n{answer[:MAX_ANSWER]}"}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "Grade", "strict": True, "schema": SCHEMA}}}


def _mock_opener(req, timeout):
    """Canned verdict: agrees with the deterministic check the caller passed in metadata."""
    body = json.loads(req.data)
    det = bool(body.get("metadata", {}).get("det"))
    out = {"id": "mock-grade", "choices": [{"message": {"content": json.dumps(
        {"correct": det, "reason": "MOCK verdict (canned, agrees with the deterministic check)"})}}],
        "usage": {"prompt_tokens": 300, "completion_tokens": 30}}
    return 200, json.dumps(out).encode()


class SparkGrader:
    def __init__(self, *, model: str | None = None, client: Any = None, mock: bool | None = None,
                 round_id: str = "", arm: str = "") -> None:
        from wattnot.meta.client import GuardedClient, meta_mock_on
        self.mock = meta_mock_on() if mock is None else mock
        self.model = model or os.environ.get("WATTNOT_SPARK_MODEL", "muse-spark-1.3")
        if client is None:
            if self.mock:
                from wattnot.meta.ledger import SpendLedger
                led = SpendLedger(Path(tempfile.mkdtemp(prefix="spark-grade-mock-")) / "ledger.json")
                client = GuardedClient(opener=_mock_opener, key="mock", ledger=led)
            else:
                client = GuardedClient(timeout_s=60)
        self.client, self.round_id, self.arm = client, round_id, arm
        self.label = "MOCK" if self.mock else "LIVE"
        self.stopped: str | None = None
        self.records: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, item_id: str, dataset: str, prompt: str, answer: str, deterministic: bool) -> dict:
        rec = {"id": str(item_id), "dataset": dataset, "arm": self.arm, "deterministic": bool(deterministic),
               "spark": None, "spark_reason": "", "spark_label": self.label}
        if self.stopped:
            rec["spark_reason"] = f"not graded: {self.stopped}"
        else:
            from wattnot.meta.ledger import BudgetExceeded
            from wattnot.meta.spark_trace import _parse
            body = request_body(self.model, prompt or "", answer or "")
            if self.mock:
                body["metadata"] = {"det": bool(deterministic)}
            try:
                data = self.client(body, purpose=f"spark-grade:{self.round_id or 'run'}:{dataset}")
                got = _parse(data["choices"][0]["message"]["content"]) or {}
                if isinstance(got.get("correct"), bool):
                    rec["spark"], rec["spark_reason"] = got["correct"], str(got.get("reason", ""))[:300]
                else:
                    rec["spark_reason"] = "unparseable Spark reply"
            except BudgetExceeded as e:
                self.stopped = f"Meta cap reached ({e})"
                rec["spark_reason"] = f"not graded: {self.stopped}"
            except Exception as e:  # noqa: BLE001 - a failed grade is recorded, never faked
                rec["spark_reason"] = f"Spark call failed ({type(e).__name__})"
        rec["disagree"] = rec["spark"] is not None and rec["spark"] != rec["deterministic"]
        with self._lock:
            self.records.append(rec)
        return rec

    def summary(self) -> dict:
        g = [r for r in self.records if r["spark"] is not None]
        dis = [r for r in g if r["disagree"]]
        return {"label": self.label, "answers": len(self.records), "spark_graded": len(g),
                "agreement": round(1 - len(dis) / len(g), 4) if g else None,
                "disagreements": len(dis), "stopped": self.stopped}


class AnswerRecorder:
    """Same call shape as SparkGrader, but makes no Meta call: it keeps the prompt, the answer and
    the deterministic verdict so Spark can grade them off the GPU host after copy-back
    (`matrix_round --grade`). The GPU host never holds the Meta key."""

    label = "RECORDED"

    def __init__(self, round_id: str = "", arm: str = "") -> None:
        self.round_id, self.arm, self.mock, self.stopped = round_id, arm, False, None
        self.records: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, item_id, dataset, prompt, answer, deterministic) -> dict:
        rec = {"id": str(item_id), "dataset": dataset, "arm": self.arm,
               "deterministic": bool(deterministic), "spark": None, "spark_reason": "pending off-box grade",
               "spark_label": self.label, "prompt": (prompt or "")[:2000], "answer": (answer or "")[:MAX_ANSWER]}
        with self._lock:
            self.records.append(rec)
        return rec

    def summary(self) -> dict:
        return {"label": self.label, "answers": len(self.records), "spark_graded": 0,
                "agreement": None, "disagreements": 0, "stopped": None}


def grader_from_env(config: Path | None = None) -> SparkGrader | AnswerRecorder | None:
    """A grader when the matrix config asks for it and this run is a matrix round; else None.
    WATTNOT_MATRIX_RECORD=1 (set on the GPU host) records answers for an off-box grade instead."""
    rid = os.environ.get(ROUND_ENV, "").strip()
    if not rid or not load_config(config).get("spark_grade_all"):
        return None
    if os.environ.get(RECORD_ENV, "").strip() == "1":
        return AnswerRecorder(round_id=rid, arm=os.environ.get(ARM_ENV, ""))
    return SparkGrader(model=load_config(config).get("spark_grade_model"), round_id=rid,
                       arm=os.environ.get(ARM_ENV, ""))


SparkHook = Callable[[str, str, str, str, bool], dict]
