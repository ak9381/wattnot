import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "credits_phase", Path(__file__).resolve().parents[1] / "scripts" / "credits_phase.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)


def test_spends_down_to_margin_and_never_over_caps(tmp_path):
    p = cp.plan(99.57, 50.0)
    for prov in ("modal", "baseten"):
        t, cap = p[prov]["total_usd"], p[prov]["total_cap_usd"]
        assert t <= cap and t >= cap - max(b["usd"] for b in p["blocks"] if b["provider"] == prov)
    assert {b["dataset"] for b in p["blocks"] if "dataset" in b} == set(cp.DATASETS)
    assert p["needs_go"] and p["label"] == "ESTIMATE"
    d = cp.dry_run(p, tmp_path, flow=False)
    assert d["all_ok"] and d["label"] == "SIMULATED"


def test_unknown_baseten_balance_plans_nothing_there():
    p = cp.plan(97.0, None)
    assert p["baseten"]["passes"] == 0 and not [b for b in p["blocks"] if b["provider"] == "baseten"]
