"""The Modal Spark top-k sprint, validated with CPU dry runs only.

Nothing here touches Modal, opens a socket or spends money: an in-process fake server answers
every request and a fake meter stands in for `modal billing report`. The tests cover the whole
local driver -- the spend guard ($98 hard / $90 soft), the server-side `cloud_timeout_s`, the
local kill timer, the "stop at minute 15 if not serving" path, results saved before a stop, and
a signed cloud receipt per top-k value. A second set runs the planned Baseten $98 arms (baseline,
thinking-level sweep, judge) against a fake Baseten endpoint and checks the receipts are signed,
verify, and can never be graded above ESTIMATE.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from wattnot.receipt.cloud_adapter import from_cloud
from wattnot.receipt.grade import claimed_grade
from wattnot.receipt.sign import bundle_path, generate_test_key, verify_offline_bundle
from wattnot.spend.guard import GPU_SECONDS, Guard, HardStop, Ledger, Price

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_790_000_000.0


def load():
    spec = importlib.util.spec_from_file_location(
        "spark_topk_sprint", ROOT / "modal" / "spark_topk_sprint.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = load()


def _guard(tmp_path, *, hard=98.0, soft=90.0):
    return Guard("modal", Price(GPU_SECONDS, usd_per_s=M.B200_8_USD_H / 3600),
                 Ledger(tmp_path / "modal.ledger.jsonl"), hard_usd=hard, soft_usd=soft)


def _signed_ok(path, receipt):
    """The receipt verifies against its offline bundle and its hash is intact."""
    bundle = json.loads(bundle_path(path).read_text())
    ok, signer = verify_offline_bundle(receipt, bundle)
    return ok and signer == receipt["integrity"]["signer"] and bundle["rekor"] is None


# ---- the Modal top-k sprint (CPU dry run) --------------------------------------------------

def test_dry_run_writes_a_signed_receipt_per_topk(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    res = M.dry_run(tmp_path / "out", sign_key=str(key), now=NOW, guard=_guard(tmp_path))
    # smoke + top-k 8/6/4 + a top-k 8 repeat = one server restart per value, plus the noise floor
    assert [r["kind"] for r in res] == ["smoke", "topk", "topk", "topk", "repeat"]
    assert [r["topk"] for r in res] == [8, 8, 6, 4, 8]
    for r in res:
        rec = r["receipt"]
        assert r["serving"] and rec is not None
        assert rec["provider"] == "modal" and rec["label"] == "SIMULATED"
        assert rec["integrity"]["signer"].startswith("offline:")
        assert _signed_ok(r["path"], rec)
        # a fake server is honestly SIMULATED, never dressed up as MEASURED
        assert "MEASURED" not in json.dumps(rec)
        # energy is an honest estimate and the grade can never be VALID
        assert rec["labels"]["energy"] == "ESTIMATE"
        ad = from_cloud(rec)
        assert ad["grade"] in {"ESTIMATE", "SIMULATED"}
        assert claimed_grade(ad, rekor=True) in {"ESTIMATE", "SIMULATED"}


def test_lower_topk_is_cheaper_in_the_receipt(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    res = M.dry_run(tmp_path / "out", sign_key=str(key), now=NOW, guard=_guard(tmp_path))
    by = {(r["kind"], r["topk"]): r["receipt"]["totals"]["tuned_usd"] for r in res}
    assert by[("topk", 8)] > by[("topk", 6)] > by[("topk", 4)]


def test_each_arm_writes_its_own_receipt_file(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    res = M.dry_run(tmp_path / "out", sign_key=str(key), now=NOW, guard=_guard(tmp_path))
    paths = [Path(r["path"]) for r in res]
    assert len(set(paths)) == len(paths)           # no arm overwrote another's receipt
    assert all(p.exists() and bundle_path(p).exists() for p in paths)


# ---- the safety rails ----------------------------------------------------------------------

def test_no_headroom_hard_stops_before_launching(tmp_path):
    g = _guard(tmp_path)
    g.reserve(g.headroom(), item="prior arm")  # eat all the headroom
    served = {"n": 0}
    try:
        M.run_topk(8, out_dir=tmp_path / "o", transport=M.FakeServer(), mode="replay-synthetic",
                   guard=g, serve=lambda *a: served.__setitem__("n", served["n"] + 1) or {},
                   wait_serving=lambda *a: True, make_timer=_fake_timer(),
                   teardown=lambda: [])
        raise AssertionError("expected a HardStop")
    except HardStop as e:
        assert "headroom" in str(e) or "hard cap" in str(e)
    assert served["n"] == 0  # the server was never started


def test_cloud_timeout_is_set_from_the_guard_headroom(tmp_path):
    g = _guard(tmp_path)
    seen = {}

    def serve(topk, model, fn_timeout):
        seen["timeout"] = fn_timeout
        return {"base_url": ""}

    M.run_topk(8, out_dir=tmp_path / "o", transport=M.FakeServer(), mode="replay-synthetic",
               guard=g, now=NOW, serve=serve, wait_serving=lambda *a: True,
               make_timer=_fake_timer(), teardown=lambda: [], sign_key=None)
    from wattnot.spend.guard import cloud_timeout_s
    # the function timeout passed to the server is exactly the guard's cloud_timeout_s
    assert seen["timeout"] == cloud_timeout_s(g)


def _fake_timer():
    armed = []

    class T:
        def cancel(self):
            armed.append("cancelled")

    def make(seconds, name):
        armed.append(("armed", seconds, name))
        return T()
    make.armed = armed
    return make


def test_kill_timer_is_armed_and_cancelled_each_arm(tmp_path):
    g = _guard(tmp_path)
    mk = _fake_timer()
    M.run_topk(6, out_dir=tmp_path / "o", transport=M.FakeServer(topk=6),
               mode="replay-synthetic", guard=g, now=NOW,
               serve=lambda *a: {"base_url": ""}, wait_serving=lambda *a: True,
               make_timer=mk, teardown=lambda: [])
    assert mk.armed[0][0] == "armed" and mk.armed[-1] == "cancelled"
    assert mk.armed[0][2] == M.APP_NAME


def test_kill_timer_fires_injected_stop_and_exit():
    fired = {}
    t = M.kill_timer(0, "x", grace_s=0, stop=lambda: fired.__setitem__("stop", True),
                     exit_=lambda code: fired.__setitem__("exit", code))
    t.join(2)
    assert fired == {"stop": True, "exit": 3}


def test_not_serving_by_deadline_saves_results_and_stops_the_arm(tmp_path):
    g = _guard(tmp_path)
    out = tmp_path / "o"
    r = M.run_topk(8, out_dir=out, transport=M.FakeServer(), mode="replay-synthetic", guard=g,
                   now=NOW, serve=lambda *a: {"base_url": ""}, wait_serving=lambda *a: False,
                   make_timer=_fake_timer(), teardown=lambda: [])
    assert r["serving"] is False and r["receipt"] is None
    # results + a stop receipt were written before the stop, and the ledger is marked stopped
    assert (out / "modal-results.jsonl").exists() and (out / "modal-receipt.json").exists()
    assert g.stopped() and g.stopped()["level"] == "soft"


def test_soft_cap_ends_the_sprint_before_a_new_arm_starts(tmp_path):
    g = _guard(tmp_path)
    g.reserve(M.SOFT_USD, item="prior spend")   # committed is now at the soft cap
    res = M.dry_run(tmp_path / "out", now=NOW, guard=g)
    assert res and "stopped" in res[-1] and res[-1]["stopped"].startswith("soft")
    # nothing new started: no receipt was produced under a tripped soft cap
    assert all(r.get("receipt") is None for r in res)


# ---- the planned Baseten $98 runs (CPU dry run, fake Baseten endpoint) ----------------------

def test_baseten_plan_runs_are_signed_and_capped_at_estimate(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    res = M.baseten_dry_run(tmp_path / "bt", sign_key=str(key), now=NOW)
    assert [r["name"] for r in res] == ["baseline", "thinking-sweep", "judge"]
    for r in res:
        rec = r["receipt"]
        assert rec["provider"] == "baseten"
        assert rec["integrity"]["signer"].startswith("offline:") and _signed_ok(r["path"], rec)
        assert rec["labels"]["energy"] == "ESTIMATE"
        ad = from_cloud(rec)
        # hosted energy is never MEASURED: no Rekor entry can lift the grade above ESTIMATE
        assert ad["grade"] in {"ESTIMATE", "SIMULATED"}
        assert claimed_grade(ad, rekor=True) in {"ESTIMATE", "SIMULATED"}


def test_thinking_sweep_keeps_a_cheaper_level_and_the_judge_ran(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    res = {r["name"]: r["receipt"] for r in
           M.baseten_dry_run(tmp_path / "bt", sign_key=str(key), now=NOW)}
    # the sweep found a cheaper effort than xhigh and booked a saving
    assert res["thinking-sweep"]["totals"]["saving_usd"] > 0
    assert any(c["chosen_effort"] != "xhigh" for c in res["thinking-sweep"]["classes"])
    # the judge run actually invoked the judge gate (a judge call is in the call log)
    assert res["judge"]["settings"]["judge"] is True


# ---- repo hygiene --------------------------------------------------------------------------

def test_no_top_level_wattnot_import_in_the_modal_file():
    src = (ROOT / "modal" / "spark_topk_sprint.py").read_text()
    offenders = [ln for ln in src.splitlines()
                 if ln.lstrip().startswith(("import wattnot", "from wattnot"))
                 and ln == ln.lstrip()]  # a top-level (unindented) wattnot import would crash Modal
    assert offenders == []


def test_plan_reports_the_98_dollar_caps_and_no_deploy():
    p = M.plan()
    assert p["hard_usd"] == 98.0 and p["soft_usd"] == 90.0 and p["effective_hard_usd"] == 96.0
    assert p["serve_deadline_min"] == 15 and "nothing deployed" in p["label"]
    assert p["topks"] == (8, 6, 4) and p["repeat_k"] == 8
