"""Review round 6 regressions (Oct 30)."""
import json
import subprocess
from pathlib import Path


def _plan(tmp_path):
    from wattnot.longrun.driver import plan_glimmer_test
    return {b.id: b for b in plan_glimmer_test(tmp_path)}


def test_eu_export_skips_the_sigstore_bundle(tmp_path):
    from wattnot.longrun.driver import plan_glimmer_test  # noqa: F401
    eu = _plan(tmp_path)["t1-eu"]
    cmd = eu.live[-1]
    assert "grep -v" in cmd and "sigstore" in cmd and "pipefail" in cmd
    d = tmp_path / "runs" / "t1-sweep"
    d.mkdir(parents=True)
    (d / "proof-a.json").write_text("{}")
    (d / "proof-a.sigstore.json").write_text("{}")
    out = subprocess.run(["bash", "-c", f"ls {d}/proof-*.json | grep -v '\\.sigstore\\.json$'"],
                         capture_output=True, text=True).stdout.split()
    assert out == [str(d / "proof-a.json")]


def test_live_sweep_makes_wonder_a_standard_tier(tmp_path):
    sweep = _plan(tmp_path)["t1-sweep"].live
    i = sweep.index("--guardrail-config")
    cfg = json.loads(Path(sweep[i + 1]).read_text())
    assert cfg["tiers"]["muse-wonder"] == "standard"
    from wattnot.approvals.risk import RiskConfig, tier_for
    assert tier_for(RiskConfig.from_dict(cfg), "muse-wonder") == "standard"


def test_prefix_ab_zero_pass_on_both_arms_fails():
    from wattnot.context_saver.prefix_cache import evaluate_ab
    arm = {"pass_pct": {"mean": 0.0}, "p99_ms": {"mean": None}, "ttft_ms": {"mean": None},
           "cache_hit_rate": {"mean": None}, "j_per_correct": None, "label": "MEASURED"}
    r = evaluate_ab(arm, dict(arm))
    assert r["kept"] is False and r["gate"]["passed"] is False


def test_vision_client_http_error_is_a_miss(monkeypatch):
    import wattnot.vision.client as vc
    cls = next(v for v in vars(vc).values() if isinstance(v, type) and hasattr(v, "_call")
               and hasattr(v, "_body"))
    obj = cls.__new__(cls)
    monkeypatch.setattr(obj, "_body", lambda item: {}, raising=False)
    def boom(body):
        raise OSError("503")
    monkeypatch.setattr(obj, "_call", boom, raising=False)
    assert obj(object()) == ""
