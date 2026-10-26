"""T13: receipt build, JCS canonicalisation, SHA-256 chain, lint and grades."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from wattnot.receipt import (
    build_from_dir,
    build_receipt,
    canonical_sha256,
    canonicalize,
    grade,
    lint,
    verify_hash,
)
from wattnot.schemas import validate_receipt

FIX = Path(__file__).parent / "fixtures"
RC = FIX / "receipts"


def _load(name):
    return json.loads((RC / f"{name}.json").read_text())


def _run():
    import importlib.util
    spec = importlib.util.spec_from_file_location("mk", RC / "make.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    return mk, mk.run("run_20261028_100000_a00009", mk.BASE, mk.CAND)


@pytest.mark.parametrize("line", (FIX / "jcs-vectors.jsonl").read_text().splitlines())
def test_jcs_vectors_from_rfc_8785(line):
    v = json.loads(line)
    assert canonicalize(v["in"]) == v["out"]


def test_jcs_rejects_nan():
    with pytest.raises(ValueError):
        canonicalize({"x": float("nan")})


@pytest.mark.parametrize("name", ["valid", "estimate", "noise"])
def test_fixtures_validate_and_hash(name):
    r = _load(name)
    validate_receipt(r)
    assert lint(r) == []
    ok, want, got = verify_hash(r)
    assert ok and want == got == r["integrity"]["sha256"]


def test_fixtures_regenerate_byte_for_byte(tmp_path):
    mk, _ = _run()
    before = {n: (RC / f"{n}.json").read_text() for n in ("valid", "estimate", "noise")}
    mk.main()
    assert {n: (RC / f"{n}.json").read_text() for n in before} == before


def test_editing_one_digit_changes_the_hash():
    r = _load("valid")
    forged = copy.deepcopy(r)
    forged["result"]["billed_saving_pct"] = 19.5736
    assert canonical_sha256(forged) != r["integrity"]["sha256"]
    assert not verify_hash(forged)[0]


def test_grades():
    assert _load("valid")["grade"] == "VALID"
    assert _load("estimate")["grade"] == "ESTIMATE"
    n = _load("noise")
    assert n["grade"] == "NOISE" and n["result"]["billed_saving_pct"] == 0
    assert not n["result"]["billable"] and n["result"]["welch"]["p_value"] >= 0.05
    v = _load("valid")
    assert grade(v, sha_ok=True, sig_ok=True, rekor_ok=True) == "VALID"
    assert grade(v, sha_ok=True, sig_ok=True, rekor_ok=False) == "ESTIMATE"
    assert grade(v, sha_ok=False, sig_ok=True, rekor_ok=True) == "UNVERIFIABLE"
    assert grade({"energy_wh": 1.2}, sha_ok=True, sig_ok=True, rekor_ok=True) == "UNVERIFIABLE"


def test_offline_build_is_at_most_estimate():
    _, run = _run()
    assert build_receipt(run, created_at="2026-10-28T10:00:00+00:00")["grade"] == "ESTIMATE"


def test_water_is_estimate_money_is_usd_and_trace_is_included():
    r = _load("valid")
    for k in ("water_ml_per_req", "baseline_water_ml_per_req"):
        assert r["estimates"][k]["label"] == "ESTIMATE"
    assert r["result"]["currency"] == "USD"
    assert r["candidate"]["usd_per_req"]["mean"] == pytest.approx(
        r["candidate"]["wh_per_req"]["mean"] / 1000 * 0.12, rel=1e-3)
    assert r["planner_trace"][0]["model"] == "muse-spark-1.3"
    assert {"t", "df", "p_value", "alpha"} <= set(r["result"]["welch"])


def test_water_label_other_than_estimate_fails_schema():
    r = _load("valid")
    r["estimates"]["water_ml_per_req"]["label"] = "MEASURED"
    with pytest.raises(jsonschema.ValidationError):
        validate_receipt(r)


def test_lint_catches_prompt_and_long_strings():
    r = _load("valid")
    r["candidate"]["prompt"] = "hello"
    r["subject"]["model"] = "x" * 201
    errs = lint(r)
    assert any("prompt" in e for e in errs) and any("201 chars" in e for e in errs)
    _, run = _run()
    run["subject"]["outputs"] = ["secret"]
    with pytest.raises(ValueError, match="lint"):
        build_receipt(run)


def test_planner_trace_comes_from_decision_events_and_chain_links(tmp_path):
    _, run = _run()
    run.pop("planner_trace")
    d = tmp_path / "runs" / run["run_id"]
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps(run))
    live = Path(__file__).parents[1] / "web" / "run" / "demo-events.live.jsonl"
    (d / "events.jsonl").write_text(live.read_text())
    out = tmp_path / "receipts"
    p1 = build_from_dir(d, out, created_at="2026-10-28T10:00:00+00:00")
    r1 = json.loads(p1.read_text())
    assert r1["planner_trace"] and r1["planner_trace"][0]["source"] == "spark"
    assert r1["integrity"]["prev_sha256"] is None
    run["run_id"] = "run_20261028_110000_a0000a"
    (d / "run.json").write_text(json.dumps(run))
    r2 = json.loads(build_from_dir(d, out, created_at="2026-10-28T11:00:00+00:00").read_text())
    assert r2["integrity"]["prev_sha256"] == r1["integrity"]["sha256"]


def test_compare_to_is_optional_and_hashed():
    _, run = _run()
    a = build_receipt(run, created_at="2026-10-28T10:00:00+00:00")
    run["compare_to"] = "run_20261027_090000_e0a3b1"
    b = build_receipt(run, created_at="2026-10-28T10:00:00+00:00")
    assert "compare_to" not in a and b["compare_to"]
    assert a["integrity"]["sha256"] != b["integrity"]["sha256"]


def test_cli_receipt_build(tmp_path, capsys):
    from wattnot.cli import main
    _, run = _run()
    d = tmp_path / "runs" / run["run_id"]
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps(run))
    assert main(["receipt", "build", "--run", str(d), "--out", str(tmp_path / "r")]) == 0
    assert "grade ESTIMATE" in capsys.readouterr().out
