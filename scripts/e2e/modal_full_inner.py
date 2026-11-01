"""Inside the Modal A100 container: one full-feature round on our own vLLM serving Glimmer 30B.

Runs against a vLLM OpenAI server on 127.0.0.1:8000 that this script starts and stops itself
(no docker on Modal). Every number is labelled. Steps, in order:

  probe       power-cap write test (NVML). Modal refuses it; recorded as OFF with the reason.
  without     stock serving: BF16, prefix caching OFF, thinking ON at the full budget,
              concurrency 4. Our set (92) + GSM8K (60) + HumanEval (40). NVML energy counter
              brackets the whole arm (MEASURED).
  batch_kv    WattNot batch/KV sweep on a 24-item slice: concurrency x max-num-seqs, keep the
              lowest J/correct that holds the quality gate (MEASURED).
  fp8         FP8 keep/reject gate: restart with --quantization fp8, same slice; keep only if
              accuracy holds (-2 pts) and J/correct drops (MEASURED). Eye + hearing re-checked
              on the candidate before keeping it.
  with        WattNot serving: kept precision, prefix caching ON, kept batch size, cheapest-first
              router (thinking OFF first, step up to a per-tier thinking budget only when the fixed
              check fails), reasoning-leak strip, joule-SLO + p99 limit checked after.
  agent       agent saver blocks on the WattNot server: agentwaste_gpu_ab (dedup, memory
              compression, Code Mode sandbox), langchain_pass (waste proxy OFF vs ON).
  eye/hearing vision gate on the SYNTHETIC Muse Image set, spoken-answer gate on the cached
              Muse Voice Transcribe transcripts.

    python scripts/e2e/modal_full_inner.py --out /results/r13 [--limit 3] [--fake]

--fake runs every step against an in-process fake server and fake NVML (MOCK numbers), so the
plumbing is testable with no GPU and no spend.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from wattnot.longrun.grade import grade_one  # noqa: E402
from wattnot.longrun.tasks import build  # noqa: E402
from wattnot.longrun.thinking_budget import budget_tokens  # noqa: E402
from wattnot.proof.client import Reply  # noqa: E402
from wattnot.e2e.fullstack import RequestLevers  # noqa: E402

LEVERS = RequestLevers()

MODEL = "meta-models/Muse-Glimmer-30B"
PORT = 8000
BASE = f"http://127.0.0.1:{PORT}/v1"
PUB_N = {"gsm8k": 60, "humaneval": 40}
PUB_BUDGET = {"gsm8k": 512, "humaneval": 768}
FULL_THINK = 3072                       # the stock arm's token budget (public_bench_ab default)
THINK_X = 1.0                           # WattNot per-tier thinking budget multiplier
GATE_DROP_PTS = 2.0                     # quality gate: accuracy may drop at most 2 points
P99_LIMIT = 1.10                        # user-set p99 limit: +10% (receipt `limits` default)
BATCH_GRID = ((4, 64), (16, 64), (32, 128))   # (client concurrency, --max-num-seqs)
SLICE_N = 24


# ---------- meter ------------------------------------------------------------------------------

class Nvml:
    def __init__(self, fake=False):
        self.fake = fake
        self._e = 0.0
        self._t = time.time()
        if not fake:
            import pynvml
            pynvml.nvmlInit()
            self.p = pynvml
            self.h = pynvml.nvmlDeviceGetHandleByIndex(0)

    def energy_j(self) -> float:
        if self.fake:
            now = time.time()
            self._e += (now - self._t) * 300.0
            self._t = now
            return self._e
        return self.p.nvmlDeviceGetTotalEnergyConsumption(self.h) / 1000.0

    def power_w(self) -> float:
        return 300.0 if self.fake else self.p.nvmlDeviceGetPowerUsage(self.h) / 1000.0

    def name(self) -> str:
        if self.fake:
            return "FAKE-GPU"
        n = self.p.nvmlDeviceGetName(self.h)
        return n.decode() if isinstance(n, bytes) else n

    def cap_probe(self) -> dict:
        if self.fake:
            return {"writable": False, "reason": "fake meter", "label": "MOCK"}
        try:
            cur = self.p.nvmlDeviceGetPowerManagementLimit(self.h)
            self.p.nvmlDeviceSetPowerManagementLimit(self.h, cur)
            return {"writable": True, "stock_w": cur / 1000, "label": "MEASURED"}
        except Exception as e:  # noqa: BLE001
            return {"writable": False, "reason": f"NVML refused the power-limit write ({type(e).__name__}); "
                    "Modal containers have no privileged GPU control", "label": "MEASURED"}


# ---------- server -----------------------------------------------------------------------------

class Server:
    def __init__(self, model_path: str, log_dir: Path, fake=False):
        self.model_path, self.log_dir, self.fake = model_path, log_dir, fake
        self.proc, self.cfg, self.fake_srv = None, None, None

    def start(self, *, fp8=False, prefix=False, max_seqs=64) -> dict:
        self.stop()
        self.cfg = {"fp8": fp8, "prefix_caching": prefix, "max_num_seqs": max_seqs}
        t0 = time.time()
        if self.fake:
            from tests.fakes_vllm import start_fake  # noqa: PLC0415
            self.fake_srv = start_fake(PORT)
        else:
            argv = ["vllm", "serve", self.model_path, "--served-model-name", MODEL,
                    "--reasoning-parser", "muse_glimmer", "--max-model-len", "8192",
                    "--gpu-memory-utilization", "0.90", "--max-num-seqs", str(max_seqs),
                    "--limit-mm-per-prompt.image", "1", "--port", str(PORT),
                    "--enable-prefix-caching" if prefix else "--no-enable-prefix-caching"]
            if fp8:
                argv += ["--quantization", "fp8"]
            tag = f"{'fp8' if fp8 else 'bf16'}-{'pc' if prefix else 'nopc'}-{max_seqs}"
            self.proc = subprocess.Popen(argv, stdout=open(self.log_dir / f"vllm-{tag}.log", "wb"),
                                         stderr=subprocess.STDOUT)
            deadline = time.time() + 900
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"vllm exited rc={self.proc.returncode} ({tag})")
                try:
                    with urllib.request.urlopen(f"{BASE}/models", timeout=5):  # noqa: S310
                        break
                except Exception:  # noqa: BLE001
                    time.sleep(3)
            else:
                raise RuntimeError("vllm not ready in 15 min")
        self.cfg["start_s"] = {"value": round(time.time() - t0, 1), "label": "MEASURED"}
        return self.cfg

    def stop(self):
        if self.fake_srv:
            self.fake_srv.shutdown()
            self.fake_srv = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        time.sleep(0 if self.fake else 5)


# ---------- chat -------------------------------------------------------------------------------

class Calls:
    def __init__(self):
        self.rows, self.lock = [], threading.Lock()

    def add(self, r):
        with self.lock:
            self.rows.append(r)


def strip_leak(text: str) -> str:
    """Reasoning-leak strip: drop any <think>..</think> the parser let through."""
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.strip()


class Chat:
    """chat(messages, max_tokens) -> Reply over the local vLLM; thinking on or off."""

    def __init__(self, calls, arm, item, think: bool, budget: int | None):
        self.calls, self.arm, self.item, self.think, self.budget = calls, arm, item, think, budget
        self.levers = LEVERS if arm == "with_wattnot" else None
        self.context_limit = None

    def __call__(self, messages, max_tokens):
        mt = self.budget if self.budget else max_tokens
        body = {"model": MODEL, "messages": messages, "max_tokens": mt, "temperature": 0, "seed": 0,
                "chat_template_kwargs": {"enable_thinking": self.think},
                # Glimmer ignores enable_thinking=false (smoke-r04): effort is what cuts reasoning
                "reasoning_effort": "medium" if self.think else "low"}
        lev = {}
        if self.levers is not None:   # shared pipeline: agent saver + waste guard (wattnot.e2e.fullstack)
            nb, lev = self.levers.apply(body)
            if nb is None:
                self.calls.add({"item": self.item, "arm": self.arm, "status": 0, "finish": "guard-stop",
                                "latency_s": 0.0, "prompt_tokens": 0, "cached_tokens": 0,
                                "completion_tokens": 0, "reasoning_tokens": 0, "label": "MEASURED"})
                return Reply(0.0, 0, "", "error", 0, False)
            body = nb
        if not self.think and self.budget:
            # thinking-budget enforcer: think within B tokens, force the close, then answer
            # (Glimmer ignores enable_thinking=false and reasoning_effort=low; smoke-r04/r05)
            from wattnot.longrun.thinking_budget import BudgetChat
            bc = BudgetChat(f"{BASE}/chat/completions", MODEL, self.budget)
            r = bc(body["messages"], max_tokens)
            self.calls.add({"item": self.item, "arm": self.arm, "think": "budget", "max_tokens": self.budget,
                            "status": 200 if r.finish != "error" else 599, "latency_s": round(r.latency_s, 4),
                            "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": r.tokens,
                            "reasoning_tokens": None, "forced_close": sum(bc.forced.values()),
                            "answer_cut": sum(bc.cut.values()), "finish": r.finish,
                            "levers": lev.get("guard"), "label": "MEASURED",
                            "note": "prompt tokens not returned on the two-phase path"})
            return Reply(r.latency_s, r.tokens, strip_leak(r.text), r.finish, 0, False)
        t0 = time.perf_counter()
        status, d = 599, {}
        try:
            req = urllib.request.Request(f"{BASE}/chat/completions", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600) as r:  # noqa: S310
                status, d = r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            status = e.code
        except Exception:  # noqa: BLE001
            pass
        lat = time.perf_counter() - t0
        u = d.get("usage") or {}
        ch = (d.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        text = strip_leak(msg.get("content") or "") if status == 200 else ""
        rs = msg.get("reasoning_content") or msg.get("reasoning") or ""
        rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
        fin = (ch.get("finish_reason") or "stop") if status == 200 else "error"
        ct = int(u.get("completion_tokens", 0))
        cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
        self.calls.add({"item": self.item, "arm": self.arm, "think": self.think, "max_tokens": mt,
                        "status": status, "latency_s": round(lat, 4),
                        "prompt_tokens": int(u.get("prompt_tokens", 0)), "cached_tokens": cached,
                        "completion_tokens": ct, "reasoning_tokens": rt,
                        "reasoning_chars": len(rs), "finish": fin, "levers": lev.get("guard"), "label": "MEASURED"})
        rtok = int(rt) if rt is not None else len(rs) // 4
        return Reply(lat, ct, text, fin, rtok, rt is not None)


def _bench():
    spec = importlib.util.spec_from_file_location("public_bench_ab", ROOT / "scripts" / "public_bench_ab.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def items(b, limit=0):
    out = [("ours", t.id, t) for t in build()]
    for name, n in PUB_N.items():
        for x in b.load(name)[:n]:
            out.append((name, str(x.get("task_id", x.get("question", "")[:40])), x))
    if limit:
        by = {}
        for it in out:
            by.setdefault(it[0], []).append(it)
        out = [it for v in by.values() for it in v[:limit]]
    return out


def attempt(b, calls, arm, ds, iid, obj, think, budget):
    item = f"{ds}:{iid}"
    t0 = time.perf_counter()
    if ds == "ours":
        cap = {}

        def hook(_i, _d, prompt, answer, det):
            cap.update(prompt=prompt, answer=answer)
            return None
        g = grade_one(obj, Chat(calls, arm, item, think, budget), hook)
        ok, prompt, ans = g.ok, cap.get("prompt", obj.prompt), cap.get("answer", "")
        tier = obj.tier
    else:
        pf, gf = b.grader(ds)
        prompt = pf(obj)
        r = Chat(calls, arm, item, think, budget or PUB_BUDGET[ds])([{"role": "user", "content": prompt}],
                                                                    PUB_BUDGET[ds])
        ok = r.finish not in ("error", "length") and bool(gf(obj, r.text))
        ans, tier = r.text, ds
    return {"id": iid, "set": ds, "dataset": tier, "prompt": prompt, "answer": ans,
            "deterministic": bool(ok), "wall_s": round(time.perf_counter() - t0, 4)}


def run_item(b, calls, arm, it):
    ds, iid, obj = it
    if arm == "without_wattnot":
        r = attempt(b, calls, arm, ds, iid, obj, True, FULL_THINK)
        r["tries"] = ["think-full"]
        return r
    # cheapest-first router: effort low, then the per-tier thinking budget only on a failed check
    first = budget_tokens(obj, 0.5) if ds == "ours" else PUB_BUDGET[ds] // 2
    r = attempt(b, calls, arm, ds, iid, obj, False, first)
    r["tries"] = ["low"]
    if not r["deterministic"]:
        bud = budget_tokens(obj, THINK_X) if ds == "ours" else PUB_BUDGET[ds] * 4
        r2 = attempt(b, calls, arm, ds, iid, obj, True, bud)
        r2["wall_s"] = round(r2["wall_s"] + r["wall_s"], 4)
        r2["tries"] = ["low", f"medium-{bud}"]
        r = r2
    return r


def pctl(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 4)


def run_arm(b, nv, arm, its, conc, raw: Path, tag: str):
    calls = Calls()
    e0, t0 = nv.energy_j(), time.time()
    samples, stop = [], threading.Event()

    def sampler():
        while not stop.is_set():
            samples.append((round(time.time() - t0, 2), round(nv.power_w(), 1)))
            stop.wait(1.0)
    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    ans, chunks = [], []
    step = max(1, min(max(conc * 2, 8), len(its) // 4))
    with ThreadPoolExecutor(conc) as ex:
        for i in range(0, len(its), step):
            c0 = nv.energy_j()
            got = list(ex.map(lambda it: run_item(b, calls, arm, it), its[i:i + step]))
            chunks.append({"n": len(got), "j": round(nv.energy_j() - c0, 2)})
            ans += got
    stop.set()
    th.join()
    j, wall = nv.energy_j() - e0, time.time() - t0
    for a in ans:
        a["arm"] = arm
    correct = sum(a["deterministic"] for a in ans)
    per = {}
    for a in ans:
        p = per.setdefault(a["set"], [0, 0])
        p[0] += a["deterministic"]
        p[1] += 1
    rows = calls.rows
    walls = [a["wall_s"] for a in ans]
    summ = {"tag": tag, "arm": arm, "answers": len(ans), "correct": correct,
            "accuracy": round(correct / len(ans), 4) if ans else None,
            "per_set": {k: {"correct": v[0], "answers": v[1]} for k, v in per.items()},
            "calls": len(rows), "errors": sum(r["status"] != 200 for r in rows),
            "truncated": sum(r["finish"] == "length" for r in rows),
            "tokens": {"prompt": sum(r["prompt_tokens"] for r in rows),
                       "cached": sum(r["cached_tokens"] for r in rows),
                       "completion": sum(r["completion_tokens"] for r in rows), "label": "MEASURED"},
            "energy_j": {"value": round(j, 1), "label": "SIMULATED" if nv.fake else "MEASURED",
                         "method": "NVML total-energy counter across the whole arm"},
            "energy_wh": {"value": round(j / 3600, 4), "label": "SIMULATED" if nv.fake else "MEASURED"},
            "wall_s": {"value": round(wall, 1), "label": "MEASURED"},
            "avg_w": {"value": round(j / wall, 1) if wall else None, "label": "MEASURED"},
            "j_per_correct": {"value": round(j / correct, 1) if correct else None,
                              "label": "SIMULATED" if nv.fake else "MEASURED"},
            "item_latency_s": {"p50": pctl(walls, 0.5), "p99": pctl(walls, 0.99), "label": "MEASURED"},
            "stepped_up": sum(len(a["tries"]) > 1 for a in ans), "concurrency": conc,
            "wh_per_item_samples": {"values": [round(c["j"] / c["n"] / 3600, 6) for c in chunks if c["n"]],
                                    "label": "SIMULATED" if nv.fake else "MEASURED",
                                    "method": f"NVML counter per chunk of {step} items / items in chunk"}}
    raw.mkdir(parents=True, exist_ok=True)
    for name, data in (("answers", ans), ("calls", rows)):
        with open(raw / f"{tag}.{name}.jsonl", "w") as f:
            for x in data:
                f.write(json.dumps(x) + "\n")
    with open(raw / f"{tag}.power.csv", "w") as f:
        f.write("t_s,watts\n" + "".join(f"{t},{w}\n" for t, w in samples))
    return summ, ans


def gate(base, cand):
    """Quality gate + energy: keep the candidate only if accuracy holds and J/correct drops."""
    a0, a1 = base["accuracy"] or 0, cand["accuracy"] or 0
    j0, j1 = base["j_per_correct"]["value"], cand["j_per_correct"]["value"]
    acc_ok = (a0 - a1) * 100 <= GATE_DROP_PTS
    j_ok = bool(j0 and j1 and j1 < j0)
    return {"keep": acc_ok and j_ok, "accuracy_ok": acc_ok, "energy_ok": j_ok,
            "acc_drop_pts": round((a0 - a1) * 100, 2), "label": "MEASURED"}


def sub(argv, out: Path, timeout: float) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"})
        rc, tail = p.returncode, (p.stdout + p.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        rc, tail = 124, "timeout"
    out.mkdir(parents=True, exist_ok=True)
    (out / "block.log").write_text(tail)
    return {"rc": rc, "wall_s": round(time.time() - t0, 1), "out": out.name}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model-path", default=MODEL)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--skip", nargs="*", default=[], help="steps to skip (smoke tests)")
    a = ap.parse_args(argv)
    out, raw = a.out, a.out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    nv = Nvml(a.fake)
    srv = Server(a.model_path, raw, a.fake)
    b = _bench()
    its = items(b, a.limit)
    sl = its[:: max(1, len(its) // SLICE_N)][:SLICE_N] if not a.limit else its
    doc = {"started_utc": datetime.now(UTC).isoformat(), "gpu": nv.name(), "fake": a.fake,
           "idle_w": {"value": round(nv.power_w(), 1), "label": "MEASURED", "when": "before vLLM start"},
           "cap_probe": nv.cap_probe(), "items": len(its), "slice": len(sl), "steps": {}}
    S = doc["steps"]
    (out / "probe").mkdir(exist_ok=True)
    (out / "probe" / "probe.json").write_text(json.dumps({"power_cap": doc["cap_probe"], "gpu": doc["gpu"],
                                                          "idle_w": doc["idle_w"]}, indent=1))

    def save():
        txt = json.dumps(doc, indent=1)
        if a.fake:   # nothing from a fake run may read as MEASURED
            txt = txt.replace('"MEASURED"', '"MOCK"')
        (out / "inner.json").write_text(txt)

    try:
        # 1. without WattNot: stock serving
        S["without_server"] = srv.start(fp8=False, prefix=False, max_seqs=64)
        S["without"], _ = run_arm(b, nv, "without_wattnot", its, 4, raw, "without")
        save()
        # 2. batch / KV sweep (WattNot lever) on the slice, BF16 + prefix cache
        sweep = []
        for conc, seqs in BATCH_GRID:
            if "batch" in a.skip:
                break
            srv.start(fp8=False, prefix=True, max_seqs=seqs)
            s, _ = run_arm(b, nv, "with_wattnot", sl, conc, raw, f"batch-c{conc}-s{seqs}")
            sweep.append({"concurrency": conc, "max_num_seqs": seqs, **s})
        ok = [x for x in sweep if x["j_per_correct"]["value"]]
        base_acc = max((x["accuracy"] for x in ok), default=0)
        ok = [x for x in ok if (base_acc - x["accuracy"]) * 100 <= GATE_DROP_PTS]
        best = min(ok, key=lambda x: x["j_per_correct"]["value"]) if ok else {"concurrency": 4, "max_num_seqs": 64}
        S["batch_kv"] = {"grid": sweep, "kept": {"concurrency": best["concurrency"],
                                                 "max_num_seqs": best["max_num_seqs"]}, "label": "MEASURED"}
        save()
        conc, seqs = best["concurrency"], best["max_num_seqs"]
        # 3. FP8 keep/reject gate on the slice, with eye + hearing re-checked on the candidate
        fp8 = False
        if "fp8" not in a.skip and sweep:
            bf = next(x for x in sweep if x["concurrency"] == conc)
            try:
                srv.start(fp8=True, prefix=True, max_seqs=seqs)
                s, _ = run_arm(b, nv, "with_wattnot", sl, conc, raw, "fp8")
                g = gate(bf, s)
                eye = sub([sys.executable, "-m", "wattnot.vision.run", "--base-url", BASE, "--out",
                           str(out / "fp8-eye"), "--budget-s", "90"] + (["--fake"] if a.fake else []),
                          out / "fp8-eye", 300)
                ear = sub([sys.executable, "-m", "wattnot.spokenanswer.run", "--base-url", BASE, "--out",
                           str(out / "fp8-hearing"), "--budget-s", "90"] + (["--fake"] if a.fake else []),
                          out / "fp8-hearing", 300)
                g["eye_rc"], g["hearing_rc"] = eye["rc"], ear["rc"]
                fp8 = g["keep"] and eye["rc"] == 0 and ear["rc"] == 0
                S["fp8"] = {"bf16": {k: bf[k] for k in ("accuracy", "j_per_correct")}, "fp8": s, "gate": g,
                            "kept": fp8}
            except RuntimeError as e:
                S["fp8"] = {"kept": False, "error": str(e)[:300]}
            save()
        # 4. with WattNot: every lever on
        S["with_server"] = srv.start(fp8=fp8, prefix=True, max_seqs=seqs)
        S["with"], _ = run_arm(b, nv, "with_wattnot", its, conc, raw, "with")
        S["with"]["request_levers"] = dict(LEVERS.stats)
        wo, wi = S["without"], S["with"]
        p99_ok = (wi["item_latency_s"]["p99"] or 0) <= P99_LIMIT * (wo["item_latency_s"]["p99"] or 1e9)
        S["limits"] = {"p99_limit": f"+{round((P99_LIMIT - 1) * 100)}%", "p99_ok": p99_ok,
                       "joule_slo_ok": bool(wi["j_per_correct"]["value"] and wo["j_per_correct"]["value"]
                                            and wi["j_per_correct"]["value"] <= wo["j_per_correct"]["value"]),
                       "quality_gate": gate(wo, wi), "label": "MEASURED"}
        save()
        # 5. agent saver, eye, hearing, thinking budget on the WattNot server
        fk = ["--fake"] if a.fake else []
        blocks = {
            "agentwaste": [sys.executable, "scripts/agentwaste_gpu_ab.py", "--base-url", BASE, "--reps", "2",
                           "--out", str(out / "agentwaste")] + fk,
            "langchain": [sys.executable, "scripts/langchain_pass.py", "--dataset", "own", "--n",
                          str(a.limit or 20), "--out", str(out / "langchain")]
                         + (["--fake"] if a.fake else ["--base-url", BASE]),
            "eye": [sys.executable, "-m", "wattnot.vision.run", "--base-url", BASE, "--out", str(out / "eye"),
                    "--budget-s", "90"] + fk,
            "spoken": [sys.executable, "-m", "wattnot.spokenanswer.run", "--base-url", BASE, "--out",
                        str(out / "spoken"), "--budget-s", "90"] + fk,
            "think": [sys.executable, "-m", "wattnot.longrun.thinking_budget", "--per-tier",
                      str(a.limit or 2), "--out", str(out / "think")]
                     + (["--fake"] if a.fake else ["--url", f"{BASE}/chat/completions"]),
        }
        S["blocks"] = {}
        for k, cmd in blocks.items():
            if k in a.skip:
                continue
            e0 = nv.energy_j()
            r = sub(cmd, out / k, 1500)
            r["energy_j"] = {"value": round(nv.energy_j() - e0, 1), "label": "SIMULATED" if a.fake else "MEASURED"}
            S["blocks"][k] = r
            save()
    finally:
        srv.stop()
        doc["ended_utc"] = datetime.now(UTC).isoformat()
        save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
