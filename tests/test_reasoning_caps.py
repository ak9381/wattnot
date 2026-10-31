"""The reasoning capability probe + caps file the vision/spoken/probe checks read (item C)."""

from __future__ import annotations

import json

from wattnot import reasoning


def _server(*, think_accepted: bool, baseline_rt: int, low_rt: int, think_rt: int, measured=True):
    """A fake chat server: returns usage reasoning tokens that depend on which knob was sent, and
    rejects (HTTP-400-like) the enable_thinking request when ``think_accepted`` is False."""
    def send(body: dict):
        ctk = body.get("chat_template_kwargs")
        if ctk and not think_accepted:
            return False, None, "HTTP 400"
        if ctk:
            rt = think_rt
        elif body.get("reasoning_effort") == "low":
            rt = low_rt
        else:
            rt = baseline_rt
        if measured:
            data = {"choices": [{"message": {"content": "5"}}],
                    "usage": {"completion_tokens": 20,
                              "completion_tokens_details": {"reasoning_tokens": rt}}}
        else:  # no usage: must estimate from the reasoning text
            data = {"choices": [{"message": {"content": "5", "reasoning_content": "x" * (rt * 4)}}]}
        return True, data, "ok"
    return send


def test_probe_enables_thinking_only_when_accepted_and_it_reduces_reasoning():
    v = reasoning.probe_reasoning(
        _server(think_accepted=True, baseline_rt=800, low_rt=200, think_rt=50), "m")
    assert v["apply"]["reasoning_effort"] == "low"
    assert v["apply"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert v["probes"]["enable_thinking_false"]["reasoning_tokens"] == 50
    assert v["probes"]["enable_thinking_false"]["label"] == "MEASURED"


def test_probe_keeps_thinking_when_server_rejects_the_field():
    v = reasoning.probe_reasoning(
        _server(think_accepted=False, baseline_rt=800, low_rt=200, think_rt=0), "m")
    assert v["apply"] == {"reasoning_effort": "low"}            # no chat_template_kwargs
    assert v["probes"]["enable_thinking_false"]["accepted"] is False


def test_probe_keeps_thinking_when_it_does_not_reduce_tokens():
    # accepted but no reduction (think_rt == baseline): do not turn it on
    v = reasoning.probe_reasoning(
        _server(think_accepted=True, baseline_rt=300, low_rt=300, think_rt=300), "m")
    assert "chat_template_kwargs" not in v["apply"]


def test_probe_estimates_tokens_when_usage_is_absent():
    v = reasoning.probe_reasoning(
        _server(think_accepted=True, baseline_rt=800, low_rt=200, think_rt=50, measured=False), "m")
    assert v["probes"]["baseline"]["label"] == "ESTIMATE"
    assert v["probes"]["baseline"]["reasoning_tokens"] == 800   # len/4 round-trips the estimate


def test_load_apply_reads_the_caps_file_else_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv(reasoning.CAPS_ENV, str(tmp_path / "missing.json"))
    assert reasoning.load_apply() == {"reasoning_effort": "low"}   # absent file -> low default
    p = tmp_path / "caps.json"
    p.write_text(json.dumps({"apply": {"reasoning_effort": "low",
                                       "chat_template_kwargs": {"enable_thinking": False}}}))
    monkeypatch.setenv(reasoning.CAPS_ENV, str(p))
    assert reasoning.load_apply()["chat_template_kwargs"] == {"enable_thinking": False}


def test_write_and_apply_round_trip(tmp_path):
    v = reasoning.probe_reasoning(
        _server(think_accepted=True, baseline_rt=800, low_rt=200, think_rt=50), "m")
    p = reasoning.write_caps(v, tmp_path / "caps.json")
    body = reasoning.apply_to_body({"model": "m", "messages": []}, reasoning.load_apply(p))
    assert body["reasoning_effort"] == "low"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
