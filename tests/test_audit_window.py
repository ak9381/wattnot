"""Regression: the meter-audit window must cover the bucket the run ended in.

The usage endpoint returns minute-aligned 1m buckets and treats end_time as the exclusive
upper edge. The start is floored to the minute (inclusive lower edge); the end must therefore
be pushed up to the next minute boundary, or the bucket the run finished in is dropped whenever
the (padded) end lands on a minute boundary -- which whole-second receipt timestamps make common.
"""

from __future__ import annotations

from wattnot.cloud.audit import window_for


def _rcpt(start: str, end: str) -> dict:
    return {"window": {"start": start, "end": end}}


def test_start_is_floored_to_the_minute():
    start, _ = window_for(_rcpt("2026-10-29T10:00:30Z", "2026-10-29T10:05:20Z"), pad_min=0)
    assert start == "2026-10-29T10:00:00Z"


def test_end_on_a_minute_boundary_is_pushed_past_it():
    # 10:05:00 is the start of the [10:05:00, 10:06:00) bucket; an exclusive end_time of
    # 10:05:00 would exclude it, so the whole final minute of calls would be missed.
    _, end = window_for(_rcpt("2026-10-29T10:00:30Z", "2026-10-29T10:05:00Z"), pad_min=0)
    assert end == "2026-10-29T10:06:00Z"


def test_partial_final_minute_is_ceiled():
    _, end = window_for(_rcpt("2026-10-29T10:00:30Z", "2026-10-29T10:05:20Z"), pad_min=0)
    assert end == "2026-10-29T10:06:00Z"


def test_pad_is_applied_before_ceiling():
    # 10:05:20 + 2m = 10:07:20, which ceils to 10:08:00.
    _, end = window_for(_rcpt("2026-10-29T10:00:30Z", "2026-10-29T10:05:20Z"), pad_min=2)
    assert end == "2026-10-29T10:08:00Z"
