"""Receipt grades, as defined in schemas/SCHEMAS.md.

VALID        MEASURED, hash matches, signature OK and a Rekor entry found.
ESTIMATE     integrity OK but the measurement is an ESTIMATE, or the receipt
             is signed offline (no Rekor entry), so it can't be VALID.
NOISE        integrity OK but Welch p >= alpha: the saving is not billed.
UNVERIFIABLE no hash, no signature check, or not our spec.
"""

from __future__ import annotations

from collections.abc import Mapping

SPEC = "wattnot.receipt/1"


def claimed_grade(receipt: Mapping, *, rekor: bool) -> str:
    """The grade the builder writes. The verifier re-derives it."""

    w = receipt["result"]["welch"]
    if not receipt["result"]["billable"] or w["p_value"] >= w.get("alpha", 0.05):
        return "NOISE"
    if receipt["measurement"]["label"] != "MEASURED" or not rekor:
        return "ESTIMATE"
    return "VALID"


def grade(receipt: Mapping, *, sha_ok: bool, sig_ok: bool, rekor_ok: bool) -> str:
    """Grade a received receipt from its content and the integrity checks."""

    if not isinstance(receipt, Mapping) or receipt.get("spec") != SPEC or not sha_ok \
            or not sig_ok:
        return "UNVERIFIABLE"
    return claimed_grade(receipt, rekor=rekor_ok)
