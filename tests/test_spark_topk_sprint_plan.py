import importlib.util, json
from pathlib import Path
p = Path(__file__).resolve().parents[1] / "modal/spark_topk_sprint.py"
spec = importlib.util.spec_from_file_location("sprint", p); S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

def test_plan_under_budget():
    pl = S.plan(); assert pl["minutes"] == 90 and pl["usd_estimate"] <= 80

def test_stock_has_no_override():
    assert "--hf-overrides" not in S.serve_args(8)
    a = S.serve_args(4); assert json.loads(a[a.index("--hf-overrides") + 1]) == {"num_experts_per_tok": 4}

def test_resume_skips_done():
    assert S.done_arms({"8": {"status": "done"}, "6": {"status": "partial"}}) == [8]
