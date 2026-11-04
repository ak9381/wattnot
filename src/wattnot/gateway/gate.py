"""Per-model saver gate: a saver stays on for a model only while it is no worse than stock there.

Two savers can cost more than they save on some models:
* routing: the "small" tier is a different body (e.g. reasoning_effort=low). On some served models
  (Muse Glimmer on vLLM, 4 Nov H100 run) low effort makes the model think MORE, so the small tier
  uses more tokens than the baseline. Shadow pairs (same request, baseline tier) measure this.
* the agent guard's nudges and injected ``done`` tool: +~50 prompt tokens per agent call (a
  "repeated N times, call done" note). They only pay back when the guard actually ends a loop. If
  it never does over ``done_window`` agent calls, or a shadow pair loses, the guard stops changing
  requests for that model (token and step budgets still stop runaway runs).

A gate decision is logged as an event. With ``state_path`` set, decisions, pairs and agent-call
counts are saved to disk and reloaded on start, so a restart does not re-run the warm-up. Energy here follows tokens
(J = tokens x J/token), so "no worse on tokens" is the per-request energy test; the NVML trace
remains the MEASURED check for a whole run.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path


# Savers that change the prompt the model reads (extra tools, notes, rewritten history) and
# savers that change how much the model thinks / which tier answers. routing's small tier and the
# thinking cap are one lever on a single served model (both set reasoning_effort), so judged together.
PROMPT_SAVERS = ("agent_waste", "context_saver", "vision", "brief_answers")
EFFORT_SAVERS = ("routing", "thinking_cap")


def blame(applied: list[str], pairs: list[dict]) -> tuple[list[str], str]:
    """Which of the applied savers to switch off for a losing set of pairs.

    * WattNot's prompt longer than stock's: a prompt saver added text (e.g. the done tool), so
      only the prompt savers that grow prompts (agent_waste) are blamed; vision/context_saver only
      shrink, so they are blamed only when nothing else could have grown the prompt.
    * otherwise (same prompt, more output or a different answer): the effort lever is blamed.
    * one saver applied, or no split known: that saver / all of them, as before."""
    if len(applied) <= 1:
        return list(applied), "only saver applied"
    known = [p for p in pairs if p.get("small_in") is not None and p.get("base_in") is not None]
    if known:
        d_in = sum(p["small_in"] for p in known) - sum(p["base_in"] for p in known)
        if d_in > 0:
            grow = [sv for sv in applied if sv == "agent_waste"] or [
                sv for sv in applied if sv in PROMPT_SAVERS]
            if grow:
                return grow, f"prompt +{d_in} tokens vs stock over {len(known)} pairs"
        eff = [sv for sv in applied if sv in EFFORT_SAVERS]
        if eff:
            return eff, f"prompt not longer (diff {d_in:+d}); output or answer differs"
    return list(applied), "no prompt/output split logged"


class SaverGate:
    def __init__(self, min_pairs: int = 3, min_agreement: float = 0.8, done_window: int = 10,
                 tolerance: float = 1.0, state_path: str | Path | None = None, noop_pairs: int = 2):
        self.min_pairs, self.min_agree, self.done_window, self.tol = (
            int(min_pairs), float(min_agreement), int(done_window), float(tolerance))
        self.off: dict[tuple[str, str], dict] = {}
        self.pairs: dict[str, list[dict]] = {}
        self.done: dict[str, dict] = {}
        self.events: list[dict] = []
        self.tries: dict[str, int] = {}     # shadow pairs seen per mix, repeats included
        self.noop: dict[str, dict] = {}      # saver mixes proven to leave the request unchanged
        self.noop_pairs = int(noop_pairs)
        self._lock = threading.Lock()
        self.state_path = Path(state_path) if state_path else None
        self._load()

    # ---- persistence -------------------------------------------------------------------
    def _load(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        try:
            d = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return
        for e in d.get("off") or []:
            self.off[(e["saver"], e["model"])] = e
        self.events = list(d.get("events") or [])
        self.pairs = {k: list(v) for k, v in (d.get("pairs") or {}).items()}
        self.done = {k: dict(v) for k, v in (d.get("done") or {}).items()}
        self.noop = {k: dict(v) for k, v in (d.get("noop") or {}).items()}
        self.tries = {k: int(v) for k, v in (d.get("tries") or {}).items()}

    def _save(self) -> None:
        if not self.state_path:
            return
        d = {"off": list(self.off.values()), "events": self.events, "pairs": self.pairs,
             "done": self.done, "noop": self.noop, "tries": self.tries, "saved": round(time.time(), 3)}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(self.state_path)   # atomic: a crash never leaves half a file

    def reset(self) -> None:
        """Forget every decision (new model or a fresh test)."""
        with self._lock:
            self.off, self.pairs, self.done, self.events, self.noop, self.tries = {}, {}, {}, [], {}, {}
            self._save()

    def mark_noop(self, savers: list[str], model: str | None, why: str) -> None:
        """Savers that cannot change this model's request (e.g. both tiers are the same model and
        the served model ignores reasoning_effort): no shadow calls, no warm-up, request unchanged."""
        m = str(model)
        with self._lock:
            for sv in savers:
                if (sv, m) in self.off:
                    continue
                e = {"ts": round(time.time(), 3), "event": "saver_noop", "saver": sv, "model": m, "why": why}
                self.off[(sv, m)] = e
                self.events.append(e)
            self._save()

    def allow(self, saver: str, model: str | None) -> bool:
        return (saver, str(model)) not in self.off

    def _switch_off(self, saver: str, model: str, why: str, **ev) -> None:
        if (saver, model) in self.off:
            return
        e = {"ts": round(time.time(), 3), "event": "saver_off", "saver": saver, "model": model,
             "why": why, **ev}
        self.off[(saver, model)] = e
        self.events.append(e)

    def is_noop(self, model: str | None, applied: list[str] | None) -> bool:
        return self._key(model, applied) in self.noop

    @staticmethod
    def _key(model, applied) -> str:
        return f"{model}|{'+'.join(sorted(applied or ['routing']))}"

    def warming(self, model: str | None, applied: list[str] | None = None) -> bool:
        """True until this mix of savers has min_pairs shadow pairs on this model (shadow every call
        till then), so a saver that only starts applying later (after another is switched off) is
        calibrated on its own too."""
        with self._lock:
            k = self._key(model, applied)
            if k in self.noop:
                return False
            # warm-up ends after min_pairs shadow checks even when they repeat one prompt (a
            # workload of one repeated prompt must not be shadowed forever)
            return len(self.pairs.get(k, [])) < self.min_pairs and self.tries.get(k, 0) < self.min_pairs

    def record_pair(self, model: str | None, small_total: int, base_total: int, agree: bool | None,
                    applied: list[str] | None = None, small_in: int | None = None,
                    base_in: int | None = None, prompt_key: str | None = None,
                    small_out: int | None = None, base_out: int | None = None) -> None:
        """One shadow pair: WattNot's request vs the same request at stock. A losing pair switches
        off only the saver(s) most likely responsible (see ``blame``); the next mix of savers is
        then calibrated on its own pairs (``warming``), so a second culprit is still caught."""
        m = str(model)
        with self._lock:
            try:
                self._record_pair(m, small_total, base_total, agree, applied, small_in, base_in,
                                  prompt_key, small_out, base_out)
            finally:
                self._save()

    def _record_pair(self, m, small_total, base_total, agree, applied, small_in, base_in,
                     prompt_key, small_out, base_out) -> None:
        k = self._key(m, applied)
        ps = self.pairs.setdefault(k, [])
        # a pair counts once per distinct prompt: 60 requests made of 10 prompts sent 6 times
        # are 10 samples, not 60, so the same prompt never fills the warm-up on its own
        self.tries[k] = self.tries.get(k, 0) + 1
        dup = prompt_key is not None and any(p.get("prompt") == prompt_key for p in ps)
        if not dup:
            ps.append({"prompt": prompt_key, "small": int(small_total), "base": int(base_total), "agree": None if agree is None else bool(agree),
                       "small_in": None if small_in is None else int(small_in),
                       "base_in": None if base_in is None else int(base_in)})
        # effort-only mix that changed nothing (same prompt, same output length, same answer):
        # after noop_pairs distinct prompts the mix is a no-op on this model, so stop shadowing
        # it and stop applying it (the request goes out byte-identical to stock)
        eff_only = bool(applied) and all(sv in EFFORT_SAVERS for sv in applied)
        if eff_only and not dup and small_out is not None and base_out is not None:
            same = [p for p in ps if p.get("small_in") == p.get("base_in") and p.get("agree") is not False
                    and p.get("small") == p.get("base")]
            if len(same) == len(ps) and len(ps) >= self.noop_pairs:
                self.noop[k] = {"pairs": len(ps), "ts": round(time.time(), 3)}
                for sv in applied:
                    if (sv, m) not in self.off:
                        e = {"ts": round(time.time(), 3), "event": "saver_noop", "saver": sv, "model": m,
                             "why": "no effect on this model: same prompt, same output and same answer as stock "
                                    f"over {len(ps)} distinct prompts (shadow checks stopped)", "pairs": len(ps)}
                        self.off[(sv, m)] = e
                        self.events.append(e)
                return
        if all(not self.allow(sv, m) for sv in (applied or ["routing"])):
            return
        # one pair at 1.5x stock or worse is already a clear loss: stop paying for more calibration
        clear_loss = int(small_total) > 1.5 * int(base_total)
        if len(ps) < self.min_pairs and self.tries[k] < self.min_pairs and not clear_loss:
            return
        recent = ps[-max(self.min_pairs, 10):]
        sm, bs = sum(p["small"] for p in recent), sum(p["base"] for p in recent)
        scored = [p for p in recent if p["agree"] is not None]   # loop breaks: tokens only
        ag = sum(p["agree"] for p in scored) / len(scored) if scored else 1.0
        why = ("WattNot's request used more tokens than stock" if sm > bs * self.tol
               else "answers differ from stock" if ag < self.min_agree else None)
        if why:
            live = [sv for sv in (applied or ["routing"]) if self.allow(sv, m)]
            culprits, detail = blame(live, recent)
            for sv in culprits:
                self._switch_off(sv, m, why, wattnot_tokens=sm, stock_tokens=bs,
                                 agreement=round(ag, 3), pairs=len(recent), blamed=detail,
                                 kept_on=[x for x in live if x not in culprits])

    def record_agent_call(self, model: str | None, done_injected: bool, guard_stopped: bool) -> None:
        if not done_injected and not guard_stopped:
            return
        m = str(model)
        with self._lock:
            d = self.done.setdefault(m, {"calls": 0, "stops": 0})
            d["calls"] += 1
            d["stops"] += int(bool(guard_stopped))
            if d["calls"] >= self.done_window and d["stops"] == 0:
                self._switch_off("agent_waste", m, "agent guard changed requests and never ended a loop",
                                 calls=d["calls"])
            self._save()

    def status(self) -> dict:
        with self._lock:
            return {"off": [dict(v) for v in self.off.values()], "events": list(self.events),
                    "pairs": {k: len(p) for k, p in self.pairs.items()},
                    "noop": dict(self.noop), "persisted": bool(self.state_path),
                    "agent_calls": {m: dict(v) for m, v in self.done.items()},
                    "rule": "a saver stays on for a model only while it is no worse than stock there"}
