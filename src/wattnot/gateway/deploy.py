"""Deployment choices for the Configure screen -> the same wattnot.yaml the gateway reads,
plus copyable commands for Mac/Linux and PowerShell. Pure functions, no network."""
from __future__ import annotations

import yaml

UPSTREAMS = {
    "meta": {"base_url": "https://api.meta.ai/v1", "key_env": "META_API_KEY"},
    "vllm": {"base_url": "http://localhost:8000/v1", "key_env": "VLLM_API_KEY"},
    "baseten": {"base_url": "https://inference.baseten.co/v1", "key_env": "BASETEN_API_KEY"},
}
MODES = ("gateway", "gpu", "both")
SAVERS = ("agent_waste", "routing", "context_saver", "thinking_cap", "vision", "voice", "brief_answers")
OFF_BY_DEFAULT = ("brief_answers",)   # opt-in savers: off unless the choice turns them on
APPROVAL = ("manual", "auto-low-risk", "watch-only")


def to_config(choice: dict) -> dict:
    """Validated choice -> config dict. Unknown values raise ValueError. Never holds a secret."""
    mode = choice.get("deployment", "gateway")
    if mode not in MODES:
        raise ValueError(f"deployment must be one of {MODES}")
    cfg: dict = {"deployment": mode}
    if mode in ("gateway", "both"):
        up = choice.get("upstream", "meta")
        if up in UPSTREAMS:
            cfg["upstream"] = dict(UPSTREAMS[up])
        elif isinstance(up, str) and up.startswith(("http://", "https://")):
            cfg["upstream"] = {"base_url": up.rstrip("/"), "key_env": "UPSTREAM_API_KEY"}
        else:
            raise ValueError("upstream must be meta, vllm, baseten or an http(s) URL")
        port = int(choice.get("port", 8780))
        if not 1024 <= port <= 65535:
            raise ValueError("port must be 1024-65535")
        cfg["port"] = port
        on = choice.get("savers") or {}
        bad = set(on) - set(SAVERS)
        if bad:
            raise ValueError(f"unknown savers: {sorted(bad)}")
        cfg["savers"] = {s: {"enabled": bool(on.get(s, s not in OFF_BY_DEFAULT))} for s in SAVERS}
        cfg["integrations"] = {"langfuse": {"enabled": bool(choice.get("langfuse", False))}}
    if mode in ("gpu", "both"):
        lo, hi = int(choice.get("cap_min_w", 300)), int(choice.get("cap_max_w", 375))
        if not 100 <= lo <= hi <= 1000:
            raise ValueError("cap range must satisfy 100 <= min <= max <= 1000 W")
        appr = choice.get("approval", "manual")
        if appr not in APPROVAL:
            raise ValueError(f"approval must be one of {APPROVAL}")
        gpus = str(choice.get("gpus", "all")).strip() or "all"
        if gpus != "all" and not all(g.strip().isdigit() for g in gpus.split(",")):
            raise ValueError("gpus must be 'all' or a list like 0,1")
        cfg["node"] = {"enabled": True, "gpus": gpus, "cap_min_w": lo, "cap_max_w": hi,
                       "sam_rack_check": bool(choice.get("sam_rack_check", True)), "approval": appr}
        cfg["energy"] = {"source": "nvml"}
    return cfg


def render(choice: dict) -> dict:
    cfg = to_config(choice)
    text = "# wattnot.yaml, written by the Configure screen. Keys stay in env vars, never here.\n" + \
        yaml.safe_dump(cfg, sort_keys=False)
    mode, port = cfg["deployment"], cfg.get("port", 8780)
    key_env = (cfg.get("upstream") or {}).get("key_env", "META_API_KEY")
    posix = ["pip install -e .   # from the wattnot repo", f"export {key_env}=...   # your key, never in the yaml"]
    ps = ["pip install -e .   # from the wattnot repo", f'$env:{key_env} = "..."   # your key, never in the yaml']
    if cfg.get("integrations", {}).get("langfuse", {}).get("enabled"):
        posix.append("export LANGFUSE_HOST=https://cloud.langfuse.com LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=...")
        ps.append('$env:LANGFUSE_HOST = "https://cloud.langfuse.com"; $env:LANGFUSE_PUBLIC_KEY = "..."; $env:LANGFUSE_SECRET_KEY = "..."')
    if mode in ("gateway", "both"):
        posix.append(f"wattnot gateway --config wattnot.yaml --port {port}")
        ps.append(f"python -m wattnot.cli gateway --config wattnot.yaml --port {port}")
    if mode in ("gpu", "both"):
        posix.append("sudo -E wattnot node --config wattnot.yaml   # no GPU here? it replays the recorded H100 (REPLAY)")
        ps.append("python -m wattnot.cli node --config wattnot.yaml   # real caps need Linux + NVIDIA; else REPLAY H100")
    then = {"python": f'OpenAI(base_url="http://localhost:{port}/v1", api_key="unused")'} if mode != "gpu" else {}
    return {"yaml": text, "config": cfg, "commands": {"posix": posix, "powershell": ps}, "client": then}
