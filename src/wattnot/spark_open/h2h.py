"""Head to head: the same prompts on our own Spark Open (llama.cpp, one H100) and on Baseten.

Both legs answer the same fixed-check prompt set, one request at a time, at the same
reasoning effort. What is measured and what is not:

| Number | Local leg | Baseten leg |
|---|---|---|
| Quality (fixed checks) | MEASURED | MEASURED (REPLAY from a recording) |
| Latency, tokens | MEASURED | MEASURED (REPLAY from a recording) |
| Cost per 1k answers | MEASURED seconds x the listed rental rate | MEASURED tokens x list price |
| Energy per answer | MEASURED if GPU and host (RAPL) both read, else ESTIMATE | ESTIMATE (FLOPs) |

The Baseten leg has a hard spend cap (default $0.50, kept in a file so repeated runs add up).
Before every call we price its worst case (prompt + the full max_tokens). If spent + worst case
would go over the cap, the leg stops and says so. The key comes only from BASETEN_API_KEY.

The receipt holds numbers and hashes. It does not hold prompts or answers.

    python -m wattnot.spark_open.h2h --local-url http://127.0.0.1:8090/v1 --baseten replay ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from wattnot.cloud import BASE_URL, MODEL
from wattnot.cloud import energy as cloud_energy
from wattnot.cloud.efforts import max_tokens_for, stop_for
from wattnot.cloud.pricing import Rates, cost_usd
from wattnot.cloud.transport import (
    CloudError,
    MODAL_DEFAULT_MODEL,
    FixtureTransport,
    LiveTransport,
    ModalLiveTransport,
    Request,
    Transport,
)
from wattnot.gates.fixed import item_checks, run_check
from wattnot.receipt.canon import canonical_sha256
from wattnot.runner.workload import WorkloadItem, load_workload
from wattnot.spark_open.bench import GpuPower, HostPower, LocalChat

SPEC = "wattnot.spark-h2h/1"
DEFAULT_CAP_USD = 0.50
RENTAL_INR_PER_H = 447.28  # E2E 1x H100, INR 379.05/h + 18% GST (listed price)
INR_PER_USD = 88.0  # ASSUMED, change it with --inr-per-usd
TARIFF_USD_PER_KWH = 0.12  # assumption, same as receipts
CHARS_PER_TOKEN = 3.0  # worst-case pricing of a prompt
LEDGER_ENV = "WATTNOT_BASETEN_H2H_LEDGER"
MODAL_GPU = "H100"  # Modal dedicated endpoint GPU for the hosted leg
MODAL_GPU_USD_PER_H = 0.001097 * 3600  # modal.com/pricing, H100 SXM5, read 2026-10-28 (ESTIMATE)
PLACEMENTS = ("local", "baseten", "modal")  # every compare runs all three


class BudgetStop(RuntimeError):
    """The next call could take Baseten spend past the cap."""


# ---- Baseten spend cap ---------------------------------------------------------------------


class SpendCap:
    """Persistent USD cap for this harness's Baseten calls (a JSON file outside the repo)."""

    def __init__(self, cap_usd: float = DEFAULT_CAP_USD, path: str | Path | None = None) -> None:
        self.cap_usd = float(cap_usd)
        self.path = Path(
            path
            or os.environ.get(LEDGER_ENV)
            or Path.home() / ".wattnot" / "baseten_h2h_spend.json"
        ).expanduser()
        self.session_usd = 0.0

    def _read(self) -> list[float]:
        try:
            return [float(x) for x in json.loads(self.path.read_text())["usd"]]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    @property
    def spent_usd(self) -> float:
        return sum(self._read())

    def check(self, worst_case_usd: float) -> None:
        if self.spent_usd + worst_case_usd > self.cap_usd:
            raise BudgetStop(
                f"spent ${self.spent_usd:.4f} + worst case ${worst_case_usd:.4f} "
                f"would pass the ${self.cap_usd:.2f} cap"
            )

    def add(self, usd: float) -> None:
        self.session_usd += usd
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"usd": [*self._read(), round(usd, 8)], "cap_usd": self.cap_usd})
        )


def worst_case_usd(rates: Rates, prompt: str, max_tokens: int) -> float:
    return cost_usd(rates, math.ceil(len(prompt) / CHARS_PER_TOKEN) + 24, 0, max_tokens)


# ---- one answer, either backend -------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    item_id: str
    passed: bool
    latency_s: float
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str | None
    cached_tokens: int = 0
    reasoning_tokens: int | None = None
    decode_ms: float | None = None
    usd: float | None = None
    status: int = 200
    text: str = field(default="", repr=False, compare=False)  # never written to the receipt


def check_text(item: WorkloadItem, text: str, *, truncated: bool) -> bool:
    checks = item_checks(item)
    if not checks or truncated or not text.strip():
        return False
    return all(run_check(c, text, truncated=truncated) for c in checks)


class BasetenLeg:
    """Baseten hosted Muse Spark Open through a cloud transport, behind the spend cap."""

    name = "baseten"
    model = MODEL
    send_effort = True
    usd_per_s: float | None = None  # None: price by tokens at the list rate

    def __init__(
        self,
        transport: Transport,
        *,
        effort: str = "low",
        cap: SpendCap | None = None,
        rates: Rates | None = None,
        billed: bool = True,
    ) -> None:
        self.transport, self.effort = transport, effort
        self.cap, self.rates, self.billed = cap, rates or Rates(), billed

    def complete(self, item: WorkloadItem, rep: int = 0) -> Answer:
        mt = max_tokens_for(self.effort, item.max_tokens)
        if self.billed and self.cap is not None:
            self.cap.check(worst_case_usd(self.rates, item.prompt, mt))
        body = {
            "model": MODEL,
            "reasoning_effort": self.effort,
            "max_tokens": mt,
            "temperature": 0,
            "messages": [{"role": "user", "content": item.prompt}],
        }
        stop = stop_for(self.effort)
        if stop:
            body["stop"] = stop
        body["model"] = getattr(self.transport, "model", None) or self.model
        if not self.send_effort:
            body.pop("reasoning_effort", None)
        rep_ = self.transport(Request(f"{item.id}|{self.effort}|{rep}", body))
        data = rep_.body
        if rep_.status != 200:
            return Answer(item.id, False, rep_.latency_s, 0, 0, "error", status=rep_.status)
        u = data.get("usage") or {}
        pt, ct = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        reasoning = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
        ch = (data.get("choices") or [{}])[0]
        text = (ch.get("message") or {}).get("content") or ""
        fin = ch.get("finish_reason")
        usd = (rep_.latency_s * self.usd_per_s if self.usd_per_s is not None
               else cost_usd(self.rates, pt, cached, ct))
        if self.billed and self.cap is not None:
            self.cap.add(usd)
        return Answer(
            item.id,
            check_text(item, text, truncated=fin == "length"),
            rep_.latency_s,
            pt,
            ct,
            fin,
            cached,
            None if reasoning is None else int(reasoning),
            None,
            usd,
            200,
            text,
        )


class ModalLeg(BasetenLeg):
    """Muse Spark Open on a Modal Shared Endpoint: same model, prompts, effort and checks as
    the Baseten leg, priced per token at Modal's list rate (the same $1.25/0.15/4.25).

    `billing="gpu"` is for a Dedicated Endpoint (for example Muse Glimmer 30B): each answer
    is priced at wall seconds x the H100 list rate, an ESTIMATE that leaves out idle and
    cold-start time, which Modal also bills."""

    name = "modal"
    model = MODAL_DEFAULT_MODEL
    send_effort = True
    usd_per_s = None  # Shared Endpoint: tokens x list price, same as Baseten

    def __init__(self, *a, billing: str = "tokens", **kw) -> None:
        super().__init__(*a, **kw)
        self.billing = billing
        if billing == "gpu":  # Dedicated Endpoint: billed GPU seconds
            self.usd_per_s = MODAL_GPU_USD_PER_H / 3600


class LocalLeg:
    """Our llama-server. Power is read around the whole leg."""

    name = "local"

    def __init__(
        self,
        chat: LocalChat,
        *,
        effort: str = "low",
        gpu: GpuPower | None = None,
        host: HostPower | None = None,
        threads: int = 24,
        resident_gb: float = 0.0,
        warmup: bool = True,
    ) -> None:
        self.chat, self.effort = chat, effort
        self.gpu, self.host = gpu or GpuPower(), host or HostPower()
        self.threads, self.resident_gb, self.warmup = threads, resident_gb, warmup
        self.power: dict = {}

    def run(self, items: Sequence[WorkloadItem]) -> list[Answer]:
        out: list[Answer] = []
        if self.warmup and items:
            self.chat.complete(items[0], max_tokens=8)
        self.gpu.start()
        self.host.start()
        t0 = time.monotonic()
        for it in items:
            mt = max_tokens_for(self.effort, it.max_tokens)
            c = self.chat.complete(it, max_tokens=mt)
            out.append(
                Answer(
                    it.id,
                    check_text(it, c.text, truncated=c.truncated),
                    c.latency_s,
                    c.prompt_tokens,
                    c.completion_tokens,
                    c.finish_reason,
                    c.cached_tokens,
                    None,
                    c.decode_ms,
                    None,
                    c.status,
                    c.text,
                )
            )
        wall = time.monotonic() - t0
        g = self.gpu.stop()
        h = self.host.stop(threads=self.threads, resident_gb=self.resident_gb)
        self.power = {"wall_s": wall, "gpu": g, "host": h}
        return out


# ---- numbers per leg ------------------------------------------------------------------------


def _pct(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))] if s else 0.0


def summarize_leg(
    name: str,
    answers: Sequence[Answer],
    *,
    labels: dict,
    power: dict | None = None,
    rental_usd_per_h: float | None = None,
    rates: Rates | None = None,
    tariff: float = TARIFF_USD_PER_KWH,
) -> dict:
    """One leg's row. `labels` carries MEASURED / REPLAY / etc. for the leg's data source."""

    n = len(answers)
    lat = [a.latency_s for a in answers]
    toks = sum(a.completion_tokens for a in answers)
    ptoks = sum(a.prompt_tokens for a in answers)
    ctoks = sum(a.cached_tokens for a in answers)
    row: dict = {
        "backend": name,
        "answers": n,
        "failed_calls": sum(a.status != 200 for a in answers),
        "truncated": sum(a.finish_reason == "length" for a in answers),
        "quality": {
            "pass_pct": round(100 * sum(a.passed for a in answers) / n, 3) if n else 0.0,
            "passed_items": sum(a.passed for a in answers),
            "label": labels["quality"],
            "gate": "fixed answer checks; truncated or empty counts as a fail",
        },
        "latency_s": {
            "p50": round(statistics.median(lat), 4) if lat else None,
            "p99": round(_pct(lat, 0.99), 4),
            "label": labels["latency"],
        },
        "tokens": {"prompt": ptoks, "cached": ctoks, "completion": toks, "label": labels["tokens"]},
    }
    decode = [a.decode_ms for a in answers]
    if answers and all(d is not None for d in decode) and sum(decode) > 0:
        row["tok_s"] = {
            "value": round(toks / (sum(decode) / 1000), 3),
            "label": labels["latency"],
            "how": "decoded tokens over llama-server decode time",
        }
    else:
        wall = sum(lat)
        row["tok_s"] = {
            "value": round(toks / wall, 3) if wall else None,
            "label": labels["latency"],
            "how": "completion tokens over request wall time (includes network and thinking)",
        }
    if name == "local":
        secs = (power or {}).get("wall_s") or sum(lat)
        usd_h = rental_usd_per_h or 0.0
        row["cost_per_1k_answers"] = {
            "usd": round(secs / n * 1000 / 3600 * usd_h, 4) if n else None,
            "label": labels["cost"],
            "how": f"measured seconds per answer x listed rental ${usd_h:.3f}/h, one at a time",
            "rate_usd_per_h": round(usd_h, 4),
        }
        g, h = (power or {}).get("gpu") or {}, (power or {}).get("host") or {}
        gj, hj = g.get("joules"), h.get("joules")
        if gj is not None and h.get("label") == "MEASURED":
            j, lab, bound = gj + hj, "MEASURED", "GPU+host"
        elif gj is not None:
            j, lab, bound = gj + (hj or 0.0), "ESTIMATE", "GPU measured, host ESTIMATE"
        else:
            j, lab, bound = None, "unavailable", "no GPU power reading on this box"
        row["energy_per_answer"] = {
            "wh": None if j is None or not n else round(j / n / 3600, 6),
            "label": lab,
            "boundary": bound,
            "gpu_label": g.get("label"),
            "host_label": h.get("label"),
            "host_method": h.get("method"),
            "wh_per_1k_answers": None if j is None or not n else round(j / n / 3600 * 1000, 3),
        }
    else:
        if name == "modal" and labels.get("billing") == "gpu":  # wall time x H100 power band
            wall = sum(lat)
            lo, hi = wall * 350 / 3600, wall * 700 / 3600
        else:
            lo, hi = cloud_energy.wh_range(ptoks, ctoks, toks)
        row["cost_per_1k_answers"] = {
            "usd": round(sum(a.usd or 0.0 for a in answers) / n * 1000, 4) if n else None,
            "label": labels["cost"],
            "how": (
                f"wall seconds x Modal {MODAL_GPU} list rate ${MODAL_GPU_USD_PER_H:.2f}/h "
                "(dedicated endpoint; idle and cold start not counted)"
            ) if labels.get("billing") == "gpu" else (
                "MEASURED token usage x list price "
                f"(${(rates or Rates()).input_per_m}/{(rates or Rates()).cached_per_m}/"
                f"{(rates or Rates()).output_per_m} per 1M in/cached/out); "
                "not checked against an invoice"
            ),
        }
        row["energy_per_answer"] = {
            "wh": None if not n else round((lo + hi) / 2 / n, 6),
            "wh_range": None if not n else [round(lo / n, 6), round(hi / n, 6)],
            "label": "ESTIMATE",
            "boundary": f"FLOP model of H100-class GPUs; not measured on {name.capitalize()}'s GPUs",
            "wh_per_1k_answers": None if not n else round((lo + hi) / 2 / n * 1000, 3),
        }
    e = row["energy_per_answer"]
    if e.get("wh") is not None:
        e["usd_at_tariff_per_1k"] = round(e["wh_per_1k_answers"] / 1000 * tariff, 5)
    return row


def compare(local: dict, hosted: dict, *, tie_pts: float = 1.0) -> dict:
    """Who wins each axis. The router, not this, decides where a workload goes."""

    def lower(a, b, tol=0.03):
        if a is None or b is None:
            return "n/a"
        if abs(a - b) <= tol * max(abs(a), abs(b)):
            return "tie"
        return "local" if a < b else "baseten"

    lq, hq = local["quality"]["pass_pct"], hosted["quality"]["pass_pct"]
    quality = "tie" if abs(lq - hq) <= tie_pts else ("local" if lq > hq else "baseten")
    lj, hj = local["energy_per_answer"]["wh"], hosted["energy_per_answer"]["wh"]
    return {
        "quality": quality,
        "quality_diff_pts": round(lq - hq, 3),
        "cost_per_1k": lower(
            local["cost_per_1k_answers"]["usd"], hosted["cost_per_1k_answers"]["usd"]
        ),
        "latency_p50": lower(local["latency_s"]["p50"], hosted["latency_s"]["p50"]),
        "energy_per_answer": lower(lj, hj)
        if local["energy_per_answer"]["label"] != "unavailable"
        else "n/a",
        "energy_note": (
            "local is " + local["energy_per_answer"]["label"] + ", Baseten is ESTIMATE; "
            "a MEASURED vs ESTIMATE gap is a rough guide only"
        ),
    }


def build_receipt(
    workload: Path,
    items: Sequence[WorkloadItem],
    legs: dict,
    answers: dict,
    *,
    effort: str,
    notes: Sequence[str],
    prev_sha256: str | None = None,
    created_at: str | None = None,
    spend: dict | None = None,
    placements_rows: list | None = None,
) -> dict:
    per_item = [
        {
            "id": it.id,
            **{
                b: next(
                    (
                        {
                            "passed": a.passed,
                            "latency_s": round(a.latency_s, 4),
                            "completion_tokens": a.completion_tokens,
                            "answer_sha256": hashlib.sha256(a.text.encode()).hexdigest()[:16],
                        }
                        for a in answers.get(b, [])
                        if a.item_id == it.id
                    ),
                    None,
                )
                for b in legs
            },
        }
        for it in items
    ]
    r = {
        "spec": SPEC,
        "created_at": created_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "workload": {
            "name": workload.stem,
            "items": len(items),
            "effort": effort,
            "file_sha256": hashlib.sha256(workload.read_bytes()).hexdigest(),
        },
        "legs": legs,
        "comparison": compare(legs["local"], legs["baseten"])
        if "local" in legs and "baseten" in legs
        else None,
        "items": per_item,
        "notes": list(notes),
        "baseten_spend": spend,
        "routing": None,
        "placements": placements_rows,
    }
    r["integrity"] = {
        "canonicalization": "JCS RFC 8785",
        "sha256": canonical_sha256(r),
        "prev_sha256": prev_sha256,
    }
    return r


# ---- CLI -------------------------------------------------------------------------------------


def open_baseten(mode: str, *, fixture: Path | None) -> tuple[Transport | None, str]:
    if mode == "off":
        return None, "off"
    if mode == "replay":
        return FixtureTransport.load(fixture), "replay"
    return LiveTransport(base_url=BASE_URL), "live"  # reads BASETEN_API_KEY, raises if unset


def run_h2h(
    wl: Path,
    items: Sequence[WorkloadItem],
    *,
    effort: str = "low",
    local_url: str | None = None,
    tiny: bool = False,
    baseten: str = "replay",
    fixture: Path | None = None,
    cap_usd: float = DEFAULT_CAP_USD,
    cap_path: str | Path | None = None,
    rental_inr_per_h: float = RENTAL_INR_PER_H,
    inr_per_usd: float = INR_PER_USD,
    threads: int = 24,
    resident_gb: float = 0.0,
    baseten_transport: Transport | None = None,
    local_chat: LocalChat | None = None,
    modal: str = "off",
    modal_transport: Transport | None = None,
    modal_cap_usd: float = DEFAULT_CAP_USD,
    modal_billing: str = "tokens",
    local_levers: Sequence[str] = ("adaptive_topk", "tiered_experts_kmeans", "quantization"),
) -> dict:
    """Run both legs and return the receipt (not yet routed). Nothing is written to disk
    except the Baseten spend file when `baseten == "live"`."""

    legs: dict = {}
    answers: dict = {}
    skipped: dict = {}
    if not (local_url or local_chat):
        skipped["local"] = "no local GPU server given (--local-url)"
    if baseten == "off":
        skipped["baseten"] = "Baseten leg switched off"
    if modal == "off":
        skipped["modal"] = "Modal endpoint not configured (MODAL_ENDPOINT_URL + proxy token)"
    if baseten == "replay" and isinstance(baseten_transport or fixture, (FixtureTransport, Path)):
        have = (
            baseten_transport.doc
            if isinstance(baseten_transport, FixtureTransport)
            else json.loads(Path(fixture).read_text())
        ).get("replies", {})
        items = [it for it in items if f"{it.id}|{effort}|0" in have]  # the recording's prompts
    notes: list[str] = [
        "Same prompts, same fixed checks, same reasoning effort on both legs.",
        "Local runs one request at a time; batching on the box would lower its cost per 1k "
        "answers, and that is not measured here.",
        f"GPU rental INR {rental_inr_per_h}/h at an ASSUMED INR {inr_per_usd}/USD.",
    ]
    spend = None
    if local_url or local_chat:
        leg = LocalLeg(
            local_chat or LocalChat(local_url),
            effort=effort,
            threads=threads,
            resident_gb=resident_gb,
        )
        ans = leg.run(items)
        lab = {
            "quality": "MEASURED",
            "latency": "MEASURED",
            "tokens": "MEASURED",
            "cost": "MEASURED seconds x listed rate",
        }
        legs["local"] = summarize_leg(
            "local",
            ans,
            labels=lab,
            power=leg.power,
            rental_usd_per_h=rental_inr_per_h / inr_per_usd,
        )
        legs["local"]["model"] = (
            "tiny GGUF on CPU (plumbing check)" if tiny else "Muse Spark Open 816B (GGUF)"
        )
        answers["local"] = ans
        if tiny:
            notes.append(
                "DRY RUN: the local leg is a small model on a CPU box. Its numbers show the "
                "harness works. They say nothing about Spark Open on an H100."
            )
    if baseten != "off":
        transport = baseten_transport or open_baseten(baseten, fixture=fixture)[0]
        cap = SpendCap(cap_usd, cap_path) if baseten == "live" else None
        bl = BasetenLeg(transport, effort=effort, cap=cap, billed=baseten == "live")
        ans, stopped = [], None
        for it in items:
            try:
                ans.append(bl.complete(it))
            except BudgetStop as e:
                stopped = str(e)
                break
            except (CloudError, KeyError) as e:  # a replay gap or a network error
                stopped = f"{type(e).__name__}: {e}"[:200]
                break
        src = "REPLAY" if baseten == "replay" else "MEASURED"
        lab = {
            "quality": src,
            "latency": src,
            "tokens": src,
            "cost": "REPLAY (recorded tokens x list price)"
            if baseten == "replay"
            else "MEASURED tokens x list price",
        }
        legs["baseten"] = summarize_leg("baseten", ans, labels=lab, rates=Rates())
        legs["baseten"].update(model=MODEL, mode=baseten, stopped=stopped)
        answers["baseten"] = ans
        if baseten == "live":
            spend = {
                "session_usd": round(cap.session_usd, 6),
                "cap_usd": cap.cap_usd,
                "total_usd_in_ledger": round(cap.spent_usd, 6),
                "label": "MEASURED tokens x list price",
            }
        elif stopped:
            notes.append(f"Baseten replay stopped early: {stopped}")
        if stopped and baseten == "live":
            notes.append(f"Baseten leg stopped early: {stopped}")
    if modal != "off":
        mt = modal_transport or (FixtureTransport.load(fixture) if modal == "replay"
                                 else ModalLiveTransport())
        mcap = SpendCap(modal_cap_usd, cap_path and Path(str(cap_path) + ".modal")) \
            if modal == "live" else None
        ml = ModalLeg(mt, effort=effort, cap=mcap, billed=modal == "live",
                      billing=modal_billing)
        ans, stopped = [], None
        for it in items:
            try:
                ans.append(ml.complete(it))
            except BudgetStop as e:
                stopped = str(e)
                break
            except (CloudError, KeyError) as e:
                stopped = f"{type(e).__name__}: {e}"[:200]
                break
        src = "REPLAY" if modal == "replay" else "MEASURED"
        gpu = modal_billing == "gpu"
        lab = {"quality": src, "latency": src, "tokens": src,
               "billing": modal_billing,
               "cost": "ESTIMATE (wall seconds x Modal GPU list rate)" if gpu
               else ("REPLAY (recorded tokens x list price)" if modal == "replay"
                     else "MEASURED tokens x list price")}
        legs["modal"] = summarize_leg("modal", ans, labels=lab, rates=Rates())
        legs["modal"].update(model=getattr(mt, "model", None) or ModalLeg.model, mode=modal,
                             stopped=stopped, billing=modal_billing,
                             gpu=MODAL_GPU if gpu else None)
        answers["modal"] = ans
        if stopped:
            notes.append(f"Modal leg stopped early: {stopped}")
    if "local" in legs:
        legs["local"]["levers"] = list(local_levers)
    if not legs:
        raise ValueError("nothing to run: give a local url and/or baseten replay|live")
    # Only items both legs answered are compared, so one stopped leg cannot skew the other.
    full = [it for it in items if all(any(x.item_id == it.id for x in answers[b]) for b in legs)]
    if len(full) < len(items) and len(legs) > 1:
        notes.append(f"Compared on {len(full)} of {len(items)} items: the ones both legs answered.")
        keep = {it.id for it in full}
        for b in list(legs):
            sub = [x for x in answers[b] if x.item_id in keep]
            power = None
            if b == "local":  # scale the whole-run power window to the compared answers
                tot = sum(x.latency_s for x in answers[b]) or 1.0
                f = sum(x.latency_s for x in sub) / tot
                p = leg.power
                power = {
                    "wall_s": p["wall_s"] * f,
                    "gpu": {
                        **p["gpu"],
                        "joules": None if p["gpu"]["joules"] is None else p["gpu"]["joules"] * f,
                    },
                    "host": {
                        **p["host"],
                        "joules": None if p["host"]["joules"] is None else p["host"]["joules"] * f,
                    },
                }
            legs[b] = summarize_leg(
                b,
                sub,
                labels={
                    "quality": legs[b]["quality"]["label"],
                    "latency": legs[b]["latency_s"]["label"],
                    "tokens": legs[b]["tokens"]["label"],
                    "cost": legs[b]["cost_per_1k_answers"]["label"],
                },
                power=power,
                rental_usd_per_h=rental_inr_per_h / inr_per_usd if b == "local" else None,
                rates=Rates(),
            ) | {k: legs[b][k] for k in ("model", "mode", "stopped") if k in legs[b]}
    return build_receipt(wl, full, legs, answers, effort=effort, notes=notes, spend=spend,
                         placements_rows=placements(legs, skipped))


def placements(legs: dict, skipped: dict) -> list[dict]:
    """One row per placement, always all three, for the comparison graphs. A placement
    that did not run says why instead of showing a number."""

    rows = []
    for p in PLACEMENTS:
        leg = legs.get(p)
        if leg is None:
            rows.append({"id": p, "status": "not_run",
                         "why": skipped.get(p, "not configured for this run")})
            continue
        rows.append({
            "id": p, "status": "ran" if leg["answers"] else "no_answers",
            "model": leg.get("model"), "mode": leg.get("mode"), "levers": leg.get("levers"),
            "pass_pct": {"value": leg["quality"]["pass_pct"], "label": leg["quality"]["label"]},
            "p99_s": {"value": leg["latency_s"]["p99"], "label": leg["latency_s"]["label"]},
            "usd_per_1k": {"value": leg["cost_per_1k_answers"]["usd"],
                           "label": leg["cost_per_1k_answers"]["label"]},
            "wh_per_1k": {"value": leg["energy_per_answer"]["wh_per_1k_answers"],
                          "label": leg["energy_per_answer"]["label"]},
        })
    return rows


def main(argv=None) -> int:  # pragma: no cover - exercised by scripts/spark1gpu_dryrun.sh
    ap = argparse.ArgumentParser(prog="wattnot.spark_open.h2h")
    ap.add_argument("--workload", default="workloads/cloud-effort-v1.jsonl")
    ap.add_argument("--items", type=int, default=15)
    ap.add_argument("--effort", default="low", choices=("low", "medium", "high", "xhigh"))
    ap.add_argument("--local-url", help="a running llama-server, e.g. http://127.0.0.1:8090/v1")
    ap.add_argument(
        "--local-tiny",
        action="store_true",
        help="label local numbers as a tiny CPU model (plumbing check)",
    )
    ap.add_argument("--baseten", choices=("off", "replay", "live"), default="replay")
    ap.add_argument("--baseten-fixture", default="data/cloud/recorded-smoke.json")
    ap.add_argument("--baseten-cap-usd", type=float, default=DEFAULT_CAP_USD)
    ap.add_argument("--modal", choices=("auto", "off", "live"), default="auto",
                    help="auto: live when MODAL_ENDPOINT_URL and a proxy token are set")
    ap.add_argument("--modal-cap-usd", type=float, default=DEFAULT_CAP_USD)
    ap.add_argument("--modal-billing", choices=("tokens", "gpu"), default="tokens",
                    help="tokens: Shared Endpoint (Muse Spark Open); gpu: Dedicated Endpoint")
    ap.add_argument("--rental-inr-per-h", type=float, default=RENTAL_INR_PER_H)
    ap.add_argument("--inr-per-usd", type=float, default=INR_PER_USD)
    ap.add_argument("--threads", type=int, default=24)
    ap.add_argument("--resident-gb", type=float, default=0.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    wl = Path(a.workload)
    items = list(load_workload(wl))[: a.items]
    try:
        r = run_h2h(
            wl,
            items,
            effort=a.effort,
            local_url=a.local_url,
            tiny=a.local_tiny,
            baseten=a.baseten,
            fixture=Path(a.baseten_fixture),
            cap_usd=a.baseten_cap_usd,
            rental_inr_per_h=a.rental_inr_per_h,
            inr_per_usd=a.inr_per_usd,
            threads=a.threads,
            resident_gb=a.resident_gb,
            modal=_modal_mode(a.modal),
            modal_cap_usd=a.modal_cap_usd,
            modal_billing=a.modal_billing,
        )
        from wattnot.spark_open.router import attach

        attach(r)  # the router always picks a placement per workload
    except ValueError as e:
        print(e)
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "h2h.json").write_text(json.dumps(r, indent=2) + "\n")
    print_summary(r)
    return 0


def _modal_mode(flag: str) -> str:
    if flag != "auto":
        return flag
    need = ("MODAL_ENDPOINT_URL", "MODAL_PROXY_TOKEN_ID", "MODAL_PROXY_TOKEN_SECRET")
    return "live" if all(os.environ.get(k, "").strip() for k in need) else "off"


def print_summary(r: dict) -> None:
    for b, row in r["legs"].items():
        print(
            f"{b}: pass {row['quality']['pass_pct']}% ({row['quality']['label']}), "
            f"p50 {row['latency_s']['p50']}s, ${row['cost_per_1k_answers']['usd']}/1k "
            f"({row['cost_per_1k_answers']['label']}), "
            f"{row['energy_per_answer']['wh_per_1k_answers']} Wh/1k "
            f"({row['energy_per_answer']['label']})"
        )
    if r.get("comparison"):
        print("comparison:", {k: v for k, v in r["comparison"].items() if k != "energy_note"})


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
