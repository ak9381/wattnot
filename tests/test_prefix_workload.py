"""The prefix-cache workload must feed the model its context and give a reasoning model room."""

from __future__ import annotations

from pathlib import Path

from wattnot.runner.workload import load_workload

WL = Path(__file__).resolve().parents[1] / "workloads" / "prefix-cache-v1.jsonl"


def test_prefix_workload_prompt_carries_the_context_and_room():
    wl = load_workload(WL, kind="proof")
    items = list(wl)
    assert items, "workload is empty"
    prefixes = set()
    for it in items:
        # the proof ChatClient only sends `prompt`, so the shared repo context must live IN the
        # prompt (RUN3: context was a dropped field and every answer was empty -> 0% pass).
        assert "REPO CONTEXT" in it.prompt and "ANSWER:" in it.prompt
        assert it.max_tokens >= 512   # reasoning room (was 64, which truncated every answer)
        assert it.meta.get("checks")  # the gold check is preserved
        prefixes.add(it.prompt.split("Question:")[0])
    # one identical shared prefix across every request is exactly what prefix caching caches
    assert len(prefixes) == 1


def test_gold_answer_is_present_in_the_context():
    wl = load_workload(WL, kind="proof")
    first = list(wl)[0]
    want = first.meta["checks"][0]["value"]   # e.g. "-27.1"
    assert want in first.prompt               # answerable from the given context
