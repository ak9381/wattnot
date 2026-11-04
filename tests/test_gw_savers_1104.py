"""Gateway savers, 4 Nov: gate setup line, no-op effort savers, persisted gate with distinct-prompt
pairs, opt-in brief answers, tool-call agreement and block validity."""
import json

from fastapi.testclient import TestClient

from wattnot.gateway import deploy
from wattnot.gateway.build import build_app, is_short_qa, load_config, same_model_tiers
from wattnot.gateway.gate import SaverGate
from wattnot.gateway.monitor import prompt_key
from wattnot.gateway.traffic import arm_agreement, setup_split
from wattnot.node.answercheck import block_validity

M = "meta-models/Muse-Glimmer-30B"


def upstream(calls, text="17 + 26 = 43"):
    def up(body, headers):
        calls.append(body)
        return {"id": "x", "object": "chat.completion", "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 60, "completion_tokens": 20}}
    return up


def glimmer_cfg(tmp_path, **gate):
    cfg = load_config(None)
    cfg["savings_log"] = str(tmp_path / "s.jsonl")
    cfg["tiers"] = [{"name": "small", "model": M, "body_extra": {"reasoning_effort": "low"}},
                    {"name": "big", "model": M, "body_extra": {}}]
    cfg["gate"].update(gate)
    return cfg


def ask(c, text):
    return c.post("/v1/chat/completions", json={"model": M, "messages": [{"role": "user", "content": text}]})


# (1) one-time setup line -------------------------------------------------------------------
def test_setup_split_charges_warmup_at_steady_median_and_shows_excess_apart():
    per = [["agent", 1020.0, "gate warm-up"], ["agent", 1040.0, "gate warm-up"], ["agent", 1044.0, "gate warm-up"]] + \
          [["agent", 400.0 + i] for i in range(10)]
    s = setup_split(per)
    assert s["requests"] == 3 and s["steady_j_per_request"] == 404.5
    assert s["joules"] == round(1020 + 1040 + 1044 - 3 * 404.5, 1)
    assert "ESTIMATE" in s["label"] and "MEASURED" in s["label"]


def test_setup_split_none_without_warmup_or_steady_requests():
    assert setup_split([["text", 100.0]] * 5) is None
    assert setup_split([["text", 900.0, "gate warm-up"], ["text", 100.0]]) is None


def test_card_shows_setup_line_apart(tmp_path):
    from wattnot.gateway import plug
    log = tmp_path / "s.jsonl"
    log.write_text(json.dumps({"ts": 5, "status": 200}) + "\n")
    run = {"run_id": "seq-x", "workload": "agent", "without": {"joules": 100.0}, "with": {"joules": 90.0, "t0": 1, "t1": 10, "requests": 1,
           "setup": {"requests": 3, "joules": 1850.0}, "shadow_excluded": {"calls": 3, "joules": 1224.3}}}
    (tmp_path / "sequential.runs.jsonl").write_text(json.dumps(run) + "\n")
    out = plug.gate_by_run(str(log), None)
    assert out[0]["setup"]["joules"] == 3074.3 and out[0]["setup"]["shadow_calls"] == 3


# (2) effort savers that cannot change anything -------------------------------------------------
def test_same_model_effort_noop_from_config_sends_stock_body_and_no_shadow(tmp_path):
    cfg = glimmer_cfg(tmp_path, effort_noop_models=[M])
    assert same_model_tiers(cfg)
    calls = []
    c = TestClient(build_app(cfg, upstream=upstream(calls)))
    for q in ("What is 17 + 26?", "Capital of Kenya?", "12 x 11?"):
        assert ask(c, q).status_code == 200
    assert calls and all("reasoning_effort" not in b for b in calls)
    assert len(calls) == 3   # no shadow calls to stock
    st = c.app.state.gate.status()
    assert {e["saver"] for e in st["events"] if e["event"] == "saver_noop"} == {"routing", "thinking_cap"}


def test_effort_noop_detected_from_identical_pairs_stops_shadowing():
    g = SaverGate(min_pairs=3)
    for i in range(2):
        g.record_pair(M, 80, 80, True, ["routing", "thinking_cap"], small_in=60, base_in=60,
                      prompt_key=f"p{i}", small_out=20, base_out=20)
    assert not g.warming(M, ["routing", "thinking_cap"])
    assert not g.allow("routing", M) and not g.allow("thinking_cap", M)
    assert g.events[-1]["event"] == "saver_noop"


def test_effort_that_changes_output_is_not_noop():
    g = SaverGate(min_pairs=3)
    g.record_pair(M, 80, 80, True, ["routing"], small_in=60, base_in=60, prompt_key="a", small_out=20, base_out=20)
    g.record_pair(M, 90, 80, True, ["routing"], small_in=60, base_in=60, prompt_key="b", small_out=30, base_out=20)
    assert g.allow("routing", M) and not g.noop


# (3) persisted gate, distinct prompts -----------------------------------------------------------
def test_gate_state_survives_restart(tmp_path):
    p = tmp_path / "gate.json"
    g = SaverGate(min_pairs=3, state_path=p)
    for i in range(3):
        g.record_pair(M, 2589, 2217, False, ["agent_waste"], small_in=745, base_in=694, prompt_key=f"k{i}")
    assert not g.allow("agent_waste", M)
    g2 = SaverGate(min_pairs=3, state_path=p)
    assert not g2.allow("agent_waste", M) and not g2.warming(M, ["agent_waste"])
    assert g2.status()["persisted"]
    g2.reset()
    assert SaverGate(state_path=p).allow("agent_waste", M)


def test_repeated_prompt_counts_once():
    g = SaverGate(min_pairs=3)
    for _ in range(2):
        g.record_pair(M, 100, 100, True, ["context_saver"], prompt_key="same")
    assert g.warming(M, ["context_saver"])
    assert len(g.pairs[g._key(M, ["context_saver"])]) == 1          # 2 repeats = 1 sample
    g.record_pair(M, 100, 100, True, ["context_saver"], prompt_key="same")
    assert not g.warming(M, ["context_saver"])   # warm-up still ends after 3 checks: no endless shadowing
    assert len(g.pairs[g._key(M, ["context_saver"])]) == 1


def test_prompt_key_ignores_model_and_effort():
    a = {"model": "x", "reasoning_effort": "low", "messages": [{"role": "user", "content": "hi"}]}
    b = {"model": "y", "messages": [{"role": "user", "content": "hi"}]}
    assert prompt_key(a) == prompt_key(b)
    assert prompt_key(a) != prompt_key({"messages": [{"role": "user", "content": "ho"}]})


# (4) brief answers, opt-in -----------------------------------------------------------------------
def test_brief_answers_off_by_default(tmp_path):
    cfg = glimmer_cfg(tmp_path)
    assert cfg["savers"]["brief_answers"]["enabled"] is False
    calls = []
    c = TestClient(build_app(cfg, upstream=upstream(calls)))
    ask(c, "What is 17 + 26?")
    assert all(m["role"] != "system" for m in calls[0]["messages"])
    assert deploy.to_config({"deployment": "gateway"})["savers"]["brief_answers"]["enabled"] is False
    assert deploy.to_config({"deployment": "gateway", "savers": {"brief_answers": True}})["savers"]["brief_answers"]["enabled"]


def test_brief_answers_on_adds_one_system_line_to_short_qa_only(tmp_path):
    cfg = glimmer_cfg(tmp_path, effort_noop_models=[M])
    cfg["savers"]["brief_answers"]["enabled"] = True
    calls = []
    c = TestClient(build_app(cfg, upstream=upstream(calls)))
    ask(c, "What is 17 + 26?")
    first = calls[0]["messages"][0]
    assert first["role"] == "system" and "briefly" in first["content"]
    assert not is_short_qa({"messages": [{"role": "user", "content": "x"}], "tools": [{"type": "function"}]}, 300)
    assert not is_short_qa({"messages": [{"role": "user", "content": "x " * 2000}]}, 300)
    assert not is_short_qa({"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]}, 300)


def test_brief_answers_switched_off_when_answers_differ():
    g = SaverGate(min_pairs=3)
    for i in range(3):
        g.record_pair(M, 60, 100, False, ["brief_answers"], small_in=75, base_in=60, prompt_key=str(i))
    assert not g.allow("brief_answers", M)


# (5) agent agreement and block validity ----------------------------------------------------------
def _tc(name, args):
    return [[name, json.dumps(args, sort_keys=True)]]


def test_arm_agreement_uses_tool_name_and_args():
    wo = {"texts": ["", "", ""], "tool_calls": [_tc("search", {"q": "a"}), _tc("calc", {"x": 1}), _tc("calc", {"x": 1})]}
    w = {"texts": ["", "", ""], "tool_calls": [_tc("search", {"q": "a"}), _tc("calc", {"x": 2}), _tc("lookup", {"x": 1})]}
    assert arm_agreement(w, wo) == round(1 / 3, 3)
    assert arm_agreement(wo, wo) == 1.0


def test_empty_vs_empty_text_is_not_agreement():
    assert arm_agreement({"texts": ["", ""]}, {"texts": ["", ""]}) == 0.0
    assert arm_agreement({"texts": ["17 + 26 = 43"]}, {"texts": ["43"]}) == 1.0


def test_clean_block_is_valid():
    samples = [(375.0, 370.0 + (i % 3)) for i in range(30)]
    joules = sum(p for _, p in samples) / len(samples) * 30.0
    assert block_validity(375, 375.0, 375.0, samples, joules, 30.0)["valid"]
    # 8% counter drift (normal 1 Hz sampling error) still valid
    assert block_validity(375, 375.0, 375.0, samples, joules * 1.08, 30.0)["valid"]
    # missing readings never drop a block on their own
    assert block_validity(700, None, None, [(None, None)] * 5, 1000.0, 10.0)["valid"]
    assert block_validity(700, 700.4, 699.6, [(700.0, 500.0)] * 2, 1000.0, 10.0)["valid"]


def test_dirty_block_is_dropped():
    samples = [(375.0, 370.0)] * 10 + [(700.0, 600.0)] * 2
    assert not block_validity(375, 375.0, 700.0, samples, 5000, 12.0)["valid"]
    clean = [(375.0, 370.0)] * 10
    assert not block_validity(375, 375.0, 375.0, clean, 370 * 10 * 1.5, 10.0)["valid"]


# card fixes: voice has its own kind; headline = latest run, totals secondary ------------------
def test_voice_and_images_keep_their_own_kind():
    from wattnot.gateway.traffic import item_class, plan
    assert item_class("voice", {"messages": [{"role": "user", "content": "hi"}]}) == "voice"
    assert item_class("images", {"messages": []}) == "images"
    assert item_class("text", {"messages": [{"role": "user", "content": "hi"}]}) == "text"
    assert {k for k, _ in plan("voice", 20)} == {"voice"}


def test_sequential_arm_transcribes_voice_and_labels_it_voice():
    from wattnot.gateway.traffic import TrafficRun

    class G:
        estimate, label = False, None
        def energy_mj(self): return 0
        def limit_w(self): return None
    sent = []
    tr = TrafficRun(lambda b, h: {}, transcribe=lambda wav: "what is two plus two")
    tr.gpu = G()
    tr.event = lambda **k: None
    out = tr._arm("without", [("voice", {"wav": __file__})], 0, lambda b, i: sent.append(b) or {"text": "4"})
    assert sent[0]["messages"][0]["content"].startswith("what is two plus two") and "wav" not in sent[0]
    assert out["per_request_j"][0][0] == "voice"


def test_measured_headline_is_latest_run_with_all_runs_totals(tmp_path):
    from wattnot.gateway import plug
    log = tmp_path / "s.jsonl"
    log.write_text("")
    runs = [{"run_id": "seq-a", "workload": "images", "without": {"joules": 1000.0, "requests": 60, "usd": 0.2},
             "with": {"joules": 750.0, "requests": 60, "usd": 0.18}},
            {"run_id": "seq-b", "workload": "voice", "without": {"joules": 360.0, "requests": 60, "usd": 0.1},
             "with": {"joules": 324.0, "requests": 60, "usd": 0.09}}]
    (tmp_path / "sequential.runs.jsonl").write_text("\n".join(json.dumps(r) for r in runs) + "\n")
    m = plug.measured(str(log))
    assert m["latest"]["run_id"] == "seq-b" and m["latest"]["workload"] == "voice"
    assert m["latest"]["energy_wh"]["saved_pct"] == 10.0 and m["latest"]["requests"] == 60
    assert m["latest"]["spend_usd"] == 0.09
    assert m["all_runs"]["runs"] == 2 and m["all_runs"]["requests"] == 120
    assert m["all_runs"]["energy_wh"]["saved_pct"] == round(100 * (1360 - 1074) / 1360, 2)
    assert m["all_runs"]["spend_usd"] == 0.27
