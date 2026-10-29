"""Spend guard for paid providers (hosted token APIs and per-second GPU clouds).

Design (all $ are list-price ESTIMATES until reconciled with the provider's own bill):
- Ledger: append-only JSONL on disk (fsync per line) + file lock. Resuming re-reads it, so a
  restart never resets spend. `committed` = sum of recorded spend + open reservations.
- Before every call/block: `reserve(worst_case)`. If committed + worst_case would cross the
  HARD cap (minus a billing-lag margin) -> HardStop before spending. Once committed >= SOFT
  cap, no NEW item may start (the current one finishes) -> SoftStop.
- After: `settle(reservation, actual)` from token counts x price, or GPU-seconds x rate.
- Reconcile: `reconcile(reported_usd)` takes max(own, provider-reported); the provider number
  can only push spend UP, never down (billing lag protection).
- Provider errors: 402 / out-of-credit -> immediate hard stop. 429 -> bounded backoff, then
  soft stop. Any stop runs the same `finish()` path: save results, copy them, write the
  receipt, tear down (e.g. stop cloud apps), verify nothing is left running.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

TOKEN, GPU_SECONDS = "token", "gpu_seconds"


class SoftStop(Exception):
    """Soft cap reached: finish the current item, start nothing new."""


class HardStop(Exception):
    """Hard cap (or provider out-of-credit) reached: stop now, cleanly."""


class ProviderError(Exception):
    def __init__(self, status: int, msg: str = ""):
        super().__init__(f"provider status {status} {msg}".strip())
        self.status = status


@dataclass(frozen=True)
class Price:
    kind: str                       # TOKEN or GPU_SECONDS
    usd_in_per_m: float = 0.0       # token: $ per 1M input tokens
    usd_out_per_m: float = 0.0      # token: $ per 1M output tokens
    usd_per_s: float = 0.0          # gpu_seconds: all-in $/s (GPUs + CPU + memory)

    def tokens(self, tin: int, tout: int) -> float:
        return tin / 1e6 * self.usd_in_per_m + tout / 1e6 * self.usd_out_per_m

    def seconds(self, s: float) -> float:
        return s * self.usd_per_s


class Ledger:
    """Append-only, fsynced JSONL. Safe to re-open after a crash or resume."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    @contextlib.contextmanager
    def _lock(self):
        with open(self.path, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield fh
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def events(self) -> list[dict]:
        st = self.path.stat()
        key = (st.st_size, st.st_mtime_ns)
        if getattr(self, "_cache_key", None) == key:
            return self._cache
        out = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:   # torn last line after a crash: ignore it
                continue
        self._cache_key, self._cache = key, out
        return out

    def append(self, ev: dict) -> None:
        ev = {"t": time.time(), **ev}
        with self._lock() as fh:
            fh.write(json.dumps(ev, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def state(self) -> dict:
        spent, open_res, reported, stop = 0.0, {}, 0.0, None
        for e in self.events():
            k = e.get("kind")
            if k == "reserve":
                open_res[e["rid"]] = e["usd"]
            elif k == "settle":
                open_res.pop(e["rid"], None)
                spent += e["usd"]
            elif k == "reconcile":
                reported = max(reported, e["usd"])
            elif k == "stop":
                stop = e
        # an un-settled reservation from a crashed run counts in full (worst case)
        own = spent + sum(open_res.values())
        return {"spent": round(spent, 6), "open": round(sum(open_res.values()), 6),
                "reported": round(reported, 6), "committed": round(max(own, reported), 6),
                "stopped": stop}


@dataclass
class Guard:
    provider: str
    price: Price
    ledger: Ledger
    hard_usd: float = 98.0
    soft_usd: float = 90.0
    lag_margin_usd: float = 2.0      # billing lag / rounding headroom kept below hard
    max_429: int = 3
    sleep: Callable[[float], None] = time.sleep
    _n: int = field(default=0, init=False)

    # ---- reads
    def committed(self) -> float:
        return self.ledger.state()["committed"]

    def headroom(self) -> float:
        return self.hard_usd - self.lag_margin_usd - self.committed()

    def stopped(self) -> dict | None:
        return self.ledger.state()["stopped"]

    # ---- gate
    def check_new_item(self) -> None:
        st = self.ledger.state()
        if st["stopped"] and st["stopped"].get("level") == "hard":
            raise HardStop(f"{self.provider}: previously hard-stopped ({st['stopped']['why']})")
        if st["committed"] >= self.hard_usd - self.lag_margin_usd:
            raise HardStop(f"{self.provider}: committed ${st['committed']:.4f} at hard cap")
        if st["committed"] >= self.soft_usd:
            raise SoftStop(f"{self.provider}: committed ${st['committed']:.4f} >= soft ${self.soft_usd}")

    def reserve(self, worst_usd: float, item: str = "") -> str:
        if worst_usd < 0:
            raise ValueError("negative reservation")
        if worst_usd > self.headroom():
            raise HardStop(f"{self.provider}: item {item!r} worst case ${worst_usd:.4f} > "
                           f"headroom ${self.headroom():.4f}")
        self._n += 1
        rid = f"{self.provider}-{os.getpid()}-{time.time_ns()}-{self._n}"
        self.ledger.append({"kind": "reserve", "provider": self.provider, "rid": rid,
                            "usd": round(worst_usd, 6), "item": item})
        return rid

    def settle(self, rid: str, usd: float, **meta) -> None:
        self.ledger.append({"kind": "settle", "provider": self.provider, "rid": rid,
                            "usd": round(usd, 6), **meta})

    def reconcile(self, reported_usd: float, source: str) -> None:
        """Provider-reported spend can only raise the ledger, never lower it."""
        self.ledger.append({"kind": "reconcile", "provider": self.provider,
                            "usd": round(reported_usd, 6), "source": source})

    def mark_stop(self, level: str, why: str) -> None:
        self.ledger.append({"kind": "stop", "provider": self.provider, "level": level, "why": why})

    # ---- one guarded call with provider-error handling
    def call(self, fn: Callable[[], dict], worst_usd: float, item: str = "") -> dict:
        """fn() returns {'tokens_in','tokens_out',...} or {'seconds',...}; may raise ProviderError."""
        tries = 0
        while True:
            rid = self.reserve(worst_usd, item)
            try:
                out = fn()
            except ProviderError as e:
                # bill the worst case: a failed call may still have been charged
                self.settle(rid, worst_usd, item=item, error=e.status)
                if e.status == 402:
                    raise HardStop(f"{self.provider}: out of credit (402)") from e
                if e.status == 429 and tries < self.max_429:
                    tries += 1
                    self.sleep(min(2 ** tries, 30))
                    continue
                raise SoftStop(f"{self.provider}: provider error {e.status}") from e
            except BaseException:
                self.settle(rid, worst_usd, item=item, error="exception")
                raise
            usd = (self.price.tokens(out.get("tokens_in", 0), out.get("tokens_out", 0))
                   if self.price.kind == TOKEN else self.price.seconds(out.get("seconds", 0.0)))
            self.settle(rid, usd, item=item, tokens_in=out.get("tokens_in"),
                        tokens_out=out.get("tokens_out"), seconds=out.get("seconds"))
            return out


def cloud_timeout_s(guard: Guard, teardown_s: float = 120.0) -> int:
    """Server-side backstop for per-second clouds: the remote function's own timeout, so the
    cap holds even if the local watchdog/laptop dies. 0 means: do not launch."""
    if guard.price.kind != GPU_SECONDS or guard.price.usd_per_s <= 0:
        raise ValueError("cloud_timeout_s needs a GPU-seconds price")
    return max(0, int(guard.headroom() / guard.price.usd_per_s - teardown_s))


def finish(guard: Guard, level: str, why: str, results: list, out_dir: Path,
           copy_to: Path | None, teardown: Callable[[], list[str]] | None) -> dict:
    """The single graceful-stop path. Returns the receipt."""
    out_dir.mkdir(parents=True, exist_ok=True)
    res_path = out_dir / f"{guard.provider}-results.jsonl"
    res_path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in results))
    left: list[str] = []
    if teardown:
        left = teardown() or []          # returns names still running after stop
    st = guard.ledger.state()
    receipt = {"provider": guard.provider, "stop_level": level, "why": why,
               "items_done": len(results), "ledger_committed_usd": st["committed"],
               "ledger_spent_usd": st["spent"], "provider_reported_usd": st["reported"],
               "soft_usd": guard.soft_usd, "hard_usd": guard.hard_usd,
               "orphans_after_teardown": left, "label": "ESTIMATE until reconciled"}
    (out_dir / f"{guard.provider}-receipt.json").write_text(json.dumps(receipt, indent=1))
    if copy_to:
        copy_to.mkdir(parents=True, exist_ok=True)
        for p in (res_path, out_dir / f"{guard.provider}-receipt.json", guard.ledger.path):
            (copy_to / p.name).write_bytes(p.read_bytes())
    if level != "done":
        guard.mark_stop(level, why)
    return receipt


def run_items(guard: Guard, items: Iterable[dict], call: Callable[[dict], dict],
              worst_usd: Callable[[dict], float], out_dir: Path, copy_to: Path | None = None,
              teardown: Callable[[], list[str]] | None = None,
              done_ids: set[str] | None = None) -> dict:
    """Token-priced loop (hosted API). Resumable via done_ids."""
    results: list[dict] = []
    level, why = "done", "all items finished"
    done_ids = done_ids or set()
    try:
        for it in items:
            if it["id"] in done_ids:
                continue
            guard.check_new_item()
            out = guard.call(lambda: call(it), worst_usd(it), item=it["id"])
            results.append({"id": it["id"], **out})
    except SoftStop as e:
        level, why = "soft", str(e)
    except HardStop as e:
        level, why = "hard", str(e)
    except KeyboardInterrupt:
        level, why = "hard", "interrupted"
    return finish(guard, level, why, results, out_dir, copy_to, teardown)


def run_timed_block(guard: Guard, name: str, planned_s: float, step: Callable[[float], dict],
                    tick_s: float = 30.0, teardown_s: float = 120.0) -> dict:
    """GPU-second block (Modal). Reserves the planned block + teardown up front, then meters
    in ticks; stops early if the hard cap would be crossed. `step(dt)` advances the work by up
    to dt seconds and returns {'seconds': used, 'done': bool, ...}; it may raise ProviderError."""
    guard.check_new_item()
    need = guard.price.seconds(planned_s + teardown_s)
    if need > guard.headroom():
        # shrink to what fits instead of refusing outright, if at least one tick fits
        fit_s = guard.headroom() / guard.price.usd_per_s - teardown_s
        if fit_s < tick_s:
            raise HardStop(f"{guard.provider}: block {name!r} does not fit headroom")
        planned_s = fit_s
    used, last = 0.0, {}
    while used < planned_s:
        dt = min(tick_s, planned_s - used)
        # never start a tick whose cost + teardown would pass the hard cap
        if guard.price.seconds(dt + teardown_s) > guard.headroom() + 1e-6:
            raise HardStop(f"{guard.provider}: block {name!r} reached hard cap at {used:.0f}s")
        last = guard.call(lambda: step(dt), guard.price.seconds(dt), item=f"{name}@{used:.0f}s")
        used += last.get("seconds", dt)
        if last.get("done"):
            break
    return {"block": name, "seconds": used, **{k: v for k, v in last.items() if k != "seconds"}}
