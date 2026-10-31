"""`wattnot init`: write a config, run preflight, start in watch-only mode."""

from __future__ import annotations

import json
from pathlib import Path

from wattnot import preflight


def init(path: Path, *, act: bool = False, force: bool = False) -> dict:
    report = preflight.run()
    if path.exists() and not force:
        try:
            cfg = json.loads(path.read_text())
        except ValueError as e:
            raise SystemExit(f"{path} is not valid JSON ({e}); fix it or rerun with --force")
        if not isinstance(cfg, dict):
            raise SystemExit(f"{path} is not a JSON object; fix it or rerun with --force")
        cfg.setdefault("mode", "watch")
        if act:
            cfg["mode"] = "act"
    else:
        cfg = {"mode": "act" if act else "watch", "max_p99_increase": 0.10, "dataset": "mixed",
               "auto_approve_max_cap_drop_pct": 58.0}
    cfg["features"] = {f["id"]: f["available"] for f in report["features"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    return {"config": str(path), "mode": cfg["mode"], "preflight": report}


def main(args) -> int:
    path = Path(args.config) if args.config else Path.home() / ".wattnot" / "config.json"
    out = init(path, act=args.act, force=args.force)
    print(preflight.render(out["preflight"]))
    print()
    print(f"Config: {out['config']}")
    if out["mode"] == "watch":
        print("Mode: WATCH-ONLY. WattNot reads telemetry and shows what it would save, with a "
              "receipt, and changes nothing. When you are ready: wattnot init --act")
    else:
        print("Mode: ACT. Approved changes (power caps, serving config) will be applied.")
    return 0
