"""Effort step-up routing: a runaway low call is cut by the low cap and the reason a step-up to
medium fires (length/format/wrong) is recorded, so a cut runaway is told apart from a real miss.

Root cause this covers: on the gsm8k "Janet's ducks" item a low try ran away to 2009 reasoning
tokens and hit finish=length at the old 1500 cap, costing more than the medium retry. The low cap
is now a runaway cap (768), and run_item records WHY it stepped up.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from wattnot.cloud import efforts
from wattnot.cloud.transport import Reply as CReply

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "e2e" / "baseten_round.py"
_spec = importlib.util.spec_from_file_location("baseten_round", _PATH)
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)


# --- the low cap is a runaway cap, not a budget ---------------------------------------------

def test_low_allowance_is_a_runaway_cap():
    # Measured low calls used 21-363 reasoning tokens; 768 is well above that but below a medium
    # call, so a runaway low is cut early.
    assert efforts.DEFAULT_ALLOWANCE["low"] == 768
    assert efforts.max_tokens_for("low", 512) == 512 + 768
    # still ordered below the higher levels
    assert efforts.max_tokens_for("low", 512) < efforts.max_tokens_for("medium", 512)


# --- the reason a step-up fired -------------------------------------------------------------

def test_step_up_reason_classifies_length_format_wrong():
    assert br.step_up_reason({"finish": "length", "answer": "partial thought"}) == "length"
    assert br.step_up_reason({"finish": "context-limit", "answer": ""}) == "length"
    assert br.step_up_reason({"finish": "stop", "answer": "   "}) == "format"
    assert br.step_up_reason({"finish": "stop", "answer": "ANSWER: 25"}) == "wrong"


# --- run_item drives the step-up with a fake chat -------------------------------------------

class _FakeBench:
    """Minimal public-dataset bench: a fixed prompt and a grader that accepts exactly 'RIGHT'."""

    def grader(self, ds):
        return (lambda obj: "how many ducks?"), (lambda obj, text: (text or "").strip() == "RIGHT")


class _FakeTransport:
    """Return a canned reply per reasoning_effort so run_item's low-then-medium path is exercised."""

    model = "meta-models/muse-spark-open"

    def __init__(self, by_effort):
        self.by_effort = by_effort
        self.seen = []

    def __call__(self, req):
        effort = req.body.get("reasoning_effort")
        self.seen.append(effort)
        content, finish = self.by_effort[effort]
        return CReply(200, {"choices": [{"message": {"content": content}, "finish_reason": finish}],
                            "usage": {"prompt_tokens": 12, "completion_tokens": 30,
                                      "completion_tokens_details": {"reasoning_tokens": 7}}}, 0.1)


class _NoCap:
    session_usd = 0.0

    def check(self, w):
        pass

    def add(self, usd):
        pass


def _run(by_effort):
    br.BasetenChat.levers = None   # no waste-guard rewrite in the test
    br.BasetenChat.log = None      # no EventLog
    calls = br.Calls()
    tr = _FakeTransport(by_effort)
    it = ("gsm8k", "janet-ducks", {"question": "how many ducks?"})
    return br.run_item(_FakeBench(), tr, _NoCap(), calls, "with_wattnot", it), tr


def test_runaway_low_length_steps_up_with_reason_length():
    r, tr = _run({"low": ("", "length"), "medium": ("RIGHT", "stop")})
    assert r["tries"] == ["low", "medium"]
    assert r["step_up_reason"] == "length"
    assert r["deterministic"] is True
    assert tr.seen == ["low", "medium"]


def test_valid_low_is_kept_with_no_step_up():
    r, tr = _run({"low": ("RIGHT", "stop")})
    assert r["tries"] == ["low"]
    assert "step_up_reason" not in r
    assert r["deterministic"] is True
    assert tr.seen == ["low"]  # medium never called


def test_wrong_low_steps_up_with_reason_wrong():
    r, _ = _run({"low": ("WRONG", "stop"), "medium": ("RIGHT", "stop")})
    assert r["tries"] == ["low", "medium"]
    assert r["step_up_reason"] == "wrong"


def test_empty_low_steps_up_with_reason_format():
    r, _ = _run({"low": ("", "stop"), "medium": ("RIGHT", "stop")})
    assert r["tries"] == ["low", "medium"]
    assert r["step_up_reason"] == "format"
