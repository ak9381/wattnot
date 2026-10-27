"""Receipt grades, as defined in schemas/SCHEMAS.md.

VALID        MEASURED, hash matches, signature OK and a Rekor entry found.
ESTIMATE     integrity OK but the measurement is an ESTIMATE, or the receipt
             is signed offline (no Rekor entry), so it can't be VALID.
SIMULATED    integrity OK but the numbers come from the fake backend, not a GPU.
NOISE        integrity OK but Welch p >= alpha: the saving is not billed.
UNVERIFIABLE no hash, no signature check, or not our spec.
"""

from __future__ import annotations

from collections.abc import Mapping

SPEC = "wattnot.receipt/1"


def claimed_grade(receipt: Mapping, *, rekor: bool) -> str:
    """The grade the builder writes. The verifier re-derives it.

    A receipt with no significance test (`result.welch.applicable` is false, as for hosted
    cloud receipts and flex receipts) skips the NOISE check, and can never be VALID.
    """

    w = receipt["result"]["welch"]
    tested = w.get("applicable", True) is not False
    if tested and (not receipt["result"]["billable"] or w["p_value"] >= w.get("alpha", 0.05)):
        return "NOISE"
    if receipt["measurement"]["label"] == "SIMULATED":
        return "SIMULATED"
    if receipt["measurement"]["label"] != "MEASURED" or not rekor or not tested:
        return "ESTIMATE"
    return "VALID"


def grade(receipt: Mapping, *, sha_ok: bool, sig_ok: bool, rekor_ok: bool,
          derived_ok: bool = True) -> str:
    """Grade a received receipt from its content and the integrity checks.

    `derived_ok` is False when a stored derived block (joules) disagrees with the receipt.
    """

    if not isinstance(receipt, Mapping) or receipt.get("spec") != SPEC or not sha_ok \
            or not sig_ok or not derived_ok:
        return "UNVERIFIABLE"
    return claimed_grade(receipt, rekor=rekor_ok)
