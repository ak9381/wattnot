"""One place for the user settings every WattNot layer reads.

Settings come from, in order (later wins):

1. built-in defaults below
2. a JSON config file: ``$WATTNOT_CONFIG``, else ``./wattnot.json``, else ``~/.wattnot/config.json``
3. environment variables (``WATTNOT_MAX_P99_INCREASE``)

``max_p99_increase`` is a fraction: 0.10 means the slowest 1% of requests (p99) may get at most
10% slower than stock. The GPU sweep, the open-model checks, the router and the cloud tool all
read it, so one number controls the latency gate everywhere. ``--p99-slack`` on the cloud tool
is an alias that overrides it for one run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "max_p99_increase": 0.10,
    # Auto-approve rule shown next to the p99 limit on the setup screen
    # (matches `wattnot approvals policy --max-cap-drop-pct`).
    "auto_approve_max_cap_drop_pct": 58.0,
    # The set savings are measured on: auto | humaneval | gsm8k | mixed | custom (see datasets.py)
    "dataset": "mixed",
    # "watch": read telemetry and show would-be savings and receipts, change nothing.
    # "act": apply approved changes (power caps, serving config). `wattnot init` writes "watch";
    # only `wattnot init --act`, WATTNOT_MODE=act, an edit to the config, or a command's own
    # --act flag (the long-run VM path passes it) turns acting on. Watch is the safe default.
    "mode": "watch",
    # Context saver on the agent path (tool-output dedup + memory compression).
    # saver_min_tokens is the long-conversation threshold: calls shorter than this (estimated
    # tokens) pass through untouched. 500 is the measured sweet spot from r04e (see middleware).
    "context_saver": True,
    "saver_min_tokens": 500,
}


def config_path() -> Path | None:
    env = os.environ.get("WATTNOT_CONFIG")
    if env:
        return Path(env)
    for p in (Path.cwd() / "wattnot.json", Path.home() / ".wattnot" / "config.json"):
        if p.is_file():
            return p
    return None


def load() -> dict[str, Any]:
    out = dict(DEFAULTS)
    p = config_path()
    if p is not None and p.is_file():
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            raise ValueError(f"WattNot config {p} is not valid JSON: {e}") from e
        if isinstance(data, dict):
            out.update(data)
    env = os.environ.get("WATTNOT_MAX_P99_INCREASE")
    if env:
        out["max_p99_increase"] = env
    if os.environ.get("WATTNOT_MODE"):
        out["mode"] = os.environ["WATTNOT_MODE"]
    if out.get("mode") not in ("watch", "act"):
        raise ValueError(f"mode must be 'watch' or 'act' (got {out.get('mode')!r})")
    out["max_p99_increase"] = check_p99(out["max_p99_increase"])
    if os.environ.get("WATTNOT_SAVER"):
        out["context_saver"] = os.environ["WATTNOT_SAVER"]
    if os.environ.get("WATTNOT_SAVER_MIN_TOKENS"):
        out["saver_min_tokens"] = os.environ["WATTNOT_SAVER_MIN_TOKENS"]
    out["context_saver"] = check_on_off(out["context_saver"])
    out["saver_min_tokens"] = check_min_tokens(out["saver_min_tokens"])
    return out


def check_on_off(v: Any) -> bool:
    """Accept true/false, on/off, 1/0, yes/no."""
    if isinstance(v, bool):
        return v
    t = str(v).strip().lower()
    if t in ("1", "true", "on", "yes"):
        return True
    if t in ("0", "false", "off", "no"):
        return False
    raise ValueError(f"context_saver must be on or off (got {v!r})")


def check_min_tokens(v: Any) -> int:
    """The saver's long-conversation threshold: a whole number of tokens, 0 to 1,000,000."""
    if isinstance(v, bool):
        raise ValueError(f"saver_min_tokens must be a number (got {v!r})")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"saver_min_tokens must be a number (got {v!r})") from None
    if f != int(f) or not 0 <= f <= 1_000_000:
        raise ValueError(f"saver_min_tokens must be a whole number from 0 to 1000000 (got {v!r})")
    return int(f)


def watch_only() -> bool:
    return load()["mode"] == "watch"


class WatchOnlyError(RuntimeError):
    pass


def require_act(what: str) -> None:
    """Refuse a change while WattNot is in watch-only mode."""
    if watch_only():
        raise WatchOnlyError(
            f"watch-only mode: not applying {what}. WattNot shows the would-be saving and a "
            "receipt but changes nothing. Turn acting on with `wattnot init --act` "
            "(or set \"mode\": \"act\" in your config).")


def check_p99(v: Any) -> float:
    """Accept 0.10 or "10%"; return a fraction in [0, 1]."""
    if isinstance(v, str) and v.strip().endswith("%"):
        f = float(v.strip()[:-1]) / 100
    else:
        f = float(v)
    if not 0 <= f <= 1:
        raise ValueError(f"max_p99_increase must be between 0 and 1 (got {v!r})")
    return f


def max_p99_increase(override: float | None = None) -> float:
    """The p99 limit as a fraction. ``override`` (e.g. --p99-slack) wins when given."""
    return check_p99(override) if override is not None else load()["max_p99_increase"]


def max_p99_increase_pct(override: float | None = None) -> float:
    return round(max_p99_increase(override) * 100, 6)


def receipt_block(*, p99: float | None = None) -> dict[str, Any]:
    """What every receipt records about the limits it was judged against."""
    f = max_p99_increase(p99)
    return {"max_p99_increase": f, "max_p99_increase_pct": round(f * 100, 6),
            "source": "override" if p99 is not None else _source()}


def _source() -> str:
    if os.environ.get("WATTNOT_MAX_P99_INCREASE"):
        return "env:WATTNOT_MAX_P99_INCREASE"
    p = config_path()
    return f"config:{p.name}" if p is not None and p.is_file() else "default"
