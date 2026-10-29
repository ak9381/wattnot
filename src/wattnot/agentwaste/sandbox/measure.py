"""Honest before/after accounting for one Code Mode candidate, using the sandbox run.

Vocabulary, kept identical to the rest of the agent-waste layer (and colour-coded the same way
in the card):

* ``MEASURED``   -- read from the recorded trace (the "before"), or produced by actually running
  the sandbox (wall time, CPU seconds, peak RSS, exit status, and whether the net / out-of-dir
  write were truly refused).
* ``TOKENIZER``  -- a token count of the generated program and its final result, using the
  repo's own approximation (``chars // 4``, the same rule ``route/stack.py`` uses). It is a
  *count of bytes we hold*, not a billed figure from any model, so it carries its own label and
  says so -- never dressed up as MEASURED-from-a-model.
* ``ESTIMATE``   -- anything that would need a live model we do not call: the single model round
  trip Code Mode implies, and the model-side latency/energy of emitting the program.
* ``null`` + reason -- never invented. CPU-package energy is read from RAPL
  (``/sys/class/powercap``) when that interface is readable; where it is not (containers, no
  permission), the value is ``null`` and the reason is recorded.
"""

from __future__ import annotations

import os
from pathlib import Path

# RUN2's measured joules-per-generated-token (same source the bench summary pins), used only to
# express the modelled model-side energy as an ESTIMATE; never presented as measured here.
from wattnot.agentwaste.bench.summary import j_per_token

RAPL_ROOT = Path("/sys/class/powercap")
# The repo's token approximation: ~4 characters per token. route/stack.py uses `chars // 4`.
TOKENIZER = "repo approximation: len(text) // 4 (same rule as route/stack.py)"


def count_tokens(text: str) -> int:
    """Approximate tokens the repo's way: four characters per token. A count, not a bill."""
    return len(text) // 4


def _v(value, label: str, **extra) -> dict:
    d = {"value": value, "label": label}
    d.update(extra)
    return d


def rapl_reading() -> tuple[int | None, str]:
    """Sum the readable RAPL package energy counters (microjoules), or (None, reason).

    We only read; we never write a powercap file. Unreadable (no such interface, or no
    permission) is reported honestly, not guessed.
    """
    if not RAPL_ROOT.is_dir():
        return None, f"no RAPL interface at {RAPL_ROOT}"
    total = 0
    read_any = False
    for entry in sorted(RAPL_ROOT.glob("intel-rapl:*")):
        f = entry / "energy_uj"
        if os.access(f, os.R_OK):
            try:
                total += int(f.read_text().strip())
                read_any = True
            except (OSError, ValueError):
                continue
    if not read_any:
        return None, "RAPL present but energy_uj not readable (permission)"
    return total, "RAPL intel-rapl:* energy_uj"


def measure_energy_j(fn):
    """Run ``fn`` and return ``(result, joules_or_None, reason)`` from RAPL deltas when readable."""
    before, reason = rapl_reading()
    result = fn()
    after, _ = rapl_reading()
    if before is None or after is None:
        return result, None, reason
    # RAPL counters wrap; a negative delta means a wrap we will not try to reconstruct.
    delta_uj = after - before
    if delta_uj < 0:
        return result, None, "RAPL counter wrapped during run"
    return result, round(delta_uj / 1_000_000, 4), reason


def before_from_trace(row: dict) -> dict:
    """The 'before' side: the recorded multi-turn trace, straight from the detection row.

    ``row`` is one entry of ``codemode.analyze(...)["rows"]`` -- its ``before`` block already
    carries MEASURED round trips / tokens / wall time and an ESTIMATE energy. We pass those
    through unchanged so the two cards agree.
    """
    b = row["before"]
    return {
        "round_trips": b["round_trips"],
        "total_tokens": b["total_tokens"],
        "wall_s": b["wall_s"],
        "joules": b["joules"],
    }


def after_from_sandbox(sb, program_src: str, result_text: str, energy_j, energy_reason: str) -> dict:
    """The 'after' side: one code execution, measured in the sandbox plus honest token counts."""
    code_tokens = count_tokens(program_src)
    result_tokens = count_tokens(result_text)
    # Model-side energy of emitting the program: ESTIMATE via RUN2's per-token rate on the code
    # tokens (a stand-in rate, never a measurement of the program's own model).
    model_energy_est = round(code_tokens * j_per_token(), 1)
    joules_label = "MEASURED" if energy_j is not None else "PENDING"
    return {
        # One model call would emit the program; we did not call a model, so it is an ESTIMATE.
        "model_round_trips": _v(1, "ESTIMATE"),
        # We actually executed the program exactly once -- that part is measured.
        "code_executions": _v(1, "MEASURED"),
        "code_tokens": _v(code_tokens, "TOKENIZER", note=TOKENIZER),
        "result_tokens": _v(result_tokens, "TOKENIZER", note=TOKENIZER),
        "total_tokens": _v(code_tokens + result_tokens, "TOKENIZER", note=TOKENIZER),
        "sandbox_wall_s": _v(sb.wall_s, "MEASURED"),
        "cpu_s": _v(sb.cpu_s, "MEASURED"),
        "peak_rss_kb": _v(sb.peak_rss_kb, "MEASURED"),
        # Model/network latency of the single turn is not run live here.
        "model_latency_s": _v(None, "ESTIMATE", note="no live model call"),
        "sandbox_energy_j": _v(energy_j, joules_label, note=energy_reason),
        "model_energy_j": _v(model_energy_est, "ESTIMATE",
                             note="RUN2 j/token on code tokens; stand-in rate, not measured"),
    }


def saved(before: dict, after: dict) -> dict:
    """Per-field saving where both sides are real and comparable; else null + a reason label.

    Only round trips and total tokens are compared before/after (the sandbox's wall time and the
    trace's wall time are different clocks, so we do not subtract them -- that would be a lie).
    """
    out: dict = {}
    b_rt = before["round_trips"]["value"]
    a_rt = after["model_round_trips"]["value"]
    out["round_trips"] = _v(b_rt - a_rt if None not in (b_rt, a_rt) else None,
                            "MEASURED-vs-ESTIMATE")
    b_tok = before["total_tokens"]["value"]
    a_tok = after["total_tokens"]["value"]
    out["total_tokens"] = _v(b_tok - a_tok if None not in (b_tok, a_tok) else None,
                             "MEASURED-vs-TOKENIZER")
    return out
