"""Verifier core: check a receipt step by step, then grade it.

Steps, in order (each one is reported, even after a failure):
  schema     the receipt matches schemas/receipt.schema.json and spec wattnot.receipt/1
  sha256     JCS(receipt minus integrity) hashes to integrity.sha256
  signature  the bundle next to the receipt signs that hash (signer reported)
  rekor      a transparency-log entry (log_index); offline bundles have none
  grade      VALID / ESTIMATE / SIMULATED / NOISE / UNVERIFIABLE, re-derived, never trusted
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.grade import SPEC, grade
from wattnot.receipt.sign import bundle_path, verify_offline_bundle


@dataclass
class Step:
    name: str
    ok: bool
    detail: dict = field(default_factory=dict)


@dataclass
class Report:
    path: str
    steps: list[Step]
    grade: str
    claimed_grade: str | None

    def to_dict(self) -> dict:
        return {"path": self.path, "grade": self.grade, "claimed_grade": self.claimed_grade,
                "steps": [{"name": s.name, "ok": s.ok, **s.detail} for s in self.steps]}


def _schema_step(receipt) -> Step:
    if not isinstance(receipt, dict) or receipt.get("spec") != SPEC:
        spec = receipt.get("spec") if isinstance(receipt, dict) else None
        return Step("schema", False, {"error": f"spec is {spec!r}, want {SPEC}"})
    try:
        from wattnot.schemas import validate_receipt
        validate_receipt(receipt)
    except Exception as e:  # jsonschema.ValidationError and friends
        return Step("schema", False, {"error": str(e).splitlines()[0][:200]})
    return Step("schema", True, {"spec": SPEC})


TRUSTED_KEYS = Path(__file__).resolve().parents[2] / "verifier" / "trusted-keys.json"


def load_trusted(path: str | Path | None = None) -> set[str] | None:
    """Signer ids we accept. None means: accept any key that verifies."""

    p = Path(path) if path else TRUSTED_KEYS
    if not p.exists():
        return None
    return {k["signer"] for k in json.loads(p.read_text())["keys"]}


def verify_receipt(receipt_path: str | Path, bundle: str | Path | None = None,
                   trusted: set[str] | None = None) -> Report:
    receipt_path = Path(receipt_path)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        step = Step("schema", False, {"error": f"cannot read JSON: {e}"[:200]})
        return Report(str(receipt_path), [step], "UNVERIFIABLE", None)

    bpath = Path(bundle) if bundle else bundle_path(receipt_path)
    b = json.loads(bpath.read_text(encoding="utf-8")) if bpath.exists() else None
    return verify_document(receipt, b, trusted=trusted, path=str(receipt_path),
                           missing=f"no bundle at {bpath.name}")


def verify_document(receipt, bundle: dict | None, *, trusted: set[str] | None = None,
                    path: str = "<upload>", missing: str = "no bundle given") -> Report:
    """Verify an in-memory receipt (and bundle). Same steps as verify_receipt."""

    steps = [_schema_step(receipt)]
    integ = receipt.get("integrity") if isinstance(receipt, dict) else None
    integ = integ if isinstance(integ, dict) else {}
    expected = integ.get("sha256")
    try:
        got = canonical_sha256(receipt)
    except Exception as e:  # not canonicalizable
        got = None
        steps.append(Step("sha256", False, {"expected": expected, "error": str(e)[:200]}))
    else:
        steps.append(Step("sha256", bool(expected) and got == expected,
                          {"expected": expected, "got": got}))

    b = bundle
    if not isinstance(b, dict):
        steps.append(Step("signature", False, {"error": missing}))
        b = {}
    else:
        ok, why = verify_offline_bundle(receipt, b) if isinstance(receipt, dict) \
            else (False, "receipt is not a JSON object")
        detail = {"signer": why} if ok else {"error": why}
        if ok and integ.get("signer") not in (None, why):
            ok, detail = False, {"error": f"receipt names signer {integ.get('signer')}, "
                                          f"bundle is {why}"}
        if ok and trusted is not None and why not in trusted:
            ok, detail = False, {"error": f"signer {why} is not a trusted key"}
        steps.append(Step("signature", ok, detail))

    rekor = b.get("rekor") if isinstance(b, dict) else None
    log_index = rekor.get("log_index") if isinstance(rekor, dict) else None
    steps.append(Step("rekor", log_index is not None,
                      {"log_index": log_index} if log_index is not None
                      else {"note": "no Rekor entry (offline signature): grade at most ESTIMATE"}))

    sha_ok, sig_ok = steps[1].ok, steps[2].ok
    g = grade(receipt, sha_ok=steps[0].ok and sha_ok, sig_ok=sig_ok,
              rekor_ok=log_index is not None) if isinstance(receipt, dict) else "UNVERIFIABLE"
    claimed = integ.get("grade") if integ else None
    steps.append(Step("grade", g != "UNVERIFIABLE", {"grade": g, "claimed": claimed}))
    return Report(path, steps, g, claimed)


def format_report(r: Report) -> str:
    lines = [f"wattnot verify {r.path}"]
    for s in r.steps:
        mark = "ok  " if s.ok else "FAIL"
        d = s.detail
        if s.name == "sha256":
            info = f"expected {d.get('expected')}  got {d.get('got', d.get('error'))}"
        elif s.name == "signature":
            info = f"signer {d['signer']}" if s.ok else d.get("error", "")
        elif s.name == "rekor":
            info = f"log_index {d['log_index']}" if s.ok else d.get("note", "")
            mark = "ok  " if s.ok else "none"
        elif s.name == "grade":
            info = f"{d['grade']}" + (f"  (receipt claims {d['claimed']})"
                                      if d.get("claimed") and d["claimed"] != d["grade"] else "")
        else:
            info = d.get("spec") or d.get("error", "")
        lines.append(f"  {mark} {s.name:<9} {info}")
    return "\n".join(lines)
