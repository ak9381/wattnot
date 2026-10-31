"""A statistic that cannot be computed (NaN/Inf, e.g. a 95% interval from one round) must be
written as null with a reason, never crash JCS canonicalisation (retest 31 Oct, t1-sweep)."""
import pytest

from wattnot.receipt.canon import canonical_sha256, canonicalize, scrub_nonfinite


def test_scrub_replaces_nan_and_inf_with_paths():
    clean, found = scrub_nonfinite({"a": {"ci95_half": float("nan")}, "b": [1.0, float("inf")]})
    assert clean == {"a": {"ci95_half": None}, "b": [1.0, None]}
    assert found == ["a.ci95_half", "b[1]"]


def test_scrubbed_body_canonicalises_and_canon_stays_strict():
    body = {"steps": [{"energy_change_pct": {"mean": -27.8, "sd": 0.0, "ci95_half": float("nan")}}]}
    with pytest.raises(ValueError):
        canonicalize(body)
    clean, found = scrub_nonfinite(body)
    assert found == ["steps[0].energy_change_pct.ci95_half"]
    assert len(canonical_sha256(clean)) == 64


def test_finite_body_is_unchanged():
    body = {"x": 1, "y": [2.5, "s", None, True]}
    clean, found = scrub_nonfinite(body)
    assert clean == body and found == []
