"""Cloud receipt -> spec receipt: valid under the same spec, never better than ESTIMATE."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot.cli import main
from wattnot.cloud import receipt as cloud_receipt
from wattnot.cloud import run as cloud_run
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.cloud_adapter import from_cloud
from wattnot.receipt.grade import claimed_grade
from wattnot.receipt.lint import lint
from wattnot.schemas import validate_receipt
from wattnot.verify import load_trusted, verify_document, verify_receipt

ROOT = Path(__file__).resolve().parents[1]
KEY = ROOT / "verifier" / "samples" / "test-key.pem"
NOW = 1_792_000_000.0


@pytest.fixture(scope="module")
def cloud(tmp_path_factory):
    d = tmp_path_factory.mktemp("cloud")
    t, mode, _ = cloud_run.open_transport(live=False, fixture=cloud_run.DEMO_FIXTURE)
    path, r = cloud_run.run(cloud_run.Options(), t, mode, out_dir=d, now=NOW)
    return json.loads(path.read_text()), path


def _reseal(c):
    c["integrity"]["sha256"] = canonical_sha256(c)
    return c


def test_adapted_receipt_is_valid_under_the_spec(cloud):
    r = from_cloud(cloud[0])
    validate_receipt(r)
    assert lint(r) == []
    assert r["spec"] == "wattnot.receipt/1" and r["profile"] == "cloud"
    assert r["integrity"]["sha256"] == canonical_sha256(r)
    assert r["cloud"]["source_receipt"]["sha256"] == cloud[0]["integrity"]["sha256"]


def test_demo_fixture_is_simulated_everywhere(cloud):
    r = from_cloud(cloud[0])
    assert r["grade"] == "SIMULATED" and r["measurement"]["label"] == "SIMULATED"
    assert r["joules"]["label"] == "SIMULATED"
    assert "MEASURED" not in json.dumps(r)


def test_energy_is_a_range_and_arms_are_xhigh_vs_tuned(cloud):
    r = from_cloud(cloud[0])
    assert r["baseline"]["params"] == {"reasoning_effort": "xhigh"}
    rng = r["candidate"]["wh_per_req"]["range"]
    assert rng["low"] < r["candidate"]["wh_per_req"]["mean"] < rng["high"]
    assert r["joules"]["candidate"]["j_per_answer_range"]["high"] > \
        r["joules"]["candidate"]["j_per_answer_range"]["low"]
    assert r["candidate"]["wh_per_req"]["basis"].startswith("middle of an ESTIMATE range")
    assert r["result"]["welch"]["applicable"] is False
    assert r["result"]["billable"] is False and r["result"]["billed_saving_pct"] == 0.0


def test_live_run_is_estimate_and_can_never_be_valid(cloud):
    live = json.loads(json.dumps(cloud[0]))
    live["mode"] = "live"
    live["label"] = "MEASURED"
    live["labels"].update(tokens="MEASURED", usd="MEASURED", latency="MEASURED")
    for c in live["classes"]:
        for k in ("measured", "would_have_cost_xhigh", "saving"):
            c[k]["label"] = "MEASURED"
    live["totals"]["label"] = "MEASURED"
    _reseal(live)
    r = from_cloud(live)
    assert r["measurement"]["label"] == "ESTIMATE" and r["grade"] == "ESTIMATE"
    assert r["cloud"]["labels"]["usd"] == "MEASURED"
    # even with a Rekor entry the grade stays at ESTIMATE: no significance test
    assert claimed_grade(r, rekor=True) == "ESTIMATE"
    bundle = {"mediaType": "x", "rekor": {"log_index": 1}}
    assert verify_document(r, bundle).grade == "UNVERIFIABLE"  # bad bundle: still not VALID


def test_changed_cloud_receipt_is_refused(cloud):
    bad = json.loads(json.dumps(cloud[0]))
    bad["totals"]["saving_usd"] += 1
    with pytest.raises(ValueError, match="hash does not match"):
        from_cloud(bad)


def test_not_a_cloud_receipt_is_refused():
    with pytest.raises(ValueError, match="not a cloud receipt"):
        from_cloud({"spec": "wattnot.receipt/1"})


def test_zero_requests_is_refused_not_a_zero_division(cloud):
    # A receipt with no requests must raise a clean ValueError, not ZeroDivisionError when
    # dividing the energy ranges by the request count.
    c = json.loads(json.dumps(cloud[0]))
    c["totals"]["requests"] = 0
    _reseal(c)
    with pytest.raises(ValueError, match="zero requests"):
        from_cloud(c)


def test_banned_wording_is_refused(cloud):
    c = json.loads(json.dumps(cloud[0]))
    c["unverified"] = ["this run is carbon neutral"]
    _reseal(c)
    # the cloud receipt's own check would have refused it at build time; the adapter does too
    with pytest.raises(ValueError, match="banned wording"):
        from_cloud(c)
    assert "neutral" in cloud_receipt.BANNED


def test_long_notes_are_trimmed_to_pass_the_linter(cloud):
    c = json.loads(json.dumps(cloud[0]))
    c["unverified"] = ["x" * 400]
    _reseal(c)
    assert len(from_cloud(c)["cloud"]["unverified"][0]) == 200


def test_pass_rate_drop_shows_in_joules_per_good_answer(cloud):
    c = json.loads(json.dumps(cloud[0]))
    for cl in c["classes"]:
        for t in cl["tried"]:
            if t.get("effort") == cl["chosen_effort"] and "quality_pct" in t:
                t["quality_pct"] = 50.0
    _reseal(c)
    r = from_cloud(c)
    cand = r["joules"]["candidate"]
    assert cand["pass_rate"] < r["joules"]["baseline"]["pass_rate"]
    assert cand["j_per_good_answer"] > cand["j_per_answer"]


def test_cli_from_cloud_then_verify(cloud, tmp_path, capsys):
    assert main(["receipt", "from-cloud", str(cloud[1]), "--out", str(tmp_path),
                 "--sign-key", str(KEY)]) == 0
    out = capsys.readouterr().out
    assert "grade SIMULATED" in out
    p = next(x for x in tmp_path.glob("rcpt_*.json") if "sigstore" not in x.name)
    rep = verify_receipt(p, trusted=load_trusted())
    assert rep.grade == "SIMULATED" and all(s.ok for s in rep.steps if s.name != "rekor")


def test_committed_cloud_vector_verifies():
    rep = verify_receipt(ROOT / "verifier" / "vectors" / "real-cloud.json",
                         trusted=load_trusted())
    assert rep.grade == "SIMULATED"
