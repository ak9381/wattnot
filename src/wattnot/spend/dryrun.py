"""CPU-only dry run of the spend guard with mocked providers. No network, $0.

python -m wattnot.spend.dryrun --out reports/spend-dryrun
Scenarios: baseten soft+hard caps, baseten 402, baseten 429 storm, modal timed blocks to the
cap with app teardown, modal preemption error, crash+resume keeps the ledger.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .guard import (GPU_SECONDS, TOKEN, Guard, HardStop, Ledger, Price, ProviderError,
                    SoftStop, finish, run_items, run_timed_block)

SPARK = Price(TOKEN, usd_in_per_m=1.25, usd_out_per_m=4.25)          # Baseten list (29 Oct)
B200X8 = Price(GPU_SECONDS, usd_per_s=8 * 6.25 / 3600 + (32 * 0.0473 + 512 * 0.008) / 3600)


class MockBaseten:
    """Big outputs so the cap is reached in a few hundred calls. fail_at: {call_no: status}."""

    def __init__(self, tout=20_000, fail_at=None, always=None):
        self.n, self.tout, self.fail_at, self.always = 0, tout, fail_at or {}, always
        self.billed = 0.0  # what the 'provider' thinks it billed

    def __call__(self, item):
        self.n += 1
        st = self.always or self.fail_at.get(self.n)
        if st:
            raise ProviderError(st, "mock")
        tin, tout = 500, self.tout
        self.billed += SPARK.tokens(tin, tout)
        return {"tokens_in": tin, "tokens_out": tout, "ok": True}


class MockModal:
    def __init__(self, preempt_at_s=None):
        self.apps: dict[str, str] = {}
        self.clock = 0.0
        self.preempt_at_s = preempt_at_s

    def start(self, name):
        self.apps[name] = "running"

    def step(self, dt):
        if self.preempt_at_s is not None and self.clock + dt > self.preempt_at_s:
            raise ProviderError(503, "preempted")
        self.clock += dt
        return {"seconds": dt, "done": False}

    def stop_all(self) -> list[str]:
        for k in self.apps:
            self.apps[k] = "stopped"
        return [k for k, v in self.apps.items() if v != "stopped"]


def items(n, prefix="tier"):
    return [{"id": f"{prefix}-{i:04d}"} for i in range(n)]


def worst_baseten(_it):  # worst case per call: 2k in + 32k out
    return SPARK.tokens(2_000, 32_000)


def sc_baseten_caps(out: Path) -> dict:
    g = Guard("baseten", SPARK, Ledger(out / "baseten-caps.ledger.jsonl"))
    host = MockBaseten()
    r = run_items(g, items(5000), host, worst_baseten, out / "baseten-caps", out / "copy-baseten-caps")
    r["provider_billed_mock"] = round(host.billed, 4)
    return r


def sc_baseten_402(out: Path) -> dict:
    g = Guard("baseten", SPARK, Ledger(out / "baseten-402.ledger.jsonl"))
    return run_items(g, items(500), MockBaseten(fail_at={40: 402}), worst_baseten, out / "baseten-402")


def sc_baseten_429(out: Path) -> dict:
    g = Guard("baseten", SPARK, Ledger(out / "baseten-429.ledger.jsonl"), sleep=lambda s: None)
    return run_items(g, items(500), MockBaseten(always=429), worst_baseten, out / "baseten-429")


def _modal_run(out: Path, tag: str, preempt_at_s=None) -> dict:
    g = Guard("modal", B200X8, Ledger(out / f"modal-{tag}.ledger.jsonl"))
    m = MockModal(preempt_at_s)
    plan = [("load", 12 * 60), ("k8", 22 * 60), ("k6", 25 * 60), ("k4", 25 * 60),
            ("k8-repeat", 30 * 60), ("glimmer-h100-extra", 60 * 60)]   # deliberately > $98
    done, level, why = [], "done", "all blocks finished"
    m.start("wattnot-spark-topk")
    try:
        for name, secs in plan:
            done.append(run_timed_block(g, name, secs, m.step))
    except SoftStop as e:
        level, why = "soft", str(e)
    except HardStop as e:
        level, why = "hard", str(e)
    # teardown cost is real money too: settle 120 s of the container winding down
    rid = g.reserve(0.0, "teardown")
    g.settle(rid, B200X8.seconds(min(120, max(0.0, g.headroom() / B200X8.usd_per_s))))
    r = finish(g, level, why, done, out / f"modal-{tag}", out / f"copy-modal-{tag}", m.stop_all)
    r["apps_after"] = m.apps
    r["blocks"] = [d["block"] for d in done]
    return r


def sc_resume(out: Path) -> dict:
    led = out / "baseten-resume.ledger.jsonl"
    g1 = Guard("baseten", SPARK, Ledger(led))
    host = MockBaseten()
    r1 = run_items(g1, items(60), host, worst_baseten, out / "resume-1")
    # simulate a crash mid-call: reservation written, never settled
    g1.reserve(worst_baseten(None), "crashed-call")
    g2 = Guard("baseten", SPARK, Ledger(led))          # fresh process, same ledger
    before = g2.committed()
    r2 = run_items(g2, items(5000), host, worst_baseten, out / "resume-2",
                   done_ids={f"tier-{i:04d}" for i in range(60)})
    return {"first_run_committed": r1["ledger_committed_usd"], "after_crash_committed": before,
            "second_run": r2}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/spend-dryrun")
    a = ap.parse_args(argv)
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    res = {"baseten_caps": sc_baseten_caps(out), "baseten_402": sc_baseten_402(out),
           "baseten_429": sc_baseten_429(out), "modal_caps": _modal_run(out, "caps"),
           "modal_preempt": _modal_run(out, "preempt", preempt_at_s=40 * 60),
           "resume": sc_resume(out)}
    (out / "summary.json").write_text(json.dumps(res, indent=1, default=str))
    ok = (res["baseten_caps"]["stop_level"] == "soft"
          and res["baseten_caps"]["ledger_committed_usd"] <= 98
          and res["baseten_402"]["stop_level"] == "hard"
          and res["baseten_429"]["stop_level"] == "soft"
          and res["modal_caps"]["ledger_committed_usd"] <= 98
          and res["modal_caps"]["stop_level"] in ("soft", "hard")
          and not res["modal_caps"]["orphans_after_teardown"]
          and res["modal_preempt"]["stop_level"] == "soft"
          and not res["modal_preempt"]["orphans_after_teardown"]
          and res["resume"]["after_crash_committed"] > res["resume"]["first_run_committed"]
          and res["resume"]["second_run"]["ledger_committed_usd"] <= 98)
    for k, v in res.items():
        v2 = v.get("second_run", v)
        print(f"{k:15s} stop={v2.get('stop_level')!s:5s} items={v2.get('items_done')!s:5s} "
              f"ledger=${v2.get('ledger_committed_usd')} why={v2.get('why')}")
    print("DRYRUN", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
