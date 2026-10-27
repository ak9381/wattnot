"""The rack photo audit: SAM output decides which GPUs may be touched and how hard.

Everything replays saved SAM output from rack/*/sam-raw. No network, no key, no spend,
except the client tests, which use a fake transport.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixtures.nvml_fake import FakeNVML
from wattnot.approvals import ApprovalGate, Approvals, CallbackApplier, NotApproved
from wattnot.approvals import model as m
from wattnot.approvals import policy as pol
from wattnot.approvals.gate import narrow_bounds
from wattnot.approvals.proposers import physical_check, with_physical_check
from wattnot.control import CapGuard
from wattnot.control.planned import Measured, planned_loop
from wattnot.meta.ledger import BudgetExceeded, SpendLedger
from wattnot.planner import DecisionMaker
from wattnot.sam import physical as ph
from wattnot.sam.client import SamClient, output_text

RACK = Path(__file__).resolve().parents[1] / "rack"
KNOBS = {"max_num_seqs": 256, "enable_prefix_caching": True, "quantization": "none"}


@pytest.fixture(scope="module")
def specs():
    return ph.load_fleet(RACK / "audit" / "fleet.json")[0]


@pytest.fixture(scope="module")
def on(specs):
    return ph.audit_fleet(specs, sam=True)


@pytest.fixture(scope="module")
def off(specs):
    return ph.audit_fleet(specs, sam=False)


def _box(i, y1, y2, w=960, h=1400, x1=100, x2=800):
    return f"{i}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={w};h={h}|><|mask;x=0;y=0;data=1,1,~a|>"


def _raw(*spans, prompt="1U server"):
    return {prompt: "<0f>" + ",".join(_box(i, a, b) for i, (a, b) in enumerate(spans))}


# ---- boxes -> units -----------------------------------------------------------------------

def test_cisco_photo_gives_ten_outlined_units():
    s = ph.scan_dir(RACK / "cisco")
    assert s.slots == list(range(10)) and {u.how for u in s.units} == {"outlined"}


def test_hpe_photo_gives_four_units_two_of_them_inferred_from_the_stack():
    s = ph.scan_dir(RACK / "hpe")
    assert s.slots == [0, 1, 2, 3]
    assert [u.how for u in s.units] == ["outlined", "outlined", "inferred", "inferred"]


def test_a_gap_between_units_keeps_slot_numbers_honest():
    s = ph.units_from_raw(_raw((100, 200), (200, 300), (500, 600)))  # 2 free slots in between
    assert s.slots == [0, 1, 4]


def test_a_box_that_is_the_whole_rack_is_not_a_unit():
    raw = {"server": "<0f>" + _box(0, 0, 1380, w=960, h=1400, x1=0, x2=950)}
    assert ph.units_from_raw(raw).units == ()


def test_empty_or_missing_output_means_no_units():
    assert ph.units_from_raw({}).units == ()
    assert ph.units_from_raw({"server": ""}).units == ()


# ---- units + inventory -> audit -----------------------------------------------------------

def test_sam_on_finds_what_the_inventory_missed(on):
    r1, r2 = on.racks
    assert r1.present_nodes == 10 and r1.unlisted == (8, 9) and r1.unverified == ()
    assert r2.present_nodes == 4 and r2.unverified == (12, 13) and r2.unlisted == ()
    assert on.eligible_gpus() == list(range(12))


def test_sam_off_trusts_the_inventory(off):
    assert off.eligible_gpus() == list(range(14))
    assert [r.present_nodes for r in off.racks] == [8, 6]


def test_ceilings_differ_with_and_without_sam(on, off):
    # R1: 6,000 W for 10 real nodes (inventory says 8); R2: 2,800 W for 4 (inventory says 6)
    assert [r.ceiling_w for r in on.racks] == [600, 700]
    assert [r.ceiling_w for r in off.racks] == [700, 466]
    assert on.racks[0].over_budget_w_if_trusting_inventory == 1000


def test_removing_one_sam_box_removes_one_gpu(specs):
    """The decision follows the SAM output: drop unit 3 from its boxes and GPU 3 goes."""

    def without(raw, drop_y1):
        out = {}
        for p, t in raw.items():
            recs = re.split(r",(?=\d+<\|box)", t.removeprefix("<0f>"))
            keep = [r for r in recs if abs(float(re.search(r"y1=([\d.]+);", r).group(1))
                                           - drop_y1) > 6]
            out[p] = "<0f>" + ",".join(keep)
        return out

    raw = ph.load_raw(RACK / "cisco")
    full = ph.units_from_raw(raw)
    y1 = full.units[3].box[1]
    mutated = ph.units_from_raw(without({k: v for k, v in raw.items() if k != "server"}, y1))
    assert len(mutated.units) == 9
    spec = specs[0]
    a = ph.audit_rack(spec, mutated)
    assert 3 in a.unverified and not a.limit_for(3).eligible and a.limit_for(2).eligible


def test_limit_for_unknown_gpu_is_ineligible(on):
    lim = on.limit_for(99)
    assert not lim.eligible and "no audited rack" in lim.reason
    with pytest.raises(ph.NotVerified):
        on.require(12)


def test_demo_inventory_must_say_simulated(tmp_path):
    f = tmp_path / "rack" / "fleet.json"
    f.parent.mkdir()
    f.write_text(json.dumps({"label": "MEASURED", "racks": []}))
    with pytest.raises(ValueError, match="SIMULATED"):
        ph.load_fleet(f)


def test_labels_are_honest(on):
    doc = on.to_dict()
    assert doc["label"] == "SIMULATED"
    for r in doc["racks"]:
        assert r["labels"] == {"units": "MEASURED", "inventory": "SIMULATED",
                               "budget_w": "ESTIMATE", "ceiling_w": "ESTIMATE"}
    off_doc = ph.audit_fleet(ph.load_fleet(RACK / "audit" / "fleet.json")[0], sam=False).to_dict()
    assert all(r["labels"]["units"] == "SIMULATED" for r in off_doc["racks"])


def test_recommendation_items_label_counts_watts_and_advice(on):
    items = on.racks[0].recommend_items(0, start_cap_w=700)
    by = {i.key: i for i in items}
    assert by["physical_nodes_seen"].label == "MEASURED" and by["physical_nodes_seen"].value == 10
    assert by["physical_ceiling_w"].label == "ESTIMATE" and by["physical_ceiling_w"].value == 600
    assert by["physical_over_budget_w"].value == 1000
    assert by["physical_start_over_ceiling_w"].value == 100
    advice = [i for i in items if i.label == "ADVICE"]
    assert advice and not any(re.search(r"\d", i.text) for i in advice)
    off_items = ph.audit_rack(on.racks[0].spec, None).recommend_items(0)
    assert [i.label for i in off_items] == ["ADVICE"]


# ---- planner ------------------------------------------------------------------------------

def _gate(max_p99=1.32):
    def gate(meas):
        ok = meas.p99_s <= max_p99
        return SimpleNamespace(passed=ok, failed=[] if ok else ["p99"])
    return gate


def _trial(cap, knobs):
    # SIMULATED response curve, same shape as tests/test_planner.py
    return Measured(1.0 if cap >= 275 else 1.0 + (275 - cap) / 50, 0.048 * cap / 300)


def _run(on, gpu, *, start=700, gate=None, sent=None):
    nv = FakeNVML(power_limit_mw=start * 1000)
    seen = sent if sent is not None else []
    maker = DecisionMaker(lambda body: seen.append(body) or {"choices": []})
    with CapGuard(nv, 0, handle_sigterm=False) as g:
        res = planned_loop(g, maker, baseline=Measured(1.0, 0.048), trial=_trial,
                           gate=gate or _gate(), knobs=KNOBS, slack=0.2,
                           physical=(on, gpu))
        return res, nv, g


def test_planner_steps_to_the_rack_ceiling_first_and_sees_it(on):
    res, nv, g = _run(on, gpu=0)
    assert res.physical["cap_w"] == 600 and res.physical["from_cap_w"] == 700
    assert res.physical["passed"] and res.cap_w <= 600
    assert g.top_w == 600  # never above the ceiling, even if a model asks for more
    keys = {i.key for i in res.recommendations.actions}
    assert {"physical_nodes_seen", "physical_ceiling_w", "physical_over_budget_w"} <= keys
    a = res.recommendations.assumptions
    assert a["gpus_per_rack"] == 10 and a["rack_budget_w"] == 6000  # photo, not inventory


def test_planner_summary_carries_the_physical_block(on):
    from wattnot.planner import summarize
    s = summarize(cap_w=600, min_w=200, max_w=600, step_w=25, base_p99_s=1.0, p99_s=1.0,
                  slack=0.2, wh_per_req=0.04, base_wh_per_req=0.048, gates=[], knobs=KNOBS,
                  physical=on.racks[0].summary())
    assert s["physical"]["ceiling_w"] == 600 and s["physical"]["nodes_seen"] == 10
    assert "physical" not in summarize(cap_w=600, min_w=200, max_w=600, step_w=25,
                                       base_p99_s=1.0, p99_s=1.0, slack=0.2, wh_per_req=0.04,
                                       base_wh_per_req=0.048, gates=[], knobs=KNOBS)


def test_planner_refuses_a_gpu_with_no_unit_and_writes_nothing(on):
    nv = FakeNVML(power_limit_mw=700_000)
    with CapGuard(nv, 0, handle_sigterm=False) as g, pytest.raises(ph.NotVerified):
        planned_loop(g, DecisionMaker(None), baseline=Measured(1.0, 0.048), trial=_trial,
                     gate=_gate(), knobs=KNOBS, physical=(on, 12))
    assert nv.power_history == []


def test_planner_reverts_when_the_ceiling_breaks_the_gate(on):
    # p99 limit 0.9: any cap under 700 W would fail on this curve, so the 600 W ceiling fails
    def trial(cap, knobs):
        return Measured(1.0 if cap >= 700 else 1.5, 0.048)

    nv = FakeNVML(power_limit_mw=700_000)
    with CapGuard(nv, 0, handle_sigterm=False) as g:
        res = planned_loop(g, DecisionMaker(None), baseline=Measured(1.0, 0.048), trial=trial,
                           gate=_gate(1.1), knobs=KNOBS, physical=(on, 0))
        assert not res.physical["passed"] and g.cap_w() == 700 and res.decisions == []
    assert res.recommendations.verdict == "no change recommended"
    assert any(i.key == "physical_ceiling_gate_failed" for i in res.recommendations.actions)


def test_rack_with_headroom_lets_the_planner_alone(on):
    res, nv, g = _run(on, gpu=8, start=600)  # R2 ceiling is 700 W; start 600 is under it
    assert res.physical is None and g.top_w == 700


# ---- gate ---------------------------------------------------------------------------------

def cap_spec(gpu, watts):
    return {
        "kind": m.POWER_CAP, "title": f"Cap GPU {gpu} at {watts} W", "summary": "Lower the cap.",
        "change": {"target": f"gpu:{gpu}", "gpu": gpu, "before": {"power_cap_w": 700},
                   "after": {"power_cap_w": watts}, "drop_pct": 10.0},
        "savings": [{"metric": "energy per request", "value": 9.5, "unit": "%",
                     "label": "MEASURED"}],
        "checks": {"quality": {"passed": True, "detail": "ok"},
                   "p99": {"passed": True, "detail": "ok", "increase_pct": 2.0}},
        "receipt_preview": {"profile": "gpu", "label": "MEASURED"}, "ttl_s": 3600}


@pytest.fixture
def apps(tmp_path):
    return Approvals(tmp_path / "p", clock=lambda: 1_800_000_000.0)


def _gate_obj(apps):
    return ApprovalGate(apps, mode="pre_approved", name="audit test", poll_s=0,
                        announce=lambda s: None, sleep=lambda s: None)


def test_gate_refuses_a_cap_on_a_gpu_with_no_unit_before_proposing(apps, on):
    done = []
    ap = CallbackApplier(lambda p: done.append(p["id"]), lambda t: None)
    with pytest.raises(NotApproved, match="physical check failed.*slot 4"):
        _gate_obj(apps).request(cap_spec(12, 500), applier=ap, physical=on)
    assert done == [] and apps.list() == []  # nothing proposed, nothing applied


def test_gate_refuses_a_cap_above_the_rack_ceiling(apps, on):
    with pytest.raises(NotApproved, match="above the rack's per-node ceiling of 600"):
        _gate_obj(apps).request(cap_spec(0, 650), physical=on)


def test_gate_applies_a_good_cap_and_records_the_physical_check(apps, on):
    ap = CallbackApplier(lambda p: {}, lambda t: None)
    p = _gate_obj(apps).request(cap_spec(0, 550), applier=ap, physical=on)
    assert p["status"] == m.APPLIED
    chk = p["checks"]["physical"]
    assert chk["passed"] and chk["ceiling_w"] == 600 and chk["label"] == "MEASURED"


def test_without_sam_the_same_cap_on_the_missing_gpu_goes_through(apps, off):
    ap = CallbackApplier(lambda p: {}, lambda t: None)
    p = _gate_obj(apps).request(cap_spec(12, 450), applier=ap, physical=off)
    assert p["status"] == m.APPLIED  # the inventory said GPU 12 exists; no one checked


def test_session_bounds_are_cut_to_what_the_photo_supports(apps, on):
    b = {"power_cap_w": {"min": 400, "max": 700}, "gpus": [0, 8, 12]}
    cut = narrow_bounds(b, on)
    assert cut["gpus"] == [0, 8] and cut["power_cap_w"]["max"] == 600
    assert cut["physical"]["dropped_gpus"].keys() == {12}
    assert b["power_cap_w"]["max"] == 700  # the caller's dict is not changed
    with _gate_obj(apps).session(title="try", bounds=b, physical=on) as s:
        s.check_cap(550, 0)
        with pytest.raises(NotApproved, match="outside the approved 400-600"):
            s.check_cap(650, 0)
        with pytest.raises(NotApproved, match="GPU 12 is not in this grant"):
            s.check_cap(500, 12)


def test_session_with_no_supported_gpu_is_refused(apps, on):
    with pytest.raises(NotApproved, match="physical check failed"):
        _gate_obj(apps).session(title="try", bounds={"power_cap_w": {"min": 400, "max": 700},
                                                     "gpus": [12, 13]}, physical=on)
    with pytest.raises(NotApproved, match="below the requested minimum"):
        narrow_bounds({"power_cap_w": {"min": 650, "max": 700}, "gpus": [0]}, on)


def test_auto_approve_policy_blocks_a_failed_physical_check(on):
    policy = pol.Policy.from_dict({**pol.TEMPLATE, "enabled": True})
    good = m.build(with_physical_check(cap_spec(0, 550), on), ts=1_800_000_000.0)
    bad = {**good, "checks": {**good["checks"], "physical": physical_check(on, 12, 500)}}
    assert policy.evaluate(good)[0] or "physical" not in " ".join(policy.evaluate(good)[1])
    ok, why = policy.evaluate(bad)
    assert not ok and any("rack photo check" in w for w in why)


# ---- ledger and client --------------------------------------------------------------------

def test_ledger_charges_sam_per_image_and_refuses_over_the_cap(tmp_path):
    led = SpendLedger(tmp_path / "l.json", cap_usd=0.005, soft_usd=0.004)
    a = led.charge_image("sam-3.1", "t")
    led.charge_image("sam-3.1", "t")
    assert led.total() == 0.005
    with pytest.raises(BudgetExceeded):
        led.charge_image("sam-3.1", "t")
    led.refund(a, "HTTP 500")
    assert led.total() == 0.0025


def _ok_reply(text="<0f>0<|box;x1=1;y1=2;x2=3;y2=4;w=9;h=9|>"):
    body = {"output": [{"content": [{"type": "output_text", "text": text}]}]}
    return lambda req, t: (200, json.dumps(body).encode())


def test_client_logs_the_call_and_never_stores_the_key(tmp_path):
    led = SpendLedger(tmp_path / "l.json")
    seen = {}

    def opener(req, timeout):
        seen["auth"] = req.headers["Authorization"]
        seen["body"] = json.loads(req.data)
        return _ok_reply()(req, timeout)

    c = SamClient(key="sekret-key-123", ledger=led, opener=opener)
    text = c.segment(b"\xff\xd8jpg", "1U server")
    assert text.startswith("<0f>") and seen["body"]["model"] == "sam-3.1"
    assert led.total() == 0.0025
    assert "sekret-key-123" not in (tmp_path / "l.json").read_text()


def test_client_refunds_a_rejected_call_and_stops_at_the_cap(tmp_path):
    led = SpendLedger(tmp_path / "l.json", cap_usd=0.0025, soft_usd=0.002)
    bad = SamClient(key="k", ledger=led, opener=lambda r, t: (500, b"{}"))
    with pytest.raises(Exception, match="HTTP 500"):
        bad.segment(b"x", "server")
    assert led.total() == 0.0
    ok = SamClient(key="k", ledger=led, opener=_ok_reply())
    ok.segment(b"x", "server")
    with pytest.raises(BudgetExceeded):
        ok.segment(b"x", "server")


def test_output_text_joins_parts():
    data = {"output": [{"content": [{"type": "output_text", "text": "a"},
                                    {"type": "other"}, {"type": "output_text", "text": "b"}]}]}
    assert output_text(data) == "ab"


# ---- ablation, overlay, scripts -----------------------------------------------------------

def test_ablation_shows_both_directions(specs):
    from wattnot.sam import ablation
    r = ablation.run(specs)
    assert r["eligible_gpus"]["sam_on"] == list(range(12))
    by = {p["gpu"]: p for p in r["planner_final_cap_w"]}
    assert (by[0]["sam_off"]["cap_w"], by[0]["sam_on"]["cap_w"]) == (700, 600)   # tighter
    assert (by[8]["sam_off"]["cap_w"], by[8]["sam_on"]["cap_w"]) == (466, 700)   # looser
    assert "refused" in by[12]["sam_on"] and "refused" not in by[12]["sam_off"]
    assert r["labels"]["photos_and_unit_counts"] == "MEASURED"


def test_overlay_draws_every_unit(on, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    from wattnot.sam.overlay import render
    out = render(on.racks[1], tmp_path / "r2.jpg")
    assert Image.open(out).size[0] > 400


def test_audit_script_replays_without_a_key(capsys, monkeypatch):
    import importlib.util
    monkeypatch.delenv("META_API_KEY", raising=False)
    spec = importlib.util.spec_from_file_location(
        "sam_audit", Path(__file__).resolve().parents[1] / "scripts" / "sam_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main([]) == 0
    assert "eligible GPUs: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]" in capsys.readouterr().out
