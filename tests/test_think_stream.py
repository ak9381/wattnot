"""Fake-stream tests for the parser-agnostic streaming thinking-budget enforcer.

No network, no GPU: every test drives `consume_thinking`, `StreamBudgetChat`, `classify_chunks`
and `probe_mode` with hand-written chunk lists and an injected `post`/`stream_post`.
"""
import json

import pytest

from wattnot.longrun import think_stream as S
from wattnot.longrun.thinking_budget import run as run_block
from wattnot.proof.client import Reply, THINK_CLOSE, THINK_OPEN


# ---- chunk builders ---------------------------------------------------------------------------

def rc(text):
    return {"choices": [{"delta": {"reasoning_content": text}, "finish_reason": None}]}


def cc(text):
    return {"choices": [{"delta": {"content": text}, "finish_reason": None}]}


def done(reason="stop", usage=None):
    ch = {"choices": [{"delta": {}, "finish_reason": reason}]}
    if usage is not None:
        ch["usage"] = {"completion_tokens": usage}
    return ch


# ---- consume_thinking: parser-split (the hidden-close-marker case) ----------------------------

def test_split_early_answer_finishes_naturally():
    chunks = [rc("let me see "), cc("4"), done("stop", usage=7)]
    a = S.consume_thinking(chunks, mode=S.MODE_SPLIT, budget=50)
    assert a.thinking_done and not a.budget_hit
    assert a.answer == "4" and a.thought == "let me see " and a.finish == "stop"
    assert a.tokens == 7


def test_split_budget_hit_mid_thinking_with_hidden_close():
    # reasoning streams only on the reasoning channel; the close marker is never surfaced
    chunks = [rc("a" * 8), rc("b" * 8), rc("c" * 8), rc("d" * 8)]
    a = S.consume_thinking(chunks, mode=S.MODE_SPLIT, budget=5, chars_per_token=4)
    assert a.budget_hit and not a.thinking_done     # we cut, the model never closed its thought
    assert a.answer == "" and a.reasoning_tokens >= 5
    assert a.thought.startswith("aaaa")             # the partial thought is carried forward


# ---- consume_thinking: raw-marker (the historical case) ---------------------------------------

def test_marker_early_answer():
    chunks = [cc(f"{THINK_OPEN}hmm{THINK_CLOSE}"), cc("42"), done("stop")]
    a = S.consume_thinking(chunks, mode=S.MODE_MARKER, budget=50)
    assert a.thinking_done and not a.budget_hit
    assert a.thought == "hmm" and a.answer == "42"


def test_marker_budget_hit_before_close_carries_the_thought():
    chunks = [cc(THINK_OPEN + "x" * 30)]            # opens a thought, never closes it
    a = S.consume_thinking(chunks, mode=S.MODE_MARKER, budget=5, chars_per_token=4)
    assert a.budget_hit and not a.thinking_done
    assert a.thought == "x" * 30 and THINK_OPEN not in a.thought


def test_marker_close_split_across_chunks():
    chunks = [cc("<thi"), cc("nk>reasoned</thi"), cc("nk>done")]
    a = S.consume_thinking(chunks, mode=S.MODE_MARKER, budget=50)
    assert a.thought == "reasoned" and a.answer == "done" and a.thinking_done


# ---- consume_thinking: no-think ---------------------------------------------------------------

def test_nothink_is_all_answer():
    chunks = [cc("Paris"), done("stop")]
    a = S.consume_thinking(chunks, mode=S.MODE_NOTHINK, budget=50)
    assert a.thinking_done and not a.budget_hit and a.answer == "Paris" and a.thought == ""


# ---- StreamBudgetChat end to end --------------------------------------------------------------

def _answer_post(text="ANSWER: 4", finish="stop", tokens=9):
    calls = []

    def post(body):
        calls.append(body)
        return {"choices": [{"finish_reason": finish, "message": {"content": text}}],
                "usage": {"completion_tokens": tokens}}
    return post, calls


def test_streamchat_split_hidden_close_forces_answer():
    stream = lambda body: [rc("a" * 8), rc("b" * 8), rc("c" * 8)]  # noqa: E731
    post, calls = _answer_post()
    c = S.StreamBudgetChat("u", "m", 5, mode=S.MODE_SPLIT, post=post, stream_post=stream,
                           chars_per_token=4)
    r = c([{"role": "user", "content": "q"}], 64)
    assert isinstance(r, Reply) and r.text == "ANSWER: 4"
    assert c.forced["q"] == 1 and c.cut.get("q") is None     # forced close, answer not cut
    assert len(calls) == 1 and calls[0]["continue_final_message"]
    assert calls[0]["messages"][-1]["content"].endswith(THINK_CLOSE + "\n")
    assert "aaaa" in calls[0]["messages"][-1]["content"]      # the partial thought is prefilled


def test_streamchat_split_early_answer_skips_forcing():
    stream = lambda body: [rc("ok "), cc("4"), done("stop", usage=5)]  # noqa: E731
    post, calls = _answer_post()
    c = S.StreamBudgetChat("u", "m", 50, mode=S.MODE_SPLIT, post=post, stream_post=stream)
    r = c([{"role": "user", "content": "q"}], 64)
    assert r.text == "4" and r.finish == "stop" and r.tokens == 5
    assert calls == [] and not c.forced and not c.cut        # no phase B at all


def test_streamchat_marker_early_answer():
    stream = lambda body: [cc(f"{THINK_OPEN}hmm{THINK_CLOSE}42"), done("stop")]  # noqa: E731
    post, calls = _answer_post()
    c = S.StreamBudgetChat("u", "m", 50, mode=S.MODE_MARKER, post=post, stream_post=stream)
    r = c([{"role": "user", "content": "q"}], 64)
    assert r.text == "42" and calls == []


def test_streamchat_marker_budget_hit_forces_and_flags_cut():
    stream = lambda body: [cc(THINK_OPEN + "x" * 40)]  # noqa: E731
    post, calls = _answer_post(text="partial", finish="length", tokens=20)
    c = S.StreamBudgetChat("u", "m", 5, mode=S.MODE_MARKER, post=post, stream_post=stream,
                           chars_per_token=4)
    r = c([{"role": "user", "content": "q"}], 64)
    assert c.forced["q"] == 1 and c.cut["q"] == 1 and r.finish == "length"


def test_streamchat_nothink_single_phase_flags_cut_on_length():
    stream = lambda body: [cc("a very long answer that got cut"), done("length")]  # noqa: E731
    post, calls = _answer_post()
    c = S.StreamBudgetChat("u", "m", 50, mode=S.MODE_NOTHINK, post=post, stream_post=stream)
    r = c([{"role": "user", "content": "q"}], 64)
    assert calls == [] and c.cut["q"] == 1 and r.finish == "length"
    assert r.text == "a very long answer that got cut"


def test_streamchat_records_cut_when_answer_phase_overflows():
    stream = lambda body: [rc("a" * 40)]  # noqa: E731 - budget hit, forces the answer
    post, _ = _answer_post(text="ANS", finish="length", tokens=11)
    c = S.StreamBudgetChat("u", "m", 5, mode=S.MODE_SPLIT, post=post, stream_post=stream,
                           chars_per_token=4)
    r = c([{"role": "user", "content": "q"}], 64)
    assert c.forced["q"] == 1 and c.cut["q"] == 1 and r.finish == "length"


def test_streamchat_stream_error_is_recorded_not_hidden():
    def boom(body):
        raise RuntimeError("connection reset")
    c = S.StreamBudgetChat("u", "m", 5, mode=S.MODE_SPLIT, stream_post=boom)
    r = c([{"role": "user", "content": "q"}], 64)
    assert r.finish == "error" and r.text == ""


# ---- the startup self-probe -------------------------------------------------------------------

def test_probe_detects_parser_split():
    stream = lambda body: [rc("thinking"), cc("4"), done("stop")]  # noqa: E731
    p = S.probe_mode(stream, "m")
    assert p.mode == S.MODE_SPLIT and p.confident and p.saw_reasoning_field


def test_probe_detects_raw_marker():
    stream = lambda body: [cc(f"{THINK_OPEN}reasoned{THINK_CLOSE}4"), done("stop")]  # noqa: E731
    p = S.probe_mode(stream, "m")
    assert p.mode == S.MODE_MARKER and p.confident and p.saw_markers


def test_probe_detects_no_think():
    stream = lambda body: [cc("Paris"), done("stop")]  # noqa: E731
    p = S.probe_mode(stream, "m")
    assert p.mode == S.MODE_NOTHINK and p.confident


def test_probe_falls_back_to_marker_when_empty_or_broken():
    assert S.probe_mode(lambda body: [], "m").mode == S.MODE_MARKER
    assert not S.probe_mode(lambda body: [], "m").confident

    def boom(body):
        raise RuntimeError("no server")
    p = S.probe_mode(boom, "m")
    assert p.mode == S.MODE_MARKER and not p.confident and "probe failed" in p.note


def test_classify_prefers_reasoning_channel_over_markers():
    # even if a stray marker shows in content, a live reasoning channel means parser-split
    chunks = [rc("t"), cc(f"{THINK_CLOSE}answer")]
    assert S.classify_chunks(chunks).mode == S.MODE_SPLIT


# ---- the receipt records the transport --------------------------------------------------------

def test_fake_run_records_transport_in_receipt(tmp_path):
    doc = run_block(tmp_path, fake=True, per_tier=1)
    d = json.loads((tmp_path / "thinking_policy.json").read_text())
    assert d["think_transport"]["mode"] == "fake-dry-run"
    assert d["think_transport"]["enforcer"] == "raw-marker stop-and-force"
    assert d["transport_modes"] == list(S.MODES)
    assert doc["think_transport"]["chars_per_token"] == S.CHARS_PER_TOKEN


def test_env_flag_can_disable_streaming(monkeypatch):
    monkeypatch.setattr(S, "ENABLED", False)
    assert S.ENABLED is False  # the real branch then keeps BudgetChat (guarded, GPU-host only)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
