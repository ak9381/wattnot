"""Reasoning-model token caps, spoken context, and concurrent answering (RUN3 fixes)."""

from __future__ import annotations

from wattnot.spokenanswer.assets import load_context, load_spoken_set
from wattnot.spokenanswer.client import ServedSpokenAnswerer, answer_spoken_set
from wattnot.vision.assets import load_eye_test
from wattnot.vision.client import ServedVisionAnswerer, answer_eye_test


def test_committed_spoken_context_answers_every_question():
    # the rack-scene telemetry must contain the ground truth for every question, or the live gate
    # can never pass (RUN3: 0/20, answers like '0' with no context).
    spoken = load_spoken_set()
    ctx = load_context()
    assert ctx and "R12" in ctx
    missing = [it.answer for it in spoken.items if it.answer.lower() not in ctx.lower()]
    assert not missing, missing


def test_spoken_answerer_sends_the_context_as_a_system_message():
    a = ServedSpokenAnswerer({}, context="RACK STATE: 8 GPUs", max_completion_tokens=1536)
    item = next(iter(load_spoken_set().items))
    body = a._body(item)
    assert body["messages"][0] == {"role": "system", "content": "RACK STATE: 8 GPUs"}
    assert body["max_completion_tokens"] == 1536


def test_vision_answerer_token_cap_is_configurable():
    eye = load_eye_test()   # committed placeholder images exist, so _body can inline one
    a = ServedVisionAnswerer(eye.base_dir, max_completion_tokens=2048)
    assert a._body(eye.items[0])["max_completion_tokens"] == 2048


def test_checks_ask_for_low_reasoning_by_default():
    # the checks grade only message.content, so by default they send reasoning_effort low and no
    # enable_thinking (that is only added when the t0-probe verdict says the server honours it).
    eye = load_eye_test()
    vb = ServedVisionAnswerer(eye.base_dir)._body(eye.items[0])
    assert vb["reasoning_effort"] == "low" and "chat_template_kwargs" not in vb
    sb = ServedSpokenAnswerer({}, context="x")._body(next(iter(load_spoken_set().items)))
    assert sb["reasoning_effort"] == "low"


def test_checks_apply_enable_thinking_when_the_probe_verdict_allows_it():
    apply = {"reasoning_effort": "low", "chat_template_kwargs": {"enable_thinking": False}}
    eye = load_eye_test()
    vb = ServedVisionAnswerer(eye.base_dir, reasoning_apply=apply)._body(eye.items[0])
    assert vb["chat_template_kwargs"] == {"enable_thinking": False}


def test_concurrent_answering_still_respects_the_budget():
    # a slow reasoning answer that cannot finish inside a tiny budget: the gate marks budget_hit
    # and leaves the unanswered items out (counted as misses), never blocking past the budget.
    import time
    spoken = load_spoken_set()

    def slow(it):
        time.sleep(0.3)
        return it.answer

    t0 = time.monotonic()
    answers, meta = answer_spoken_set(spoken, slow, budget_s=0.05, concurrency=8)
    assert meta["budget_hit"] and meta["concurrency"] == 8
    assert meta["answered"] < len(spoken)
    assert time.monotonic() - t0 < 2.0   # did not block waiting for every slow call


def test_concurrent_answering_collects_all_within_budget():
    spoken = load_spoken_set()
    answers, meta = answer_spoken_set(spoken, lambda it: it.answer, budget_s=30.0, concurrency=8)
    assert meta["answered"] == len(spoken) and not meta["budget_hit"]


def test_eye_test_supports_concurrency():
    eye = load_eye_test(require_files=False)
    answers, meta = answer_eye_test(eye, lambda it: "ok", concurrency=4)
    assert meta["answered"] == len(eye) and meta["concurrency"] == 4
