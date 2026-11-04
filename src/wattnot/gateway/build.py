"""Build the gateway from one ``wattnot.yaml``.

    wattnot gateway --config wattnot.yaml            # serves http://127.0.0.1:8780/v1
    wattnot dash --port 8770 --gateway wattnot.yaml  # same gateway mounted at /gw/v1 on the dashboard

Request side (works on any upstream, including the Meta API): agent-waste guard, small-tier-first
routing with step-up, context saver, thinking cap, image downscale, a voice route (Muse Voice).
Every request writes one savings record (see ``savings.py``).
"""
from __future__ import annotations

import base64
import contextvars
import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from wattnot.gateway.savings import SavingsLog, record_for

DEFAULTS: dict[str, Any] = {
    "upstream": {"base_url": "https://api.meta.ai/v1", "key_env": "META_API_KEY", "timeout_s": 120},
    "tiers": [{"name": "small", "model": "muse-spark-1.3", "body_extra": {"reasoning_effort": "low"}},
              {"name": "big", "model": "muse-spark-1.3", "body_extra": {"reasoning_effort": "high"}}],
    "baseline": {"tier": "big", "shadow_rate": 0.1, "min_agreement": 0.8, "min_samples": 10},
    "savers": {
        "agent_waste": {"enabled": True, "preset": "harness"},   # WasteConfig(): what the H100 agentwaste block ran
        "routing": {"enabled": True, "final_check": False, "max_stepups": 1},
        "context_saver": {"enabled": True, "min_tokens": 500},
        # the H100 sealed holdout's kept stack (wattnot.longrun.holdout.FROZEN_STACK), applied with
        # the harness's own apply_stack: reasoning_effort low + a 1536-token output budget
        "thinking_cap": {"enabled": True, "reasoning_effort": "low", "max_tokens": 1536, "harness": "holdout"},
        "vision": {"enabled": True, "max_side_px": 768},
        "voice": {"enabled": True},
        # opt-in, off by default: a one-line "answer briefly" system note on short plain Q&A (no
        # tools, no images, short prompt). The gate judges it like any saver: if answers stop
        # matching stock, or the request costs more tokens, it switches itself off.
        "brief_answers": {"enabled": False, "max_prompt_tokens": 300,
                          "note": "Answer briefly: give the answer and at most one short line of working. "
                                  "Do not restate the question or repeat the result."},
    },
    "energy": {"source": "estimate", "j_per_token": {"muse-spark-1.3": 7.86, "default": 7.86}},
    "savings_log": "out/gateway/savings.jsonl",
    "node": {"enabled": False, "gpus": "all", "cap_min_w": 300, "cap_max_w": 375,
             "sam_rack_check": True, "approval": "manual", "log": "out/gateway/node.jsonl"},
    "integrations": {"langfuse": {"enabled": False}},
    "deployment": "gateway",
    "port": 8780,
    # per-model saver gate (gateway/gate.py). state_path: where decisions survive a restart (default:
    # next to the savings log). effort_noop_models: served models known to ignore reasoning_effort,
    # so routing/thinking_cap are never applied or shadow-checked there when both tiers are that model.
    "gate": {"min_pairs": 3, "done_window": 10, "state_path": None, "effort_noop_models": []},
}

_ctx_changed_box: contextvars.ContextVar[dict | None] = contextvars.ContextVar("wn_gw_ctx", default=None)
_calls: contextvars.ContextVar[list | None] = contextvars.ContextVar("wn_gw_calls", default=None)


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out


def load_config(path: str | Path | None) -> dict:
    """The YAML merged over DEFAULTS. Unknown saver names raise, so a typo never silently disables one."""
    import yaml

    raw = yaml.safe_load(Path(path).read_text()) if path else {}
    raw = raw or {}
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    unknown = set(raw.get("savers") or {}) - set(DEFAULTS["savers"])
    if unknown:
        raise ValueError(f"unknown savers in config: {sorted(unknown)}")
    import copy
    cfg = _merge(copy.deepcopy(DEFAULTS), raw)   # deep copy: callers may edit nested keys
    for t in cfg["tiers"]:
        if t.get("reasoning") or t.get("max_output"):
            raise ValueError(f"tier {t.get('name')}: output-budget tiers were dropped; use body_extra")
    names = [t["name"] for t in cfg["tiers"]]
    if cfg["baseline"]["tier"] not in names:
        raise ValueError(f"baseline.tier {cfg['baseline']['tier']!r} is not one of {names}")
    return cfg


def _key(cfg: dict) -> str:
    env = cfg["upstream"].get("key_env") or ""
    if os.environ.get(env, "").strip():
        return os.environ[env].strip()
    if "meta.ai" in cfg["upstream"]["base_url"]:
        from wattnot.meta.client import load_key
        return load_key()
    return ""


def same_model_tiers(cfg: dict) -> bool:
    """True when every tier serves the same model (routing then only changes body extras)."""
    return len({t.get("model") for t in cfg.get("tiers") or []}) == 1


def is_short_qa(body: dict, max_prompt_tokens: int) -> bool:
    """Plain short question: no tools, no images, prompt under max_prompt_tokens (chars/4)."""
    if body.get("tools"):
        return False
    msgs = body.get("messages") or []
    if not msgs or not isinstance(msgs[-1], dict) or msgs[-1].get("role") != "user":
        return False
    n = 0
    for m in msgs:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            if any(isinstance(p, dict) and p.get("type") not in (None, "text") for p in c):
                return False
            c = " ".join(str(p.get("text") or "") for p in c if isinstance(p, dict))
        n += len(str(c or "")) // 4
    return n <= max_prompt_tokens


def cap_thinking(body: dict, max_tokens: int) -> dict:
    """Clamp the output budget (thinking + answer). Unset means the cap is applied."""
    out = dict(body)
    asked = out.pop("max_tokens", None) or out.get("max_completion_tokens")
    out["max_completion_tokens"] = min(int(asked), max_tokens) if asked else max_tokens
    return out


def shrink_images(body: dict, max_side: int) -> tuple[dict, int]:
    """Downscale inline (data: URI) images so the longest side is <= max_side. Returns (body, n shrunk)."""
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return body, 0
    n = 0
    new_msgs = []
    for m in msgs:
        parts = m.get("content") if isinstance(m, dict) else None
        if not isinstance(parts, list):
            new_msgs.append(m)
            continue
        new_parts = []
        for p in parts:
            url = ((p or {}).get("image_url") or {}).get("url", "") if isinstance(p, dict) else ""
            if p.get("type") == "image_url" and url.startswith("data:image/") and ";base64," in url:
                small = _shrink_data_uri(url, max_side)
                if small != url:
                    n += 1
                    p = {**p, "image_url": {**p["image_url"], "url": small}}
            new_parts.append(p)
        new_msgs.append({**m, "content": new_parts})
    return ({**body, "messages": new_msgs} if n else body), n


def _shrink_data_uri(url: str, max_side: int) -> str:
    from PIL import Image

    head, b64 = url.split(",", 1)
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    if max(img.size) <= max_side:
        return url
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    fmt = "PNG" if "png" in head else "JPEG"
    (img if fmt == "PNG" else img.convert("RGB")).save(buf, format=fmt)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


def upstream_headers(cfg: dict) -> dict:
    """Extra auth headers from env vars, e.g. a Modal proxy token:
    upstream.headers_env: {Modal-Key: MODAL_PROXY_TOKEN_ID, Modal-Secret: MODAL_PROXY_TOKEN_SECRET}."""
    return {k: os.environ[v].strip() for k, v in (cfg["upstream"].get("headers_env") or {}).items()
            if os.environ.get(v, "").strip()}


def http_upstream(cfg: dict):
    """POST to the configured upstream with the gateway's own key (the caller's key is ignored)."""
    base = cfg["upstream"]["base_url"].rstrip("/")
    timeout = float(cfg["upstream"].get("timeout_s", 120))
    if "api.meta.ai" in base:
        # Meta API: the guarded client checks the spend cap first and logs every call to the
        # shared spend ledger (~/.wattnot/meta_spend.json, or WATTNOT_META_LEDGER)
        from wattnot.meta.client import GuardedClient
        gc = GuardedClient(key=_key(cfg) or None, base=base, timeout_s=timeout)

        def meta_call(body: dict, headers: dict) -> dict:
            return gc(body, purpose="gateway:live")

        return meta_call

    def call(body: dict, headers: dict) -> dict:
        key = _key(cfg)
        h = {"Content-Type": "application/json"}
        if key:
            h["Authorization"] = f"Bearer {key}"
        h.update(upstream_headers(cfg))
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return json.loads(r.read().decode())

    return call


def build_app(cfg: dict, upstream=None, log: SavingsLog | None = None):
    """The FastAPI gateway. ``upstream(body, headers) -> dict`` is injectable for tests."""
    from fastapi import Request, UploadFile
    from fastapi.responses import JSONResponse

    from wattnot.agentwaste.config import WasteConfig
    from wattnot.agentwaste.server import create_app
    from wattnot.context_saver.middleware import ContextSaver

    sv = cfg["savers"]
    state: dict = {}
    base_tier_ref = [next(t for t in cfg["tiers"] if t["name"] == cfg["baseline"]["tier"])]
    raw_up = upstream or http_upstream(cfg)
    log = log or SavingsLog(cfg["savings_log"])
    from wattnot.gateway.gate import SaverGate
    gcfg = cfg.get("gate") or {}
    _slog = Path(cfg["savings_log"])
    gate = SaverGate(min_pairs=int(gcfg.get("min_pairs", 3)), done_window=int(gcfg.get("done_window", 10)),
                     min_agreement=float(cfg["baseline"].get("min_agreement", 0.8)),
                     state_path=gcfg.get("state_path") or _slog.with_name(_slog.stem + ".gate-state.json"))
    gate_key = base_tier_ref[0]["model"]   # one served model per gateway: the gate is per model
    if same_model_tiers(cfg) and gate_key in (gcfg.get("effort_noop_models") or []):
        gate.mark_noop(["routing", "thinking_cap"], gate_key,
                       "both tiers are this model and it ignores reasoning_effort (config "
                       "gate.effort_noop_models): request left unchanged, no shadow checks")
    from wattnot.gateway.langfuse import LangfuseSink
    lf = LangfuseSink.from_config(cfg)

    def wrapped(body: dict, headers: dict) -> dict:
        mon = state.get("monitor")
        if mon is not None and (mon.rolled_back or not gate.allow("routing", gate_key)) \
                and body.get("model") is not None:
            # canary rollback: routing failed the live agreement check, so every call goes to
            # the baseline tier (tier extras of the small tier are replaced by the baseline's)
            from wattnot.agentwaste.server import tier_body as _tb
            small_extra = set().union(*[set((t.get("body_extra") or {})) for t in cfg["tiers"]])
            body = _tb({k: v for k, v in body.items() if k not in small_extra}, base_tier_ref[0])
        done_name = "done"
        info: dict = {"model": body.get("model"), "images_shrunk": 0,
                      "done_injected": isinstance(body.get("tools"), list) and any(
                          (t.get("function") or {}).get("name") == done_name for t in body["tools"] if isinstance(t, dict)),
                      "n_messages": len(body.get("messages") or []),
                      "model_tier": next((t["name"] for t in cfg["tiers"]
                                          if t["model"] == body.get("model")
                                          and all(body.get(k) == v for k, v in (t.get("body_extra") or {}).items())),
                                         None)}
        tc = sv["thinking_cap"]
        sink0 = _calls.get()
        first = not sink0                       # a step-up (2nd call) keeps its own tier's effort
        if tc["enabled"] and first and gate.allow("thinking_cap", gate_key) \
                and not (mon is not None and mon.rolled_back):   # canary rollback covers the same lever
            eff = tc.get("reasoning_effort")
            had = body.get("reasoning_effort")
            if tc.get("harness") == "holdout":
                from wattnot.longrun.holdout import apply_stack
                body = apply_stack(body, {"savers": {"reasoning_effort": eff} if eff else {},
                                          "max_tokens": int(tc["max_tokens"])})
            else:
                body = cap_thinking({**body, "reasoning_effort": eff} if eff else body, int(tc["max_tokens"]))
            info["thinking_low"] = bool(eff) and had != eff
        ba = sv.get("brief_answers") or {}
        if ba.get("enabled") and first and gate.allow("brief_answers", gate_key) and is_short_qa(
                body, int(ba.get("max_prompt_tokens", 300))):
            body = {**body, "messages": [{"role": "system", "content": ba["note"]}, *body["messages"]]}
            info["brief"] = True
        if sv["vision"]["enabled"] and gate.allow("vision", gate_key):
            body, info["images_shrunk"] = shrink_images(body, int(sv["vision"]["max_side_px"]))
        resp = raw_up(body, headers)
        info["usage"] = dict((resp or {}).get("usage") or {})
        try:
            info["text"] = str(((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or "")[:2000]
            info["tool_calls"] = ((resp.get("choices") or [{}])[0].get("message") or {}).get("tool_calls")
        except (AttributeError, IndexError):
            info["text"] = ""
        sink = _calls.get()
        if sink is not None:
            sink.append(info)
        return resp

    aw = sv["agent_waste"]
    if aw["enabled"]:
        wcfg = WasteConfig.second_layer() if aw.get("preset") == "second_layer" else WasteConfig()
    else:   # off: limits so loose nothing ever trips, and no injected done tool
        wcfg = WasteConfig(max_steps=10**6, max_total_tokens=10**12, repeat_limit=10**6, noop_limit=10**6,
                           reasoning_hard_cap=10**9, reasoning_floor=10**9, inject_done=False, max_nudges=0)
    if aw["enabled"] and aw.get("lean_nudge", True):
        # gateway: a healthy agent call is forwarded byte-identical (no done tool); a looping one
        # gets its repeated tool turns removed plus a one-line note, so it is never longer than stock
        import dataclasses as _dc
        wcfg = _dc.replace(wcfg, lean_nudge=True)
    ro = sv["routing"]
    tiers = cfg["tiers"] if ro["enabled"] else [t for t in cfg["tiers"] if t["name"] == cfg["baseline"]["tier"]]
    saver = ContextSaver(min_tokens=int(sv["context_saver"]["min_tokens"])) if sv["context_saver"]["enabled"] else None
    if saver is not None:   # note when it really rewrote messages, so the gate judges it as a saver
        _tf = saver.transform

        def _transform(msgs, _tf=_tf):
            out, acct = _tf(msgs)
            box = _ctx_changed_box.get()
            if out != msgs and box is not None:
                box["changed"] = True
            return out, acct
        saver.transform = _transform
    app = create_app(wrapped, wcfg, context_saver=saver, tiers=tiers,
                     label="MEASURED" if cfg["energy"]["source"] == "nvml" else "LIVE-API",
                     final_check=bool(ro.get("final_check")), max_stepups=int(ro.get("max_stepups", 1)),
                     short_bypass_tokens=None if saver else 0,
                     waste_gate=lambda b: gate.allow("agent_waste", gate_key),
                     context_gate=lambda b: gate.allow("context_saver", gate_key))
    base_tier = next(t for t in cfg["tiers"] if t["name"] == cfg["baseline"]["tier"])
    from wattnot.gateway.monitor import Monitor
    monitor = Monitor(cfg, raw_up, base_tier, log.path.with_name(log.path.stem + ".shadow.jsonl"),
                      gate=gate, gate_key=gate_key)
    app.state.gate = gate
    app.state.monitor = monitor
    state["monitor"] = monitor

    @app.middleware("http")
    async def savings_mw(request: Request, call_next):
        if not request.url.path.endswith("/v1/chat/completions") or request.method != "POST":
            return await call_next(request)
        raw = await request.body()
        sink: list = []
        token = _calls.set(sink)
        ctx_box = {"changed": False}
        ctx_token = _ctx_changed_box.set(ctx_box)
        try:
            resp = await call_next(request)
        finally:
            _calls.reset(token)
            _ctx_changed_box.reset(ctx_token)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        rec = log.append(record_for(body, sink, resp.status_code, base_tier, cfg,
                                    run=request.headers.get("x-wattnot-run"), ratio=monitor.ratio()))
        changed = False
        tc_eff = sv["thinking_cap"]["enabled"] and sv["thinking_cap"].get("reasoning_effort")
        if isinstance(body.get("tools"), list) and body["tools"]:
            n_in = len(body.get("messages") or [])
            # done tool added, a nudge appended, or (lean nudge) repeated tool turns removed
            changed = any(i.get("done_injected") or i.get("n_messages", n_in) != n_in for i in sink)
            # a nudged call the model answered in text (no more tool calls) ended the loop too
            ended = bool(rec.get("guard_stopped")) or (changed and bool(sink) and not sink[-1].get("tool_calls")
                                                       and bool((sink[-1].get("text") or "").strip()))
            gate.record_agent_call(gate_key, changed, ended)
        for i in sink:
            i["applied"] = (["agent_waste"] if changed else []) + (
                ["routing"] if i.get("model_tier") != base_tier["name"] else []) + (
                # routing's small tier at the thinking saver's effort is the same lever: judge both
                ["thinking_cap"] if i.get("thinking_low") or (
                    tc_eff and i.get("model_tier") != base_tier["name"] and next(
                        (t for t in cfg["tiers"] if t["name"] == i.get("model_tier")), {}
                    ).get("body_extra", {}).get("reasoning_effort") == tc_eff) else []) + (
                ["vision"] if i.get("images_shrunk") else []) + (
                ["brief_answers"] if i.get("brief") else []) + (
                ["context_saver"] if ctx_box["changed"] else [])
        if resp.status_code == 200 and sink and sink[-1].get("applied"):
            monitor.maybe_shadow(body, sink[-1], rec["receipt_id"])
        lf.send(rec)
        resp.headers["x-wattnot-receipt"] = rec["receipt_id"]
        return resp

    @app.get("/v1/wattnot/savings")
    def savings(last: int = 20) -> JSONResponse:
        return JSONResponse(log.summary(last=last))

    @app.get("/v1/wattnot/monitor")
    def monitor_view() -> JSONResponse:
        return JSONResponse({**monitor.status(), "gate": gate.status()})

    @app.get("/v1/wattnot/config")
    def config_view() -> JSONResponse:
        safe = {k: v for k, v in cfg.items() if k != "upstream"}
        safe["upstream"] = {"base_url": cfg["upstream"]["base_url"]}
        # server-side levers the H100 run did not prove: the gateway never turns these on
        safe["not_proven_on_h100"] = {"fp8": "off", "batching": "off", "kv_cache": "off"}
        return JSONResponse(safe)

    if sv["voice"]["enabled"]:
        @app.post("/v1/audio/transcriptions")
        async def transcribe(file: UploadFile) -> JSONResponse:
            from wattnot.spokenanswer.client import transcribe_wav_live
            try:
                text = transcribe_wav_live(await file.read())
            except (urllib.error.URLError, OSError, ValueError) as e:
                return JSONResponse(status_code=502, content={"detail": f"transcribe failed: {type(e).__name__}"})
            return JSONResponse({"text": text})

    return app


def serve(config: str | None, host: str = "127.0.0.1", port: int = 8780) -> int:
    import uvicorn

    cfg = load_config(config)
    print(f"WattNot gateway on http://{host}:{port}/v1  upstream={cfg['upstream']['base_url']}  "
          f"savings log={cfg['savings_log']}", flush=True)
    uvicorn.run(build_app(cfg), host=host, port=port, log_level="warning")
    return 0
