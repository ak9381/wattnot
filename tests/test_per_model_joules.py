import importlib.util
from pathlib import Path

from wattnot.receipt import build_receipt
from wattnot.receipt.per_model import J_PER_TOKEN, per_model_joules, spark_trace_id

RC = Path(__file__).parent / "fixtures" / "receipts"


def _mk():
    spec = importlib.util.spec_from_file_location("mk", RC / "make.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    return mk


def test_builder_adds_served_line_and_trace_id():
    mk = _mk()
    r = build_receipt(mk.run("run_20261028_100000_a00009", mk.BASE, mk.CAND))
    m = r["measurement"]
    assert m["spark_trace_id"].startswith("spark-") and len(m["spark_trace_id"]) == 22
    served = m["per_model_joules"][0]
    assert served["role"] == "served" and served["label"] == "MEASURED"
    # j_per_answer = candidate wh_per_req mean x 3600
    assert abs(served["j_per_answer"] - r["candidate"]["wh_per_req"]["mean"] * 3600) < 1e-3
    # the planner is hosted and has no token count here -> not metered, never MEASURED
    planner = next(x for x in m["per_model_joules"] if x["role"] == "planner")
    assert planner["where"] == "hosted" and planner["status"] == "not metered"


def test_hosted_role_with_tokens_is_an_estimate():
    receipt = {"subject": {"model": "x", "workload": {"n_requests": 100}},
               "candidate": {"wh_per_req": {"mean": 0.01}},
               "measurement": {"label": "MEASURED"}, "planner_trace": []}
    lines = per_model_joules(receipt, roles=[
        {"role": "judge", "model": "Llama 3.1 8B (judge)", "tokens": 1000, "label": "ESTIMATE"},
        {"role": "gate", "model": "muse-spark-1.3 (gate explanation)", "tokens": 500},
        {"role": "trimmer", "model": "local-trimmer", "where": "local", "joules": 42.0}])
    judge = next(x for x in lines if x["role"] == "judge")
    assert judge["label"] == "ESTIMATE" and judge["j_total"] == round(1000 * J_PER_TOKEN, 1)
    gate = next(x for x in lines if x["role"] == "gate")
    assert gate["label"] == "ESTIMATE" and gate["where"] == "hosted"
    trimmer = next(x for x in lines if x["role"] == "trimmer")
    assert trimmer["where"] == "local" and trimmer["j_total"] == 42.0


def test_trace_id_prefers_events_then_planner_trace():
    ev = [{"type": "decision", "data": {"choice": {"power_cap_w": 350}}}]
    assert spark_trace_id(events=ev).startswith("spark-")
    assert spark_trace_id(planner_trace=[{"source": "spark", "reason": "x"}]).startswith("spark-")
    assert spark_trace_id(planner_trace=[{"source": "fallback"}]) is None
    assert spark_trace_id() is None
