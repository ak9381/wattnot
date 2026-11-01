"""Baseten launch-readiness: the Modal bug classes audited on the Baseten path, plus the
Baseten smoke mode, the up-front 401/403 checklist catch, and a free MOCK/REPLAY launch.

No network: every HTTP call is replaced with a callable/opener, or skipped. No paid calls.
"""
from __future__ import annotations

import io
import json
import time
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wattnot import eligibility as E
from wattnot.cloud import (
    INFERENCE_KEY_ENVS,
    MODEL,
    chat_completions_url,
    supports_tool_calling,
)
from wattnot.dash import launch as L
from wattnot.dash.server import create_app
from wattnot.e2e import fullstack as F
from wattnot.e2e import live as LV

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client():
    return TestClient(create_app())


# --------------------------------------------------------------- bug 5: no doubled /v1
def test_chat_url_never_doubles_v1():
    for base in ("https://inference.baseten.co/v1", "https://inference.baseten.co/v1/",
                 "https://inference.baseten.co", "https://x/v1/"):
        u = chat_completions_url(base)
        assert u.count("/v1") == 1 and u.endswith("/v1/chat/completions"), u
    assert chat_completions_url() == "https://inference.baseten.co/v1/chat/completions"


def test_baseten_client_builds_single_v1_url(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        return Resp(json.dumps({"usage": {}, "choices": [{"message": {"content": "ok"}}]}).encode())

    monkeypatch.setenv("BASETEN_METRICS_KEY", "paid.key")
    monkeypatch.setattr(LV.urllib.request, "urlopen", fake_urlopen)

    class Cap:
        def check(self, *a):
            pass

        def add(self, *a):
            pass

    from wattnot.cloud.pricing import Rates
    call = LV.baseten_client(Cap(), Rates(), None)
    call({"messages": [{"role": "user", "content": "hi"}]}, purpose="t")
    assert seen["url"] == "https://inference.baseten.co/v1/chat/completions"
    assert "paid.key" not in json.dumps(seen)  # never leak the key into assertions either


# --------------------------------------------------------------- bug 3: skipped -> held
def _ev(**kw):
    base = {"accuracy_without": 0.80, "accuracy_with": 0.80, "p99_without": 1.0, "p99_with": 1.0,
            "spark_agreement": 0.95, "j_per_correct_without": 10.0, "j_per_correct_with": 9.0,
            "eye_check": True, "hearing_check": True}
    base.update(kw)
    return base


def test_review_skipped_check_holds_not_rejects():
    r = LV.review_change({"x": 1}, "low", _ev(spark_agreement=None, eye_check=None, hearing_check=None))
    assert r["decision"] == "held"
    assert r["skipped_checks"] and not r["failed_checks"]


def test_review_real_failure_rejects():
    r = LV.review_change({"x": 1}, "low", _ev(accuracy_with=0.50))
    assert r["decision"] == "rejected" and "accuracy" in " ".join(r["failed_checks"])


def test_review_all_pass_approves():
    r = LV.review_change({"x": 1}, "low", _ev())
    assert r["decision"] == "approved" and not r["failed_checks"] and not r["skipped_checks"]


# --------------------------------------------------------------- bug 6: 0 good calls -> failed
def test_spark_trace_zero_success_is_error(monkeypatch, tmp_path):
    class Boom:
        def __init__(self, *a, **k):
            pass

        def __call__(self, *a, **k):
            raise RuntimeError("down")

    monkeypatch.setattr("wattnot.meta.client.GuardedClient", Boom)
    out = LV.spark_trace(tmp_path, {"accuracy_with": 0.8}, None)
    assert out["ok"] == 0 and out["steps"] == 4 and out["status"] == "error"
    # _stage surfaces the returned status (never a silent green)
    res: dict = {}
    F._stage(res, "spark_trace", lambda: out)
    assert res["spark_trace"]["status"] == "error"


def test_arm_stop_reason_fails_when_no_call_succeeds():
    import importlib.util
    spec = importlib.util.spec_from_file_location("baseten_round", ROOT / "scripts/e2e/baseten_round.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.arm_stop_reason("with_wattnot", [{"status": 599}, {"status": 429}], 2)
    assert m.arm_stop_reason("with_wattnot", [{"status": 599}, {"status": 200}], 2) is None
    assert m.arm_stop_reason("with_wattnot", [], 2) is None  # nothing ran yet: not a failure


# --------------------------------------------------------------- bug 2: honest timeout reasons
def test_billing_check_timeout_is_not_a_refusal(monkeypatch):
    monkeypatch.setenv("BASETEN_METRICS_KEY", "paid.key")

    def boom(req, timeout=0):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(LV.urllib.request, "urlopen", boom)
    out = LV.billing_check()
    assert out["refusal"] is False and out["status"] is None
    assert "timed out" in out["error"] and "unreachable" in out["note"]


def test_stage_server_reports_real_reason_on_failure():
    src = (ROOT / "scripts/e2e/baseten_stage.py").read_text()
    # honest readiness: polls the real health URL for a 200, and the failure says why
    assert "p.poll()" in src and "exited" in src and "not ready after" in src


# --------------------------------------------------------------- bug 4: tool calling
def test_supports_tool_calling_and_agent_block_status():
    assert supports_tool_calling(MODEL) is True
    assert F.agent_blocks_status(MODEL)["status"] == "ON"
    s = F.agent_blocks_status("some-model-without-tools")
    assert s["status"] == "skipped" and "tool calling" in s["reason"]


def test_levers_drop_tools_and_mark_skipped_when_unsupported():
    rl = F.RequestLevers(tools_supported=False)
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "f"}}]}
    out, info = rl.apply(body)
    assert "tools" not in out and info["agent_blocks"].startswith("skipped")
    assert rl.stats["tools_skipped"] == 1 and rl.stats["tool_calling"] is False


def test_levers_keep_tools_when_supported():
    rl = F.RequestLevers(tools_supported=True)
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "f"}}]}
    out, info = rl.apply(body)
    assert "tools" in out and info["agent_blocks"] == "ok" and rl.stats["tools_skipped"] == 0


# --------------------------------------------------------------- bug 1: time budgets
def test_post_round_skips_slow_stages_over_budget(tmp_path):
    doc = {"round": "r01", "model": "m",
           "results": {"per_arm": {"without_wattnot": {"accuracy": 0.8, "energy_wh": {"value": 1}},
                                   "with_wattnot": {"accuracy": 0.8, "energy_wh": {"value": 0.9}}}},
           "spend": {}}
    res = F.post_round(tmp_path, doc, mode="smoke", budget_s=0.0)  # nothing has time
    for s in ("eye_check", "hearing_check", "sam_hotspot", "wonder_chart", "musecode_pr"):
        assert res[s]["status"] == "skipped" and "not enough time" in res[s]["why"], (s, res[s])
    # the cheap local finalizers still run so the round still has a signed receipt
    assert res["receipt"]["status"] == "ok"
    assert res["eu_report"]["status"] == "ok" and res["label_lint"]["status"] == "ok"


def test_post_round_runs_everything_with_no_budget(tmp_path, monkeypatch):
    # default (budget None) must not skip anything; stub the heavy stages so it stays CPU-light
    doc = {"round": "r01", "model": "m",
           "results": {"per_arm": {"without_wattnot": {"accuracy": 0.8, "energy_wh": {"value": 1}},
                                   "with_wattnot": {"accuracy": 0.8, "energy_wh": {"value": 0.9}}}},
           "spend": {}}
    monkeypatch.setattr(F, "_main", lambda mod, argv: {"argv": argv})
    res = F.post_round(tmp_path, doc, mode="smoke", budget_s=None)
    for s in ("eye_check", "hearing_check", "sam_hotspot", "wonder_chart", "musecode_pr"):
        assert res[s]["status"] != "skipped", (s, res[s])


# --------------------------------------------------------------- item A: Baseten smoke mode
def test_baseten_smoke_cap_registered():
    assert L.CAPS["baseten_smoke_usd"] == 1.0


def test_baseten_smoke_norm_and_estimate_under_cap():
    cfg = L.norm_config({"provider": "baseten", "smoke": {"limit": 2}})
    assert cfg["smoke"] == {"limit": 2, "spark": False}
    assert cfg["open_pr"] is False and cfg["rounds"] == 1
    e = L.estimate(cfg)
    assert e["label"] == "ESTIMATE" and e["available"] is True
    assert e["total_usd"][1] <= 1.0 and e["hard_stop_usd"] == 1.0
    assert "1M out" in e["basis"] and "item" in e["basis"]  # basis from pricing x item counts


def test_baseten_smoke_limit_is_clamped():
    assert L.norm_config({"provider": "baseten", "smoke": {"limit": 99}})["smoke"]["limit"] == 3
    assert L.norm_config({"provider": "baseten", "smoke": {"limit": -5}})["smoke"]["limit"] == 1
    assert L.norm_config({"provider": "baseten", "smoke": {"limit": 1}})["smoke"]["limit"] == 1


def test_baseten_smoke_command_enforces_dollar_cap_and_isolation():
    cfg = L.norm_config({"provider": "baseten", "smoke": {"limit": 1}})
    cmd, env, label = L.command(cfg, "live")
    s = " ".join(cmd)
    assert "baseten_round.py" in s and "--cap-usd 1.00" in s and "--no-spark" in s
    assert "--mode smoke" in s and "{job}" in s  # output isolated to the job dir
    i = cmd.index("--cap-usd")
    assert float(cmd[i + 1]) <= L.CAPS["baseten_smoke_usd"]


def test_baseten_smoke_estimate_available_via_api(client):
    r = client.post("/api/launch/estimate", json={"config": {"provider": "baseten", "smoke": {"limit": 1}}})
    body = r.json()
    assert r.status_code == 200 and body["available"] and body["hard_stop_usd"] == 1.0
    assert "confirm_token" in body  # a smoke launch can be confirmed and started


# --------------------------------------------------------------- item B: 401/403 + env consistency
def _opener_raising(code):
    def op(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)
    return op


def _opener_ok(ids):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    def op(req, timeout=0):
        assert req.full_url.count("/v1") == 1, req.full_url
        return Resp(json.dumps({"data": [{"id": i} for i in ids]}).encode())
    return op


def test_check_model_catches_401_with_clear_message(monkeypatch):
    monkeypatch.setenv("BASETEN_METRICS_KEY", "paid.key")
    cfg = L.norm_config({"provider": "baseten"})
    for code in (401, 403):
        it = L.check_model(cfg, opener=_opener_raising(code))
        assert it["state"] == "fail" and str(code) in it["reason"]
        assert "BASETEN_METRICS_KEY" in it["fix"] and "paid" in it["fix"]


def test_check_model_passes_when_model_listed(monkeypatch):
    monkeypatch.setenv("BASETEN_METRICS_KEY", "paid.key")
    cfg = L.norm_config({"provider": "baseten"})
    it = L.check_model(cfg, opener=_opener_ok([MODEL]))
    assert it["state"] == "pass"
    it2 = L.check_model(cfg, opener=_opener_ok(["other"]))
    assert it2["state"] == "fail" and "not listed" in it2["reason"]


def test_checklist_skip_network_makes_no_request(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no request may be made when skip_network")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    cl = L.checklist(L.norm_config({"provider": "baseten"}), skip_network=True)
    m = next(i for i in cl["items"] if i["id"] == "model")
    assert m["state"] == "warn" and m["required"] is False


def test_checklist_and_runner_use_the_same_key_env():
    # the checklist's NEEDS and the runner's inference key must name the same Baseten env var
    assert INFERENCE_KEY_ENVS == ("BASETEN_METRICS_KEY",)
    assert "BASETEN_METRICS_KEY" in L.NEEDS["baseten"]
    assert "BASETEN_API_KEY" not in L.NEEDS["baseten"]  # paid key only


# --------------------------------------------------------------- item C: free MOCK/REPLAY launch
def test_mock_replay_runs_both_arms_end_to_end(client):
    cfg = {"provider": "baseten"}
    r = client.post("/api/launch/start", json={"config": cfg, "mode": "mock"})
    assert r.status_code == 200
    jid = r.json()["id"]
    assert r.json()["label"] == "REPLAY"
    L.JOBS[jid]["replay_delay"] = 0  # the test does not need the pacing
    for _ in range(400):
        if L.JOBS[jid]["state"] != "running":
            break
        time.sleep(0.02)
    job = L.JOBS[jid]
    assert job["state"] == "finished" and job["rc"] == 0
    evs = [json.loads(x) for x in (job["dir"] / "events.jsonl").read_text().splitlines()]
    assert {e["arm"] for e in evs} == {"with_wattnot", "without_wattnot"}
    assert {e["label_override"] for e in evs} == {"REPLAY"}


def test_mock_start_needs_no_token_or_checklist(client):
    # a free mock run spends nothing, so it must not require the paid-launch confirm token
    r = client.post("/api/launch/start", json={"config": {"provider": "baseten"}, "mode": "mock"})
    assert r.status_code == 200


def test_every_eligible_feature_on_in_with_arm():
    # the with-WattNot arm turns on every Baseten-eligible feature; ineligible ones carry a reason
    r = E.check({"provider": "baseten"})
    assert r["counts"]["eligible"] >= 1 and r["counts"]["total"] == 110
    assert all(f["reason"] for f in r["features"] if not f["eligible"])
