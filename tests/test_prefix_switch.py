import json
from pathlib import Path

import pytest

from wattnot.context_saver.prefix_switch import DEFAULT_THRESHOLD, choose, prompt_text, shared_prefix_ratio

ROOT = Path(__file__).resolve().parents[1]


def _rows(name):
    return [json.loads(l) for l in (ROOT / "workloads" / name).read_text().splitlines() if l.strip()]


def test_short_questions_turn_cache_off():
    qs = [f"What is {i} + {i * 3}?" for i in range(50)]
    d = choose(qs, mode="auto")
    assert d["enabled"] is False and d["vllm_flag"] == "--no-enable-prefix-caching"
    assert d["shared_prefix_ratio"] < DEFAULT_THRESHOLD


def test_agent_loop_turns_cache_on():
    system = "You are a coding agent. Tools: read_file, run_tests, edit. " * 20
    turns = [[{"role": "system", "content": system}, {"role": "user", "content": f"step {i}"}] for i in range(10)]
    d = choose(turns, mode="auto")
    assert d["enabled"] is True and d["shared_prefix_ratio"] > 0.8


def test_real_workloads_split_the_right_way():
    assert choose(_rows("prefix-cache-v1.jsonl"), mode="auto")["enabled"] is True
    assert choose(_rows("tiers-v1.jsonl"), mode="auto")["enabled"] is False


def test_threshold_and_force_are_configurable(monkeypatch):
    qs = ["x" * 100 + str(i) for i in range(10)]
    assert choose(qs, mode="auto", threshold=0.95)["enabled"] is False
    assert choose(qs, mode="auto", threshold=0.5)["enabled"] is True
    monkeypatch.setenv("WATTNOT_PREFIX_CACHE", "on")
    d = choose(["a?"])
    assert d["enabled"] and d["mode"] == "on" and "forced" in d["reason"]
    monkeypatch.setenv("WATTNOT_PREFIX_CACHE", "auto")
    monkeypatch.setenv("WATTNOT_PREFIX_CACHE_THRESHOLD", "0.99")
    assert choose(qs)["threshold"] == 0.99 and choose(qs)["enabled"] is False


def test_bad_config_is_refused():
    with pytest.raises(ValueError):
        choose([], mode="maybe")
    with pytest.raises(ValueError):
        choose([], mode="auto", threshold=1.5)


def test_tiny_overlap_does_not_count_and_empty_is_zero():
    assert shared_prefix_ratio(["Solve: 1", "Solve: 2"]) == 0.0
    assert shared_prefix_ratio([]) == 0.0


def test_prompt_text_handles_objects_and_dicts():
    class Item:
        prompt = "hello"
    assert prompt_text(Item()) == "hello"
    assert prompt_text({"messages": [{"role": "user", "content": "hi"}]}) == "hi"
    assert prompt_text({"task_id": 1, "prompt": "def f():"}) == "def f():"


def test_decision_is_recorded_shape():
    d = choose(["q1", "q2"], mode="auto", workload="ours")
    for k in ("enabled", "mode", "threshold", "shared_prefix_ratio", "prompts", "workload", "reason", "label"):
        assert k in d


def test_fake_inner_forced_on_uses_cache(tmp_path):
    import os, subprocess, sys
    out = tmp_path / "r"
    env = {**os.environ, "WATTNOT_PREFIX_CACHE": "on"}
    subprocess.run([sys.executable, "scripts/e2e/modal_full_inner.py", "--fake", "--limit", "1", "--out", str(out)],
                   cwd=ROOT, check=True, timeout=600, env=env)
    S = json.loads((out / "inner.json").read_text())["steps"]
    assert S["prefix_cache_switch"]["enabled"] is True and S["with_server"]["prefix_caching"] is True
    assert S["without_server"]["prefix_caching"] is False
