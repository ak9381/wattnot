"""Tests for Voice Guide (src/wattnot/meta/voiceguide.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wattnot.api.app import create_app
from wattnot.meta.voiceguide import (
    ALLOWED_ACTIONS,
    VoiceGuideActionError,
    classify_intent,
    get_quick_ack,
    parse_run_setup,
    process_voice_query,
    transcribe_audio,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_intent_classification() -> None:
    assert classify_intent("compare runs") == "navigate"
    assert classify_intent("show last night's report") == "navigate"
    assert classify_intent("configure a run with vllm") == "configure"
    assert classify_intent("prefill 350W caps") == "configure"
    assert classify_intent("what was the energy saving on the last run?") == "answer"
    assert classify_intent("tell me water consumption in mL") == "answer"
    assert classify_intent("what does 25% mean?") == "explain"
    assert classify_intent("explain 350W") == "explain"
    assert classify_intent("what does this mean?") == "explain"
    assert classify_intent("tell me about this number") == "explain"


def test_quick_ack_fast_path() -> None:
    nav_ack = get_quick_ack("navigate", "compare runs")
    assert nav_ack["intent"] == "navigate"
    assert "Compare" in nav_ack["ack"]

    cfg_ack = get_quick_ack("configure", "configure workload")
    assert cfg_ack["intent"] == "configure"
    assert "pre-fill" in cfg_ack["ack"]

    ans_ack = get_quick_ack("answer", "what is saving?")
    assert ans_ack["intent"] == "answer"
    assert "signed receipts" in ans_ack["ack"]

    exp_ack = get_quick_ack("explain", "what does 25% mean?")
    assert exp_ack["intent"] == "explain"
    assert "element" in exp_ack["ack"].lower()


def test_navigate_intent_compare_and_report() -> None:
    # Test "compare runs"
    res1 = process_voice_query("compare runs", root_dir=REPO_ROOT, mock=True)
    assert res1["action"] == "navigate"
    assert res1["intent"] == "navigate"
    assert "/dash/comparetree.html" in res1["route"]
    assert "prompt" in res1

    # Test "show last night's report"
    res2 = process_voice_query("show last night's report", root_dir=REPO_ROOT, mock=True)
    assert res2["action"] == "navigate"
    assert res2["intent"] == "navigate"
    assert "report.html" in res2["route"]
    assert "target_receipt_id" in res2


def test_answer_intent_cites_signed_receipt() -> None:
    # Query asking about energy savings
    res = process_voice_query("what was the billed saving on rcpt_20261027_run2_cap350?", root_dir=REPO_ROOT, mock=True)
    assert res["action"] == "answer"
    assert res["intent"] == "answer"
    assert "rcpt_20261027_run2_cap350" in res["text"]
    assert "citations" in res
    assert any("rcpt_" in c for c in res["citations"])
    assert "[MEASURED]" in res["text"] or "[ESTIMATE]" in res["text"]


def test_answer_intent_refuses_unanswerable_query() -> None:
    res = process_voice_query("what is the weather today in Honolulu?", root_dir=REPO_ROOT, mock=True)
    assert res["action"] == "answer"
    assert res["intent"] == "answer"
    assert res.get("refusal") is True
    assert "only answer questions grounded in verified" in res["text"]
    assert res["citations"] == []


def test_configure_intent_returns_prefill_requiring_user_review() -> None:
    res = process_voice_query("configure a new run for vllm", root_dir=REPO_ROOT, mock=True)
    assert res["action"] == "prefill"
    assert res["intent"] == "configure"
    assert "prefill" in res
    assert "vllm" in res["prefill"]["stack"]
    assert "caps_w" in res["prefill"]
    # Enforce note that user must press Start
    assert "Start" in res["prefill"]["note"] or "Start" in res["text"]


def test_action_allowlist_enforcement() -> None:
    assert ALLOWED_ACTIONS == {"navigate", "answer", "prefill", "explain"}
    # Verify that attempting an action outside the allowlist raises VoiceGuideActionError
    with pytest.raises(VoiceGuideActionError):
        from wattnot.meta import voiceguide

        # Simulate a malicious or accidental mutation action
        voiceguide.ALLOWED_ACTIONS = frozenset({"navigate", "answer"})
        try:
            res = {"action": "launch", "intent": "launch"}
            if res["action"] not in voiceguide.ALLOWED_ACTIONS:
                raise VoiceGuideActionError(f"Action '{res['action']}' forbidden")
        finally:
            voiceguide.ALLOWED_ACTIONS = ALLOWED_ACTIONS


def test_explain_intent() -> None:
    res = process_voice_query("what does 25% mean?", root_dir=REPO_ROOT, mock=True)
    assert res["action"] == "explain"
    assert res["intent"] == "explain"
    assert "25%" in res["target_query"]
    assert "Highlighting" in res["text"] or "Locating" in res["text"]
    assert res["label"] in {"MOCK", "MEASURED"}


def test_voiceguide_api_endpoints() -> None:
    app = create_app(str(REPO_ROOT))
    client = TestClient(app)

    # POST query (navigate)
    resp = client.post("/api/voiceguide", json={"query": "compare runs"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "navigate"
    assert "/dash/comparetree.html" in data["route"]

    # POST query (explain)
    resp_exp = client.post("/api/voiceguide", json={"query": "what does 25% mean?"})
    assert resp_exp.status_code == 200
    data_exp = resp_exp.json()
    assert data_exp["action"] == "explain"
    assert data_exp["intent"] == "explain"

    # GET query
    resp_get = client.get("/api/voiceguide?q=configure+new+run")
    assert resp_get.status_code == 200
    data_get = resp_get.json()
    assert data_get["action"] == "prefill"
    assert "prefill" in data_get


SPOKEN_SETUP_PHRASINGS = [
    # (phrase, expected cap_w, expected workload, expected framework)
    ("try 325 watts on the batch workload with langchain", 325, "batch", "langchain"),
    ("try three fifty watts on the batch workload with langchain", 350, "batch", "langchain"),
    ("set cap to 325W for batch workload using llamaindex", 325, "batch", "llamaindex"),
    ("use 400 W with plain-api on the replay workload", 400, "replay", "plain-api"),
    ("try 300 watts with openai-python", 300, None, "openai-python"),
    ("configure 350 watts batch workload", 350, "batch", None),
    ("prepare run with langchain at three hundred watts", 300, None, "langchain"),
    ("set up a run on the proof workload", None, "proof", None),
    ("try 325 w on batch workload with openai python", 325, "batch", "openai-python"),
]


@pytest.mark.parametrize(
    "phrase, cap_w, workload, framework", SPOKEN_SETUP_PHRASINGS
)
def test_spoken_run_setup_prefill_phrasings(phrase, cap_w, workload, framework) -> None:
    assert classify_intent(phrase) == "configure"
    parsed = parse_run_setup(phrase)
    assert parsed["cap_w"] == cap_w
    assert parsed["workload"] == workload
    assert parsed["framework"] == framework

    res = process_voice_query(phrase, root_dir=REPO_ROOT, mock=True)
    assert res["intent"] == "configure"
    assert res["action"] == "prefill"
    assert res["prefill"]["cap_w"] == cap_w
    assert res["prefill"]["workload"] == workload
    assert res["prefill"]["framework"] == framework


def test_spoken_setup_unknown_fields_stay_null() -> None:
    parsed = parse_run_setup("configure a new run")
    assert parsed == {"cap_w": None, "workload": None, "framework": None}
    res = process_voice_query("configure a new run", root_dir=REPO_ROOT, mock=True)
    assert res["action"] == "prefill"
    assert res["prefill"]["cap_w"] is None
    assert res["prefill"]["workload"] is None
    assert res["prefill"]["framework"] is None


def test_prefill_never_starts_stops_or_changes_run() -> None:
    res = process_voice_query(
        "try 325 watts on the batch workload with langchain", root_dir=REPO_ROOT, mock=True
    )
    assert res["action"] == "prefill"
    assert res["action"] not in {"launch", "start", "stop", "change"}
    for forbidden in ("launch", "start", "stop", "change"):
        assert forbidden not in res
    assert "Start" in res["prefill"]["note"] or "Start" in res["text"]
    assert "never starts, stops, or changes a run" in res["prefill"]["note"]


def test_all_four_intents_covered() -> None:
    assert classify_intent("compare runs") == "navigate"
    assert (
        classify_intent("what was the billed saving on rcpt_20261027_run2_cap350?") == "answer"
    )
    assert (
        classify_intent("try 325 watts on the batch workload with langchain") == "configure"
    )
    assert classify_intent("what does 25% mean?") == "explain"


def test_rejected_non_allowlisted_action(monkeypatch) -> None:
    monkeypatch.setattr(
        "wattnot.meta.voiceguide.ALLOWED_ACTIONS", frozenset({"navigate", "answer"})
    )
    with pytest.raises(VoiceGuideActionError):
        process_voice_query(
            "try 325 watts on the batch workload with langchain",
            root_dir=REPO_ROOT,
            mock=True,
        )
