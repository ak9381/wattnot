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
    # run2's measurement block is labelled MEASURED and its water figure ESTIMATE;
    # the answer must carry the receipt's own labels, not hardcoded ones.
    assert "[MEASURED]" in res["text"]
    assert "mL/req [ESTIMATE]" in res["text"]


def test_answer_labels_follow_receipt_not_hardcoded(tmp_path) -> None:
    # A SIMULATED receipt must never be spoken as MEASURED.
    from wattnot.receipt.build import build_receipt

    run = {
        "run_id": "run_20261029_sim",
        "subject": {"model": "llama-8b", "host": "sim-box", "mode": "mock",
                    "workload": {"n_requests": 10}},
        "baseline": {"params": {"cap_w": 400}, "reps": 2, "p99_ms": 120.0,
                     "wh_per_req": {"mean": 2.5, "samples": [2.5, 2.4]},
                     "quality": {"fixed_pass_rate": 0.95}},
        "candidate": {"params": {"cap_w": 350}, "reps": 2, "p99_ms": 130.0,
                      "wh_per_req": {"mean": 2.0, "samples": [2.0, 1.9]},
                      "quality": {"fixed_pass_rate": 0.95}},
        "measurement": {"meter": "nvml", "hz": 10, "csv": "trace.csv", "label": "SIMULATED"},
    }
    receipt = build_receipt(run, created_at="2026-10-29T00:00:00+00:00")
    rdir = tmp_path / "receipts"
    rdir.mkdir()
    (rdir / f"{receipt['receipt_id']}.json").write_text(json.dumps(receipt))

    res = process_voice_query(
        f"what was the saving on {receipt['receipt_id']}?", root_dir=tmp_path, mock=True
    )
    assert res["action"] == "answer"
    assert "[SIMULATED]" in res["text"]
    assert "Wh [MEASURED]" not in res["text"]
    assert "% [MEASURED]" not in res["text"]


def test_grounding_rejects_forged_receipts(tmp_path) -> None:
    # Hash-valid but signature-forged receipts must not ground answers.
    import shutil

    rdir = tmp_path / "receipts"
    rdir.mkdir()
    for n in ("forged-rehashed", "forged-otherkey"):
        shutil.copy(REPO_ROOT / "verifier" / "samples" / f"{n}.json", rdir / f"{n}.json")
        shutil.copy(REPO_ROOT / "verifier" / "samples" / f"{n}.sigstore.json",
                    rdir / f"{n}.sigstore.json")
    res = process_voice_query("what was the energy saving?", root_dir=tmp_path, mock=True)
    assert res.get("refusal") is True
    assert res["citations"] == []


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


def test_voiceguide_post_content_type_handling() -> None:
    app = create_app(str(REPO_ROOT))
    client = TestClient(app)

    # application/json with a charset parameter is still accepted.
    ok = client.post("/api/voiceguide", content=json.dumps({"query": "compare runs"}),
                     headers={"content-type": "application/json; charset=utf-8"})
    assert ok.status_code == 200
    assert ok.json()["action"] == "navigate"

    # A non-JSON media type is refused cleanly with a 415 and a JSON body.
    bad = client.post("/api/voiceguide", content="compare runs",
                      headers={"content-type": "text/plain"})
    assert bad.status_code == 415
    assert bad.json()["code"] == "unsupported_media_type"

    # application/json that is not valid JSON is a clean 400, not a guessed parse.
    broken = client.post("/api/voiceguide", content="{not json",
                         headers={"content-type": "application/json"})
    assert broken.status_code == 400
    assert broken.json()["code"] == "bad_json"


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


def test_voiceguide_api_endpoint_post_and_get() -> None:
    import base64

    app = create_app(REPO_ROOT)
    client = TestClient(app)

    # Test GET
    resp_get = client.get("/api/voiceguide?q=compare+runs")
    assert resp_get.status_code == 200
    data_get = resp_get.json()
    assert data_get["action"] == "navigate"
    assert "/dash/comparetree.html" in data_get["route"]

    # Test POST JSON query text
    resp_post = client.post("/api/voiceguide", json={"query": "show last night's report"})
    assert resp_post.status_code == 200
    data_post = resp_post.json()
    assert data_post["action"] == "navigate"
    assert "report.html" in data_post["route"]

    # Test POST JSON base64 audio
    audio_payload = base64.b64encode(b"compare runs").decode("ascii")
    resp_audio = client.post("/api/voiceguide", json={"audio_b64": audio_payload})
    assert resp_audio.status_code == 200
    data_audio = resp_audio.json()
    assert data_audio["action"] == "navigate"
    assert "/dash/comparetree.html" in data_audio["route"]



# --- voice fixes 2026-10-29 -------------------------------------------------
def test_voice_run_two_resolves_to_run2_receipt():
    from pathlib import Path
    from wattnot.meta.voiceguide import process_voice_query
    root = Path(__file__).resolve().parents[1]
    for q in ("what did run two save", "what did run to save", "what did run 2 save"):
        out = process_voice_query(query_text=q, root_dir=root)
        assert not out.get("refusal"), q
        assert any("run2" in c for c in out["citations"]), q
        assert out["label"] == "MOCK"


def test_undecodable_audio_without_live_says_type_instead():
    from wattnot.meta.voiceguide import process_voice_query
    out = process_voice_query(audio_data=b"\x1aE\xdf\xa3\x00\x00\xff", root_dir=".")
    assert out["transcript"] == ""
    assert "Type your question" in out["text"]
    assert out["label"] == "MOCK"


def test_api_voice_audio_is_mock_unless_switched_on(monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from wattnot.api.app import create_app
    monkeypatch.delenv("WATTNOT_VOICE_LIVE", raising=False)
    c = TestClient(create_app("."))
    r = c.post("/api/voiceguide", json={"audio_b64": base64.b64encode(b"RIFF\x00\x00\xff\xfe").decode()})
    assert r.status_code == 200 and r.json()["label"] == "MOCK"
    assert "Type your question" in r.json()["text"]


def test_why_325_rejected_answered_from_receipt():
    # The headline live-replay transcript asks exactly this; it must be answered
    # from the signed Wonder addendum trials, not refused.
    for q in ("why was 325 watts rejected", "Why was 325 watts rejected?", "why was 325 w rejected"):
        out = process_voice_query(query_text=q, root_dir=REPO_ROOT, mock=True)
        assert out["action"] == "answer", q
        assert not out.get("refusal"), q
        # Same measured facts the client (receiptanswers.js) shows, read from the
        # receipt: p99 rise vs the 10% limit, J per correct, pass rate, MEASURED.
        assert "p99 latency rose 10.9% against the 10% limit" in out["text"], q
        assert "11.8444 J" in out["text"], q
        assert "93.7%" in out["text"], q
        assert "[MEASURED]" in out["text"], q
        assert "rcpt_20261027_run2_cap350" in out["citations"], q
        assert "rcpt_20261027_run2_cap350.wonder_live" in out["citations"], q


def test_accepted_cap_question_reports_kept():
    out = process_voice_query(query_text="why did 350 watts fail", root_dir=REPO_ROOT, mock=True)
    assert not out.get("refusal")
    assert "350 W was kept" in out["text"]
    assert "within the 10% limit" in out["text"]


def test_unknown_cap_rejection_keeps_honest_refusal():
    # A cap with no trial in the receipt must still refuse, not invent numbers.
    out = process_voice_query(query_text="why was 999 watts rejected", root_dir=REPO_ROOT, mock=True)
    assert out.get("refusal") is True
    assert out["citations"] == []


def test_api_voice_live_failure_degrades(monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    import wattnot.meta.voiceguide as vg
    from wattnot.api.app import create_app
    monkeypatch.setenv("WATTNOT_VOICE_LIVE", "1")
    monkeypatch.delenv("WATTNOT_META_MOCK", raising=False)
    def boom(*a, **k):
        raise OSError("net down")
    monkeypatch.setattr(vg, "transcribe_audio", boom)
    c = TestClient(create_app("."))
    r = c.post("/api/voiceguide", json={"audio_b64": base64.b64encode(b"RIFF\xff\xfe").decode()})
    assert r.status_code == 502 and "Type your question" in r.json()["text"]
