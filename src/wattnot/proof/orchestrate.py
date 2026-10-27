"""RUN2 from the control machine: the whole chain, written as one `wattnot.event/1` stream.

    python -m wattnot.proof.orchestrate --mode live --host IP ...     the real H100 run
    python -m wattnot.proof.orchestrate --mode rehearsal              fake GPU, fake model, MOCK

Steps, in order (each is a step on the dashboard's Architecture map):

    room_scan      SAM 3.1 rack masks (replay of a saved real call, or live), the H100 placed in
                   one rack slot, its power and temperature read live from NVML
    baseline       the 111-prompt answer gate at the stock cap
    quality_gate   the gate must reject a deliberately bad setting and accept stock again
    cap_sweep      Muse Spark proposes a cap (real API call from this machine), the GPU host applies
                   it, a paired stock/cap measurement runs, the gate decides: repeat
    adaptive_topk  adaptive top-k vs normal top-k on a small MoE model (optional, time-boxed)
    keep_rollback  keep the best accepted cap, or roll back to stock
    receipt        build, hash, sign (offline key, no public log) and verify

The GPU host emits `EVT` lines; this machine gives each one its run id, sequence number and time,
validates it against the schema and appends it to `runs/<run_id>/events.jsonl` and to the live file
the dashboard follows. The Meta key never leaves this machine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from wattnot.events import Emitter
from wattnot.events.emitter import new_run_id
from wattnot.proof import roomscan
from wattnot.proof.events import Stream, check_labels, read_raw
from wattnot.proof.planner import BisectPlanner
from wattnot.proof.run import best_of, history_row, write_report
from wattnot.proof.transport import LocalTransport, Transport

DEFAULT_GRID = (600, 500, 400, 350, 300)
MODEL = "meta-llama/Llama-3.1-8B-Instruct"


class Live:
    """Emitter + the live file + the label bookkeeping."""

    def __init__(self, run_id: str, runs_root: Path, live_file: Path | None, *, mock: bool) -> None:
        self.em = Emitter(run_id, root=runs_root)
        self.path = self.em.path
        self.live_file = live_file
        self.mock = mock
        self.problems: list[str] = []
        self.count = 0
        if live_file is not None:
            live_file.parent.mkdir(parents=True, exist_ok=True)

    def sink(self, raw: dict) -> None:
        """Validate and write one event. A bad event is logged and skipped, never fatal: a paid
        run must not stop because one line was malformed."""
        try:
            ev = self.em.emit(
                raw["type"],
                raw.get("data") or {},
                step_id=raw.get("step_id"),
                parent_step_id=raw.get("parent_step_id"),
            )
        except Exception as e:  # noqa: BLE001
            self.problems.append(f"dropped {raw.get('type')}: {type(e).__name__}: {str(e)[:160]}")
            return
        self.count += 1
        self.problems += check_labels(ev)
        if self.live_file is not None:
            with self.live_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
                f.flush()

    def close(self) -> None:
        self.em.close()


def vm_line(live: Live, grabbed: dict) -> Callable[[str], None]:
    """Line handler for a GPU-host command: EVT lines become events, `*_JSON` lines are kept."""

    def on_line(line: str) -> None:
        ev = read_raw(line)
        if ev is not None:
            live.sink(ev)
            return
        for key in ("STEP_JSON", "SNAP_JSON", "APPLY_JSON", "ADAPTIVE_JSON"):
            if line.startswith(key + " "):
                grabbed[key] = json.loads(line[len(key) + 1 :])
                return
        if line.strip():
            print("[vm]", line[:240], flush=True)

    return on_line


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def workload_sha() -> str | None:
    from wattnot.runner.workload import workloads_dir

    p = workloads_dir() / "proof-v1.jsonl"
    return sha_file(p) if p.exists() else None


def drive(a, *, transport: Transport, planner, scan_base: dict | None, out: Path) -> dict:
    mock = a.mode == "rehearsal"
    label = "MOCK" if mock else "MEASURED"
    run_id = new_run_id()
    live = Live(run_id, a.runs_root, a.live_file, mock=mock)
    s = Stream(live.sink, label=label)
    remote_out = a.remote_out
    vm_common = [
        "--out",
        remote_out,
        "--port",
        str(a.port),
        "--model",
        a.model,
        "--repeat",
        str(a.repeat),
        "--concurrency",
        str(a.concurrency),
        "--settle-s",
        str(a.settle_s),
        "--warmup",
        str(a.warmup),
        "--emit",
    ]
    if mock:
        vm_common += ["--fake", "--fake-delay", str(a.fake_delay)]
    grabbed: dict = {}
    t_start = time.time()

    def vm(cmd: str, *extra: str, timeout: float = 7200) -> int:
        grabbed.clear()
        return transport.run([cmd, *vm_common, *extra], vm_line(live, grabbed), timeout_s=timeout)

    status, failure = "ok", None
    adaptive_info: dict | None = None
    scan = None
    snap: dict = {}
    best = None
    try:
        live.sink(
            {
                "type": "run.started",
                "data": {
                    "mode": "mock" if mock else "live",
                    "label": label,
                    "run": "RUN2",
                    "host": {
                        "provider": "fake" if mock else "e2e",
                        "gpu": a.gpu_label,
                        "gpu_index": 0,
                        "cap_range_w": [200, 700],
                    },
                    "model": a.model,
                    "workload": "proof-v1 (111 prompts: 50 math, 29 code, 32 QA)",
                    "run_id": run_id,
                    "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "narration": {
                        "text": (
                            "Rehearsal on a fake GPU: nothing here is measured."
                            if mock
                            else "This is the real H100 run."
                        ),
                        "source": "template",
                    },
                },
            }
        )

        # ------------------------------------------------------------------ 1. room scan
        top = s.start(
            "room_scan",
            "sam",
            "masks",
            "Room scan · SAM 3.1",
            top="room_scan",
            inputs="the room photo; a text prompt: 'server rack'",
            say="Scanning the room: SAM finds the racks, the H100 goes in one slot.",
        )
        sam_tag = "MEASURED" if a.sam == "live" else "REPLAY"
        with s.step(
            "room_scan.sam",
            "sam",
            "masks",
            "SAM 3.1 cuts the racks out of the photo",
            parent="room_scan",
            inputs="photo + prompt 'server rack' ("
            + ("a live call, about $0.0025" if a.sam == "live"
               else "a saved raw output from a real call, replayed") + ")",
        ) as st:
            if scan_base is None:
                scan_base = roomscan.build(
                    a.room, live_sam=(a.sam == "live"), out=out / "room", scan_json=a.scan_json
                )
            st.tag = sam_tag
            found = (scan_base.get("sam") or {}).get("racks_found", len(scan_base["racks"]))
            st.outputs = f"{found} racks found"
            st.add("racks found", found, "", sam_tag)
        with s.step(
            "room_scan.check",
            "sam",
            "check",
            "Cross-check each rack: temperature against power",
            parent="room_scan",
            inputs="a thermal frame (SIMULATED) and per-GPU power (REPLAY of RUN1's trace)",
        ) as st:
            st.tag = "ADVICE"
            hot = [r["id"] for r in scan_base["racks"] if r.get("decision") == "derate"]
            st.outputs = f"hot at high power (a cap can help): {', '.join(hot) or 'none'}"
            st.add("racks to derate", len(hot), "", "ADVICE")
        rc = vm("snap", "--load-s", str(a.load_s))
        snap = grabbed.get("SNAP_JSON") or {}
        if rc != 0 or not snap:
            raise RuntimeError("could not read the GPU (snap failed)")
        scan = roomscan.place_h100(scan_base, snap, label=label)
        planner_hint = roomscan.hint(scan)
        with s.step(
            "room_scan.draw",
            "sam",
            "draw",
            "Draw the room, with the H100 in its slot",
            parent="room_scan",
            inputs=f"slot {scan['real_rack']} = the real card",
        ) as st:
            st.tag = label
            slot = roomscan.slot_of(scan)
            st.outputs = (
                f"{scan['real_rack']}: {slot['before']['power_w']:.0f} W, "
                f"{slot['before']['temp_c']} °C (GPU sensor)"
            )
            st.add("H100 power, stock cap", slot["before"]["power_w"], "W")
            st.add("H100 GPU temperature", slot["before"]["temp_c"], "°C")
            s.emit(
                "step.progress",
                {
                    "node": "sam",
                    "sub": "draw",
                    "pct": 100,
                    "message": "room drawn; the other racks are SIMULATED",
                    **roomscan.payload(scan, "before"),
                },
                step_id="room_scan.draw",
                parent="room_scan",
            )
        top.tag = label
        top.outputs = (
            f"{found} racks; the H100 sits in {scan['real_rack']} "
            f"({slot['before']['power_w']:.0f} W, {slot['before']['temp_c']} °C live)"
        )
        top.add("racks found", found, "", sam_tag)
        top.add("H100 power (stock)", slot["before"]["power_w"], "W")
        top.add("H100 temperature", slot["before"]["temp_c"], "°C")
        s.finish(top)
        (out / "room_scan_before.json").write_text(
            json.dumps({k: v for k, v in scan.items() if not k.startswith("_")}, indent=1)
        )

        # ------------------------------------------------------------------ 2. baseline
        if vm("baseline") != 0:
            raise RuntimeError("baseline failed")
        # ------------------------------------------------------------------ 3. quality gate
        rc = vm("control")
        if rc != 0:
            status, failure = (
                "failed",
                "the gate did not reject the bad setting (or rejected stock)",
            )
            raise RuntimeError(failure)

        # ------------------------------------------------------------------ 4. cap sweep
        sw = s.start(
            "cap_sweep",
            "e2e_rig",
            "sweep",
            "Cap sweep: Muse Spark proposes, the gate decides",
            top="cap_sweep",
            inputs=f"caps 300 to <700 W in 25 W steps; {a.reps} paired rounds per cap",
            say="Muse Spark picks a cap, the card runs it against stock, the gate decides.",
        )
        if hasattr(planner, "context"):
            planner.context = planner_hint
        done: list[dict] = []
        tried: set[int] = set()
        deadline = (t_start + a.sweep_budget_s) if a.sweep_budget_s else None
        k = 0

        def measure(cap: int, by: str, reason: str | None) -> None:
            rc2 = vm("step", "--cap", str(cap), "--reps", str(a.reps))
            summ = grabbed.get("STEP_JSON")
            if rc2 != 0 or not summ:
                raise RuntimeError(f"step {cap} W failed")
            summ["chosen_by"], summ["planner_reason"] = by, reason
            done.append(summ)
            tried.add(cap)
            e, p = summ["energy_change_pct"], summ["p99_change_pct"]
            s.emit(
                "step.progress",
                {
                    "node": "e2e_rig",
                    "sub": "sweep",
                    "pct": min(95, int(100 * len(done) / (a.max_proposals + len(a.grid)))),
                    "message": (
                        f"{cap} W: {'accepted' if summ['accepted'] else 'rejected'} · "
                        f"J/request {e['mean']:+.1f}%, p99 {p['mean']:+.1f}%"
                    ),
                },
                step_id="cap_sweep",
                parent=None,
            )

        for _ in range(a.max_proposals):
            if deadline and time.time() > deadline:
                break
            k += 1
            hist = [history_row(x) for x in done]
            pid = f"cap_sweep.propose{k}"
            with s.step(
                pid,
                "agent",
                "call",
                "Ask Muse Spark for the next cap",
                parent="cap_sweep",
                inputs=f"the allowed caps and {len(hist)} measured trial(s) so far",
            ) as st:
                prop = planner.propose(hist)
                usage = getattr(planner, "last_usage", {}) or {}
                mname = getattr(planner, "model", "muse-spark")
                by = prop.get("by", getattr(planner, "name", "planner"))
                real = by == "muse-spark"
                st.tag = label if real else "ADVICE"
                st.outputs = (
                    f"{prop['kind']} {prop['cap_w']} W: {prop['reason']}"
                    if prop["kind"] == "try"
                    else f"stop: {prop['reason']}"
                )
                st.add("input tokens", usage.get("input"), "", label)
                st.add("output tokens", usage.get("output"), "", label)
                s.emit(
                    "trial.proposed",
                    {
                        "cap_w": prop.get("cap_w"),
                        "kind": prop["kind"],
                        "by": by,
                        "reason": prop["reason"],
                        "muse_error": prop.get("muse_error"),
                    },
                    step_id=pid,
                    parent="cap_sweep",
                )
                s.emit(
                    "decision",
                    {
                        "choice": {
                            "kind": "cap",
                            "power_cap_w": prop.get("cap_w"),
                            "knob": None,
                            "value": None,
                        },
                        "reason": prop["reason"],
                        "model": mname if real else "bisect-fallback",
                        "tokens": {
                            "input": usage.get("input", 0),
                            "output": usage.get("output", 0),
                        },
                        "clamped": False,
                        "stop": prop["kind"] == "stop",
                        "label": label,
                    },
                    step_id=pid,
                    parent="cap_sweep",
                )
                s.emit(
                    "step.progress",
                    {
                        "node": "agent",
                        "sub": "parse",
                        "pct": 100,
                        "message": prop["reason"],
                        "narration": {
                            "text": prop["reason"],
                            "source": "muse-spark" if real else "template",
                            "model": mname if real else None,
                        },
                    },
                    step_id=pid,
                    parent="cap_sweep",
                )
            if prop["kind"] == "stop":
                break
            measure(int(prop["cap_w"]), by, prop["reason"])
        for cap in a.grid or ():
            if cap in tried or (deadline and time.time() > deadline):
                continue
            measure(cap, "grid", None)
        best = best_of(done)
        sw.tag = label
        sw.outputs = (
            f"{len(done)} caps tested; best accepted: "
            f"{best['cap_w']} W ({best['energy_change_pct']['mean']:+.1f}% J/request)"
            if best
            else f"{len(done)} caps tested; none passed all three gates"
        )
        sw.add("caps tested", len(done), "", label)
        sw.add("caps accepted", sum(x["accepted"] for x in done), "", label)
        if best:
            sw.add("best cap", best["cap_w"], "W", label)
            sw.add("best energy change", best["energy_change_pct"]["mean"], "%", label)
        s.finish(sw)

        # ------------------------------------------------------------------ 5. adaptive top-k
        if a.adaptive:
            rc = vm(
                "adaptive",
                "--adaptive-model",
                a.adaptive_model,
                "--adaptive-budget-s",
                str(a.adaptive_budget_s),
                timeout=a.adaptive_budget_s + 1800,
            )
            adaptive_info = grabbed.get("ADAPTIVE_JSON")
            if rc != 0 and adaptive_info is None:
                adaptive_info = {"ran": False, "reason": f"the command exited {rc}"}

        # ------------------------------------------------------------------ 6. keep / rollback
        kr = s.start(
            "keep_rollback",
            "apply",
            "keep",
            "Keep the best cap, or roll back",
            top="keep_rollback",
            inputs=("best accepted cap: " + (f"{best['cap_w']} W" if best else "none")),
            say="Keep the best cap that passed every gate, or put the card back to stock.",
        )
        after_read = None
        if best:
            if vm("apply", "--cap", str(best["cap_w"]), "--keep-cap") != 0:
                raise RuntimeError("could not apply the chosen cap")
            after_read = grabbed.get("APPLY_JSON")
            kr.outputs = (
                f"kept {best['cap_w']} W (driver read back "
                f"{(after_read or {}).get('readback_w')} W)"
            )
            kr.add("kept cap", best["cap_w"], "W", label)
            kr.add("energy change", best["energy_change_pct"]["mean"], "%", label)
            kr.add("p99 change", best["p99_change_pct"]["mean"], "%", label)
        else:
            s.emit(
                "action.rolled_back",
                {
                    "change": {"power_cap_w": None},
                    "restored_cap_w": snap["idle"]["default_w"],
                    "failed_gates": ["no cap passed quality, p99 and energy"],
                    "label": label,
                },
                step_id="keep_rollback",
                parent=None,
            )
            kr.outputs = f"rolled back: stock {snap['idle']['default_w']} W stays"
            kr.add("kept cap", snap["idle"]["default_w"], "W", label)
        s.finish(kr)
        # the room after: real stock vs chosen-cap readings of the slot
        if best:
            before = {
                "power_w": best["mean_w"]["stock"],
                "temp_c": best["max_temp_c_by_arm"]["stock"],
            }
            after = {"power_w": best["mean_w"]["cap"], "temp_c": best["max_temp_c_by_arm"]["cap"]}
            why = (
                f"Muse Spark proposed {best['cap_w']} W; the answer gate, p99 and energy gates "
                f"passed: {best['energy_change_pct']['mean']:+.1f}% J/request, "
                f"p99 {best['p99_change_pct']['mean']:+.1f}%"
            )
            scan = roomscan.finalize_h100(
                scan, before=before, after=after, cap_w=best["cap_w"], reason=why, label=label
            )
        else:
            before = {"power_w": slot["before"]["power_w"], "temp_c": slot["before"]["temp_c"]}
            scan = roomscan.finalize_h100(
                scan,
                before=before,
                after=None,
                cap_w=None,
                reason="no cap passed all three gates; stock kept",
                label=label,
            )
        s.emit(
            "step.progress",
            {
                "node": "sam",
                "sub": "draw",
                "pct": 100,
                "message": "the H100's rack, after: real stock vs chosen-cap readings",
                **roomscan.payload(scan, "after"),
            },
            step_id="room_scan",
            parent=None,
        )
        (out / "room_scan_after.json").write_text(
            json.dumps({k: v for k, v in scan.items() if not k.startswith("_")}, indent=1)
        )

        # ------------------------------------------------------------------ 7. receipt
        rec = finish_receipt(
            a,
            s,
            live,
            transport,
            out,
            label=label,
            control=None,
            steps=done,
            best=best,
            adaptive=adaptive_info,
            scan=scan,
            snap=snap,
            planner=planner,
            run_id=run_id,
            t_start=t_start,
        )
        recs = {
            "verdict": "change recommended" if best else "no change recommended",
            "applies": ["always-on inference serving where every request pays the energy cost"],
            "actions": (
                [
                    {
                        "label": label,
                        "text": (
                            f"set the power cap to {best['cap_w']} W "
                            f"(from {snap['idle']['default_w']} W). "
                            f"Measured here: {best['energy_change_pct']['mean']:+.1f}% J/request, "
                            f"p99 {best['p99_change_pct']['mean']:+.1f}%, answers pass "
                            f"{best['quality']['cap_pct']:.1f}% vs "
                            f"{best['quality']['stock_pct']:.1f}% at stock"
                        ),
                    }
                ]
                if best
                else []
            ),
        }
        s.emit("recommendations", recs, step_id="recommendations", parent=None)
        s.emit(
            "run.finished",
            {
                "status": "ok",
                "label": label,
                "run_id": run_id,
                "best_cap_w": best["cap_w"] if best else None,
                "receipt": rec["path"],
                "seconds": round(time.time() - t_start, 1),
                "narration": {"text": "The run finished.", "source": "template"},
            },
        )
    except BaseException as e:  # noqa: BLE001 - always close the stream with a reason
        status = "failed"
        failure = failure or f"{type(e).__name__}: {str(e)[:300]}"
        with_err = {"message": failure}
        try:
            live.sink({"type": "error", "data": with_err})
            live.sink(
                {
                    "type": "run.finished",
                    "data": {
                        "status": "failed",
                        "label": label,
                        "reason": failure,
                        "seconds": round(time.time() - t_start, 1),
                    },
                }
            )
        finally:
            live.close()
        transport.close()
        if isinstance(e, KeyboardInterrupt):
            raise
        return {"status": status, "failure": failure, "events": str(live.path), "run_id": run_id}
    live.close()
    transport.close()
    return {
        "status": status,
        "events": str(live.path),
        "run_id": run_id,
        "label_problems": live.problems,
        "n_events": live.count,
        "best_cap_w": best["cap_w"] if best else None,
    }


def finish_receipt(
    a,
    s: Stream,
    live: Live,
    transport: Transport,
    out: Path,
    *,
    label: str,
    control,
    steps: list[dict],
    best,
    adaptive,
    scan,
    snap,
    planner,
    run_id: str,
    t_start: float,
) -> dict:
    """Pull the results, write the report, build and sign the proof receipt, verify it."""
    from wattnot.receipt.canon import canonical_sha256
    from wattnot.receipt.sign import bundle_path, generate_test_key, sign, verify_offline_bundle

    rs = s.start(
        "receipt",
        "receipts",
        "build",
        "Receipt: build, hash, sign, verify",
        top="receipt",
        inputs="every block's raw replies (hashed), the sweep, the gate results",
    )
    with s.step(
        "receipt.pull",
        "receipts",
        "build",
        "Collect the GPU host's results",
        parent="receipt",
        inputs=f"{a.remote_out} on the GPU host",
    ) as st:
        transport.pull(a.remote_out, out / "vm")
        ctl_p = out / "vm" / "control.json"
        control = json.loads(ctl_p.read_text()) if ctl_p.exists() else None
        st.outputs = f"{sum(1 for _ in (out / 'vm').rglob('*') if _.is_file())} files"
    with s.step(
        "receipt.report",
        "receipts",
        "build",
        "Write the results table and manifest",
        parent="receipt",
        inputs="steps/*.json, control.json",
    ) as st:
        rep_dir = out / "vm"
        all_steps = [json.loads(p.read_text()) for p in sorted((rep_dir / "steps").glob("*.json"))]
        for x in all_steps:
            for d in steps:
                if d["cap_w"] == x["cap_w"]:
                    x["chosen_by"], x["planner_reason"] = (
                        d.get("chosen_by"),
                        d.get("planner_reason"),
                    )
        write_report(
            rep_dir,
            a.model,
            control,
            all_steps,
            {
                "run_id": run_id,
                "mode": a.mode,
                "reps": a.reps,
                "repeat": a.repeat,
                "concurrency": a.concurrency,
                "planner": getattr(planner, "name", "?"),
            },
        )
        st.outputs = f"results.json, results.md, MANIFEST.sha256 ({len(all_steps)} caps)"
    body = {
        "spec": "wattnot.proof-receipt/1",
        "receipt_id": f"proof-{run_id[-6:]}",
        "run_id": run_id,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "label": label,
        "mode": a.mode,
        "hardware": {
            "gpu": snap["idle"]["name"],
            "default_cap_w": snap["idle"]["default_w"],
            "cap_range_w": [snap["idle"]["min_w"], snap["idle"]["max_w"]],
        },
        "model": a.model,
        "workload": {"name": "proof-v1", "prompts": 111, "sha256": workload_sha()},
        "method": {
            "energy": "NVML total_energy_mj counter, before and after each block",
            "design": f"interleaved stock/cap blocks, {a.reps} rounds per cap",
            "accept": "quality drop <= 2 pts, mean p99 change <= +10%, "
            "95% CI of the J/request change entirely below 0",
        },
        "control": control and {k: v for k, v in control.items() if k != "blocks"},
        "steps": [
            {k: v for k, v in x.items() if k != "blocks"}
            for x in sorted(all_steps, key=lambda x: -x["cap_w"])
        ],
        "best_cap_w": best["cap_w"] if best else None,
        "planner": {
            "name": getattr(planner, "name", "?"),
            "model": getattr(planner, "model", None),
        },
        "adaptive_topk": adaptive,
        "room": {
            "slot": scan["real_rack"],
            "labels": scan.get("labels"),
            "slot_before": roomscan.slot_of(scan)["before"],
            "slot_after": roomscan.slot_of(scan)["after"],
            "sam": scan.get("sam"),
        },
        "events": {
            "file": "events.jsonl",
            "n_events_so_far": live.count,
            "sha256_so_far": sha_file(live.path),
        },
        "manifest_sha256": sha_file(rep_dir / "MANIFEST.sha256"),
        "results_sha256": sha_file(rep_dir / "results.json"),
        "integrity": {"sha256": None, "signer": None, "rekor": None},
    }
    with s.step(
        "receipt.canon",
        "receipts",
        "canon",
        "Write the receipt in canonical JSON",
        parent="receipt",
        inputs="sorted keys, no whitespace (RFC 8785)",
    ) as st:
        digest = canonical_sha256(body)
        st.outputs = f"{len(body)} top-level fields"
    with s.step(
        "receipt.hash",
        "receipts",
        "hash",
        "Hash it (SHA-256)",
        parent="receipt",
        inputs="the canonical bytes, without the integrity block",
    ) as st:
        body["integrity"]["sha256"] = digest
        rpath = out / f"{body['receipt_id']}.json"
        rpath.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        st.outputs = digest
    with s.step(
        "receipt.sign",
        "receipts",
        "sign",
        "Sign it offline (Ed25519)",
        parent="receipt",
        inputs="an offline key on this machine; nothing goes to a public log",
    ) as st:
        key = a.sign_key
        if not key.exists():
            generate_test_key(key)
        sign(rpath, no_rekor=True, key_path=key)
        body = json.loads(rpath.read_text())
        st.outputs = f"signer {body['integrity']['signer']}; no public log entry"
    with s.step(
        "receipt.verify",
        "receipts",
        "verify",
        "Check the signature and the hash",
        parent="receipt",
        inputs="the receipt and its signature bundle",
    ) as st:
        bundle = json.loads(bundle_path(rpath).read_text())
        ok, why = verify_offline_bundle(body, bundle)
        st.ok = ok
        st.outputs = why
    grade = "SIMULATED" if label == "MOCK" else "ESTIMATE"
    with s.step(
        "receipt.grade",
        "receipts",
        "grade",
        "Grade it",
        parent="receipt",
        inputs="signed offline, no public log entry",
    ) as st:
        st.tag = "ADVICE"
        st.outputs = f"{grade}: " + (
            "fake GPU, nothing measured"
            if label == "MOCK"
            else "measured and hash-checked, signed offline; VALID needs a "
            "public log entry, which needs the owner's okay"
        )
    s.emit(
        "receipt.signed",
        {
            "receipt_id": body["receipt_id"],
            "sha256": body["integrity"]["sha256"],
            "grade": grade,
            "rekor_log_index": None,
            "path": str(rpath),
            "signed": True,
            "signer": body["integrity"]["signer"],
            "note": "signed offline; not in a public log",
        },
        step_id="receipt",
        parent=None,
    )
    rs.tag = label
    rs.outputs = f"{body['receipt_id']} · sha256 {digest[:16]}… · grade {grade}"
    rs.add("fields hashed", len(body), "", label)
    s.finish(rs)
    return {"path": str(rpath), "sha256": digest, "grade": grade}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--mode", choices=["live", "rehearsal"], default="rehearsal")
    ap.add_argument("--host", help="live: the GPU host's IP")
    ap.add_argument("--key", default="~/.ssh/e2e_h100")
    ap.add_argument("--out", type=Path, default=Path("run2-out"))
    ap.add_argument("--runs-root", type=Path, default=None)
    ap.add_argument(
        "--live-file",
        type=Path,
        default=None,
        help="the file the dashboard follows (--events); refuses to reuse a non-empty one",
    )
    ap.add_argument("--remote-out", default="/root/run2/results")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--gpu-label", default="H100 80GB · E2E Networks, Delhi")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--settle-s", type=float, default=4.0)
    ap.add_argument("--warmup", type=int, default=16)
    ap.add_argument(
        "--load-s", type=float, default=30.0, help="room scan: seconds of load at stock"
    )
    ap.add_argument("--max-proposals", type=int, default=4)
    ap.add_argument("--grid", default="", help="comma list of caps to fill in after Muse's picks")
    ap.add_argument("--sweep-budget-s", type=float, default=0)
    ap.add_argument("--planner", choices=["muse", "bisect", "fake"], default="muse")
    ap.add_argument("--sam", choices=["replay", "live"], default="replay")
    ap.add_argument("--room", default="arsat")
    ap.add_argument("--scan-json", type=Path)
    ap.add_argument("--adaptive", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--adaptive-model", default="ibm-granite/granite-3.1-3b-a800m-instruct")
    ap.add_argument("--adaptive-budget-s", type=float, default=1200)
    ap.add_argument("--sign-key", type=Path, default=Path("receipt-offline-key.pem"))
    ap.add_argument("--fake-delay", type=float, default=0.01)
    ap.add_argument("--fresh", action="store_true", help="move an old live file aside first")
    a = ap.parse_args(argv)
    a.grid = tuple(int(x) for x in a.grid.split(",") if x)
    a.out.mkdir(parents=True, exist_ok=True)
    a.runs_root = a.runs_root or a.out
    if a.live_file and a.live_file.exists() and a.live_file.stat().st_size:
        if not a.fresh:
            ap.error(f"{a.live_file} already has events; use --fresh to move it aside")
        a.live_file.rename(a.live_file.with_suffix(f".{int(time.time())}.prev"))
    if a.mode == "rehearsal":
        from wattnot.proof.fake import FakeMeta, rehearsal_planner_answers
        from wattnot.proof.planner import MusePlanner

        planner = MusePlanner(FakeMeta(rehearsal_planner_answers()))
        planner.name = "muse-spark"  # the scripted client plays Muse; labelled MOCK in the events
        transport: Transport = LocalTransport(env={"PYTHONUNBUFFERED": "1"})
        a.remote_out = str(a.out / "vm")
        a.sam = "replay"
    else:
        if not a.host:
            ap.error("--mode live needs --host")
        from wattnot.proof.transport import SshTransport

        transport = SshTransport(a.host, key=a.key)
        if a.planner == "bisect":
            planner = BisectPlanner()
        else:
            from wattnot.meta.client import GuardedClient
            from wattnot.proof.planner import MusePlanner

            gc = GuardedClient()
            if not gc.key:
                ap.error("no Meta key on this machine (~/.config/muse/auth.json)")
            planner = MusePlanner(gc)
    res = drive(a, transport=transport, planner=planner, scan_base=None, out=a.out)
    print(json.dumps(res, indent=1))
    return 0 if res["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
