"""Mint rules, the certificate document, and the hash-chained register.

A certificate is a human-readable statement of what one verified receipt says. Nothing on it is
new: every figure is re-derived from the receipt, and the certificate holds the receipt's hash.

Mint rules (all enforced here, not in the renderer):

1. The receipt is checked again with the library verifier: schema, sha256, a signature from a
   trusted key, and the joules block. The grade comes from that check, not from the file.
2. Grade VALID or ESTIMATE. NOISE and UNVERIFIABLE are refused. SIMULATED is refused unless the
   caller says `allow_simulated`; then the certificate says SIMULATED on its face.
3. The receipt must show a saving: the mean is above zero and, when the receipt carries a
   confidence interval, its lower end is above zero. The pass rate must not fall by more than
   one point.
4. One receipt, one certificate. The register keeps a claim key for the receipt hash, for the
   run id, and for a cloud source receipt, so the same saving cannot be certified twice by
   rebuilding the receipt.
5. Every entry in the register links to the one before it by hash.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.joules import joules_block
from wattnot.verify import Report, load_trusted, verify_receipt

CERT_FORMAT = "wattnot.certificate/1"
REGISTER_FORMAT = "wattnot.cert-register/1"
WATERMARK_LINES = ("MOCK", "NOT TRADABLE", "NOT A CARBON CREDIT")
SERIAL_PREFIX = "WNC-MOCK-"
GRADES_OK = ("VALID", "ESTIMATE")
MAX_PASS_DROP = 0.01
DEFAULT_VERIFY_URL = "https://github.com/ak9381/wattnot/tree/main/verifier"
# Words a certificate must never carry. Only the watermark may say "carbon credit", and it
# says the opposite.
BANNED = ("neutral", "offset", "carbon-free", "carbon free", "net zero", "net-zero")
LIMITS = (
    "This is a mock. No registry, buyer or regulator accepts it.",
    "It restates one receipt. It does not say the saving would repeat on other workloads.",
    "Energy is GPU draw (or a model of it) for the listed requests, not a whole site.",
    "No carbon figure is given, and none should be read into it.",
)


class MintRefused(Exception):
    """The receipt does not qualify. `reasons` is a list of {code, detail}."""

    def __init__(self, reasons: list[dict[str, str]]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(f"{r['code']}: {r['detail']}" for r in reasons))


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def figures(r: Mapping) -> dict:
    """Every number on the certificate, re-derived from the receipt."""

    b, c = r["baseline"], r["candidate"]
    wb, wc = b["wh_per_req"]["mean"], c["wh_per_req"]["mean"]
    sp = r["result"]["saving_pct"]
    lo, hi = _num(sp.get("ci_low")), _num(sp.get("ci_high"))
    ci = {"low": round(wb * lo / 100, 9), "high": round(wb * hi / 100, 9)} \
        if lo is not None and hi is not None else None
    fig: dict[str, Any] = {
        "wh_per_request": {
            "baseline": wb, "candidate": wc, "saved": round(wb - wc, 9), "saved_ci95": ci,
            "baseline_range": b["wh_per_req"].get("range"),
            "candidate_range": c["wh_per_req"].get("range"),
        },
        "saving_pct": {"mean": sp.get("mean"), "ci_low": lo, "ci_high": hi,
                       "basis": sp.get("basis")},
    }
    ub, uc = _num(b.get("usd_per_req", {}).get("mean")), _num(c.get("usd_per_req", {}).get("mean"))
    fig["usd_per_request"] = None if ub is None or uc is None else \
        {"baseline": ub, "candidate": uc, "saved": round(ub - uc, 10), "currency": "USD"}
    j = joules_block(r)  # always re-derived, never copied
    if j["baseline"].get("j_per_good_answer") is not None \
            and j["candidate"].get("j_per_good_answer") is not None:
        fig["j_per_good_answer"] = {
            "baseline": j["baseline"]["j_per_good_answer"],
            "candidate": j["candidate"]["j_per_good_answer"],
            "change_pct": j.get("change_pct", {}).get("j_per_good_answer"),
        }
        fig["pass_rate"] = {"baseline": j["baseline"]["pass_rate"],
                            "candidate": j["candidate"]["pass_rate"]}
    else:
        fig["j_per_good_answer"] = None
        fig["pass_rate"] = None
    return fig


def _claim_keys(r: Mapping, sha: str) -> list[str]:
    keys = [f"receipt:{sha}"]
    if r.get("run_id"):
        keys.append(f"run:{r['run_id']}")
    src = (r.get("cloud") or {}).get("source_receipt") or {}
    if src.get("sha256"):
        keys.append(f"cloud:{src['sha256']}")
    return keys


def check_rules(r: Mapping, report: Report, *, allow_simulated: bool = False) -> list[dict]:
    """Return the list of broken mint rules (empty means the receipt qualifies)."""

    bad: list[dict] = []

    def no(code: str, detail: str) -> None:
        bad.append({"code": code, "detail": detail})

    steps = {s.name: s for s in report.steps}
    for name in ("schema", "sha256", "signature"):
        if not steps[name].ok:
            why = steps[name].detail.get("error") or "values do not match"
            no("not_verified", f"{name} check failed: {why}"[:200])
    if "joules" in steps and not steps["joules"].ok:
        no("not_verified", "the joules block does not match the receipt")
    g = report.grade
    if g == "SIMULATED":
        if not allow_simulated:
            no("simulated", "a SIMULATED receipt is only minted with allow_simulated (demo)")
    elif g not in GRADES_OK:
        no("grade", f"grade is {g}; only {' or '.join(GRADES_OK)} are minted")
    if bad:
        return bad
    try:
        fig = figures(r)
    except (KeyError, TypeError) as e:
        return [{"code": "not_verified", "detail": f"receipt has no usable figures: {e!r}"}]
    if fig["j_per_good_answer"] is None:
        no("no_joules", "the receipt has no pass rate, so joules per good answer cannot be derived")
    mean = _num(fig["saving_pct"]["mean"])
    if mean is None or mean <= 0:
        no("no_saving", f"saving is {fig['saving_pct']['mean']}%, not above zero")
    lo = fig["saving_pct"]["ci_low"]
    if lo is not None and lo <= 0:
        no("ci_includes_zero", f"the 95% interval starts at {lo}%, so zero is not ruled out")
    pr = fig["pass_rate"]
    if pr and pr["candidate"] < pr["baseline"] - MAX_PASS_DROP - 1e-12:
        no("quality_drop", f"pass rate fell from {pr['baseline']} to {pr['candidate']}")
    return bad


# ---------------------------------------------------------------- register

def _empty_register() -> dict:
    note = "Local file. Nothing here is public and nothing here is tradable."
    return {"format": REGISTER_FORMAT, "label": "MOCK", "note": note, "entries": []}


def load_register(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return _empty_register()
    return json.loads(p.read_text(encoding="utf-8"))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def entry_hash(entry: Mapping) -> str:
    return canonical_sha256(entry)  # leaves out `integrity`


def verify_register(reg: Mapping) -> list[str]:
    """Problems in the chain (empty list means it is intact)."""

    bad: list[str] = []
    if reg.get("format") != REGISTER_FORMAT:
        bad.append(f"format is {reg.get('format')!r}")
    prev = None
    seen: dict[str, str] = {}
    for i, e in enumerate(reg.get("entries", [])):
        tag = e.get("serial", f"#{i + 1}")
        if e.get("prev_sha256") != prev:
            bad.append(f"{tag}: prev_sha256 does not match the entry before it")
        if (e.get("integrity") or {}).get("sha256") != entry_hash(e):
            bad.append(f"{tag}: entry was edited (hash mismatch)")
        if e.get("serial") != f"{SERIAL_PREFIX}{i + 1:04d}":
            bad.append(f"{tag}: serial out of sequence")
        for k in e.get("claim_keys", []):
            if k in seen:
                bad.append(f"{tag}: claim {k} was already certified by {seen[k]}")
            seen[k] = tag
        prev = (e.get("integrity") or {}).get("sha256")
    return bad


def lookup(reg: Mapping, serial: str) -> dict | None:
    return next((e for e in reg.get("entries", []) if e.get("serial") == serial), None)


# ---------------------------------------------------------------- certificate

def _build(r: Mapping, report: Report, *, serial: str, issued_at: str, verify_url: str) -> dict:
    sha = r["integrity"]["sha256"]
    subj = r.get("subject", {})
    host = subj.get("host", {})
    meas = r.get("measurement", {})
    url = f"{verify_url}#sha256={sha}"
    fig = figures(r)
    simulated = report.grade == "SIMULATED"
    headline = (
        f"Energy per request {fig['saving_pct']['mean']}% lower than the baseline "
        f"({fig['wh_per_request']['saved']} Wh saved per request)."
    )
    cert = {
        "format": CERT_FORMAT,
        "status": "MOCK",
        "watermark": {"lines": list(WATERMARK_LINES), "permanent": True},
        "serial": serial,
        "issued_at": issued_at,
        "title": "Verified Efficiency Statement (MOCK)",
        "receipt": {
            "receipt_id": r.get("receipt_id"), "sha256": sha, "spec": r.get("spec"),
            "grade": report.grade, "label": meas.get("label"),
            "signer": (r.get("integrity") or {}).get("signer"),
            "rekor": bool((r.get("integrity") or {}).get("rekor")),
            "created_at": r.get("created_at"),
        },
        "subject": {"model": subj.get("model"), "provider": host.get("provider"),
                    "gpu": host.get("gpu"),
                    "workload": (subj.get("workload") or {}).get("name"),
                    "mode": subj.get("mode")},
        "period": {"receipt_created_at": r.get("created_at"),
                   "requests": (subj.get("workload") or {}).get("n_requests")
                   or (r.get("cloud") or {}).get("requests")},
        "headline": headline,
        "figures": fig,
        "labels": {"grade": report.grade, "energy": meas.get("label"),
                   "basis": "SIMULATED" if simulated else report.grade},
        "verify": {"url": url, "qr_text": url,
                   "how": "Run the standalone verifier on the receipt and its .sigstore.json "
                          "bundle, then compare the sha256."},
        "limits": list(LIMITS),
        "integrity": {"canonicalization": "JCS RFC 8785", "sha256": None},
    }
    cert["integrity"]["sha256"] = canonical_sha256(cert)
    return cert


def mint(receipt_path: str | Path, register_path: str | Path, *, bundle: str | Path | None = None,
         trusted_path: str | Path | None = None, allow_simulated: bool = False,
         issued_at: str | None = None, verify_url: str = DEFAULT_VERIFY_URL) -> dict:
    """Check the rules, write the certificate into the register, return the certificate.

    Raises MintRefused (and writes nothing) if any rule fails.
    """

    receipt_path = Path(receipt_path)
    r = json.loads(receipt_path.read_text(encoding="utf-8"))
    report = verify_receipt(receipt_path, bundle, trusted=load_trusted(trusted_path))
    bad = check_rules(r, report, allow_simulated=allow_simulated)
    reg = load_register(register_path)
    if problems := verify_register(reg):
        raise MintRefused([{"code": "register", "detail": problems[0]}])
    if not bad:
        sha = canonical_sha256(r)
        keys = _claim_keys(r, sha)
        for e in reg["entries"]:
            hit = next((k for k in keys if k in e["claim_keys"]), None)
            if hit:
                bad.append({"code": "duplicate",
                            "detail": f"{hit} is already certified as {e['serial']}"})
                break
    if bad:
        raise MintRefused(bad)
    n = len(reg["entries"]) + 1
    serial = f"{SERIAL_PREFIX}{n:04d}"
    cert = _build(r, report, serial=serial, issued_at=issued_at or _now(), verify_url=verify_url)
    prev = reg["entries"][-1]["integrity"]["sha256"] if reg["entries"] else None
    entry = {"serial": serial, "minted_at": cert["issued_at"],
             "receipt_sha256": cert["receipt"]["sha256"],
             "claim_keys": _claim_keys(r, cert["receipt"]["sha256"]),
             "grade": report.grade, "label": "MOCK",
             "certificate_sha256": cert["integrity"]["sha256"], "prev_sha256": prev}
    entry["integrity"] = {"sha256": entry_hash(entry)}
    reg["entries"].append(entry)
    _atomic_write(Path(register_path), json.dumps(reg, indent=2) + "\n")
    return cert


def verify_certificate(cert: Mapping, *, receipt_path: str | Path | None = None,
                       bundle: str | Path | None = None, register: Mapping | None = None,
                       trusted_path: str | Path | None = None) -> list[str]:
    """Problems with a certificate (empty list means it checks out)."""

    bad: list[str] = []
    if cert.get("format") != CERT_FORMAT:
        bad.append(f"format is {cert.get('format')!r}")
    if (cert.get("integrity") or {}).get("sha256") != canonical_sha256(cert):
        bad.append("certificate was edited (hash mismatch)")
    wm = cert.get("watermark") or {}
    if cert.get("status") != "MOCK" or wm.get("permanent") is not True \
            or tuple(wm.get("lines", ())) != WATERMARK_LINES:
        bad.append("the MOCK watermark is missing or changed")
    text = json.dumps({k: v for k, v in cert.items() if k != "watermark"}).lower()
    bad += [f"banned wording: {w.strip()}" for w in BANNED if w in text]
    if receipt_path is not None:
        rp = Path(receipt_path)
        r = json.loads(rp.read_text(encoding="utf-8"))
        rep = verify_receipt(rp, bundle, trusted=load_trusted(trusted_path))
        if canonical_sha256(r) != (cert.get("receipt") or {}).get("sha256"):
            bad.append("receipt hash does not match this certificate")
        if rep.grade != (cert.get("receipt") or {}).get("grade"):
            bad.append(f"receipt now verifies as {rep.grade}, certificate says "
                       f"{(cert.get('receipt') or {}).get('grade')}")
        try:
            if figures(r) != cert.get("figures"):
                bad.append("figures do not match the receipt")
        except (KeyError, TypeError):
            bad.append("receipt has no usable figures")
    if register is not None:
        bad += verify_register(register)
        e = lookup(register, str(cert.get("serial")))
        if e is None:
            bad.append(f"serial {cert.get('serial')} is not in the register")
        elif e.get("certificate_sha256") != (cert.get("integrity") or {}).get("sha256"):
            bad.append("register holds a different certificate under this serial")
    return bad
