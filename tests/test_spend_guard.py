import json
import pytest
from wattnot.spend import (GPU_SECONDS, TOKEN, Guard, HardStop, Ledger, Price, ProviderError,
                           SoftStop, cloud_timeout_s, run_items, run_timed_block)
from wattnot.spend import dryrun

P = Price(TOKEN, 1.25, 4.25)


def g(tmp_path, **kw):
    return Guard("baseten", P, Ledger(tmp_path / "l.jsonl"), sleep=lambda s: None, **kw)


def test_price_math():
    assert P.tokens(1_000_000, 1_000_000) == pytest.approx(5.5)
    assert Price(GPU_SECONDS, usd_per_s=0.01).seconds(100) == pytest.approx(1.0)


def test_soft_then_hard(tmp_path):
    gd = g(tmp_path)
    rid = gd.reserve(1.0); gd.settle(rid, 90.5)
    with pytest.raises(SoftStop):
        gd.check_new_item()
    rid = gd.reserve(0.1); gd.settle(rid, 6.0)   # 96.5 >= 98-2
    with pytest.raises(HardStop):
        gd.check_new_item()


def test_reserve_refuses_over_headroom(tmp_path):
    gd = g(tmp_path)
    rid = gd.reserve(1.0); gd.settle(rid, 95.0)
    with pytest.raises(HardStop):
        gd.reserve(1.5)                 # 95+1.5 > 96 headroom


def test_ledger_persists_and_unsettled_counts(tmp_path):
    gd = g(tmp_path)
    gd.settle(gd.reserve(1.0), 10.0)
    gd.reserve(5.0)                     # crash: never settled
    assert g(tmp_path).committed() == pytest.approx(15.0)


def test_torn_line_ignored(tmp_path):
    gd = g(tmp_path)
    gd.settle(gd.reserve(1.0), 2.0)
    with open(tmp_path / "l.jsonl", "a") as f:
        f.write('{"kind": "settle", "us')
    assert g(tmp_path).committed() == pytest.approx(2.0)


def test_reconcile_only_raises(tmp_path):
    gd = g(tmp_path)
    gd.settle(gd.reserve(1.0), 10.0)
    gd.reconcile(4.0, "bill")
    assert gd.committed() == pytest.approx(10.0)
    gd.reconcile(93.0, "bill")
    with pytest.raises(SoftStop):
        gd.check_new_item()


def test_reconcile_can_cross_the_hard_cap(tmp_path):
    gd = g(tmp_path)
    gd.settle(gd.reserve(1.0), 10.0)
    gd.reconcile(97.0, "bill")           # provider bill pushes committed past hard-lag (96)
    assert gd.committed() == pytest.approx(97.0)
    with pytest.raises(HardStop):
        gd.check_new_item()


def test_reserve_negative_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="negative"):
        g(tmp_path).reserve(-1.0)


def test_cloud_timeout_s_is_zero_at_no_headroom(tmp_path):
    pr = Price(GPU_SECONDS, usd_per_s=0.01)
    gd = Guard("modal", pr, Ledger(tmp_path / "m.jsonl"))
    gd.settle(gd.reserve(0.0), 95.0)     # headroom 1.0 -> 100 s < 120 s teardown
    assert gd.headroom() == pytest.approx(1.0)
    assert cloud_timeout_s(gd) == 0      # 0 means: do not launch
    with pytest.raises(ValueError, match="GPU-seconds"):
        cloud_timeout_s(g(tmp_path))     # token price has no per-second rate


def test_402_is_hard_and_sticky(tmp_path):
    gd = g(tmp_path)
    def boom(): raise ProviderError(402)
    r = run_items(gd, [{"id": "a"}, {"id": "b"}], lambda it: boom(), lambda it: 0.1, tmp_path / "o")
    assert r["stop_level"] == "hard" and r["items_done"] == 0
    with pytest.raises(HardStop):
        g(tmp_path).check_new_item()     # a resume does not restart after out-of-credit


def test_429_retries_then_soft(tmp_path):
    calls = {"n": 0}
    def flaky(it):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ProviderError(429)
        return {"tokens_in": 10, "tokens_out": 10}
    r = run_items(g(tmp_path), [{"id": "a"}], flaky, lambda it: 0.01, tmp_path / "o")
    assert r["stop_level"] == "done" and r["items_done"] == 1 and calls["n"] == 3


def test_results_saved_and_copied(tmp_path):
    r = run_items(g(tmp_path), [{"id": "a"}], lambda it: {"tokens_in": 1, "tokens_out": 1},
                  lambda it: 0.01, tmp_path / "o", copy_to=tmp_path / "c")
    assert (tmp_path / "o" / "baseten-results.jsonl").read_text().count("\n") == 1
    assert json.loads((tmp_path / "c" / "baseten-receipt.json").read_text())["items_done"] == 1
    assert (tmp_path / "c" / "l.jsonl").exists() and r["stop_level"] == "done"


def test_timed_block_shrinks_and_timeout(tmp_path):
    pr = Price(GPU_SECONDS, usd_per_s=0.01)
    gd = Guard("modal", pr, Ledger(tmp_path / "m.jsonl"))
    gd.settle(gd.reserve(0.0), 80.0)
    # headroom 16 -> 1600 s minus 120 teardown; planned 1 h shrinks
    out = run_timed_block(gd, "b", 3600, lambda dt: {"seconds": dt}, tick_s=30)
    assert out["seconds"] <= 1480 + 1e-6
    assert gd.committed() <= 96.0 + 1e-6
    assert cloud_timeout_s(gd) <= 120


def test_dryrun_passes(tmp_path):
    assert dryrun.main(["--out", str(tmp_path / "d")]) == 0
    s = json.loads((tmp_path / "d" / "summary.json").read_text())
    assert s["modal_caps"]["apps_after"] == {"wattnot-spark-topk": "stopped"}
    assert s["baseten_caps"]["ledger_committed_usd"] <= 98
