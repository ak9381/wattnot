import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "reselect", Path(__file__).resolve().parents[1] / "scripts" / "reselect_sweep.py")
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)

POL = {"enabled": True, "allowed_labels": ["MEASURED"],
       "kinds": {"power_cap": {"min_saving_pct": 3, "max_p99_increase_pct": 10,
                               "max_cap_drop_pct": 58}}}


def step(cap, e, p99):
    return {"cap_w": cap, "label": "MEASURED", "energy_change_pct": {"mean": e},
            "p99_change_pct": {"mean": p99}}


def test_policy_approves_350():
    assert rs.policy_decision(step(350, -32.4, 9.5), 700, POL) == ("approved", [])


def test_policy_rejects_p99():
    d, why = rs.policy_decision(step(300, -37.9, 24.0), 700, POL)
    assert d == "rejected" and "p99" in why[0]
