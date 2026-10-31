"""Which dataset a saving is measured on, and how it was chosen.

A saving only means something next to the set of prompts it was measured on. The user picks one:

* ``humaneval``  public benchmark, 40 HumanEval coding problems with unit tests (bundled)
* ``gsm8k``      public benchmark, 60 GSM8K grade-school maths problems with answers (bundled)
* ``mixed``      WattNot's own mixed set (proof-v1: extraction, classification, code tracing,
                 maths, logic) with gold answers
* ``custom``     the user's upload: JSONL, one row per prompt, each with ``prompt`` and either
                 ``expected`` (answer text) or ``tests`` (code tests)
* ``auto``       let WattNot decide: sample recent real traffic, score each prompt as code /
                 maths / general, and pick the closest set. Deterministic, no model call.

Every receipt records the set (``dataset`` block): id, kind, items, sha256 of the file bytes,
and how it was chosen, with the reason when WattNot chose it.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

CATALOG: dict[str, dict[str, Any]] = {
    "humaneval": {"title": "HumanEval (public, 40 coding problems with tests)", "kind": "public",
                  "file": "data/public_bench/humaneval_first40.jsonl", "checks": "tests",
                  "shape": "code"},
    "gsm8k": {"title": "GSM8K (public, 60 maths problems with answers)", "kind": "public",
              "file": "data/public_bench/gsm8k_test_first60.jsonl", "checks": "expected",
              "shape": "math"},
    "mixed": {"title": "WattNot mixed set (proof-v1, 111 prompts with gold answers)",
              "kind": "wattnot", "file": "workloads/proof-v1.jsonl",
              "gold": "workloads/proof-v1.gold.jsonl", "checks": "expected", "shape": "general"},
}
CHOICES = ("auto", "humaneval", "gsm8k", "mixed", "custom")
DEFAULT = "mixed"
MAX_UPLOAD_ROWS = 5000


class DatasetError(ValueError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _count(path: Path) -> int:
    return sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())


def describe(dataset_id: str, root: Path = ROOT) -> dict[str, Any]:
    if dataset_id not in CATALOG:
        raise DatasetError(f"unknown dataset {dataset_id!r}; pick one of {', '.join(CHOICES)}")
    c = CATALOG[dataset_id]
    p = root / c["file"]
    return {"id": dataset_id, "kind": c["kind"], "title": c["title"], "file": c["file"],
            "items": _count(p), "sha256": _sha(p), "checks": c["checks"]}


def validate_custom(rows: Iterable[Mapping]) -> list[dict]:
    """Check an upload. Each row: ``prompt`` (text) and ``expected`` (text) or ``tests`` (text)."""
    out = []
    for i, r in enumerate(rows, 1):
        if not isinstance(r, Mapping):
            raise DatasetError(f"row {i}: not a JSON object")
        if not isinstance(r.get("prompt"), str) or not r["prompt"].strip():
            raise DatasetError(f"row {i}: needs a non-empty 'prompt'")
        has_e, has_t = isinstance(r.get("expected"), str), isinstance(r.get("tests"), str)
        if not (has_e or has_t):
            raise DatasetError(f"row {i}: needs 'expected' (answer) or 'tests' (code tests)")
        out.append({k: r[k] for k in ("id", "prompt", "expected", "tests") if k in r})
        if len(out) > MAX_UPLOAD_ROWS:
            raise DatasetError(f"more than {MAX_UPLOAD_ROWS} rows")
    if not out:
        raise DatasetError("the upload has no rows")
    return out


def parse_upload(text: str) -> list[dict]:
    rows = []
    for n, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            continue
        try:
            rows.append(json.loads(ln))
        except ValueError as e:
            raise DatasetError(f"line {n}: not valid JSON ({e.msg})") from e
    return validate_custom(rows)


def save_custom(text: str, dest_dir: Path) -> dict[str, Any]:
    rows = parse_upload(text)
    data = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode()
    sha = hashlib.sha256(data).hexdigest()
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / f"custom-{sha[:12]}.jsonl"
    p.write_bytes(data)
    checks = "tests" if all("tests" in r for r in rows) else "expected" if all(
        "expected" in r for r in rows) else "mixed"
    return {"id": "custom", "kind": "custom", "title": "Custom upload", "file": str(p),
            "items": len(rows), "sha256": sha, "checks": checks}


_CODE = re.compile(r"\bdef \w+\(|\bclass \w+|```|\breturn\b|\bfunction\b|=>|\bimport \w+|[{};]\s*$",
                   re.M)
_MATH = re.compile(r"\$\s?\d|\d+\s*(%|percent|dollars|hours|minutes|km|miles)|how many|how much|"
                   r"\btotal\b|\d+\s*[-+*/x]\s*\d+", re.I)


def shape(prompt: str) -> str:
    if _CODE.search(prompt):
        return "code"
    if _MATH.search(prompt) and len(re.findall(r"\d+", prompt)) >= 2:
        return "math"
    return "general"


def decide(traffic: Sequence[str], *, sample: int = 200, seed: int = 7) -> dict[str, Any]:
    """Let WattNot decide: sample real traffic and pick the closest bundled set."""
    if not traffic:
        raise DatasetError("no traffic to sample; pick a dataset by hand")
    pick = list(traffic)
    if len(pick) > sample:
        pick = random.Random(seed).sample(pick, sample)
    counts = {"code": 0, "math": 0, "general": 0}
    for t in pick:
        counts[shape(t)] += 1
    n = len(pick)
    share = {k: v / n for k, v in counts.items()}
    top = max(share, key=lambda k: (share[k], k == "general"))
    # one shape must clearly dominate to pick a single public set; a blend stays on mixed
    chosen = {"code": "humaneval", "math": "gsm8k"}.get(top) if share[top] >= 0.6 else None
    chosen = chosen or "mixed"
    why = (f"sampled {n} real prompts: {counts['code']} code, {counts['math']} maths, "
           f"{counts['general']} general. ")
    why += (f"{share[top]:.0%} look like {top}, so {chosen} is the closest set."
            if chosen != "mixed" else "No single kind reaches 60%, so the mixed set is closest.")
    return {"chosen": chosen, "sampled": n, "counts": counts, "why": why, "seed": seed}


def resolve(choice: str | None = None, *, traffic: Sequence[str] | None = None,
            custom: Mapping[str, Any] | None = None, root: Path = ROOT) -> dict[str, Any]:
    """The ``dataset`` block a receipt records."""
    if choice is None:
        from wattnot.settings import load
        choice = load().get("dataset", DEFAULT)
    if choice == "custom":
        if not custom:
            raise DatasetError("custom dataset chosen but nothing uploaded")
        return {**dict(custom), "chosen_by": "user"}
    if choice == "auto":
        d = decide(traffic or [])
        return {**describe(d["chosen"], root), "chosen_by": "wattnot",
                "why": d["why"], "traffic_sampled": d["sampled"]}
    return {**describe(choice, root), "chosen_by": "user"}
