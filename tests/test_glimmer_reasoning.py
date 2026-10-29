"""Reasoning-leak safety net for the deterministic proof/quality-gate path on a reasoning model.

Muse Glimmer 30B is served with a reasoning parser, so a working parser puts the chain of thought
in `reasoning_content` and leaves `content` as the answer. RUN1 warned the parser failed to
initialise; the thinking then leaks into `content` wrapped in <think>...</think> and the fixed
checks grade the reasoning instead of the answer. `ChatClient` must strip that block, the same way
`wattnot.longrun.harness.Chat` already does, so the gate sees the answer. The strip must be a no-op
when `content` is already clean (parser working, or a non-reasoning model like Llama).
"""

from wattnot.gates.answers import check_math, check_qa
from wattnot.proof.client import strip_reasoning


def test_strip_is_noop_on_clean_answers():
    # parser working / non-reasoning model: no <think> tags -> unchanged (apart from trim)
    assert strip_reasoning("42") == "42"
    assert strip_reasoning("Paris") == "Paris"
    assert strip_reasoning("  the answer is 7  ") == "the answer is 7"
    assert strip_reasoning("```python\ndef f():\n    return 1\n```").startswith("```python")


def test_strip_removes_a_leaked_think_block():
    leaked = "<think>Let me add 17 and 25, that is 42, let me double check.</think>42"
    assert strip_reasoning(leaked) == "42"
    # the answer the gate grades is now the clean final answer, not the chain of thought
    assert check_math(strip_reasoning(leaked), "42")
    assert check_qa(strip_reasoning("<think>The capital... Paris.</think>Paris"), ["Paris"])


def test_qa_answer_is_rescued_when_thinking_leaks():
    """A one-word QA answer fails if graded with a long thinking prefix; the strip rescues it."""
    leaked = "<think>" + "reasoning " * 50 + "</think>Paris"
    assert not check_qa(leaked, ["Paris"])          # raw: too many words, fails
    assert check_qa(strip_reasoning(leaked), ["Paris"])  # stripped: clean match


def test_unterminated_block_is_stripped_to_empty():
    # thinking cut off by max_tokens (no </think>): nothing to grade, which the gate fails anyway
    assert strip_reasoning("<think>still thinking and ran out of tok") == ""


def test_request_pins_greedy_sampling(monkeypatch):
    import io
    import json as _j
    from wattnot.proof import client as c
    seen = {}

    def fake(req, timeout=None):
        seen.update(_j.loads(req.data))
        return io.BytesIO(_j.dumps({"choices": [{"message": {"content": "<think>x</think>42"},
                                                  "finish_reason": "stop"}],
                                    "usage": {"completion_tokens": 3}}).encode())
    monkeypatch.setattr(c.urllib.request, "urlopen", fake)
    r = c.ChatClient("http://x/v1", "m").ask("q", 8)
    assert r.text == "42"
    assert seen["temperature"] == 0 and seen["top_k"] == -1 and seen["seed"] == 0
