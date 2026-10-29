"""The real adaptive top-k backend: Hugging Face transformers on one GPU. Needs torch.

`install_hooks(model)` wraps each MoE layer's router (keeps the experts that cover `mass` of the
router weight, drops the rest) and swaps in an eager expert loop that skips dropped slots. The
"off" arm runs the same hooked layers with no threshold, so both arms use identical code except
for the threshold itself. Counters are kept on the GPU (no host sync per layer) in both arms.

Only the fused-expert layout is wired: Granite MoE and Qwen3-MoE (a router called
`router` or `gate` with `.top_k`, experts with `gate_up_proj`, `down_proj`, `act_fn`).
Anything else raises `AdaptiveError` before a single watt is measured.
A one-prompt sanity check also compares the hooked "off" arm with the stock model.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from typing import Any

from wattnot.moe.adaptive_topk import AdaptiveConfig, AdaptiveError


class _State:
    """Shared by every hooked layer."""

    mass: float | None = None
    used: Any = None     # torch scalar: slots kept, summed over tokens and layers
    slots: int = 0       # tokens x layers seen

    def reset(self, torch) -> None:
        self.used, self.slots = torch.zeros((), dtype=torch.float64, device=self.device), 0


def _router_of(block):
    """Granite calls the router `router`; Qwen3-MoE calls it `gate`. Same forward contract."""
    return getattr(block, "router", None) or getattr(block, "gate", None)


def find_moe_layers(model) -> list:
    layers = [m for m in model.modules()
              if hasattr(m, "experts") and hasattr(_router_of(m), "top_k")
              and hasattr(m.experts, "gate_up_proj")
              and hasattr(m.experts, "down_proj") and hasattr(m.experts, "act_fn")]
    if not layers:
        raise AdaptiveError("this model does not have the Granite MoE layout the hook knows; "
                            "adaptive top-k is only wired for Granite 3.x MoE today")
    return layers


def _router_order(out, torch) -> tuple[int, int, int]:
    """Positions of (logits, weights, index) in a Granite router's 3-tuple output.

    The index tensor is the only integer one. Logits are the only one whose last dimension is
    wider than the other float tensor's (all experts vs top-k)."""
    idx = [i for i, t in enumerate(out) if not torch.is_floating_point(t)]
    if len(idx) != 1:
        raise AdaptiveError("unexpected router output: could not find the expert index tensor")
    flo = [i for i in range(3) if i != idx[0]]
    a, b = flo
    if out[a].shape[-1] == out[b].shape[-1]:
        raise AdaptiveError("unexpected router output: logits and weights have the same width")
    lg, wt = (a, b) if out[a].shape[-1] > out[b].shape[-1] else (b, a)
    return lg, wt, idx[0]


def install_hooks(model, torch) -> tuple[_State, Callable[[], None]]:
    """Returns (state, restore). `state.mass` None means normal top-k."""
    import torch.nn.functional as F  # noqa: N812

    st = _State()
    st.device = next(model.parameters()).device
    st.reset(torch)
    saved = []

    def make_router(orig):
        def router_forward(h):
            out = orig(h)
            # transformers changed the router's return order between releases
            # ((logits, weights, index) vs (index, weights, logits));
            # tell them apart by dtype and shape.
            order = _router_order(out, torch)
            logits, w, idx = (out[i] for i in order)
            n_exp = logits.shape[-1]
            wf = w.float()
            if st.mass is None:
                keep = torch.ones_like(wf, dtype=torch.bool)
            else:
                cs = torch.cumsum(wf / wf.sum(-1, keepdim=True), -1)
                keep = torch.cat([torch.ones_like(cs[:, :1], dtype=torch.bool),
                                  cs[:, :-1] < st.mass], -1)
            st.used += keep.sum()
            st.slots += keep.shape[0]
            wk = wf * keep
            if st.mass is not None:
                wk = wk / wk.sum(-1, keepdim=True)
            # a dropped slot points at a sentinel expert id that the loop below skips
            res = {order[0]: logits, order[1]: wk.type_as(w),
                   order[2]: torch.where(keep, idx, torch.full_like(idx, n_exp))}
            return res[0], res[1], res[2]
        return router_forward

    def make_experts(mod):
        n_exp = mod.gate_up_proj.shape[0]

        def experts_forward(h, idx, w):
            out = torch.zeros_like(h)
            for e in torch.unique(idx).tolist():
                if e >= n_exp:
                    continue
                pos, tok = torch.where(idx.T == e)
                x = h[tok]
                g, u = F.linear(x, mod.gate_up_proj[e]).chunk(2, -1)
                y = F.linear(mod.act_fn(g) * u, mod.down_proj[e]) * w[tok, pos, None]
                out.index_add_(0, tok, y.to(out.dtype))
            return out
        return experts_forward

    for layer in find_moe_layers(model):
        router = _router_of(layer)
        saved.append((router, router.forward, layer.experts, layer.experts.forward))
        router.forward = make_router(router.forward)
        layer.experts.forward = make_experts(layer.experts)

    def restore() -> None:
        for router, rf, experts, ef in saved:
            router.forward, experts.forward = rf, ef

    return st, restore


class HFBackend:
    simulated = False

    def __init__(self, cfg: AdaptiveConfig, rig) -> None:
        self.cfg, self.rig = cfg, rig
        self.gpu, self.driver = getattr(rig, "gpu", "unknown GPU"), str(getattr(
            rig, "driver", "unknown"))
        self.torch = self.tok = self.model = self.st = self._restore = None
        self.top_k: int | None = None

    # -- setup
    def prepare(self, deadline: float, clock: Callable[[], float]) -> dict:
        from wattnot.runbook import _sh

        release = getattr(self.rig, "release_gpu", None)
        if release:
            release()  # vLLM from the earlier stages must let go of its GPU memory
        left = max(60.0, min(600.0, deadline - clock() - 300))
        if os.path.isdir(self.cfg.model):  # already on disk (a local path), nothing to download
            rc, tail = 0, ""
        else:
            rc, tail = _sh(["hf", "download", self.cfg.model], timeout=left)
        if rc != 0:
            raise AdaptiveError(f"model download failed: {tail.strip()[-200:]}")
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as e:
            raise AdaptiveError(f"torch / transformers not installed: {e}") from e
        if not torch.cuda.is_available():
            raise AdaptiveError("torch cannot see a GPU")
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(self.cfg.model)
        self.tok.padding_side = "left"
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(
            self.cfg.model, dtype=torch.bfloat16, device_map="cuda").eval()
        layers = find_moe_layers(self.model)
        self.top_k = int(_router_of(layers[0]).top_k)
        if self.top_k < 2:
            raise AdaptiveError(f"top-{self.top_k} router (e.g. Llama 4 Scout: 1 routed + shared "
                                "expert): there is no k to lower; not run")
        sanity = self._sanity()
        self.st, self._restore = install_hooks(self.model, torch)
        ok = self._hooked_off_matches(sanity["_stock"])
        sanity.pop("_stock")
        sanity["hooked_off_matches_stock"] = ok
        if not ok:
            raise AdaptiveError("the hooked 'off' arm does not match the stock model; "
                                "this transformers version is not supported by the hook")
        return {"top_k": self.top_k, "sanity": sanity, "layers": len(layers),
                "transformers_hook": "granite-moe", "dtype": "bfloat16"}

    def _ids(self) -> Any:
        enc = self.tok("Explain in two sentences why the sky is blue.", return_tensors="pt")
        return enc["input_ids"].to("cuda")

    def _sanity(self) -> dict:
        with self.torch.no_grad():
            return {"_stock": self.model(self._ids()).logits[0, -1].float().argmax().item()}

    def _hooked_off_matches(self, stock_top: int) -> bool:
        self.st.mass = None
        with self.torch.no_grad():
            return self.model(self._ids()).logits[0, -1].float().argmax().item() == stock_top

    # -- the experiment surface
    def set_arm(self, arm: str) -> None:
        self.st.mass = self.cfg.mass if arm == "on" else None

    def reset_counters(self) -> None:
        self.st.reset(self.torch)

    def active_experts(self) -> float | None:
        return float(self.st.used.item()) / (self.st.slots * 1.0) if self.st.slots else None

    def generate(self, prompts: list[str]) -> tuple[float, int]:
        t = self.torch
        enc = self.tok(prompts, return_tensors="pt", padding=True).to("cuda")
        n = self.cfg.max_new_tokens
        t.cuda.synchronize()
        t0 = time.perf_counter()
        with t.no_grad():
            out = self.model.generate(**enc, max_new_tokens=n, min_new_tokens=n,
                                      do_sample=False, pad_token_id=self.tok.pad_token_id)
        t.cuda.synchronize()
        sec = time.perf_counter() - t0
        return sec, int((out.shape[1] - enc["input_ids"].shape[1]) * len(prompts))

    def agreement(self) -> dict:
        """Teacher-forced next-token agreement of the adaptive arm with normal top-k, on the
        normal arm's own greedy continuations. Same idea as the CPU test."""
        t, cfg = self.torch, self.cfg
        from wattnot.moe.adaptive_topk import _agree_prompts

        agree = total = 0
        for text in _agree_prompts(cfg):
            ids = self.tok(text, return_tensors="pt")["input_ids"].to("cuda")
            plen = ids.shape[1]
            self.st.mass = None
            with t.no_grad():
                full = self.model.generate(ids, max_new_tokens=cfg.agree_new_tokens,
                                           min_new_tokens=cfg.agree_new_tokens, do_sample=False,
                                           pad_token_id=self.tok.pad_token_id)
                ref = self.model(full).logits[0, plen - 1:-1].float().argmax(-1)
                self.st.mass = cfg.mass
                got = self.model(full).logits[0, plen - 1:-1].float().argmax(-1)
            agree += int((ref == got).sum().item())
            total += int(ref.numel())
        self.st.mass = None
        return {"pct": 100.0 * agree / max(total, 1), "positions": total,
                "method": "next-token argmax vs normal top-k, teacher-forced on its greedy text"}

    def energy_meter(self):
        return self.rig.meter(None)  # NvmlMeter: reads the NVML total-energy counter

    def close(self) -> None:
        with contextlib.suppress(Exception):
            if self._restore:
                self._restore()
        self.model = None
        if self.torch is not None:
            with contextlib.suppress(Exception):
                self.torch.cuda.empty_cache()
