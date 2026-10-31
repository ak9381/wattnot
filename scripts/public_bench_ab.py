"""Public-benchmark savings check: stock (700 W, prefix caching off) vs the best passing cap.

Small pinned slices of two public sets, both MIT licensed (licences in data/public_bench/):
  * GSM8K test, first 60 items, openai/grade-school-math @ 3101c7d5072418e28b9008a6636bde82a006892c
    graded on the final number (the "#### N" gold answer vs the last number in the reply).
  * HumanEval, first 40 items, openai/human-eval @ b9453e8f1865df0a0057a1383767caa400acfbcf
    graded by running the item's own tests in a sandboxed subprocess (temp dir, timeout,
    CPU/memory limits, isolated interpreter, scrubbed env).

Same server, same prompts, same seed on both arms; only the power cap changes. Rounds alternate
(stock, cap, cap, stock, ...), energy is the GPU's NVML total-energy counter over each arm's
timed window (MEASURED), and the headline is the paired per-round change in J per correct answer
with its 95% interval and a paired-t p-value (wattnot.context_saver.prefix_cache.paired_change).
Only the final answer (message content) is graded, never the reasoning text.

    python scripts/public_bench_ab.py --cap 350 --reps 2 --out /root/run2/results/t2e-public
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DATA = ROOT / "data" / "public_bench"
SOURCES = {
    "gsm8k": {"file": "gsm8k_test_first60.jsonl", "repo": "https://github.com/openai/grade-school-math",
              "commit": "3101c7d5072418e28b9008a6636bde82a006892c", "licence": "MIT",
              "sha256": "4a665a79d70d7d83f14400fdb75f421e201ce1ae8ec94fa87157e5a3feae75aa"},
    "humaneval": {"file": "humaneval_first40.jsonl", "repo": "https://github.com/openai/human-eval",
                  "commit": "b9453e8f1865df0a0057a1383767caa400acfbcf", "licence": "MIT",
                  "sha256": "a10bc9bd9677b438a2a31a742a22ea17a74459ecc997918dfe06e8937b695818"},
}
NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def load(name: str) -> list[dict]:
    import hashlib
    src = SOURCES[name]
    raw = (DATA / src["file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != src["sha256"]:
        raise ValueError(f"{name}: pinned slice sha256 mismatch")
    return [json.loads(x) for x in raw.decode().splitlines() if x.strip()]


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", "").rstrip("."))
    except ValueError:
        return None


def gsm8k_gold(item: dict) -> float | None:
    return _num(item["answer"].split("####")[-1].strip())


def gsm8k_pred(text: str) -> float | None:
    """The final number: a '#### N' or a boxed{N} if given, else the last number in the reply."""
    text = text or ""
    for pat in (r"####\s*(-?[\d,]+(?:\.\d+)?)", r"\\boxed\{\s*\$?(-?[\d,]+(?:\.\d+)?)"):
        m = re.findall(pat, text)
        if m:
            return _num(m[-1])
    nums = NUM.findall(text)
    return _num(nums[-1]) if nums else None


def gsm8k_ok(item: dict, text: str) -> bool:
    g, p = gsm8k_gold(item), gsm8k_pred(text)
    return g is not None and p is not None and abs(g - p) < 1e-6


def gsm8k_prompt(item: dict) -> str:
    return (item["question"] + "\n\nSolve it, then give the final answer on the last line as "
            "'#### <number>'.")


def he_prompt(item: dict) -> str:
    return ("Complete this Python function. Reply with the full function in one ```python code "
            "block, nothing else.\n\n```python\n" + item["prompt"] + "```")


def he_code(item: dict, text: str) -> str:
    """The model's code: the last python block if any, else the raw text. If it did not repeat
    the signature, it is treated as a body and appended to the prompt."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text or "", re.S)
    code = blocks[-1] if blocks else (text or "")
    if f"def {item['entry_point']}" not in code:
        code = item["prompt"] + code
    return code


def _limits():  # pragma: no cover - runs in the child
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))


def he_ok(item: dict, text: str, timeout: float = 15.0) -> bool:
    """Run the item's own tests on the model's code in a sandboxed subprocess (isolated
    interpreter -I, empty env, temp cwd, CPU/memory/process/file-size limits, wall timeout)."""
    prog = (he_code(item, text) + "\n\n" + item["test"] + f"\n\ncheck({item['entry_point']})\n")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.py"
        f.write_text(prog)
        try:
            r = subprocess.run([sys.executable, "-I", str(f)], cwd=d, env={"PATH": "/usr/bin:/bin"},
                               capture_output=True, timeout=timeout,
                               preexec_fn=_limits if os.name == "posix" else None)
        except subprocess.TimeoutExpired:
            return False
    return r.returncode == 0


GRADERS = {"gsm8k": (gsm8k_prompt, gsm8k_ok), "humaneval": (he_prompt, he_ok)}


def summarize(name: str, rounds: list[dict]) -> dict:
    """rounds: [{arm, rep, joules, n, correct, errors, truncated, p50_s, p99_s}] -> paired result."""
    from wattnot.context_saver.prefix_cache import paired_change

    def arm(a):
        return sorted((r for r in rounds if r["arm"] == a), key=lambda r: r["rep"])

    st, cp = arm("stock"), arm("cap")

    def jpc(r):
        return r["joules"] / r["correct"] if r["correct"] else None

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    out = {"set": name, "label": "MEASURED", "source": SOURCES[name], "reps": len(st)}
    for a, rs in (("stock", st), ("cap", cp)):
        out[a] = {"pass_pct": mean([100 * r["correct"] / r["n"] for r in rs]),
                  "j_per_correct": mean([jpc(r) for r in rs]),
                  "j_per_req": mean([r["joules"] / r["n"] for r in rs]),
                  "p50_s": mean([r["p50_s"] for r in rs]), "p99_s": mean([r["p99_s"] for r in rs]),
                  "errors": sum(r["errors"] for r in rs), "truncated": sum(r["truncated"] for r in rs)}
    out["j_per_correct_change_pct"] = paired_change([jpc(r) for r in st], [jpc(r) for r in cp])
    out["j_per_req_change_pct"] = paired_change([r["joules"] / r["n"] for r in st],
                                                [r["joules"] / r["n"] for r in cp])
    out["p99_change_pct"] = paired_change([r["p99_s"] for r in st], [r["p99_s"] for r in cp])
    out["note"] = ("energy is GPU-only NVML (MEASURED); with few rounds the 95% interval is wide; "
                   "errors and truncations count as wrong on both arms")
    return out


def order(reps: int) -> list[tuple[str, int]]:
    """Alternating, counterbalanced: S C | C S | S C ... so neither arm always goes first."""
    seq = []
    for r in range(reps):
        seq += [("stock", r), ("cap", r)] if r % 2 == 0 else [("cap", r), ("stock", r)]
    return seq


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))] if xs else None


def run_real(o) -> dict:  # pragma: no cover - needs the GPU host
    import urllib.request

    import pynvml
    pynvml.nvmlInit()
    h = pynvml.nvmlDeviceGetHandleByIndex(0)
    url = f"{o.base_url}/chat/completions"

    def setcap(w):
        subprocess.run(["nvidia-smi", "-pl", str(w)], check=True, capture_output=True)
        got = pynvml.nvmlDeviceGetPowerManagementLimit(h) / 1000
        if abs(got - w) > 1:
            raise RuntimeError(f"cap read-back {got} W != {w} W")
        return got

    def ask(prompt):
        body = {"model": o.model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": o.max_tokens, "temperature": 0, "seed": 1234}
        req = urllib.request.Request(url, json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=o.req_timeout) as r:
                j = json.load(r)
            ch = j["choices"][0]
            return ch["message"].get("content") or "", ch.get("finish_reason"), time.time() - t0, 0
        except Exception:  # noqa: BLE001 - an error grades as wrong on its arm
            return "", "error", time.time() - t0, 1

    out = Path(o.out)
    out.mkdir(parents=True, exist_ok=True)
    sets = {n: load(n) for n in o.sets}
    rounds, raw = {n: [] for n in sets}, []
    # identical warm-up before any metered arm (not counted)
    setcap(700)
    with ThreadPoolExecutor(o.concurrency) as ex:
        list(ex.map(ask, [GRADERS["gsm8k"][0](x) for x in sets.get("gsm8k", load("gsm8k"))[:4]]))
    for a, rep in order(o.reps):
        cap = 700 if a == "stock" else o.cap
        got = setcap(cap)
        time.sleep(o.settle_s)
        for n, items in sets.items():
            pf, gf = GRADERS[n]
            e0 = pynvml.nvmlDeviceGetTotalEnergyConsumption(h)
            t0 = time.time()
            with ThreadPoolExecutor(o.concurrency) as ex:
                res = list(ex.map(ask, [pf(x) for x in items]))
            wall = time.time() - t0
            joules = (pynvml.nvmlDeviceGetTotalEnergyConsumption(h) - e0) / 1000.0
            ok = [bool(gf(x, r[0])) if r[1] != "error" else False for x, r in zip(items, res)]
            lat = [r[2] for r in res]
            rounds[n].append({"arm": a, "rep": rep, "cap_w": got, "joules": joules, "wall_s": wall,
                              "n": len(items), "correct": sum(ok),
                              "errors": sum(r[3] for r in res),
                              "truncated": sum(r[1] == "length" for r in res),
                              "p50_s": _pct(lat, 0.5), "p99_s": _pct(lat, 0.99)})
            raw.append({"set": n, "arm": a, "rep": rep,
                        "answers": [{"id": x.get("task_id", i), "ok": k, "finish": r[1], "text": r[0]}
                                    for i, (x, r, k) in enumerate(zip(items, res, ok))]})
            print(f"[public] {n} {a} rep{rep} cap {got:.0f} W: {sum(ok)}/{len(items)} correct, "
                  f"{joules:.0f} J, {wall:.0f} s", flush=True)
    setcap(700)   # leave the card at stock
    summ = {"label": "MEASURED", "cap_w": o.cap, "stock_w": 700, "prefix_caching": "off (both arms)",
            "sets": {n: summarize(n, rs) for n, rs in rounds.items()}, "rounds": rounds}
    (out / "public_bench_summary.json").write_text(json.dumps(summ, indent=1))
    (out / "public_bench_raw.json").write_text(json.dumps(raw))
    return summ


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, required=True)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sets", nargs="+", default=["gsm8k", "humaneval"])
    ap.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--model", default="meta-models/Muse-Glimmer-30B")
    ap.add_argument("--max-tokens", type=int, default=3072)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--req-timeout", type=float, default=600)
    ap.add_argument("--settle-s", type=float, default=5)
    o = ap.parse_args(argv)
    if o.cap < 294:
        print("refusing a cap below the ~294 W floor", file=sys.stderr)
        return 2
    s = run_real(o)
    print(json.dumps({n: {k: v for k, v in d.items() if k != "source"} for n, d in s["sets"].items()},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
