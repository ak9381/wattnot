"""True end-to-end Baseten smoke: the live post-answer audit, no_mocks summary, fallback-to-mock
=> FAILED, and the Meta spend guard. CPU-only: every network path is monkeypatched to a fake.
No paid calls, no Baseten/Meta/Modal network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot.e2e import fullstack as F
from wattnot.e2e import live as LV


def _doc():
    return {"round": "r01", "model": "meta-models/muse-spark-open",
            "results": {"per_arm": {"without_wattnot": {"accuracy": 0.8, "energy_wh": {"value": 1.0}},
                                    "with_wattnot": {"accuracy": 0.8, "energy_wh": {"value": 0.9}}}},
            "answers": [{"id": "a"}, {"id": "b"}], "spend": {}}


def _patch_live(monkeypatch, *, wonder_source="wonder", sam_mode="live", spark_raises=False):
    """Make post_round's live path run on CPU: fake client, eye/hearing, SAM/Wonder files, trace."""
    monkeypatch.setattr(LV, "baseten_client", lambda *a, **k: (lambda *x, **y: {}))
    monkeypatch.setattr(LV, "live_eye", lambda out, client: {"passed": True, "reason": "ok"})
    monkeypatch.setattr(LV, "live_hearing", lambda out, client, log: {"passed": True, "reason": "ok"})
    monkeypatch.setattr(LV, "review_change", lambda *a, **k: {"decision": "approved", "why": "ok"})
    monkeypatch.setattr(LV, "musecode_pr", lambda *a, **k: {"status": "ok", "decision": "built"})

    def fake_spark_trace(out, ev, log):
        if spark_raises:
            raise RuntimeError("spark down")
        return {"steps": 4, "ok": 4}
    monkeypatch.setattr(LV, "spark_trace", fake_spark_trace)

    def fake_main(mod, argv):
        out = Path(argv[argv.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        if "sam" in mod:
            (out / "hotspot_set.json").write_text(json.dumps({"mode": sam_mode, "passed": True}))
        if "wonderchart" in mod:
            (out / "wonder_chart.json").write_text(json.dumps({"mode": "live", "source": wonder_source}))
        return {"argv": argv}
    monkeypatch.setattr(F, "_main", fake_main)


def test_live_all_stages_live_sets_no_mocks_true(tmp_path, monkeypatch):
    _patch_live(monkeypatch)
    res = F.post_round(tmp_path, _doc(), mode="live", cap=object(), rates=None, do_pr=True)
    assert res["no_mocks"] is True
    for sid, route in F.FEATURE_STAGE_ROUTES.items():
        assert res[sid]["route"] == route
        assert res[sid]["live_label"] == "LIVE", (sid, res[sid])
    # SAM and Wonder are WattNot-only; eye/hearing compare both arms
    assert res["sam_hotspot"]["without_wattnot"] == "not applicable (WattNot feature)"
    assert res["wonder_chart"]["without_wattnot"] == "not applicable (WattNot feature)"
    assert set(res["eye_check"]["arms"]) == {"with_wattnot", "without_wattnot"}
    assert "medium" in res["eye_check"]["arms"]["without_wattnot"]["effort"]
    # the no_mocks flag is written into features.json
    fj = json.loads((tmp_path / "features" / "features.json").read_text())
    assert fj["no_mocks"] is True


def test_live_wonder_fallback_is_a_failed_stage(tmp_path, monkeypatch):
    _patch_live(monkeypatch, wonder_source="fallback")
    res = F.post_round(tmp_path, _doc(), mode="live", cap=object(), rates=None, do_pr=True)
    assert res["wonder_chart"]["status"] == "bad"
    assert res["wonder_chart"]["live_label"] == "FAILED"
    assert "fallback" in res["wonder_chart"]["fell_back"]
    assert res["no_mocks"] is False
    assert json.loads((tmp_path / "features" / "features.json").read_text())["no_mocks"] is False


def test_live_sam_offline_is_a_failed_stage(tmp_path, monkeypatch):
    _patch_live(monkeypatch, sam_mode="fake")
    res = F.post_round(tmp_path, _doc(), mode="live", cap=object(), rates=None, do_pr=True)
    assert res["sam_hotspot"]["live_label"] == "FAILED" and res["no_mocks"] is False


def test_live_stage_error_is_a_failed_stage(tmp_path, monkeypatch):
    _patch_live(monkeypatch, spark_raises=True)
    res = F.post_round(tmp_path, _doc(), mode="live", cap=object(), rates=None, do_pr=True)
    assert res["spark_trace"]["status"] == "bad"
    assert res["spark_trace"]["live_label"] == "FAILED" and res["no_mocks"] is False


def test_smoke_mode_never_claims_no_mocks(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "_main", lambda mod, argv: {"argv": argv})
    res = F.post_round(tmp_path, _doc(), mode="smoke", budget_s=None)
    assert res["no_mocks"] is False
    assert "feature_audit" not in res  # the live audit only runs in live mode


# ---------------------------------------------------------------- Meta spend guard
def test_meta_feature_worst_case_includes_every_live_stage():
    usd, basis = F.meta_feature_worst_case_usd(18)
    assert usd > 0 and all(w in basis for w in ("SAM", "Wonder", "Spark trace", "voice", "Spark grades"))
    # grades scale with the answer count; the fixed stages do not
    assert F.meta_feature_worst_case_usd(100)[0] > F.meta_feature_worst_case_usd(0)[0]


def test_meta_spend_guard_soft_cap_and_per_run_cap():
    worst, _ = F.meta_feature_worst_case_usd(18)
    # plenty of headroom
    assert F.meta_spend_guard(worst, 1.0, ledger_total=0.0)["ok"] is True
    # per-run cap too low for even the fixed stages
    g = F.meta_spend_guard(worst, 0.0001, ledger_total=0.0)
    assert g["ok"] is False and "per-smoke cap" in g["why"]
    # ledger near the soft cap leaves no room
    g2 = F.meta_spend_guard(worst, 1.0, ledger_total=99.999)
    assert g2["ok"] is False and "soft cap" in g2["why"]


def test_post_round_live_records_meta_guard(tmp_path, monkeypatch):
    _patch_live(monkeypatch)
    monkeypatch.setattr("wattnot.meta.ledger.SpendLedger.total", lambda self: 0.0)
    res = F.post_round(tmp_path, _doc(), mode="live", cap=object(), rates=None, do_pr=True,
                       meta_cap_usd=1.0)
    assert res["meta_guard"]["ok"] is True and res["meta_guard"]["meta_cap_usd"] == 1.0
    assert res["meta_guard"]["worst_meta_usd"] > 0
