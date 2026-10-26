#!/usr/bin/env python3
"""One-time generator for the committed workload sets (STUB — not wired up yet).

The public replay set and the Muse Code task set v1 are generated once by a
maintainer and then committed, so every test and gate run replays the same
bytes. This script is the home for that one-time generation. The live parts are
left as clearly marked stubs for the maintainer to fill in and run later; this
file never calls a model API on its own.

Usage::

    python scripts/gen_workloads.py --check      # offline: validate committed sets
    python scripts/gen_workloads.py --generate    # live, maintainer-only (stub)

The ``--generate`` path is guarded by ``WATTNOT_LIVE=1`` and still raises until
the maintainer implements it, because it will:

  * replay set   — fetch a sample from a public, no-login dataset, recording the
    source and license per item (replaces the hand-written placeholder);
  * muse-code set — run a short, scripted coding test task against Muse Spark
    Open to produce v1 prompts, logging the tiny test spend.

Both outputs must stay public (never private prompts) and must round-trip
through ``wattnot.runner.workload.load_workload`` before being committed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from wattnot.runner import workload as wl  # noqa: E402


def check() -> int:
    """Validate the committed sets load cleanly. Fully offline."""

    ok = True
    for loader in (wl.load_replay_set, wl.load_muse_code_set):
        try:
            w = loader()
        except wl.WorkloadError as exc:
            print(f"FAIL {loader.__name__}: {exc}", file=sys.stderr)
            ok = False
            continue
        print(f"ok   {w.name} ({w.kind}): {len(w)} items")
    return 0 if ok else 1


MUSE_CATEGORIES = [
    ("python", "function", "small standalone Python functions (strings, lists, dicts, dates)"),
    ("python", "debug", "find-and-fix bugs in short Python snippets included in the prompt"),
    ("sql", "query", "SQL queries over a described toy schema (orders, users, products)"),
    ("javascript", "function", "small JavaScript or TypeScript utility functions"),
    ("python", "tests", "write pytest unit tests for a short function included in the prompt"),
    ("bash", "script", "short shell one-liners or scripts for file and text tasks"),
]
TASKS_PER_CALL = 8
MAX_CALLS = 8
TASK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["tasks"],
    "properties": {"tasks": {"type": "array", "minItems": TASKS_PER_CALL,
        "maxItems": TASKS_PER_CALL, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["prompt", "max_tokens"],
            "properties": {"prompt": {"type": "string", "maxLength": 600},
                           "max_tokens": {"type": "integer", "minimum": 64, "maximum": 512}}}}},
}


def _muse_call(lang: str, kind: str, desc: str) -> tuple[list[dict], dict]:
    import json
    import urllib.request

    base = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")
    model = os.environ.get("WATTNOT_PLANNER_MODEL", "muse-spark-1.3")
    prompt = (
        f"Write {TASKS_PER_CALL} distinct, self-contained coding requests a developer might "
        f"send to a code assistant. Topic: {desc}. Each request must be answerable in under "
        "300 tokens, use only made-up names and data, and include any code it refers to. "
        "Set max_tokens to a fair answer budget. Reply with JSON only."
    )
    body = {"model": model, "reasoning_effort": "low",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "TaskSet", "schema": TASK_SCHEMA, "strict": True}}}
    req = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['META_API_KEY']}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310
        raw = json.loads(resp.read())
    tasks = json.loads(raw["choices"][0]["message"]["content"])["tasks"]
    usage = raw.get("usage") or {}
    return tasks, {"model": raw.get("model", model), "lang": lang, "kind": kind, "usage": usage}


def generate() -> int:
    """Regenerate the Muse Code task set v1 with a few small Muse Spark calls.

    Maintainer-only. Sends synthetic topic descriptions only, never user data.
    The replay set stays hand-written until a public dataset is picked.
    """

    import json
    from datetime import UTC, datetime

    if os.environ.get("WATTNOT_LIVE") != "1":
        print(
            "refusing to generate: set WATTNOT_LIVE=1 and run as the maintainer.",
            file=sys.stderr,
        )
        return 2
    if not os.environ.get("META_API_KEY"):
        print("META_API_KEY is not set", file=sys.stderr)
        return 2

    rows: list[dict] = []
    calls: list[dict] = []
    seen: set[str] = set()
    for lang, kind, desc in MUSE_CATEGORIES[:MAX_CALLS]:
        tasks, meta = _muse_call(lang, kind, desc)
        calls.append(meta)
        for task in tasks:
            text = " ".join(task["prompt"].split())
            if not text or text.lower() in seen:
                continue
            seen.add(text.lower())
            rows.append({"id": f"code-{len(rows) + 1:04d}", "prompt": text,
                         "max_tokens": int(task["max_tokens"]), "language": lang,
                         "kind": kind, "source": "muse-spark-generated"})

    out = wl.workloads_dir() / "muse-code-v1.jsonl"
    header = (
        "# Muse Code task set v1. Generated once by scripts/gen_workloads.py with\n"
        f"# {len(calls)} small Muse Spark calls on "
        f"{datetime.now(UTC).date().isoformat()}. Synthetic coding requests only.\n"
        "# Format: one JSON object per line; required keys id, prompt, max_tokens.\n"
    )
    out.write_text(header + "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    wl.load_workload(out)  # must round-trip before commit
    totals = {k: sum(int((c["usage"] or {}).get(k) or 0) for c in calls)
              for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    spend = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
             "calls": len(calls), "items": len(rows), "totals": totals, "per_call": calls}
    (out.parent / "muse-code-v1.spend.json").write_text(json.dumps(spend, indent=2) + "\n")
    print(json.dumps({"calls": len(calls), "items": len(rows), "totals": totals}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="validate the committed sets (offline)"
    )
    mode.add_argument(
        "--generate",
        action="store_true",
        help="regenerate the Muse Code set with Muse Spark (maintainer-only)",
    )
    args = parser.parse_args(argv)
    return generate() if args.generate else check()


if __name__ == "__main__":
    raise SystemExit(main())
