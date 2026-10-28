#!/usr/bin/env python3
"""wattnot_verify.py: check a WattNot receipt with nothing but Python 3.

Written for Python 3.9 or newer, tested on 3.13.

One file, standard library only. No install, no network, no WattNot code.

    python wattnot_verify.py receipt.json            # bundle: receipt.sigstore.json beside it
    python wattnot_verify.py receipt.json --bundle other.sigstore.json --json
    python wattnot_verify.py receipt.json --trust trusted-keys.json

It runs the same steps as `wattnot verify` and prints the same grade:

  schema     the receipt has the fields of spec wattnot.receipt/1 (a structural check;
             the full JSON Schema check lives in `wattnot verify`)
  sha256     JCS (RFC 8785) of the receipt minus `integrity` hashes to integrity.sha256
  signature  the bundle's Ed25519 signature covers that hash (checked here in pure Python)
  rekor      a transparency-log entry exists (offline signatures have none)
  joules     if the receipt carries a `joules` block, it is re-derived and must match
  grade      VALID / ESTIMATE / SIMULATED / NOISE / UNVERIFIABLE, always re-derived,
             never copied from the receipt

Exit code: 0 for any grade but UNVERIFIABLE, 1 for UNVERIFIABLE, 2 for bad usage.
This is an open draft (spec v0.1), not a standard.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

SPEC = "wattnot.receipt/1"
OFFLINE_MEDIA_TYPE = "application/vnd.wattnot.offline-signature.v1+json"
LABELS = ("MEASURED", "ESTIMATE", "SIMULATED")
GRADES = ("VALID", "ESTIMATE", "SIMULATED", "UNVERIFIABLE", "NOISE")
REQUIRED = ("spec", "receipt_id", "created_at", "run_id", "subject", "baseline", "candidate",
            "result", "measurement", "planner_trace", "grade", "integrity")
ARM_REQUIRED = ("params", "reps", "wh_per_req", "p99_ms", "quality")

# ---- JCS (RFC 8785) -------------------------------------------------------------------------

_ESC = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f",
        "\r": "\\r"}


def _string(s):
    out = []
    for ch in s:
        if ch in _ESC:
            out.append(_ESC[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _number(x):
    if isinstance(x, bool):
        raise TypeError("bool is not a number")
    if isinstance(x, int):
        if abs(x) >= 2 ** 53:
            x = float(x)
        else:
            return str(x)
    if not math.isfinite(x):
        raise ValueError("NaN and Infinity are not allowed in JCS")
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    r = repr(abs(x))
    mant, _, exp = r.partition("e")
    e = int(exp) if exp else 0
    int_part, _, frac = mant.partition(".")
    digits = (int_part + frac).lstrip("0")
    point = len(int_part) + e
    lead = len(int_part + frac) - len((int_part + frac).lstrip("0"))
    point -= lead
    digits = digits.rstrip("0") or "0"
    k, n = len(digits), point
    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digits
    else:
        ee = n - 1
        m = digits[0] + ("." + digits[1:] if k > 1 else "")
        s = f"{m}e{'+' if ee >= 0 else '-'}{abs(ee)}"
    return sign + s


def canonicalize(v):
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, (int, float)):
        return _number(v)
    if isinstance(v, dict):
        keys = sorted(v, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(_string(k) + ":" + canonicalize(v[k]) for k in keys) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(canonicalize(x) for x in v) + "]"
    raise TypeError(f"cannot canonicalize {type(v).__name__}")


def receipt_sha256(receipt):
    body = {k: v for k, v in receipt.items() if k != "integrity"}
    return hashlib.sha256(canonicalize(body).encode("utf-8")).hexdigest()

# ---- Ed25519 verify (RFC 8032, pure Python, verify only) --------------------------------------

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _P)


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s, pt):
    r = (0, 1, 1, 0)
    while s:
        if s & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        s >>= 1
    return r


def _eq(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _decode(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A, R = _decode(public), _decode(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if A is None or R is None or s >= _Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(),
                       "little") % _Q
    return _eq(_mul(s, _G), _add(R, _mul(h, A)))

# ---- joules (same rules as wattnot.receipt.joules) -------------------------------------------

_TOL = 1e-6


def _r(x):
    return None if x is None else round(x, 6)


def _good(j, pass_rate):
    return None if not pass_rate or pass_rate <= 0 else j / pass_rate


def _arm_joules(arm):
    wh = arm["wh_per_req"]
    pass_rate = (arm.get("quality") or {}).get("fixed_pass_rate")
    j = float(wh["mean"]) * 3600
    out = {"j_per_answer": _r(j), "pass_rate": pass_rate,
           "j_per_good_answer": _r(_good(j, pass_rate))}
    rng = wh.get("range")
    if isinstance(rng, dict):
        lo, hi = float(rng["low"]) * 3600, float(rng["high"]) * 3600
        out["j_per_answer_range"] = {"low": _r(lo), "high": _r(hi)}
        out["j_per_good_answer_range"] = {"low": _r(_good(lo, pass_rate)),
                                          "high": _r(_good(hi, pass_rate))}
    return out


def _change(a, b):
    return None if a is None or b is None or a == 0 else round(100 * (b - a) / a, 4)


def derive_joules(receipt):
    base, cand = _arm_joules(receipt["baseline"]), _arm_joules(receipt["candidate"])
    return {"unit": "J", "label": receipt["measurement"]["label"],
            "formula": "j_per_answer = wh_per_req x 3600; "
                       "j_per_good_answer = j_per_answer / pass_rate",
            "baseline": base, "candidate": cand,
            "change_pct": {"j_per_answer": _change(base["j_per_answer"], cand["j_per_answer"]),
                           "j_per_good_answer": _change(base["j_per_good_answer"],
                                                        cand["j_per_good_answer"])}}


def _same(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num) and not isinstance(a, bool) \
            and not isinstance(b, bool):
        return abs(a - b) <= _TOL
    return a == b

# ---- steps -----------------------------------------------------------------------------------


def _schema(receipt):
    if not isinstance(receipt, dict) or receipt.get("spec") != SPEC:
        spec = receipt.get("spec") if isinstance(receipt, dict) else None
        return False, {"error": f"spec is {spec!r}, want {SPEC}"}
    for k in REQUIRED:
        if k not in receipt:
            return False, {"error": f"missing field {k!r}"}
    for side in ("baseline", "candidate"):
        for k in ARM_REQUIRED:
            if not isinstance(receipt[side], dict) or k not in receipt[side]:
                return False, {"error": f"{side} is missing {k!r}"}
    integ = receipt["integrity"]
    sha = integ.get("sha256") if isinstance(integ, dict) else None
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef"
                                                         for c in sha):
        return False, {"error": "integrity.sha256 is not a 64-char hex string"}
    if receipt["grade"] not in GRADES:
        return False, {"error": "unknown grade {!r}".format(receipt["grade"])}
    if receipt["measurement"].get("label") not in LABELS:
        return False, {"error": "measurement.label must be one of {}".format(", ".join(LABELS))}
    if "prompt" in receipt or "prompts" in receipt or "outputs" in receipt:
        return False, {"error": "receipts must not carry prompt or output text"}
    return True, {"spec": SPEC}


def _signature(receipt, bundle, trusted):
    if not isinstance(bundle, dict):
        return False, {"error": "no bundle given"}
    if bundle.get("mediaType") != OFFLINE_MEDIA_TYPE:
        return False, {"error": "not an offline bundle"}
    got = receipt_sha256(receipt)
    integ = receipt.get("integrity") or {}
    if got != bundle.get("sha256") or got != integ.get("sha256"):
        return False, {"error": "sha256 does not match the receipt"}
    try:
        pub = bytes.fromhex(bundle["public_key"])
        ok = ed25519_verify(pub, bytes.fromhex(got), bytes.fromhex(bundle["signature"]))
    except (KeyError, ValueError, TypeError):
        ok = False
    if not ok:
        return False, {"error": "signature does not verify"}
    signer = "offline:" + hashlib.sha256(pub).hexdigest()[:16]
    if integ.get("signer") not in (None, signer):
        return False, {"error": f"receipt names signer {integ.get('signer')}, "
                                f"bundle is {signer}"}
    if trusted is not None and signer not in trusted:
        return False, {"error": f"signer {signer} is not a trusted key"}
    return True, {"signer": signer}


def _claimed_grade(receipt, rekor):
    res = receipt["result"]
    w = res.get("welch") or {}
    tested = w.get("applicable", True) is not False
    if tested and (not res.get("billable") or w["p_value"] >= w.get("alpha", 0.05)):
        return "NOISE"
    label = receipt["measurement"]["label"]
    if label == "SIMULATED":
        return "SIMULATED"
    if label != "MEASURED" or not rekor or not tested:
        return "ESTIMATE"
    return "VALID"


def verify_document(receipt, bundle=None, trusted=None, path="<receipt>"):
    """Returns the same dict shape as `wattnot verify --json`."""

    steps = []
    ok, d = _schema(receipt)
    steps.append({"name": "schema", "ok": ok, **d})
    schema_ok = ok
    integ = receipt.get("integrity") if isinstance(receipt, dict) else None
    integ = integ if isinstance(integ, dict) else {}
    expected = integ.get("sha256")
    try:
        got = receipt_sha256(receipt)
        steps.append({"name": "sha256", "ok": bool(expected) and got == expected,
                      "expected": expected, "got": got})
    except Exception as e:  # not canonicalizable
        got = None
        steps.append({"name": "sha256", "ok": False, "expected": expected,
                      "error": str(e)[:200]})
    if isinstance(receipt, dict):
        try:
            ok, d = _signature(receipt, bundle, trusted)
        except Exception as e:
            ok, d = False, {"error": str(e)[:200]}
    else:
        ok, d = False, {"error": "receipt is not a JSON object"}
    steps.append({"name": "signature", "ok": ok, **d})
    rekor = bundle.get("rekor") if isinstance(bundle, dict) else None
    log_index = rekor.get("log_index") if isinstance(rekor, dict) else None
    steps.append({"name": "rekor", "ok": log_index is not None,
                  **({"log_index": log_index} if log_index is not None else
                     {"note": "no Rekor entry (offline signature): grade at most ESTIMATE"})})
    derived, derived_ok = None, True
    if schema_ok:
        try:
            derived = derive_joules(receipt)
        except (KeyError, TypeError, ValueError):
            derived = None
        if "joules" in receipt:
            derived_ok = derived is not None and _same(receipt["joules"], derived)
            steps.append({"name": "joules", "ok": derived_ok,
                          **({"matches": True} if derived_ok else
                             {"error": "joules block does not match the receipt's own numbers"})})
    if isinstance(receipt, dict):
        steps.append(labels_step(receipt))
    sha_ok, sig_ok = steps[1]["ok"], steps[2]["ok"]
    if not (schema_ok and sha_ok and sig_ok and derived_ok):
        g = "UNVERIFIABLE"
    else:
        try:
            g = _claimed_grade(receipt, log_index is not None)
        except (KeyError, TypeError):
            g = "UNVERIFIABLE"
    claimed = integ.get("grade") if integ else None
    steps.append({"name": "grade", "ok": g != "UNVERIFIABLE", "grade": g, "claimed": claimed})
    return {"path": path, "grade": g, "claimed_grade": claimed, "steps": steps,
            "joules": derived}


KNOWN_LABELS = {"MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "REPLAY", "ADVICE", "SAMPLE"}


def _labels(obj, path="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "label" and isinstance(v, str):
                yield path, v
            else:
                yield from _labels(v, path + "." + k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _labels(v, "{}[{}]".format(path, i))


def labels_step(receipt):
    """Every label is known; the headline result has no SIMULATED or MOCK number."""
    found = list(_labels(receipt))
    unknown = ["{}={}".format(p, v) for p, v in found if v not in KNOWN_LABELS]
    weak = ["{}={}".format(p, v) for p, v in _labels(receipt.get("result"), "$.result")
            if v in ("SIMULATED", "MOCK")]
    counts = {}
    for _, v in found:
        counts[v] = counts.get(v, 0) + 1
    d = {"counts": counts}
    if weak:
        d["error"] = "headline result uses " + ", ".join(weak[:3])
    elif unknown:
        d["error"] = "unknown label(s): " + ", ".join(unknown[:3])
    return {"name": "labels", "ok": not unknown and not weak, **d}


def verify_chain(receipts):
    """Oldest first: each integrity.prev_sha256 must equal the previous receipt's sha256."""
    ok, links = True, []
    for i in range(1, len(receipts)):
        prev = (receipts[i - 1].get("integrity") or {}).get("sha256")
        got = (receipts[i].get("integrity") or {}).get("prev_sha256")
        good = prev is not None and got == prev
        ok = ok and good
        links.append({"index": i, "ok": good, "prev_sha256": got, "want": prev})
    return {"name": "chain", "ok": ok, "links": links, "n": len(receipts)}


def format_report(r):
    lines = ["wattnot_verify {}".format(r["path"])]
    for s in r["steps"]:
        mark = "ok  " if s["ok"] else "FAIL"
        n = s["name"]
        if n == "sha256":
            info = "expected {}  got {}".format(s.get("expected"), s.get("got", s.get("error")))
        elif n == "signature":
            info = "signer {}".format(s["signer"]) if s["ok"] else s.get("error", "")
        elif n == "rekor":
            info = "log_index {}".format(s["log_index"]) if s["ok"] else s.get("note", "")
            mark = "ok  " if s["ok"] else "none"
        elif n == "joules":
            info = "matches the receipt's numbers" if s["ok"] else s.get("error", "")
        elif n == "labels":
            info = s.get("error") or ", ".join("{} {}".format(k, v)
                                               for k, v in sorted(s["counts"].items()))
        elif n == "grade":
            info = s["grade"] + ("  (receipt claims {})".format(s["claimed"])
                                 if s.get("claimed") and s["claimed"] != s["grade"] else "")
        else:
            info = s.get("spec") or s.get("error", "")
        lines.append(f"  {mark} {n:<9} {info}")
    c = (r.get("joules") or {}).get("candidate")
    if c and c.get("j_per_answer") is not None:
        good = c.get("j_per_good_answer")
        tail = (f"{good:.1f} J per good answer" if good is not None
                else "no good answers (pass rate 0)")
        lines.append(f"  info {'':<9} candidate {c['j_per_answer']:.1f} J per answer, {tail}")
    return "\n".join(lines)


def load_trusted(path=None):
    p = Path(path) if path else Path(__file__).resolve().with_name("trusted-keys.json")
    if not p.exists():
        return None
    return {k["signer"] for k in json.loads(p.read_text())["keys"]}


def verify_path(receipt_path, bundle_path=None, trusted=None):
    receipt_path = Path(receipt_path)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"path": str(receipt_path), "grade": "UNVERIFIABLE", "claimed_grade": None,
                "steps": [{"name": "schema", "ok": False,
                           "error": (f"cannot read JSON: {e}")[:200]}], "joules": None}
    bp = Path(bundle_path) if bundle_path else receipt_path.with_name(
        receipt_path.stem + ".sigstore.json")
    try:
        bundle = json.loads(bp.read_text(encoding="utf-8")) if bp.exists() else None
    except (OSError, ValueError):
        bundle = None
    return verify_document(receipt, bundle, trusted, str(receipt_path))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    bundle = trust = None
    as_json = chain = False
    files = []
    try:
        while args:
            a = args.pop(0)
            if a == "--json":
                as_json = True
            elif a == "--chain":
                chain = True
            elif a == "--bundle":
                bundle = args.pop(0)
            elif a == "--trust":
                trust = args.pop(0)
            elif a in ("-h", "--help"):
                print(__doc__)
                return 0
            elif a.startswith("--"):
                raise IndexError
            else:
                files.append(a)
    except IndexError:
        files = []
    if not files or (bundle and len(files) != 1):
        print("usage: wattnot_verify.py RECEIPT.json [...] [--bundle B] [--trust KEYS] [--json] [--chain]",
              file=sys.stderr)
        return 2
    files = [f for f in files if not f.endswith(".sigstore.json")]
    trusted = load_trusted(trust)
    bad = 0
    for f in files:
        r = verify_path(f, bundle, trusted)
        bad += r["grade"] == "UNVERIFIABLE" or any(s["name"] == "labels" and not s["ok"]
                                                  for s in r["steps"])
        print(json.dumps(r) if as_json else format_report(r))
    if chain:
        docs = []
        for f in files:
            with open(f, encoding="utf-8") as fh:
                docs.append(json.load(fh))
        c = verify_chain(docs)
        bad += not c["ok"]
        print(json.dumps(c) if as_json else "chain {} {} receipts".format(
            "ok  " if c["ok"] else "FAIL", c["n"]))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
