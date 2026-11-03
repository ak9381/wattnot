import importlib.util, json, sys
from pathlib import Path
from types import SimpleNamespace

from wattnot.agentwaste.server import tier_body

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lcp", ROOT / "scripts" / "langchain_pass.py")
lcp = importlib.util.module_from_spec(spec); spec.loader.exec_module(lcp)


def test_cap_lowers_stepup_budget():
    t = {"name": "on", "model": "m", "reasoning": True, "max_output_cap": 1536}
    assert tier_body({"max_tokens": 2048}, t)["max_completion_tokens"] == 1536


def test_no_cap_keeps_old_budget():
    t = {"name": "on", "model": "m", "reasoning": True}
    assert tier_body({"max_tokens": 2048}, t)["max_completion_tokens"] == 2048
    off = {"name": "off", "model": "m", "max_output_cap": None}
    assert tier_body({"max_tokens": 2048}, off)["max_tokens"] == 2048


def test_step_row_reads_usage():
    ai = SimpleNamespace(response_metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5,
                         "completion_tokens_details": {"reasoning_tokens": 3}}, "finish_reason": "stop"},
                         usage_metadata=None, tool_calls=[])
    r = lcp.step_row(0, 1, ai, 0.5)
    assert (r["prompt_tokens"], r["completion_tokens"], r["reasoning_tokens"]) == (10, 5, 3)


def test_step_row_and_writer_never_raise(tmp_path):
    w = lcp.step_writer(tmp_path / "x", "saver_on", "off")
    w({"bad": object()})
    w(lcp.step_row(0, 1, object(), 0.1))
    rows = [json.loads(l) for l in (tmp_path / "x" / "langchain_steps.jsonl").read_text().splitlines()]
    assert rows and rows[-1]["arm"] == "saver_on"
