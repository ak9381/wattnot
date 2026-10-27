#!/usr/bin/env python3
"""Tune the adaptive top-k `mass` on CPU so token agreement clears the 95% gate.

    PYTHONPATH=.:src python scripts/tune_adaptive_mass.py --model /path/to/granite --out DIR

Why this exists: RUN2 measured 94.5% agreement at mass 0.9 on the H100 (484 of 512 positions),
under the 95% gate. Agreement does not need a GPU, so the threshold can be tuned here:

1. The normal-top-k model writes a greedy continuation for each prompt (cached to disk).
2. For each mass, the hooked model re-reads that text (teacher forced) and its next-token argmax
   is compared with the normal model's. Same method as `HFBackend.agreement`, same hook code.
3. Three prompt sets: `repro` (the 16 prompts RUN2 used, 32 new tokens: how far is the CPU from
   the H100 number?), `tune` (proof-v1 prompts, never used by RUN2) and `holdout` (the other
   public replay/flex prompts). The mass is picked on `tune`, then checked on the other two.
4. Per-prompt results are kept so a bootstrap interval over prompts can be printed.

Runs on bf16 weights like the H100 run. Everything it prints is MEASURED on this CPU, for token
agreement and active experts only. Energy is NOT measured here; the saving at the chosen mass is
an ESTIMATE scaled from RUN2's one measured point.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

REPRO_PROMPTS, REPRO_TOKENS = 16, 32  # what RUN2 evaluated (AdaptiveConfig defaults)
RUN2 = {"mass": 0.9, "agreement_pct": 94.53125, "active_experts": 6.81127695686901, "top_k": 8,
        "j_per_token_change_pct": -6.606486875105937}
MASSES = (0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99)


def prompt_sets(n_tune: int) -> dict[str, list[str]]:
    from wattnot.flex import load as ld
    from wattnot.runner.workload import load_replay_set, load_workload, workloads_dir

    base = [i.prompt for i in load_replay_set()] + [p.prompt for p in ld.load_prompts(
        ld.DEFAULT_PROMPTS)]
    pool = [i.prompt for i in load_workload(workloads_dir() / "proof-v1.jsonl", kind="proof")
            if len(i.prompt) <= 500]
    rnd = random.Random(11)
    rnd.shuffle(pool)
    return {"repro": base[:REPRO_PROMPTS], "tune": pool[:n_tune], "holdout": base[REPRO_PROMPTS:]}


def boot_ci(per_prompt: list[tuple[int, int]], n: int = 2000, seed: int = 5) -> tuple[float, float]:
    """95% bootstrap interval of pooled agreement, resampling prompts."""
    rnd = random.Random(seed)
    vals = []
    for _ in range(n):
        s = [per_prompt[rnd.randrange(len(per_prompt))] for _ in per_prompt]
        vals.append(100 * sum(a for a, _ in s) / max(1, sum(t for _, t in s)))
    vals.sort()
    return vals[int(0.025 * n)], vals[int(0.975 * n)]


def report(tune: dict) -> dict:
    """The recommendation and the table (no torch needed): used after a sweep and on its own."""
    from wattnot.moe.adaptive_tune import pick_mass, summary_rows

    pick = pick_mass(tune["sets"])
    rows = summary_rows(tune["sets"], tune["top_k"], tune["run2_h100"])
    print(f"{'mass':>5} {'worst set':>10} {'lo 95%':>7} {'experts':>8} {'cut %':>6} "
          f"{'saving (ESTIMATE)':>18}")
    for r in rows:
        mark = "  <- pick" if r["mass"] == pick.get("mass") else ""
        print(f"{r['mass']:>5g} {r['worst_set_agreement_pct']:>9.2f}% "
              f"{r['worst_set_ci95_low']:>6.1f} {r['active_experts']:>8.2f} "
              f"{r['expert_cut_pct']:>6.1f} {r['saving_estimate_pct']:>17.1f}%{mark}")
    return {"pick": pick, "table": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--report", type=Path,
                    help="print the pick from an existing tune.json and exit")
    ap.add_argument("--model")
    ap.add_argument("--out", type=Path, default=Path("data/adaptive-tune"))
    ap.add_argument("--masses", default=",".join(map(str, MASSES)))
    ap.add_argument("--tune-prompts", type=int, default=48)
    ap.add_argument("--tune-tokens", type=int, default=48)
    ap.add_argument("--holdout-tokens", type=int, default=48)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--batch", type=int, default=8)
    a = ap.parse_args()
    if a.report:
        report(json.loads(a.report.read_text()))
        return 0
    if not a.model:
        ap.error("--model is required (or --report)")
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from wattnot.moe.hf_backend import _router_of, find_moe_layers, install_hooks

    torch.set_num_threads(a.threads)
    a.out.mkdir(parents=True, exist_ok=True)
    masses = [float(x) for x in a.masses.split(",")]
    tok = AutoTokenizer.from_pretrained(a.model)
    tok.padding_side = "left"
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16).eval()
    top_k = int(_router_of(find_moe_layers(model)[0]).top_k)
    st, _ = install_hooks(model, torch)
    sets = prompt_sets(a.tune_prompts)
    tokens = {"repro": REPRO_TOKENS, "tune": a.tune_tokens, "holdout": a.holdout_tokens}
    t0 = time.time()
    out: dict = {"label": "MEASURED (CPU, bf16): agreement and active experts only",
                 "model": a.model.split("/")[-1], "top_k": top_k, "run2_h100": RUN2, "sets": {}}
    for name, prompts in sets.items():
        n_new = tokens[name]
        cache = a.out / f"ref-{name}.json"
        if cache.exists():
            refs = json.loads(cache.read_text())
        else:  # the normal-top-k model's own greedy text, in batches
            st.mass = None
            refs = []
            for i in range(0, len(prompts), a.batch):
                enc = tok(prompts[i:i + a.batch], return_tensors="pt", padding=True)
                with torch.no_grad():
                    full = model.generate(**enc, max_new_tokens=n_new, min_new_tokens=n_new,
                                          do_sample=False, pad_token_id=tok.pad_token_id)
                for j in range(full.shape[0]):
                    ids = tok(prompts[i + j], return_tensors="pt")["input_ids"][0].tolist()
                    new = full[j, enc["input_ids"].shape[1]:].tolist()
                    refs.append({"prompt_ids": ids, "new_ids": new})
                print(f"[{name}] reference text {min(i + a.batch, len(prompts))}/{len(prompts)}"
                      f" ({time.time() - t0:.0f}s)", flush=True)
            cache.write_text(json.dumps(refs))
        # normal top-k next-token choice at every generated position
        ref_arg, seqs = [], []
        for r in refs:
            seq = torch.tensor([r["prompt_ids"] + r["new_ids"]])
            plen = len(r["prompt_ids"])
            st.mass = None
            with torch.no_grad():
                ref_arg.append(model(seq).logits[0, plen - 1:-1].float().argmax(-1))
            seqs.append((seq, plen))
        res = {}
        for m in masses:
            per = []
            st.reset(torch)
            for (seq, plen), ra in zip(seqs, ref_arg, strict=True):
                st.mass = m
                with torch.no_grad():
                    got = model(seq).logits[0, plen - 1:-1].float().argmax(-1)
                per.append((int((got == ra).sum()), int(ra.numel())))
            st.mass = None
            act = float(st.used.item()) / max(1, st.slots)
            tot_a, tot_n = sum(p[0] for p in per), sum(p[1] for p in per)
            lo, hi = boot_ci(per)
            res[f"{m:g}"] = {"agreement_pct": 100 * tot_a / tot_n, "ci95": [lo, hi],
                             "positions": tot_n, "active_experts": act, "per_prompt": per}
            print(f"[{name}] mass {m:g}: agreement {100 * tot_a / tot_n:.2f}% "
                  f"(95% CI {lo:.1f}-{hi:.1f}) of {tot_n}, experts {act:.2f}/{top_k} "
                  f"({time.time() - t0:.0f}s)", flush=True)
        out["sets"][name] = {"prompts": len(prompts), "new_tokens": n_new, "by_mass": res}
        (a.out / "tune.json").write_text(json.dumps(out, indent=1))
    out["seconds"] = round(time.time() - t0)
    out["recommendation"] = report(out)
    (a.out / "tune.json").write_text(json.dumps(out, indent=1))
    print("done", out["seconds"], "s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
