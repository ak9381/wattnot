"""Block g6-think: an adaptive thinking budget per tier for Glimmer, picked by measured joules.

    python -m wattnot.longrun.thinking_budget --fake --out /tmp/think-dry          # CPU, MOCK
    python -m wattnot.longrun.thinking_budget --out runs/g6 --model-config muse-glimmer-30b

Mechanism (s1-style budget forcing; prior art: s1, TALE, L1, ThinkPrune, so we do not claim it):
  Phase A  the model thinks with max_tokens = B and stop = [think_close].
  Phase B  if A ran out (finish=length) we close the thought ourselves (forced close) and ask
           for the answer with an answer cap. Joules cover both phases.
What is ours: the budget per difficulty tier is chosen by measured GPU joules per correct answer,
behind a pass-rate gate, and written to thinking_policy.json for the receipt.

Fails, never silently passed: the answer phase hitting its cap (cut off), a missing or wrong
answer, and an item that passed at the 1x cell but fails at this one (regressed).

Gate per tier: among cells with pass rate >= the 1x cell - 1 pt and no regressed items, keep the
lowest J per correct answer. Nothing better -> the tier keeps 1x ("no change").

Routing a new prompt to a tier: the workload label if present, else Llama 3.2 1B (Meta) as a
cheap classifier; when unsure, round up a tier. Llama is the classifier only, not the judge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from wattnot.proof.client import Reply, strip_reasoning

from . import think_stream
from .grade import grade_one
from .tasks import TIERS, Task, build

GRID = {"easy": (0.25, 0.5, 1.0), "medium": (0.5, 1.0, 1.5), "hard": (0.5, 1.0, 2.0),
        "complex": (1.0, 1.5)}
MAX_DROP_PTS = 1.0
MAX_REGRESSED = 0
ANSWER_CAP = 512
CONTEXT = 8192
THINK_OPEN = os.environ.get("WATTNOT_THINK_OPEN", "<think>")
THINK_CLOSE = os.environ.get("WATTNOT_THINK_CLOSE", "</think>")
ROUTER_MODEL = "meta-llama/Llama-3.2-1B-Instruct"
CELL_MIN = {"easy": 2.5, "medium": 3.5, "hard": 5.0, "complex": 5.5}  # ESTIMATE, incl. grading


def n_cells() -> int:
    return sum(len(v) for v in GRID.values())


def planned_minutes() -> float:
    """~45 min: the 11 cells plus one server warm-up (ESTIMATE; a code review put it at 35-45)."""
    return round(sum(CELL_MIN[t] * len(GRID[t]) for t in TIERS) + 2.0, 1)


def budget_tokens(task: Task, x: float, base_scale: float = 8.0) -> int:
    """B for a task: x times the base (the tier's max_tokens at the model's 8x scale), kept
    inside the context with room for the prompt and the answer cap."""
    base = task.max_tokens * base_scale
    return max(16, min(int(base * x), CONTEXT - ANSWER_CAP - 1024))


# ---- the two-phase chat ------------------------------------------------------------------------

class BudgetChat:
    """A chat(messages, max_tokens) -> Reply that thinks within B tokens, then answers.

    Uses vLLM's chat API: phase A with stop=[think_close]; phase B continues the assistant
    message `open + thought + close` (continue_final_message) so no chat template is
    hand-built. `cut[prompt]` records answer phases that hit their cap; `forced[prompt]`
    counts forced closes. Both feed the gate."""

    def __init__(self, url: str, model: str, budget: int, *, answer_cap: int = ANSWER_CAP,
                 timeout_s: float = 300, post=None) -> None:
        self.url, self.model, self.budget, self.answer_cap = url, model, budget, answer_cap
        self.timeout_s, self.post = timeout_s, post or self._post
        self.cut: dict[str, int] = {}
        self.forced: dict[str, int] = {}
        self.context_limit = CONTEXT

    def _post(self, body: dict) -> dict:  # pragma: no cover - needs a server
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:  # noqa: S310
            return json.loads(r.read())

    def __call__(self, messages: list[dict], max_tokens: int) -> Reply:
        key = next((m["content"] for m in messages if m.get("role") == "user"), "")
        t0 = time.perf_counter()
        base = {"model": self.model, "temperature": 0, "seed": 0, "messages": messages}
        try:
            a = self.post({**base, "max_tokens": self.budget, "stop": [THINK_CLOSE],
                           "skip_special_tokens": False})
            ca = a["choices"][0]
            msg = ca["message"]
            thought = (msg.get("reasoning_content") or msg.get("reasoning") or "")
            content = msg.get("content") or ""
            toks = int(a.get("usage", {}).get("completion_tokens", 0))
            finished_thinking = ca.get("finish_reason") == "stop"
            if finished_thinking and content.strip() and THINK_CLOSE not in content[:4]:
                # the model closed its thought and answered inside phase A
                return Reply(time.perf_counter() - t0, toks, strip_reasoning(content), "stop")
            if not finished_thinking:
                self.forced[key] = self.forced.get(key, 0) + 1
            thought = thought or content.replace(THINK_OPEN, "")
            prefill = f"{THINK_OPEN}{thought}{THINK_CLOSE}\n"
            b = self.post({**base, "messages": messages + [{"role": "assistant",
                                                             "content": prefill}],
                           "continue_final_message": True, "add_generation_prompt": False,
                           "max_tokens": self.answer_cap})
            cb = b["choices"][0]
            toks += int(b.get("usage", {}).get("completion_tokens", 0))
            if cb.get("finish_reason") == "length":
                self.cut[key] = self.cut.get(key, 0) + 1
            text = strip_reasoning(cb["message"].get("content") or "")
            return Reply(time.perf_counter() - t0, toks, text, cb.get("finish_reason") or "stop")
        except Exception:  # noqa: BLE001
            return Reply(time.perf_counter() - t0, 0, "", "error")


# ---- the fake model (CPU dry run, MOCK) --------------------------------------------------------

def _need(task: Task) -> float:
    """How much thinking (as x of base) this fake item needs to get it right."""
    h = int(hashlib.sha256(task.id.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    lo = {"easy": 0.1, "medium": 0.3, "hard": 0.5, "complex": 0.6}[task.tier]
    return lo + h * {"easy": 0.2, "medium": 0.6, "hard": 1.2, "complex": 0.7}[task.tier]


def fake_cell(tasks: list[Task], x: float) -> list[dict]:
    """MOCK: an item passes if its budget covers what it needs; energy grows with tokens."""
    out = []
    for t in tasks:
        need = _need(t)
        used = min(x, need)
        forced = x < need
        ok = (not forced) or x >= 0.8 * need       # a forced close sometimes still works
        cut = t.tier == "hard" and forced and x < 0.4 * need
        tokens = int(t.max_tokens * 8 * used) + 40
        out.append({"id": t.id, "ok": ok and not cut, "cut": cut, "forced": forced,
                    "tokens": tokens, "joules": 0.9 * tokens + 15})
    return out


# ---- the gate ----------------------------------------------------------------------------------

@dataclass
class Cell:
    tier: str
    x: float
    items: list[dict]   # id, ok, cut, forced, tokens, joules (joules may be per cell only)
    joules: float

    @property
    def n(self) -> int:
        return len(self.items)

    @property
    def passed(self) -> set[str]:
        return {i["id"] for i in self.items if i["ok"] and not i.get("cut")}

    @property
    def pass_pct(self) -> float:
        return 100.0 * len(self.passed) / self.n if self.n else 0.0

    @property
    def j_per_correct(self) -> float | None:
        return self.joules / len(self.passed) if self.passed else None


def gate(cells: list[Cell], *, max_drop_pts: float = MAX_DROP_PTS,
         max_regressed: int = MAX_REGRESSED) -> dict:
    """Per tier: the cheapest J/correct cell that keeps pass rate within max_drop_pts of 1x and
    regresses no item that passed at 1x. Returns the policy dict (one entry per tier)."""
    policy = {}
    for tier in TIERS:
        cs = [c for c in cells if c.tier == tier]
        if not cs:
            continue
        base = next((c for c in cs if c.x == 1.0), None)
        if base is None:
            policy[tier] = {"budget_x": None, "decision": "no 1x cell: not decided"}
            continue
        rows, best = [], base
        for c in cs:
            regressed = sorted(base.passed - c.passed)
            delta = round(c.pass_pct - base.pass_pct, 2)
            ok = (delta >= -max_drop_pts and len(regressed) <= max_regressed
                  and c.j_per_correct is not None)
            rows.append({"budget_x": c.x, "n": c.n, "pass_pct": round(c.pass_pct, 2),
                         "delta_pts": delta, "regressed": len(regressed),
                         "cut_off": sum(bool(i.get("cut")) for i in c.items),
                         "forced_close_rate": round(sum(bool(i.get("forced"))
                                                        for i in c.items) / max(c.n, 1), 3),
                         "J_per_correct": (round(c.j_per_correct, 3)
                                           if c.j_per_correct is not None else None),
                         "gate": "pass" if ok else "fail"})
            if ok and c.j_per_correct < (best.j_per_correct or float("inf")):
                best = c
        saving = (None if best is base or not base.j_per_correct else
                  round(100 * (1 - best.j_per_correct / base.j_per_correct), 1))
        policy[tier] = {"budget_x": best.x, "J_per_correct": round(best.j_per_correct, 3)
                        if best.j_per_correct else None,
                        "pass_pct": round(best.pass_pct, 2),
                        "delta_pts": round(best.pass_pct - base.pass_pct, 2),
                        "saving_pct_vs_1x": saving, "n": best.n,
                        "decision": "no change" if best is base else f"{best.x}x",
                        "cells": rows}
    return policy


# ---- routing a new prompt ----------------------------------------------------------------------

def _heuristic(prompt: str) -> tuple[str, float]:
    p = prompt.lower()
    if "tool" in p or "agent" in p or "sub-agent" in p:
        return "complex", 0.7
    if "def " in p or "python" in p or "function" in p:
        return "hard", 0.7
    if len(p) > 240 or "answer:" in p:
        return "medium", 0.55
    return "easy", 0.6


def llama_classifier(url: str, timeout_s: float = 20):  # pragma: no cover - needs a server
    """Llama 3.2 1B (Meta) says easy/medium/hard/complex with a 0-1 confidence."""
    def classify(prompt: str) -> tuple[str, float]:
        body = {"model": ROUTER_MODEL, "temperature": 0, "max_tokens": 12, "messages": [
            {"role": "system", "content": "Label the task difficulty. Reply as: <tier> <0-1 "
             "confidence>. Tiers: easy (one fact or sum), medium (multi-step math), hard "
             "(write code), complex (needs tools or several steps of an agent)."},
            {"role": "user", "content": prompt[:2000]}]}
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:  # noqa: S310
            words = json.loads(r.read())["choices"][0]["message"]["content"].lower().split()
        tier = next((w for w in words if w in TIERS), "medium")
        conf = next((float(w) for w in words if w.replace(".", "", 1).isdigit()), 0.5)
        return tier, conf
    return classify


def route(prompt: str, policy: dict, *, label: str | None = None, classify=None,
          min_conf: float = 0.6) -> dict:
    """Which thinking budget a prompt gets. Unsure -> one tier up (more thinking, safer)."""
    if label in TIERS:
        tier, conf, how = label, 1.0, "label"
    else:
        try:
            tier, conf = (classify or _heuristic)(prompt)
            how = "llama-3.2-1b" if classify else "heuristic"
        except Exception:  # noqa: BLE001 - the router never blocks a request
            tier, conf, how = _heuristic(prompt) + ("heuristic (router down)",)
        if conf < min_conf and tier != "complex":
            tier, how = TIERS[TIERS.index(tier) + 1], how + ", rounded up"
    p = policy.get(tier, {})
    return {"tier": tier, "budget_x": p.get("budget_x") or 1.0, "confidence": conf, "via": how}


# ---- the block ---------------------------------------------------------------------------------

def run(out: Path, *, fake: bool, per_tier: int = 0, url: str = "", model: str = "",
        nvml=None, handle=None, log=print) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    tasks = build()
    if per_tier:
        tasks = [t for tier in TIERS for t in [x for x in tasks if x.tier == tier][:per_tier]]
    probe = None
    if not fake and think_stream.ENABLED:  # pragma: no cover - GPU host only
        probe = think_stream.probe_mode(think_stream.StreamBudgetChat(url, model, 0)._stream_post,
                                        model)
        log(f"g6-think transport: {probe.mode} (confident={probe.confident}) {probe.note}")
    cells: list[Cell] = []
    for tier in TIERS:
        ts = [t for t in tasks if t.tier == tier]
        for x in GRID[tier]:
            if fake:
                items = fake_cell(ts, x)
                cells.append(Cell(tier, x, items, sum(i["joules"] for i in items)))
            else:  # pragma: no cover - GPU host only
                # Streaming enforcer when the self-probe is confident (survives a hidden close
                # marker); the raw-marker BudgetChat stays as the fallback.
                if probe is not None and probe.confident and think_stream.ENABLED:
                    chat = think_stream.StreamBudgetChat(url, model, 0, mode=probe.mode)
                else:
                    chat = BudgetChat(url, model, 0)
                e0 = nvml.total_energy_mj(handle)
                items = []
                for t in ts:
                    chat.budget = budget_tokens(t, x)
                    chat.answer_cap = 1024 if t.tier == "hard" else ANSWER_CAP
                    g = grade_one(t, chat)
                    cut = chat.cut.get(t.prompt, 0) > 0
                    items.append({"id": t.id, "ok": g.ok and not cut, "cut": cut,
                                  "forced": chat.forced.get(t.prompt, 0) > 0,
                                  "tokens": g.tokens})
                cells.append(Cell(tier, x, items, (nvml.total_energy_mj(handle) - e0) / 1000))
            log(f"g6-think {tier} {x}x: {cells[-1].pass_pct:.1f}% pass, "
                f"{cells[-1].j_per_correct or 0:.2f} J/correct")
    pol = gate(cells)
    transport = ({"mode": "fake-dry-run", "confident": False,
                  "note": "CPU mock: no server to probe"} if fake else probe.as_receipt())
    transport["enforcer"] = ("stream-reasoning-budget" if not fake and probe and probe.confident
                             and think_stream.ENABLED else "raw-marker stop-and-force")
    transport["chars_per_token"] = think_stream.CHARS_PER_TOKEN
    doc = {"format": "wattnot.thinking-policy/1", "label": "MOCK" if fake else "MEASURED",
           "model": model or "muse-glimmer-30b (fake)", "mechanism": "stop-and-force-answer",
           "think_markers": [THINK_OPEN, THINK_CLOSE], "think_transport": transport,
           "transport_modes": list(think_stream.MODES), "grid": GRID,
           "gate": {"max_drop_pts": MAX_DROP_PTS, "max_regressed": MAX_REGRESSED,
                    "cut_off_counts_as_fail": True, "metric": "J per correct answer"},
           "router": {"model": ROUTER_MODEL, "unsure": "round up a tier"},
           "prior_art": ["s1 (budget forcing)", "TALE", "L1", "ThinkPrune"],
           "tiers": pol}
    body = json.dumps(doc, sort_keys=True).encode()
    doc["sha256"] = hashlib.sha256(body).hexdigest()
    (out / "thinking_policy.json").write_text(json.dumps(doc, indent=1))
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--per-tier", type=int, default=0)
    ap.add_argument("--url", default="http://127.0.0.1:8000/v1/chat/completions")
    ap.add_argument("--model-config", default="muse-glimmer-30b")
    a = ap.parse_args(argv)
    if a.fake:
        doc = run(a.out, fake=True, per_tier=a.per_tier)
    else:  # pragma: no cover - GPU host only
        from wattnot.probe import open_nvml
        from .harness import model_config
        n = open_nvml()
        doc = run(a.out, fake=False, per_tier=a.per_tier, url=a.url,
                  model=model_config(a.model_config)["model"], nvml=n, handle=n.device_handle(0))
    for tier, p in doc["tiers"].items():
        print(f"{tier:8} keep {p['budget_x']}x  {p['decision']:10} "
              f"J/correct {p['J_per_correct']}  pass {p['pass_pct']}% ({p['delta_pts']:+} pts)")
    print("wrote", a.out / "thinking_policy.json", f"[{doc['label']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
