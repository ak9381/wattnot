"""Split a measured window's energy across the requests served in it.

Each request gets a share of the window's Wh in proportion to the tokens it
produced. One very long request should not soak up the whole window, so a
single request is capped at `cap` of the total. Whatever the cap cuts off is
handed to the other requests in proportion to their tokens, and this repeats
until nothing is over the cap. The shares always add back up to the window Wh.

This follows the token-share method Neuralwatt describes for per-request
energy; the README credits it.

If the cap cannot be met (fewer than 1/cap requests), it is raised to 1/n so
the split is equal at worst. Zero-token windows are split evenly.
"""

from __future__ import annotations

from collections.abc import Sequence

from wattnot.meter.base import Window

__all__ = ["attribute", "split_wh"]


def attribute(window: Window, tokens_per_req: Sequence[int], cap: float = 0.5) -> list[float]:
    """Wh per request for one window. See the module docstring."""

    return split_wh(window.wh, tokens_per_req, cap)


def split_wh(total_wh: float, tokens_per_req: Sequence[int], cap: float = 0.5) -> list[float]:
    """Same as `attribute`, but takes the Wh directly."""

    if not 0 < cap <= 1:
        raise ValueError("cap must be in (0, 1]")
    if total_wh < 0:
        raise ValueError("window Wh must be >= 0")
    tokens = [int(t) for t in tokens_per_req]
    if any(t < 0 for t in tokens):
        raise ValueError("token counts must be >= 0")
    n = len(tokens)
    if n == 0:
        return []
    if total_wh == 0:
        return [0.0] * n
    weights = [float(t) for t in tokens] if sum(tokens) > 0 else [1.0] * n
    limit = max(cap, 1.0 / n) * total_wh

    shares = [0.0] * n
    capped: set[int] = set()
    remaining = total_wh
    while True:
        open_idx = [i for i in range(n) if i not in capped]
        weight = sum(weights[i] for i in open_idx)
        if weight == 0:
            # Only zero-token requests left uncapped: spread the rest evenly.
            for i in open_idx:
                shares[i] = remaining / len(open_idx)
            break
        over = [i for i in open_idx if remaining * weights[i] / weight > limit + 1e-12]
        if not over:
            for i in open_idx:
                shares[i] = remaining * weights[i] / weight
            break
        for i in over:
            shares[i] = limit
            capped.add(i)
            remaining -= limit
    return shares
