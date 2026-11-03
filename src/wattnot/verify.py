"""Verifier core: check a receipt step by step, then grade it.

Steps, in order (each one is reported, even after a failure):
  schema     the receipt matches schemas/receipt.schema.json and spec wattnot.receipt/1
  sha256     JCS(receipt minus integrity) hashes to integrity.sha256
  signature  the bundle next to the receipt signs that hash (signer reported)
  rekor      a transparency-log entry (log_index); offline bundles have none
  labels     every "label" is a known one, and the headline `result` carries no SIMULATED/MOCK
  grade      VALID / ESTIMATE / SIMULATED / NOISE / UNVERIFIABLE, re-derived, never trusted

`verify_chain` checks a list of receipts in order: each one's `integrity.prev_sha256` must
equal the previous receipt's `integrity.sha256` (the first may be null or point outside).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.grade import SPEC, grade
from wattnot.receipt.joules import check_joules
from wattnot.receipt.sci import check_sci, summary_line
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
    joules: dict | None = None  # derived j_per_answer / j_per_good_answer, None if not derivable
    sci: dict | None = None     # the receipt's sci block, when it re-derives cleanly

    def to_dict(self) -> dict:
        return {"path": self.path, "grade": self.grade, "claimed_grade": self.claimed_grade,
                "steps": [{"name": s.name, "ok": s.ok, **s.detail} for s in self.steps],
                "joules": self.joules, "sci": self.sci}


FLOW_SPEC = "wattnot.flow-receipt/1"


def _is_flow(receipt) -> bool:
    return isinstance(receipt, dict) and receipt.get("spec") == FLOW_SPEC


ROUND_FIELDS = ("kind", "round", "results", "labels", "integrity")


def _is_round(receipt) -> bool:
    """A round receipt (written by e2e post_round: kind hosted-e2e, modal-own-gpu-e2e, ...).
    It has no `spec` field; it sums one round's arms and is signed with the run's key."""
    return (isinstance(receipt, dict) and "spec" not in receipt
            and isinstance(receipt.get("kind"), str) and receipt["kind"].endswith("e2e")
            and "round" in receipt)


def _self_signed_ok(receipt) -> bool:
    return _is_flow(receipt) or _is_round(receipt)


def _schema_step(receipt) -> Step:
    if _is_flow(receipt):
        # a flow receipt: one row per layer, the layer artifacts hashed; it carries no
        # measurement block of its own, so it never grades above ESTIMATE
        lay = receipt.get("layers")
        if not isinstance(lay, list) or not all(isinstance(x, dict) and "id" in x for x in lay) \
                or not isinstance(receipt.get("integrity"), dict):
            return Step("schema", False, {"error": "flow receipt needs layers[{id,...}] and integrity"})
        return Step("schema", True, {"spec": FLOW_SPEC, "layers": len(lay)})
    if _is_round(receipt):
        missing = [k for k in ROUND_FIELDS if k not in receipt]
        if missing or not isinstance(receipt.get("results"), dict) \
                or not isinstance(receipt.get("integrity"), dict):
            return Step("schema", False, {"error": f"round receipt missing or bad: {missing or 'results/integrity'}"})
        return Step("schema", True, {"spec": f"round receipt ({receipt['kind']} {receipt['round']})"})
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


TRUST_LABELS = {"ed25519-test": "test-signed (public sample key)",
                "ed25519-wattnot": "signed (WattNot certificate key)"}


def load_trusted(path: str | Path | None = None) -> dict[str, str] | None:
    """Signer ids we accept -> their kind (a dict, so `id in trusted` works like the old set).
    None means: accept any key that verifies. Anything signed by an 'ed25519-test' key
    (WNC-0001 and every receipt before Modal round r04d) is labelled test-signed."""

    p = Path(path) if path else TRUSTED_KEYS
    if not p.exists():
        return None
    return {k["signer"]: k.get("kind", "") for k in json.loads(p.read_text())["keys"]}


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
            if _self_signed_ok(receipt):
                # the flow and each e2e round sign with a per-run (or local test) key: the signature is checked,
                # but the key proves only that this file was not changed since the run
                detail = {"signer": why, "trusted": False,
                          "note": "per-run key, not in trusted-keys.json: self-signed, grade at most "
                                  "ESTIMATE (pass --trust-signer to accept it)"}
            else:
                ok, detail = False, {"error": f"signer {why} is not a trusted key"}
        if ok and isinstance(trusted, dict) and trusted.get(why):
            detail["trust"] = TRUST_LABELS.get(trusted[why], trusted[why])
        steps.append(Step("signature", ok, detail))

    rekor = b.get("rekor") if isinstance(b, dict) else None
    log_index = rekor.get("log_index") if isinstance(rekor, dict) else None
    steps.append(Step("rekor", log_index is not None,
                      {"log_index": log_index} if log_index is not None
                      else {"note": "no Rekor entry (offline signature): grade at most ESTIMATE"}))

    derived = None
    derived_ok = True
    if isinstance(receipt, dict):
        derived_ok, jd = check_joules(receipt)
        derived = jd.get("derived")
        if jd.get("present"):  # only receipts that carry the block get this step
            steps.append(Step("joules", derived_ok,
                              {"error": jd["error"]} if not derived_ok else {"matches": True}))
    sci_out = None
    if _is_round(receipt) and "sci" in receipt:
        # a round receipt carries only the grid factor it used; the per-answer SCI lives in the
        # energy receipt, so here we just check the factor is a labelled number
        grid = (receipt.get("sci") or {}).get("grid") or {}
        g_ok = isinstance(grid.get("gco2e_per_kwh"), (int, float)) and grid.get("label") in KNOWN_LABELS
        steps.append(Step("sci", g_ok, {"matches": True, "summary": f"grid {grid.get('gco2e_per_kwh')} gCO2e/kWh [{grid.get('label')}]"}
                          if g_ok else {"error": "round sci.grid needs a labelled gco2e_per_kwh"}))
        derived_ok = derived_ok and g_ok
    elif isinstance(receipt, dict) and "sci" in receipt:
        sci_ok, sd = check_sci(receipt)
        steps.append(Step("sci", sci_ok, {"error": sd["error"]} if not sci_ok
                          else {"matches": True, "summary": summary_line(receipt["sci"])}))
        sci_out = receipt["sci"] if sci_ok else None
        derived_ok = derived_ok and sci_ok
    if isinstance(receipt, dict):
        steps.append(labels_step(receipt))
    sha_ok, sig_ok = steps[1].ok, steps[2].ok
    if _self_signed_ok(receipt):
        # no measurement block of its own and a per-run key: never above ESTIMATE
        g = "ESTIMATE" if steps[0].ok and sha_ok and sig_ok else "UNVERIFIABLE"
    else:
        g = grade(receipt, sha_ok=steps[0].ok and sha_ok, sig_ok=sig_ok,
                  rekor_ok=log_index is not None, derived_ok=derived_ok) \
            if isinstance(receipt, dict) else "UNVERIFIABLE"
    lab = next((x for x in steps if x.name == "labels"), None)
    if lab is not None and not lab.ok and g != "UNVERIFIABLE":
        # weak labels lower the grade: a SIMULATED/MOCK headline grades SIMULATED, an unknown
        # label means we can't say what the number is
        g = "UNVERIFIABLE" if lab.detail.get("unknown") else "SIMULATED"
    claimed = integ.get("grade") if integ else None
    steps.append(Step("grade", g != "UNVERIFIABLE", {"grade": g, "claimed": claimed}))
    return Report(path, steps, g, claimed, derived, sci_out)


KNOWN_LABELS = {"MEASURED", "ESTIMATE", "SIMULATED", "SYNTHETIC", "MOCK", "REPLAY", "ADVICE",
                "SAMPLE"}
WEAK = {"SIMULATED", "MOCK"}


def _labels(obj, path="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "label" and isinstance(v, str):
                yield path, v
            else:
                yield from _labels(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _labels(v, f"{path}[{i}]")


def labels_step(receipt: dict) -> Step:
    found = list(_labels(receipt))
    unknown = [f"{p}={v}" for p, v in found if v not in KNOWN_LABELS]
    # a weak headline is fine when the receipt says so itself (its grade is SIMULATED or MOCK)
    declared = (receipt.get("grade") in WEAK or (receipt.get("integrity") or {}).get("grade") in WEAK) if isinstance(receipt, dict) else False
    weak_head = [] if declared else [f"{p}={v}" for p, v in _labels(receipt.get("result"), "$.result") if v in WEAK]
    counts: dict[str, int] = {}
    for _, v in found:
        counts[v] = counts.get(v, 0) + 1
    ok = not unknown and not weak_head
    d: dict = {"counts": counts}
    if unknown:
        d["unknown"] = unknown[:5]
    if weak_head:
        d["error"] = f"headline result uses {', '.join(weak_head[:3])}"
    elif unknown:
        d["error"] = f"unknown label(s): {', '.join(unknown[:3])}"
    return Step("labels", ok, d)


def verify_chain(receipts: list) -> Step:
    """receipts: parsed receipt dicts, oldest first."""

    links = []
    ok = True
    for i, r in enumerate(receipts):
        integ = (r.get("integrity") or {}) if isinstance(r, dict) else {}
        if i == 0:
            continue
        prev = (receipts[i - 1].get("integrity") or {}).get("sha256") \
            if isinstance(receipts[i - 1], dict) else None
        good = integ.get("prev_sha256") == prev and prev is not None
        ok &= good
        links.append({"index": i, "ok": good, "prev_sha256": integ.get("prev_sha256"),
                      "want": prev})
    return Step("chain", ok, {"links": links, "n": len(receipts)})


def format_report(r: Report) -> str:
    lines = [f"wattnot verify {r.path}"]
    for s in r.steps:
        mark = "ok  " if s.ok else "FAIL"
        d = s.detail
        if s.name == "sha256":
            info = f"expected {d.get('expected')}  got {d.get('got', d.get('error'))}"
        elif s.name == "signature":
            info = (f"signer {d['signer']}" + (f" [{d['trust']}]" if d.get("trust") else "")
                    + (f"  ({d['note']})" if d.get("note") else "")) \
                if s.ok else d.get("error", "")
            if s.ok and d.get("trusted") is False:
                mark = "warn"
        elif s.name == "rekor":
            info = f"log_index {d['log_index']}" if s.ok else d.get("note", "")
            mark = "ok  " if s.ok else "none"
        elif s.name == "labels":
            info = d.get("error") or ", ".join(f"{k} {v}" for k, v in sorted(d["counts"].items())) \
                or "no labelled numbers"
        elif s.name == "joules":
            info = "matches the receipt's numbers" if s.ok else d.get("error", "")
        elif s.name == "sci":
            info = (d.get("summary") if d.get("summary", "").startswith("grid") else
                    "E x I and water match the receipt's numbers") if s.ok else d.get("error", "")
        elif s.name == "grade":
            info = f"{d['grade']}" + (f"  (receipt claims {d['claimed']})"
                                      if d.get("claimed") and d["claimed"] != d["grade"] else "")
        else:
            info = d.get("spec") or d.get("error", "")
        lines.append(f"  {mark} {s.name:<9} {info}")
    j = (r.joules or {}).get("candidate")
    if j and j.get("j_per_answer") is not None:
        good = j.get("j_per_good_answer")
        lab = j.get("label") or (r.joules or {}).get("label") or "ESTIMATE"
        lines.append(f"  info {'':<9} [{lab}] candidate {j['j_per_answer']:.1f} J per answer, "
                     + (f"{good:.1f} J per good answer" if good is not None
                        else "no good answers (pass rate 0)"))
    if r.sci:
        lines.append(f"  info {'':<9} {summary_line(r.sci)}")
        lines.append(f"  info {'':<9} I source: {r.sci['I'].get('source')}; "
                     "operational intensity per answer only, not a total-footprint claim")
    return "\n".join(lines)


REKOR_URL = "https://rekor.sigstore.dev/api/v1/log/entries?logIndex={}"


def rekor_online_step(receipt: dict, bundle: dict | None, *, fetch=None) -> Step:
    """Look the receipt's log index up on the public Rekor log and check the entry carries the
    receipt's sha256. Offline-signed receipts have no index, so this step fails for them."""

    import base64
    import urllib.request

    rk = (bundle or {}).get("rekor") or {}
    idx = rk.get("log_index")
    want = ((receipt.get("integrity") or {}).get("sha256") or "").lower()
    if idx is None:
        return Step("rekor-online", False, {"error": "no Rekor log index: signed offline, not in the public log"})
    try:
        if fetch is None:
            with urllib.request.urlopen(REKOR_URL.format(int(idx)), timeout=15) as r:  # noqa: S310
                data = json.loads(r.read())
        else:
            data = fetch(int(idx))
        entry = next(iter(data.values()))
        body = json.loads(base64.b64decode(entry["body"]))
        blob = json.dumps(body).lower()
    except Exception as e:
        return Step("rekor-online", False, {"log_index": idx, "error": f"lookup failed: {type(e).__name__}"})
    ok = bool(want) and want in blob
    return Step("rekor-online", ok, {"log_index": idx, "integrated_time": entry.get("integratedTime"),
                                     **({} if ok else {"error": "the log entry does not carry this receipt's sha256"})})
