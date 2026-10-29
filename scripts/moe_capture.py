#!/usr/bin/env python3
# HISTORICAL (RUN2, Oct 27-30): kept as the record of the earlier test. It used non-Meta
# models (Granite / Qwen) and is not called by the long run. Every model WattNot runs now is a Meta model.
"""Record which experts a Granite MoE model uses, token by token, as a JSONL stream.

It runs the real model (CPU or GPU) on a fixed set of tagged prompts and writes one `tick` line
per model forward pass: the prefill of a prompt, then one tick per generated token. Each tick says
which (layer, expert) units fired and how many times, so the dashboard can play it back or follow
the file while it grows.

Two arms run on every prompt: normal top-k ("off") and adaptive top-k ("on", drops the tail
experts once `--mass` of the router weight is reached, same rule as wattnot.moe).

    python scripts/moe_capture.py --model /path/to/granite-3.1-3b-a800m --out cap.jsonl

Needs torch and transformers. Energy is not measured here: if you pass `--meter nvml` on a GPU box
each tick carries `energy_j` from the NVML counter, otherwise the field is absent and the
dashboard says so.
"""
from __future__ import annotations

import argparse
import json
import math
import time

TOPICS = {
    "code": [
        "Write a Python function that parses a CSV of GPU power readings and returns the mean per hour.",  # noqa: E501
        "Write a Prometheus alert rule that fires when a GPU stays above 85 percent of its power limit.",  # noqa: E501
        "Show a bash one-liner that lists the five largest log files in a directory.",
        "Write a SQL query that returns the top three models by energy per request.",
    ],
    "gpu-ops": [
        "Explain why p99 latency rises when a GPU power cap is lowered, in two sentences.",
        "Compare FP8 and INT4 quantization for inference throughput and power draw.",
        "List three ways to cut idle power on a rack of inference servers.",
        "Why does batch size change joules per token on an H100?",
    ],
    "science": [
        "Explain in two sentences why the sky is blue.",
        "What does a mitochondrion do in a cell?",
        "Why do tides have two highs a day?",
        "How does a heat pump move heat from cold air to warm air?",
    ],
    "writing": [
        "Write a friendly two-line note thanking a teammate for fixing a bug.",
        "Rewrite this to sound calmer: your report is late and it matters.",
        "Write a short product tagline for a tool that saves power.",
        "Draft a polite email subject line asking for a meeting next week.",
    ],
    "math": [
        "What is 17 times 24? Show the steps.",
        "Solve for x: 3x + 7 = 31.",
        "What is the derivative of x squared times sin x?",
        "A GPU uses 400 watts for 90 seconds. How many watt-hours is that?",
    ],
    "history": [
        "Who built the first practical steam engine and when?",
        "Why did the Roman Empire build aqueducts?",
        "Summarize the cause of the 1973 oil crisis in two sentences.",
        "What was the printing press's effect on literacy in Europe?",
    ],
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--new-tokens", type=int, default=20)
    ap.add_argument("--mass", type=float, default=0.9)
    ap.add_argument("--per-topic", type=int, default=4)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--meter", choices=["none", "nvml"], default="none")
    ap.add_argument("--arms", default="off,on", help="comma list: off, on (adaptive top-k)")
    ap.add_argument("--where", default="cpu-local", help="label stored in the file, e.g. cpu-local")
    args = ap.parse_args()

    import torch
    import torch.nn.functional as F  # noqa: N812
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(args.threads)
    tok = AutoTokenizer.from_pretrained(args.model)
    dtype = torch.bfloat16
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=dtype).to(args.device).eval()
    cfg = model.config
    L, E, K = cfg.num_hidden_layers, cfg.num_local_experts, cfg.num_experts_per_tok
    flops_per_routed_token = 2 * (cfg.hidden_size * 2 * cfg.intermediate_size
                                  + cfg.intermediate_size * cfg.hidden_size)

    meter = None
    if args.meter == "nvml":
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        meter = lambda: pynvml.nvmlDeviceGetTotalEnergyConsumption(handle) / 1000.0  # noqa: E731

    layers = [m for m in model.modules() if hasattr(m, "router") and hasattr(m, "experts")
              and hasattr(m.experts, "gate_up_proj")]
    assert len(layers) == L, "unexpected MoE layout"

    class S:  # shared hook state
        mass = None
        cur_ids = None
        rec = None

    def new_rec():
        return {"units": torch.zeros(L, E, dtype=torch.long), "kept": 0, "slots": 0,
                "ent": 0.0, "top1": 0.0, "pairs": [], "upairs": []}

    def split(out, h):
        logits = next(o for o in out if o.is_floating_point() and o.shape[-1] == E)
        idx = next(o for o in out if not o.is_floating_point())
        w = next(o for o in out if o is not logits and o.is_floating_point())
        return logits, w, idx

    def hook_router(lid, orig):
        def fwd(h):
            out = orig(h)
            logits, w, idx = split(out, h)
            wf = w.float()
            if S.mass is None:
                keep = torch.ones_like(wf, dtype=torch.bool)
            else:
                cs = torch.cumsum(wf / wf.sum(-1, keepdim=True), -1)
                keep = torch.cat([torch.ones_like(cs[:, :1], dtype=torch.bool),
                                  cs[:, :-1] < S.mass], -1)
            wk = wf * keep
            if S.mass is not None:
                wk = wk / wk.sum(-1, keepdim=True)
            r = S.rec
            if r is not None:
                p = torch.softmax(logits.float(), -1)
                r["ent"] += (-(p * torch.log(p + 1e-12)).sum(-1) / math.log(E)).sum().item()
                r["top1"] += (wf.max(-1).values / wf.sum(-1)).sum().item()
                r["kept"] += int(keep.sum())
                r["slots"] += keep.shape[0]
                ei = idx[keep]
                r["units"][lid] += torch.bincount(ei, minlength=E).cpu()
                tokrep = S.cur_ids.reshape(-1, 1).expand_as(idx)[keep]
                r["pairs"].append(ei * 100000 + tokrep)
                r["upairs"].append((lid * E + ei) * 100000 + tokrep)
            sent = torch.where(keep, idx, torch.full_like(idx, E))
            new = list(out)
            for i, o in enumerate(out):
                if o is w:
                    new[i] = wk.type_as(w)
                elif o is idx:
                    new[i] = sent
            return tuple(new)
        return fwd

    def hook_experts(mod, orig):
        def fwd(h, idx, w):
            out = torch.zeros_like(h)
            for e in torch.unique(idx).tolist():
                if e >= E:
                    continue
                pos, t = torch.where(idx.T == e)
                x = h[t]
                g, u = F.linear(x, mod.gate_up_proj[e]).chunk(2, -1)
                y = F.linear(mod.act_fn(g) * u, mod.down_proj[e]) * w[t, pos, None]
                out.index_add_(0, t, y.to(out.dtype))
            return out
        return fwd

    for i, lay in enumerate(layers):
        lay.router.forward = hook_router(i, lay.router.forward)
        lay.experts.forward = hook_experts(lay.experts, lay.experts.forward)

    unit_tok: dict[int, int] = {}
    state = {"step": None, "arm": None, "seq": 0, "last": time.time(), "phase": "prefill"}
    fout = open(args.out, "w")

    def emit(obj):
        fout.write(json.dumps(obj, separators=(",", ":")) + "\n")
        fout.flush()

    def pre(_m, a, kw):
        ids = kw.get("input_ids") if kw.get("input_ids") is not None else a[0]
        S.cur_ids = ids.detach().cpu()
        S.rec = new_rec()
        state["e0"] = meter() if meter else None

    def post(_m, a, kw, _out):
        r = S.rec
        S.rec = None
        now = time.time()
        dt = now - state["last"]
        state["last"] = now
        n = int(S.cur_ids.numel())
        nz = r["units"].flatten().nonzero().flatten().tolist()
        u = r["units"].flatten()
        pairs = torch.cat(r["pairs"]) if r["pairs"] else torch.zeros(0, dtype=torch.long)
        uq, cnt = torch.unique(pairs, return_counts=True)
        tt = [[int(x // 100000), int(x % 100000), int(c)]
              for x, c in zip(uq.tolist(), cnt.tolist(), strict=True)]
        if state["arm"] == "off" and r["upairs"]:
            uu, uc = torch.unique(torch.cat(r["upairs"]), return_counts=True)
            for x, c in zip(uu.tolist(), uc.tolist(), strict=True):
                unit_tok[x] = unit_tok.get(x, 0) + c
        t = {"type": "tick", "seq": state["seq"], "step": state["step"], "arm": state["arm"],
             "phase": state["phase"], "tokens": n, "dt_s": round(dt, 4),
             "kept_slots": r["kept"], "slots": r["slots"],
             "ent": round(r["ent"] / max(r["slots"], 1), 4),
             "top1": round(r["top1"] / max(r["slots"], 1), 4),
             "r": [[i, int(u[i])] for i in nz], "tt": tt}
        if meter and state["e0"] is not None:
            t["energy_j"] = round(meter() - state["e0"], 3)
        state["seq"] += 1
        state["phase"] = "decode"
        emit(t)

    model.register_forward_pre_hook(pre, with_kwargs=True)
    model.register_forward_hook(post, with_kwargs=True)

    steps = []
    for topic, ps in TOPICS.items():
        for p in ps[:args.per_topic]:
            steps.append((topic, p))
    # interleave topics so the live view shows a mixed workload
    steps = [steps[i] for j in range(args.per_topic) for i in range(j, len(steps), args.per_topic)]
    emit({"type": "meta", "model": args.model.rstrip("/").split("/")[-1], "layers": L, "experts": E,
          "top_k": K, "mass": args.mass, "flops_per_routed_token": flops_per_routed_token,
          "hidden": cfg.hidden_size, "ffn": cfg.intermediate_size, "capture": args.where,
          "energy": "nvml" if meter else "none", "new_tokens": args.new_tokens,
          "steps": [{"id": i, "topic": t, "prompt": p} for i, (t, p) in enumerate(steps)]})
    with torch.no_grad():
        for i, (topic, p) in enumerate(steps):
            chat = [{"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": p}]
            ids = tok.apply_chat_template(chat,
                                          add_generation_prompt=True, return_tensors="pt")
            if not torch.is_tensor(ids):
                ids = ids["input_ids"]
            ids = ids.to(args.device)
            for arm in args.arms.split(","):
                S.mass = args.mass if arm == "on" else None
                state.update(step=i, arm=arm, phase="prefill", last=time.time())
                out = model.generate(ids, max_new_tokens=args.new_tokens,
                                     min_new_tokens=args.new_tokens, do_sample=False,
                                     pad_token_id=tok.pad_token_id)
                text = tok.decode(out[0, ids.shape[1]:], skip_special_tokens=True)
                emit({"type": "step.done", "step": i, "arm": arm, "text": text})
            print(f"step {i + 1}/{len(steps)} {topic}", flush=True)
    ids_seen = set()
    fout.flush()
    for line in open(args.out):
        d = json.loads(line)
        if d["type"] == "tick":
            ids_seen.update(x[1] for x in d["tt"])
    best: dict[int, list] = {}
    for x, c in unit_tok.items():
        best.setdefault(x // 100000, []).append((c, x % 100000))
    ut = {str(u): [[t, c] for c, t in sorted(v, reverse=True)[:6]] for u, v in best.items()}
    ids_seen.update(t for v in ut.values() for t, _ in v)
    vocab = {t: tok.decode([t]) for t in sorted(ids_seen)}
    emit({"type": "unit_tokens", "arm": "off", "units": ut})
    emit({"type": "vocab", "tokens": {str(k): v for k, v in vocab.items()}})
    emit({"type": "end"})


if __name__ == "__main__":
    main()
