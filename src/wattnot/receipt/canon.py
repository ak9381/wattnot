"""JSON Canonicalization Scheme (RFC 8785) and the receipt hash.

Keys are sorted by UTF-16 code units, there is no whitespace, strings use
the minimal JSON escapes, and numbers are written the way ECMAScript's
Number.prototype.toString writes them.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

_ESC = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r",
        "\t": "\\t"}


def _string(s: str) -> str:
    out = []
    for ch in s:
        if ch in _ESC:
            out.append(_ESC[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _number(x: float | int) -> str:
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
    r = repr(abs(x))  # shortest round-trip digits
    mant, _, exp = r.partition("e")
    e = int(exp) if exp else 0
    int_part, _, frac = mant.partition(".")
    digits = (int_part + frac).lstrip("0")
    point = len(int_part) + e  # decimal point position relative to digits start
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


def canonicalize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, Mapping):
        keys = sorted(value, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(f"{_string(k)}:{canonicalize(value[k])}" for k in keys) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonicalize(v) for v in value) + "]"
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    return canonicalize(value).encode("utf-8")


def canonical_sha256(receipt: Mapping) -> str:
    """SHA-256 of the canonical receipt without its `integrity` block."""

    body = {k: v for k, v in receipt.items() if k != "integrity"}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()
