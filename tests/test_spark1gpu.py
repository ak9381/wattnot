"""Side demo: fetch, power, tune loop, head to head, router. No GPU, no network, no spend."""

import json
from pathlib import Path

import pytest

from wattnot.cloud.pricing import Rates
from wattnot.cloud.transport import FixtureTransport
from wattnot.receipt.canon import canonical_sha256
from wattnot.runner.workload import load_workload, workloads_dir
from wattnot.spark_open import fetch as F
from wattnot.spark_open import router as R
from wattnot.spark_open import tune as T
from wattnot.spark_open.bench import GpuPower, HostPower, Rapl, estimate_host_joules, integrate
from wattnot.spark_open.h2h import (
    Answer,
    BasetenLeg,
    BudgetStop,
    SpendCap,
    compare,
    run_h2h,
    summarize_leg,
    worst_case_usd,
)

WL = workloads_dir() / "cloud-effort-v1.jsonl"
FIXTURE = Path(__file__).resolve().parent.parent / "data" / "cloud" / "recorded-smoke.json"


# ---- fetch ----
def _make_shards(root: Path, quant: str, n: int, size: int) -> None:
    d = root / quant
    d.mkdir(parents=True)
    for i in range(1, n + 1):
        (d / f"M-{quant}-{i:05d}-of-{n:05d}.gguf").write_bytes(b"\0" * (size // n))


def test_verify_counts_and_sizes(tmp_path):
    lst = {"UD-IQ2_S": (3, 3000)}
    _make_shards(tmp_path, "UD-IQ2_S", 3, 3000)
    assert F.verify("UD-IQ2_S", tmp_path, listing=lst)[0]
    (tmp_path / "UD-IQ2_S" / "M-UD-IQ2_S-00002-of-00003.gguf").unlink()
    ok, why = F.verify("UD-IQ2_S", tmp_path, listing=lst)
    assert not ok and "need 1..3" in why
    assert not F.verify("UD-IQ1_S", tmp_path, listing=lst)[0]


def test_download_cmd_has_no_secret_and_uses_include(tmp_path):
    cmd = F.download_cmd("UD-IQ2_S", tmp_path)
    assert cmd[:3] == ["hf", "download", F.REPO] and "UD-IQ2_S/*" in cmd
    assert not any("hf_" in c or "token" in c.lower() for c in cmd)


def test_fetch_skips_what_does_not_fit_and_falls_back(tmp_path):
    lst = {"UD-IQ2_S": (2, 2000), "UD-IQ1_S": (1, 1000)}
    r = F.fetch(tmp_path, free_gb=0.0000015, dry_run=True, listing=lst)  # 1500 bytes free
    assert r.quant == "UD-IQ1_S" and "skipped" in r.steps[0] and r.dry_run


def test_fetch_falls_back_after_a_failed_download(tmp_path):
    lst = {"UD-IQ2_S": (2, 2000), "UD-IQ1_S": (1, 1000)}
    calls = []

    def runner(cmd, check):
        calls.append(cmd)
        if "UD-IQ2_S/*" in cmd:
            raise OSError("network")
        _make_shards(tmp_path, "UD-IQ1_S", 1, 1000)

    r = F.fetch(tmp_path, free_gb=10, runner=runner, listing=lst)
    assert r.ok and r.quant == "UD-IQ1_S" and len(calls) == 2


def test_fetch_listing_matches_knobs():
    from wattnot.spark_open.knobs import QUANTS

    assert F.LISTING["UD-IQ2_S"][1] / 1e9 == pytest.approx(QUANTS["UD-IQ2_S"].approx_gb, abs=2)


# ---- power ----
def test_integrate_trapezoid_and_unavailable():
    r = integrate([(0, 100.0), (2, 200.0)])
    assert r["joules"] == 300.0 and r["label"] == "MEASURED"
    assert integrate([(0, 1.0)])["joules"] is None


def test_gpu_power_without_gpu_is_none():
    g = GpuPower(read=lambda: None)
    g.start()
    assert g.stop()["joules"] is None


def test_host_power_estimate_when_rapl_missing(tmp_path):
    t = iter([0.0, 10.0])
    h = HostPower(Rapl(tmp_path), stat=lambda: (0.0, 100.0), clock=lambda: next(t))
    h.start()
    r = h.stop(threads=10, resident_gb=100)
    assert r["label"] == "ESTIMATE"
    assert (
        r["joules"] == pytest.approx(estimate_host_joules(10.0, 0.5, 10, 100), rel=1e-3)
        and r["busy_share"] == 0.5
        and "Assumed" in r["method"]
    )


def test_host_power_rapl_is_measured(tmp_path):
    z = tmp_path / "intel-rapl:0"
    z.mkdir()
    (z / "energy_uj").write_text("1000000")
    (z / "max_energy_range_uj").write_text("100000000")
    h = HostPower(Rapl(tmp_path), stat=lambda: (0.0, 1.0))
    h.start()
    (z / "energy_uj").write_text("6000000")
    r = h.stop(threads=4, resident_gb=1)
    assert r["label"] == "MEASURED" and r["joules"] == pytest.approx(5.0)


# ---- tune loop ----
ITEMS = list(load_workload(WL))[:3]


class OomRig:
    """Experts of fewer than 54 layers stay on the CPU side, or the GPU runs out."""

    def __init__(self, floor=54, gpu=True):
        self.floor, self.gpu, self.loads = floor, gpu, []

    def run(self, k):
        self.loads.append((k.n_cpu_moe, k.ubatch))
        if k.n_cpu_moe < self.floor:
            raise RuntimeError("llama-server exited while loading (out of memory)")
        toks = 300 + 600 * (62 - k.n_cpu_moe) / 62
        return {
            "outputs": {i.id: "" for i in ITEMS},
            "latencies": [1.0, 1.1, 1.2],
            "truncated": {i.id: False for i in ITEMS},
            "gpu_j": 1000.0 if self.gpu else None,
            "gpu_label": "MEASURED",
            "gpu_avg_w": 300,
            "host_j": 500.0,
            "host_label": "ESTIMATE",
            "host_method": "x",
            "out_tokens": 300,
            "tok_s": toks / 100,
            "wall_s": 3.0,
            "load_s": 1.0,
        }


def test_fallback_policy_finds_the_floor_after_oom(tmp_path):
    rig = OomRig(floor=54)
    s = T.run_loop(rig, tmp_path, items=ITEMS, planner=T.Planner(None), max_tries=6, threads=4)
    assert s["best"]["knobs"]["n_cpu_moe"] == 54
    failed = [t for t in s["tries"] if not t["ok"]]
    assert failed and all(t["knobs"]["n_cpu_moe"] < 54 for t in failed)
    assert len({(t["knobs"]["n_cpu_moe"], t["knobs"]["ubatch"]) for t in s["tries"]}) == len(
        s["tries"]
    )
    prev = None
    for t in s["tries"]:  # hash chain
        assert t["prev_sha256"] == prev
        body = {k: v for k, v in t.items() if k != "sha256"}
        assert canonical_sha256(body) == t["sha256"]
        prev = t["sha256"]
    assert (tmp_path / "tune.json").exists()


def test_no_claim_without_host_measurement(tmp_path):
    s = T.run_loop(OomRig(), tmp_path, items=ITEMS, planner=T.Planner(None), max_tries=2, threads=4)
    best = s["best"]
    assert best["j_per_token_label"] == "ESTIMATE" and not best["billable"]


def test_baseline_failure_stops(tmp_path):
    s = T.run_loop(OomRig(floor=99), tmp_path, items=ITEMS, planner=T.Planner(None), threads=4)
    assert s["best"] is None and "baseline" in s["stopped"]


def test_validate_refuses_out_of_range_answers():
    s = T.summarize([], "UD-IQ2_S", 4096, 6)
    lo, hi = s["allowed_n_cpu_moe"]
    assert 40 <= lo <= 62 and hi == 62
    ch, why = T.validate({"kind": "try", "n_cpu_moe": lo - 5, "ubatch": 4096, "reason": "x"}, s)
    assert ch is None and why
    ch, _ = T.validate({"kind": "try", "n_cpu_moe": lo, "ubatch": 4096, "reason": "x"}, s)
    assert ch is not None and ch.n_cpu_moe == lo
    ch, why = T.validate({"kind": "try", "n_cpu_moe": lo, "ubatch": 777, "reason": "x"}, s)
    assert ch is None


def test_planner_garbage_falls_back(tmp_path):
    bad = lambda body: {"choices": [{"message": {"content": "not json"}}], "usage": {}}  # noqa: E731
    s = T.run_loop(OomRig(), tmp_path, items=ITEMS, planner=T.Planner(bad), max_tries=2, threads=4)
    assert s["best"] is not None and len(s["planner"]["errors"]) >= 1


# ---- head to head ----
def _answer(i, ok=True, lat=1.0, pt=100, ct=200, usd=0.001):
    return Answer(i, ok, lat, pt, ct, "stop", usd=usd)


LABELS = {"quality": "MEASURED", "latency": "MEASURED", "tokens": "MEASURED", "cost": "MEASURED"}


def _power(wall=10.0, gj=3000.0, hj=1000.0, host="MEASURED"):
    return {
        "wall_s": wall,
        "gpu": {"joules": gj, "label": "MEASURED"},
        "host": {"joules": hj, "label": host, "method": "m"},
    }


def test_local_cost_is_seconds_times_rate():
    ans = [_answer("a", lat=2.0), _answer("b", lat=2.0)]
    row = summarize_leg("local", ans, labels=LABELS, power=_power(wall=4.0), rental_usd_per_h=3.6)
    assert row["cost_per_1k_answers"]["usd"] == pytest.approx(2.0 * 1000 / 3600 * 3.6)
    assert row["energy_per_answer"]["label"] == "MEASURED"
    assert row["energy_per_answer"]["wh"] == pytest.approx(4000 / 2 / 3600, rel=1e-3)


def test_local_energy_labels():
    ans = [_answer("a")]
    est = summarize_leg(
        "local", ans, labels=LABELS, power=_power(host="ESTIMATE"), rental_usd_per_h=1
    )
    assert est["energy_per_answer"]["label"] == "ESTIMATE"
    no_gpu = summarize_leg("local", ans, labels=LABELS, power=_power(gj=None), rental_usd_per_h=1)
    assert (
        no_gpu["energy_per_answer"]["label"] == "unavailable"
        and no_gpu["energy_per_answer"]["wh"] is None
    )


def test_baseten_energy_is_always_estimate():
    row = summarize_leg("baseten", [_answer("a")], labels=LABELS)
    e = row["energy_per_answer"]
    assert e["label"] == "ESTIMATE" and e["wh_range"][0] < e["wh_range"][1]
    assert row["cost_per_1k_answers"]["usd"] == pytest.approx(1.0)


def test_spend_cap_blocks_the_call_that_would_pass_it(tmp_path):
    cap = SpendCap(0.01, tmp_path / "led.json")
    cap.add(0.009)
    w = worst_case_usd(Rates(), "x" * 300, 1700)
    assert w > 0.001
    with pytest.raises(BudgetStop):
        cap.check(w)
    assert SpendCap(0.01, tmp_path / "led.json").spent_usd == pytest.approx(0.009)  # persisted


def test_baseten_leg_stops_before_calling_when_over_cap(tmp_path):
    calls = []

    def tr(req):
        calls.append(req)
        raise AssertionError("must not be called")

    cap = SpendCap(0.0001, tmp_path / "led.json")
    leg = BasetenLeg(tr, cap=cap)
    with pytest.raises(BudgetStop):
        leg.complete(ITEMS[0])
    assert not calls


def test_baseten_leg_records_spend_and_checks_answer(tmp_path):
    item = next(i for i in load_workload(WL) if i.id == "arith-1")
    fx = FixtureTransport.load(FIXTURE)
    cap = SpendCap(0.5, tmp_path / "led.json")
    a = BasetenLeg(fx, cap=cap, billed=True).complete(item)
    assert a.passed and a.usd and cap.spent_usd == pytest.approx(a.usd)
    b = BasetenLeg(fx, cap=None, billed=False).complete(item)
    assert b.passed and b.usd


def test_replay_run_has_no_local_and_is_labelled(tmp_path):
    r = run_h2h(WL, list(load_workload(WL)), baseten="replay", fixture=FIXTURE)
    leg = r["legs"]["baseten"]
    assert set(r["legs"]) == {"baseten"} and leg["quality"]["label"] == "REPLAY"
    assert (
        leg["energy_per_answer"]["label"] == "ESTIMATE" and r["workload"]["items"] == leg["answers"]
    )
    body = {k: v for k, v in r.items() if k != "integrity"}
    assert r["integrity"]["sha256"] == canonical_sha256(body) or R.verify(r)
    assert "prompt" not in json.dumps(r["items"]).lower()


def test_compare_ties_and_ids():
    a = summarize_leg(
        "local", [_answer("a", usd=0)], labels=LABELS, power=_power(wall=1.0), rental_usd_per_h=1
    )
    b = summarize_leg("baseten", [_answer("a")], labels=LABELS)
    c = compare(a, b)
    assert c["quality"] == "tie" and c["cost_per_1k"] in ("local", "baseten", "tie")


# ---- router ----
def _legs(lq=100.0, bq=100.0, lcost=2.0, bcost=1.0, lp99=5.0, bp99=3.0, lwh=0.5, bwh=0.5, lfail=0):
    def leg(q, cost, p99, wh, lab, fail=0):
        return {
            "answers": 6,
            "failed_calls": fail,
            "quality": {"pass_pct": q, "label": "MEASURED"},
            "latency_s": {"p99": p99, "label": "MEASURED"},
            "cost_per_1k_answers": {"usd": cost, "label": "MEASURED"},
            "energy_per_answer": {"wh": wh, "label": lab},
        }

    return {
        "local": leg(lq, lcost, lp99, lwh, "MEASURED", lfail),
        "baseten": leg(bq, bcost, bp99, bwh, "ESTIMATE"),
    }


def test_private_workload_never_goes_to_baseten():
    d = R.route(R.WorkloadProfile("batch", private=True), _legs(lcost=100.0, bcost=0.1))
    assert d["chosen"] == "local"
    bt = next(c for c in d["candidates"] if c["id"] == "baseten")
    assert bt["status"] == "excluded" and "private" in bt["why"]


def test_quality_gate_excludes_worse_backend():
    d = R.route(R.WorkloadProfile("batch"), _legs(lq=80.0, lcost=0.1, bcost=5.0))
    assert d["chosen"] == "baseten"


def test_failed_calls_exclude_a_backend():
    d = R.route(R.WorkloadProfile("interactive"), _legs(lfail=1, lp99=0.1))
    assert d["chosen"] == "baseten"


def test_batch_picks_cheapest_then_energy_on_tie():
    assert R.route(R.WorkloadProfile("batch"), _legs(lcost=0.5, bcost=1.0))["chosen"] == "local"
    d = R.route(R.WorkloadProfile("batch"), _legs(lcost=1.0, bcost=1.02, lwh=0.2, bwh=0.9))
    assert d["chosen"] == "local" and "energy" in d["reason"]
    d = R.route(R.WorkloadProfile("batch"), _legs(lcost=1.02, bcost=1.0, lwh=0.9, bwh=0.2))
    assert d["chosen"] == "baseten"


def test_interactive_budget_and_tie():
    d = R.route(R.WorkloadProfile("interactive", p99_budget_s=4.0), _legs(lp99=9.0, bp99=3.0))
    assert d["chosen"] == "baseten"
    d = R.route(R.WorkloadProfile("interactive"), _legs(lp99=3.1, bp99=3.0, lcost=0.5, bcost=1.0))
    assert d["chosen"] == "local" and "cheaper" in d["reason"]
    d = R.route(R.WorkloadProfile("interactive", p99_budget_s=1.0), _legs(lp99=9.0, bp99=3.0))
    assert d["chosen"] == "baseten" and d["flags"]


def test_nobody_passes_gate():
    legs = _legs()
    legs["local"]["answers"] = legs["baseten"]["answers"] = 0
    assert R.route(R.WorkloadProfile("batch"), legs)["chosen"] is None


def test_attach_rehashes_and_keeps_chain(tmp_path):
    r = run_h2h(WL, list(load_workload(WL)), baseten="replay", fixture=FIXTURE)
    r["integrity"]["prev_sha256"] = "ab" * 32
    R.attach(r)
    assert r["integrity"]["prev_sha256"] == "ab" * 32 and R.verify(r)
    assert [d["workload"]["name"] for d in r["routing"]["decisions"]] == [
        "interactive",
        "batch",
        "batch+private",
    ]
    r["routing"]["decisions"][0]["chosen"] = "local"
    assert not R.verify(r)
