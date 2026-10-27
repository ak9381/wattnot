"""Answer checkers: math with a known number, code with unit tests, short QA with accepted answers.

The first gate (fixed.py) only knew exact, contains and regex. Those are fragile for model
output: "$1,200.00", "1200" and "\\boxed{1200}" are the same answer. This module normalises
the answer first, then compares. No model is in the loop, so the check can't be argued with.

Rules, in short:
* math  - take the last explicit final-answer marker (`#### n`, `\\boxed{n}`, "the answer is n",
          "Answer: n"). With no marker, fall back to the last number in the text, but only when
          the text was not cut off by the token limit. Numbers drop `$`, `,`, `%` and units, and
          read `a/b` and `12.50` as numbers. Equal when within 1e-6.
* code  - take the first ```python block (else the longest block, else the whole text when it
          has a `def`), run it with the task's assert lines in a fresh `python -I` process with
          a 6 s limit and a memory limit. Pass only on exit code 0.
* qa    - lowercase, strip accents, punctuation and the articles a/an/the, then the answer must
          equal an accepted answer, or contain one as whole words in a reply of at most 12 words.
* A reply that stopped because it hit the token limit (`finish_reason == "length"`) fails.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from fractions import Fraction

CODE_TIMEOUT_S = 6.0
CODE_MEM_BYTES = 1 << 30

_NUM = r"-?\$?\s*\d[\d,]*(?:\.\d+)?(?:\s*/\s*\d[\d,]*(?:\.\d+)?)?"
_MARKERS = [
    re.compile(r"####\s*(" + _NUM + ")"),
    re.compile(r"\\boxed\{\s*(?:\\text\{)?\s*(" + _NUM + r")"),
    re.compile(r"(?:final\s+)?answer\s*(?:is|:|=)\s*\**\s*(" + _NUM + ")", re.IGNORECASE),
]
_ANY_NUM = re.compile(_NUM)


def parse_number(text: str) -> Fraction | None:
    t = text.replace("$", "").replace(",", "").replace(" ", "").rstrip(".")
    if not t:
        return None
    try:
        if "/" in t:
            a, b = t.split("/", 1)
            return Fraction(a) / Fraction(b)
        return Fraction(t)
    except (ValueError, ZeroDivisionError):
        return None


def extract_math_answer(output: str, *, truncated: bool = False) -> Fraction | None:
    for rx in _MARKERS:
        hits = rx.findall(output)
        if hits:
            return parse_number(hits[-1])
    if truncated:
        return None
    hits = _ANY_NUM.findall(output)
    return parse_number(hits[-1]) if hits else None


def check_math(output: str, expected: str, *, truncated: bool = False) -> bool:
    got = extract_math_answer(output, truncated=truncated)
    want = parse_number(expected)
    return got is not None and want is not None and abs(got - want) <= Fraction(1, 10**6)


_ARTICLES = {"a", "an", "the"}


def norm_text(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return [w for w in s.split() if w not in _ARTICLES]


def check_qa(output: str, accepted: list[str]) -> bool:
    got = norm_text(output)
    if not got:
        return False
    for a in accepted:
        want = norm_text(a)
        if got == want:
            return True
        if len(got) <= 12 and want and any(got[i:i + len(want)] == want
                                           for i in range(len(got) - len(want) + 1)):
            return True
    return False


_BLOCK = re.compile(r"```(?:python|py|Python)?[ \t]*\n(.*?)```", re.DOTALL)


def extract_code(output: str) -> str | None:
    blocks = _BLOCK.findall(output)
    py = re.findall(r"```(?:python|py|Python)[ \t]*\n(.*?)```", output, re.DOTALL)
    if py:
        return py[0]
    if blocks:
        return max(blocks, key=len)
    if "def " in output:
        return output
    return None


def _limits() -> None:  # pragma: no cover - runs in the child
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (CODE_MEM_BYTES, CODE_MEM_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))


def check_code(output: str, tests: list[str], *, timeout_s: float = CODE_TIMEOUT_S) -> bool:
    code = extract_code(output)
    if not code or not tests:
        return False
    prog = code.rstrip() + "\n\n" + "\n".join(tests) + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run([sys.executable, "-I", "-c", prog], cwd=tmp, capture_output=True,
                               timeout=timeout_s, preexec_fn=_limits, env={"PATH": os.defpath})
        except (subprocess.TimeoutExpired, OSError):
            return False
    return r.returncode == 0


def check_answer(kind: str, output: str, spec: dict, *, truncated: bool = False) -> bool:
    """One check. `spec` is the check dict from the workload file."""

    if truncated:
        return False
    if kind == "math":
        return check_math(output, spec["value"])
    if kind == "qa":
        return check_qa(output, spec.get("aliases") or [spec["value"]])
    if kind == "code":
        return check_code(output, spec["tests"])
    raise ValueError(f"unknown answer kind {kind!r}")
