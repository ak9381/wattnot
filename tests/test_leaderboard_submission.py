from pathlib import Path

from wattnot.dash import leaderboard as L

ROOT = Path(__file__).resolve().parents[1]


def test_example_submission_is_valid():
    assert L.validate_submission(L.SUBMISSION_EXAMPLE) == []


def test_validator_reports_each_problem():
    bad = dict(L.SUBMISSION_EXAMPLE)
    bad["label"] = "GUESS"           # not in the enum
    bad["pass_pct"] = 150            # above 100
    bad["surprise"] = 1              # additionalProperties
    problems = L.validate_submission(bad)
    joined = " ".join(problems)
    assert "label" in joined and "pass_pct" in joined and "surprise" in joined


def test_missing_required_field_fails():
    bad = {k: v for k, v in L.SUBMISSION_EXAMPLE.items() if k != "receipt"}
    assert any("receipt" in p for p in L.validate_submission(bad))


def test_hosted_rows_are_estimate_from_tokens():
    rows = L.hosted_rows(ROOT)
    baseten = next(r for r in rows if "Baseten" in r["setup"])
    assert baseten["label"] == "ESTIMATE" and baseten["status"] == "estimate"
    assert baseten["j_per_correct"]["mean"] > 0
    assert str(L.J_PER_TOKEN) in baseten["note"]
    # a hosted row still awaiting its own measurement stays pending + ESTIMATE
    modal = next(r for r in rows if "Modal" in r["setup"])
    assert modal["status"] == "pending" and modal["label"] == "ESTIMATE"


def test_build_exposes_schema_and_j_per_token():
    b = L.build(ROOT)
    assert b["submission_schema"].endswith("jpc-submission.schema.json")
    assert b["j_per_token_estimate"] == L.J_PER_TOKEN
    assert b["submission_example"]["spec"] == L.SUBMISSION_SPEC
