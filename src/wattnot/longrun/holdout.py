"""Sealed holdout: the full WattNot stack vs stock, run exactly ONCE on prompts never used in tuning.

Why: every gate/pass so far (proof-v1 sweep gate, tiers-v1, GSM8K first 300 for LangChain and the
public bench, HumanEval 164, the eye/spoken/agent-waste sets) was looked at many times while the
savers were picked. Re-using a set adaptively wears it out (Dwork et al., reusable holdout). So we
keep the iterative gate for development and touch this set once, at the end, on the frozen stack.

Steps
  build   draw the pool from GSM8K test (openai/grade-school-math, MIT) minus every item any tuning
          set used, write workloads/holdout-gsm8k-v1.jsonl + a manifest, and SEAL it (sha256 of
          the file, each item, the frozen stack config, and the manifest itself).
  verify  recompute every hash; refuse on any mismatch.
  run     (GPU host) verify, claim the one-shot marker, then paired arms on the same prompts:
          stock (700 W, savers off) vs full stack (frozen cap + savers). Grade, McNemar, MDE.
  mde     print the paired minimum detectable effect (no GPU).

Labels: energy and quality from the run are MEASURED; the MDE before the run is an ESTIMATE
(it needs an assumed discordant rate); after the run the MDE uses the run's own discordant rate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
from math import comb, sqrt
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "data/holdout/gsm8k_test_full1319.jsonl"
SOURCE_SHA256 = "3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14"
SOURCE_REPO = "https://github.com/openai/grade-school-math (grade_school_math/data/test.jsonl, MIT)"
WORKLOAD = ROOT / "workloads/holdout-gsm8k-v1.jsonl"
MANIFEST = ROOT / "workloads/holdout-gsm8k-v1.manifest.json"
SEAL = ROOT / "workloads/holdout-gsm8k-v1.seal"
SEED = 20261103
# Frozen before the seal: the tuned winner and the savers. Changing any of these breaks the seal.
FROZEN_STACK = {
    "cap_w": 375,                     # pass-2 sweep winner (MEASURED -28.7% J/request, 3 Nov)
    "stock_w": 700,
    "savers": {"reasoning_effort": "low"},   # thinking budget as applied (reasoning_caps.json)
    "max_tokens": 1536, "temperature": 0, "seed": 1234,
}


def apply_stack(body: dict, stack: dict = FROZEN_STACK) -> dict:
    """The holdout's request transform for the stack arm: its savers (reasoning_effort low) and its
    output budget. The gateway's thinking saver calls this, so both apply the same tested change."""
    out = {**body, **stack["savers"]}
    asked = out.pop("max_tokens", None) or out.get("max_completion_tokens")
    out["max_completion_tokens"] = min(int(asked), int(stack["max_tokens"])) if asked else int(stack["max_tokens"])
    return out


# Every GSM8K test item a tuning or gate set has touched.
USED_PREFIX = 300   # gsm8k_test_first60/first300 (LangChain p2, public bench, matrix rounds)
SCAN_DIRS = ("workloads", "data")   # text scan: any other file quoting a GSM8K question


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(p.read_bytes())


def _load_source(src: Path = SOURCE) -> list[dict]:
    if sha256_file(src) != SOURCE_SHA256:
        raise SystemExit(f"holdout: source {src} sha256 mismatch; refusing")
    return [json.loads(ln) for ln in src.read_text().splitlines() if ln.strip()]


def used_indices(root: Path = ROOT) -> set[int]:
    used = set(range(USED_PREFIX))
    for ln in (root / "workloads/proof-v1.jsonl").read_text().splitlines():
        if ln.strip():
            s = json.loads(ln).get("source", "")
            if s.startswith("gsm8k-test-"):
                used.add(int(s.rsplit("-", 1)[1]))
    return used


def _scan_blob(root: Path, skip: tuple[Path, ...]) -> str:
    parts = []
    for d in SCAN_DIRS:
        for p in sorted((root / d).rglob("*")):
            if p.suffix in (".jsonl", ".json") and p.is_file() and p.resolve() not in skip \
                    and "gsm8k_test_first" not in p.name and not p.name.startswith("holdout-") and p.stat().st_size < 50_000_000:
                try:
                    parts.append(p.read_text(errors="ignore"))
                except OSError:
                    pass
    return "\n".join(parts)


def draw_pool(rows: list[dict], used: set[int], blob: str, max_steps: int | None) -> list[int]:
    pool = []
    for i, r in enumerate(rows):
        if i in used:
            continue
        q = r["question"][:60]
        if q in blob or json.dumps(q)[1:-1] in blob:
            continue
        if max_steps is not None and len(re.findall(r"<<", r["answer"])) > max_steps:
            continue
        pool.append(i)
    return pool


def text_hits(rows: list[dict], used: set[int], blob: str) -> list[int]:
    return [i for i, r in enumerate(rows) if i not in used
            and (r["question"][:60] in blob or json.dumps(r["question"][:60])[1:-1] in blob)]


def stack_sha(stack: dict = FROZEN_STACK) -> str:
    return sha256_bytes(json.dumps(stack, sort_keys=True).encode())


def build(n: int | None = None, max_steps: int | None = None, root: Path = ROOT,
          src: Path = SOURCE, workload: Path = WORKLOAD, manifest: Path = MANIFEST,
          seal: Path = SEAL) -> dict:
    if seal.exists():
        raise SystemExit(f"holdout: {seal} already sealed; a new draw needs a new version name")
    rows = _load_source(src)
    used = used_indices(root)
    blob = _scan_blob(root, skip=(src.resolve(), workload.resolve(), manifest.resolve()))
    pool = draw_pool(rows, used, blob, max_steps)
    rng = random.Random(SEED)
    pick = sorted(rng.sample(pool, min(n, len(pool))) if n else pool)
    items = [{"id": f"ho-{i:04d}", "source": f"gsm8k-test-{i}", "question": rows[i]["question"],
              "answer": rows[i]["answer"]} for i in pick]
    workload.write_text("".join(json.dumps(x) + "\n" for x in items))
    man = {
        "spec": "wattnot.holdout-manifest/1", "name": workload.stem,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": {"repo": SOURCE_REPO, "file": str(src.relative_to(root)) if src.is_relative_to(root) else str(src),
                   "sha256": SOURCE_SHA256, "rows": len(rows)},
        "excluded": {"first_n": USED_PREFIX, "proof_v1_indices": sorted(i for i in used if i >= USED_PREFIX),
                     "text_hits_in_repo": text_hits(rows, used, blob)},
        "max_steps": max_steps, "seed": SEED, "pool": len(pool), "n": len(items),
        "workload_sha256": sha256_file(workload),
        "item_sha256": {x["id"]: sha256_bytes(json.dumps(x, sort_keys=True).encode()) for x in items},
        "frozen_stack": FROZEN_STACK, "frozen_stack_sha256": stack_sha(),
        "rule": "run exactly once on the frozen stack; never used to pick or tune anything",
    }
    manifest.write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    seal.write_text(sha256_file(manifest) + "\n")
    return man


def verify(workload: Path = WORKLOAD, manifest: Path = MANIFEST, seal: Path = SEAL) -> dict:
    """Recompute every hash. Raises SystemExit on any mismatch (never runs on a broken seal)."""
    if not seal.exists():
        raise SystemExit("holdout: no seal; run `build` first")
    if sha256_file(manifest) != seal.read_text().strip():
        raise SystemExit("holdout: manifest changed after sealing")
    man = json.loads(manifest.read_text())
    if sha256_file(workload) != man["workload_sha256"]:
        raise SystemExit("holdout: workload changed after sealing")
    if man["frozen_stack_sha256"] != stack_sha():
        raise SystemExit("holdout: the stack config changed after sealing")
    items = [json.loads(ln) for ln in workload.read_text().splitlines() if ln.strip()]
    for x in items:
        if man["item_sha256"].get(x["id"]) != sha256_bytes(json.dumps(x, sort_keys=True).encode()):
            raise SystemExit(f"holdout: item {x['id']} changed after sealing")
    if len(items) != man["n"]:
        raise SystemExit("holdout: item count changed after sealing")
    return {"ok": True, "n": man["n"], "manifest_sha256": seal.read_text().strip()}


# ---- statistics (paired, same prompts both arms) ----------------------------------------------

def mcnemar_n(psi: float, delta: float, alpha: float = 0.05, power: float = 0.8,
              two_sided: bool = True) -> int:
    """Prompts needed to detect a quality drop `delta` with discordant-pair rate `psi`
    (Connor 1987 normal approximation)."""
    if not 0 < delta <= psi <= 1:
        raise ValueError("need 0 < delta <= psi <= 1")
    nd = NormalDist()
    za = nd.inv_cdf(1 - alpha / (2 if two_sided else 1))
    zb = nd.inv_cdf(power)
    return int(-(-((za * sqrt(psi) + zb * sqrt(psi - delta * delta)) ** 2) // (delta * delta)))


def mcnemar_mde(n: int, psi: float, alpha: float = 0.05, power: float = 0.8,
                two_sided: bool = True) -> float:
    """Smallest drop (fraction, e.g. 0.02 = 2 points) detectable with n paired prompts."""
    lo, hi = 1e-6, psi
    if mcnemar_n(psi, hi, alpha, power, two_sided) > n:
        return float("nan")   # not even an all-one-way shift is detectable
    for _ in range(80):
        mid = (lo + hi) / 2
        if mcnemar_n(psi, mid, alpha, power, two_sided) > n:
            lo = mid
        else:
            hi = mid
    return hi


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p: b = stock right / stack wrong, c = stack right / stock wrong."""
    m = b + c
    if m == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(m, i) for i in range(k + 1)) / 2 ** m)


def paired_summary(stock_ok: list[bool], stack_ok: list[bool]) -> dict:
    assert len(stock_ok) == len(stack_ok) and stock_ok
    n = len(stock_ok)
    b = sum(s and not t for s, t in zip(stock_ok, stack_ok))
    c = sum(t and not s for s, t in zip(stock_ok, stack_ok))
    psi = (b + c) / n
    diff = (c - b) / n   # stack minus stock
    se = sqrt(max(psi - diff * diff, 0) / n)
    z = NormalDist().inv_cdf(0.975)
    return {"n": n, "stock_pct": 100 * sum(stock_ok) / n, "stack_pct": 100 * sum(stack_ok) / n,
            "diff_pts": 100 * diff, "ci95_pts": [100 * (diff - z * se), 100 * (diff + z * se)],
            "discordant": {"stock_only": b, "stack_only": c, "rate": psi},
            "mcnemar_exact_p": mcnemar_exact_p(b, c),
            "mde_pts_80pct_power": 100 * mcnemar_mde(n, max(psi, 1.0 / n)) if psi else None}


def report_line(s: dict, energy_pct: float | None) -> str:
    e = f"{energy_pct:+.1f}% J/request, " if energy_pct is not None else ""
    lo, hi = s["ci95_pts"]
    mde = s.get("mde_pts_80pct_power")
    mde_txt = f"; detectable drop {mde:.1f} pts at 95%/80% power" if mde else ""
    return (f"Sealed holdout (MEASURED, run once, {s['n']} prompts the system never saw): "
            f"{e}quality {s['stock_pct']:.1f}% -> {s['stack_pct']:.1f}% "
            f"({s['diff_pts']:+.1f} pts, 95% CI {lo:+.1f} to {hi:+.1f}, McNemar p={s['mcnemar_exact_p']:.2f})"
            f"{mde_txt}")


# ---- the one-shot GPU run ---------------------------------------------------------------------

def claim_once(out: Path, manifest_sha: str) -> Path:
    """Create the one-shot marker atomically; a second run on the same seal is refused."""
    out.mkdir(parents=True, exist_ok=True)
    marker = out / "OPENED"
    try:
        with open(marker, "x") as f:
            f.write(json.dumps({"manifest_sha256": manifest_sha, "at": time.time()}) + "\n")
    except FileExistsError:
        raise SystemExit(f"holdout: {marker} exists; the sealed set was already opened once")
    return marker


def run(url: str, out: Path, model: str, workers: int = 32, fake: bool = False,
        req_timeout: float = 600.0) -> dict:
    v = verify()
    claim_once(out, v["manifest_sha256"])
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import public_bench_ab as pb   # noqa: E402 - the same GSM8K grader as every other block
    items = [json.loads(ln) for ln in WORKLOAD.read_text().splitlines() if ln.strip()]
    st = FROZEN_STACK
    res: dict = {"spec": "wattnot.holdout-run/1", "label": "MOCK" if fake else "MEASURED",
                 "manifest_sha256": v["manifest_sha256"], "stack": st, "arms": {}}
    arms = (("stock", st["stock_w"], {}), ("stack", st["cap_w"], st["savers"]))
    if fake:
        rng = random.Random(1)
        oks = {a: [rng.random() < 0.93 for _ in items] for a, *_ in arms}
        for a, w, _ in arms:
            res["arms"][a] = {"cap_w": w, "joules": None, "wall_s": 0.0}
    else:
        import subprocess
        import urllib.request
        from concurrent.futures import ThreadPoolExecutor
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)

        def ask(prompt: str, extra: dict) -> str:
            body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": st["max_tokens"], "temperature": st["temperature"],
                    "seed": st["seed"], **extra}
            req = urllib.request.Request(url, json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=req_timeout) as r:
                    return json.load(r)["choices"][0]["message"].get("content") or ""
            except Exception:  # noqa: BLE001 - an error grades as wrong on its arm
                return ""
        oks = {}
        raw = out / "raw"
        raw.mkdir(exist_ok=True)
        for a, w, extra in arms:
            subprocess.run(["nvidia-smi", "-pl", str(w)], check=True, capture_output=True)
            got = pynvml.nvmlDeviceGetPowerManagementLimit(h) / 1000
            if abs(got - w) > 1:
                raise SystemExit(f"cap read-back {got} W != {w} W")
            e0, t0 = pynvml.nvmlDeviceGetTotalEnergyConsumption(h), time.time()
            with ThreadPoolExecutor(workers) as ex:
                texts = list(ex.map(lambda x: ask(pb.gsm8k_prompt(x), extra), items))
            j = (pynvml.nvmlDeviceGetTotalEnergyConsumption(h) - e0) / 1000.0
            oks[a] = [pb.gsm8k_ok(x, t) for x, t in zip(items, texts)]
            (raw / f"{a}.jsonl").write_text("".join(json.dumps({"id": x["id"], "text": t}) + "\n"
                                                    for x, t in zip(items, texts)))
            res["arms"][a] = {"cap_w": w, "joules": j, "j_per_req": j / len(items),
                              "wall_s": time.time() - t0}
        subprocess.run(["nvidia-smi", "-pl", str(st["stock_w"])], capture_output=True)
    s = paired_summary(oks["stock"], oks["stack"])
    js, jc = res["arms"]["stock"].get("joules"), res["arms"]["stack"].get("joules")
    energy = 100 * (jc - js) / js if js and jc else None
    res.update(quality=s, energy_change_pct=energy, report=report_line(s, energy))
    if fake:
        res["report"] = res["report"].replace("MEASURED", "MOCK")
    (out / "holdout_result.json").write_text(json.dumps(res, indent=1) + "\n")
    print(res["report"])
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--n", type=int, default=None, help="draw n from the pool (default: all)")
    b.add_argument("--max-steps", type=int, default=None)
    sub.add_parser("verify")
    m = sub.add_parser("mde")
    m.add_argument("--n", type=int, required=True)
    m.add_argument("--psi", type=float, nargs="+", default=[0.024, 0.04, 0.06, 0.08, 0.113])
    r = sub.add_parser("run")
    r.add_argument("--url", required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--model", default="meta-models/Muse-Glimmer-30B")
    r.add_argument("--workers", type=int, default=32)
    r.add_argument("--fake", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        man = build(a.n, a.max_steps)
        print(json.dumps({k: man[k] for k in ("n", "pool", "workload_sha256")}))
    elif a.cmd == "verify":
        print(json.dumps(verify()))
    elif a.cmd == "mde":
        for psi in a.psi:
            print(f"psi={psi:.3f} n={a.n}: MDE {100 * mcnemar_mde(a.n, psi):.2f} pts "
                  f"(ESTIMATE; 2-pt drop needs {mcnemar_n(psi, 0.02)} prompts)")
    else:
        run(a.url, a.out, a.model, a.workers, a.fake)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
