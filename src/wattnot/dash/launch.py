"""Configure -> checklist -> cost confirm -> launch, for end-to-end runs from the dashboard.

"End to end" = WattNot as a whole framework: every eligible feature ON in the with-WattNot arm,
against a without-WattNot arm, on the chosen configuration.

Routes (all under /api/launch):
  GET  options               providers, models, datasets, default caps
  POST eligibility           {config} -> every feature + Meta model, Eligible/Ineligible + reason
  POST checklist             {config} -> free pre-launch checks, pass/warn/fail + reason + fix
  POST estimate              {config} -> cost estimate + a short-lived confirm token
  POST start                 {config, mode: mock|live, confirm_token} -> job id
  GET  jobs, jobs/{id}, jobs/{id}/stream (SSE)
  GET  rounds                real round files (both arms) for every page
  GET  approvals             held changes + Muse Code PR with their evidence
  POST approvals/decide      reviewer decision, written as structured evidence

Keys are read from the environment / key files on this machine only; responses carry key
*names* and present/missing, never values. A live launch needs a confirm token from /estimate
(which shows the cost) and every required checklist item passing. mock mode spends nothing:
Modal runs its --fake path, Baseten replays its recorded events (labelled MOCK / REPLAY).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from wattnot import eligibility as E

ROOT = Path(__file__).resolve().parents[3]
JOBS_DIR = ROOT / "out" / "launch"
REVIEWS = ROOT / "data" / "reviews"
SECRET = secrets.token_bytes(32)
TOKEN_TTL_S = 600
CAPS = {"modal_smoke_usd": 1.5, "baseten_round_usd": 8.0, "modal_round_usd": 10.0, "modal_stage_usd": 60.0, "meta_hard_usd": 120.0, "meta_soft_usd": 100.0}
DATASETS = ["ours", "gsm8k", "humaneval", "mixed", "custom JSONL", "let WattNot decide"]
KEY_FILES = {"META_API_KEY": [Path.home() / ".config" / "wattnot" / "env"],
             "MODAL_PROXY_TOKEN_ID": [Path.home() / ".config" / "wattnot" / "modal.env"],
             "BASETEN_METRICS_KEY": [Path.home() / ".config" / "wattnot" / "env"]}
NEEDS = {"baseten": ["BASETEN_METRICS_KEY", "META_API_KEY"], "modal_a100": ["META_API_KEY", "MODAL"],
         "e2e_h100": ["META_API_KEY"], "combined": ["BASETEN_METRICS_KEY", "META_API_KEY", "MODAL"]}
PY = os.environ.get("WN_PY") or sys.executable

JOBS: dict[str, dict] = {}


def now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------- config + estimate
def norm_config(c: dict) -> dict:
    prov = c.get("provider") or "baseten"
    if prov not in E.PROVIDERS:
        raise ValueError(f"unknown provider {prov!r}")
    rounds = int(c.get("rounds") or 3)
    return {"name": str(c.get("name") or "").strip()[:80], "description": str(c.get("description") or "").strip()[:300],
            "provider": prov, "model": c.get("model") or E.PROVIDERS[prov]["default_model"],
            "datasets": list(c.get("datasets") or ["ours", "gsm8k", "humaneval"]),
            "p99_pct": float(c.get("p99_pct") if c.get("p99_pct") is not None else 10),
            "approvals": "hold_high_risk",  # fixed: high-risk changes are never auto-approved
            "open_pr": bool(c.get("open_pr", True)) and not c.get("smoke"), "rounds": 1 if c.get("smoke") else max(1, min(rounds, 3)),
            "smoke": ({"limit": max(1, min(int((c.get("smoke") or {}).get("limit") or 2), 3)),
                       "spark": False} if c.get("smoke") and prov == "modal_a100" else None),
            "caps": {**CAPS, **{k: min(float(v), CAPS[k]) for k, v in (c.get("caps") or {}).items() if k in CAPS}}}


def estimate(cfg: dict) -> dict:
    p = E.PROVIDERS[cfg["provider"]]
    if not p["cost_round_usd"]:
        return {"available": False, "why": f"no runner wired for {p['label']} yet", "label": "ESTIMATE"}
    lo, hi = p["cost_round_usd"]
    n = cfg["rounds"]
    if cfg.get("smoke"):
        from importlib import util
        sp = util.spec_from_file_location("fs", ROOT / "modal" / "full_stage.py")
        m = util.module_from_spec(sp)
        sp.loader.exec_module(m)
        worst = round(m.SMOKE_CAP_S / 3600 * m.GPU_USD_H, 2)
        return {"available": worst <= cfg["caps"]["modal_smoke_usd"], "label": "ESTIMATE", "rounds": 1,
                "per_round_usd": [0.3, worst], "total_usd": [0.3, worst], "hard_stop_usd": cfg["caps"]["modal_smoke_usd"],
                "meta_usd": [0, 0], "billed_to": p["spend"], "smoke": cfg["smoke"],
                "basis": f"{cfg['smoke']['limit']} item(s) per set; container killed at {m.SMOKE_CAP_S // 60} min "
                         f"(= ${worst} at ${m.GPU_USD_H}/h); smoke-r04/r05 cost $0.38 and $0.30",
                **({} if worst <= cfg["caps"]["modal_smoke_usd"] else {"why": "worst case above the smoke cap"})}
    cap = cfg["caps"]["baseten_round_usd"] * n if cfg["provider"] == "baseten" else cfg["caps"]["modal_stage_usd"]
    return {"available": True, "label": "ESTIMATE", "rounds": n, "per_round_usd": [lo, hi],
            "total_usd": [round(lo * n, 2), round(hi * n, 2)], "hard_stop_usd": cap,
            "meta_usd": [0.05 * n, 0.25 * n], "billed_to": p["spend"],
            "basis": "recorded smoke rounds scaled to the full item count"
            + ("; each Modal round is killed at its container timeout (worst case $%.2f, cap $%.0f)"
               % (_modal_round_worst(), cfg["caps"]["modal_round_usd"]) if cfg["provider"] != "baseten" else "")}


def sign(cfg: dict, exp: int) -> str:
    msg = json.dumps(cfg, sort_keys=True).encode() + str(exp).encode()
    return f"{exp}.{hmac.new(SECRET, msg, hashlib.sha256).hexdigest()[:32]}"


def verify_token(cfg: dict, tok: str | None) -> bool:
    try:
        exp_s, _ = (tok or "").split(".", 1)
        exp = int(exp_s)
    except ValueError:
        return False
    return exp > time.time() and hmac.compare_digest(sign(cfg, exp), tok or "")


# ---------------------------------------------------------------- checklist (free checks only)
def _key_present(name: str) -> bool:
    if os.environ.get(name):
        return True
    for f in KEY_FILES.get(name, []):
        try:
            if re.search(rf"^(export\s+)?{name}=\S+", f.read_text(), re.M):
                return True
        except OSError:
            pass
    if name == "META_API_KEY":
        try:
            from wattnot.meta.client import load_key
            return bool(load_key())
        except Exception:  # noqa: BLE001
            return False
    return False


def _item(i, title, state, reason, fix="", required=True, label="MEASURED"):
    return {"id": i, "title": title, "state": state, "reason": reason, "fix": fix, "required": required,
            "label": label}


def check_keys(cfg):
    out = []
    for k in NEEDS[cfg["provider"]]:
        if k == "MODAL":
            ok = (Path.home() / ".modal.toml").exists() or _key_present("MODAL_PROXY_TOKEN_ID")
            out.append(("Modal token", ok))
        else:
            out.append((k, _key_present(k)))
    miss = [n for n, ok in out if not ok]
    return _item("keys", "Keys present on the server (values never shown)", "fail" if miss else "pass",
                 "missing: " + ", ".join(miss) if miss else "found: " + ", ".join(n for n, _ in out),
                 "export the missing key in the shell that starts the dashboard, then re-check" if miss else "")


def check_model(cfg, opener=None):
    prov = cfg["provider"]
    if prov in ("baseten", "combined"):
        key = os.environ.get("BASETEN_METRICS_KEY")
        if not key:
            return _item("model", "Model id reachable (free model list)", "fail", "BASETEN_METRICS_KEY not set",
                         "export BASETEN_METRICS_KEY")
        import urllib.request
        req = urllib.request.Request("https://inference.baseten.co/v1/models",
                                     headers={"Authorization": f"Api-Key {key}"})
        try:
            with (opener or urllib.request.urlopen)(req, timeout=15) as r:  # noqa: S310
                ids = [m.get("id") for m in json.loads(r.read()).get("data", [])]
        except Exception as e:  # noqa: BLE001
            return _item("model", "Model id reachable (free model list)", "fail",
                         f"model list failed ({type(e).__name__})", "check the key and the workspace billing page")
        want = "meta-models/muse-spark-open"
        ok = want in ids
        return _item("model", "Model id reachable (free model list)", "pass" if ok else "fail",
                     f"{want} {'listed' if ok else 'not listed'} ({len(ids)} models, no tokens spent)",
                     "" if ok else "pick a model id from the list; do not guess")
    # own GPU: the vLLM server only exists while a round runs; probing it would wake the GPU.
    st = "pass" if (ROOT / "modal" / "full_stage.py").exists() else "fail"
    return _item("model", "Model id reachable", st if prov == "modal_a100" else "warn",
                 "Glimmer 30B is loaded inside the round's own container; not probed so no GPU wakes up"
                 if prov == "modal_a100" else "no runner wired for this provider yet",
                 "" if st == "pass" else "check modal/full_stage.py", required=prov == "modal_a100", label="ESTIMATE")


def _modal_round_worst() -> float:
    from importlib import util
    sp = util.spec_from_file_location("fs", ROOT / "modal" / "full_stage.py")
    m = util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return round(m.ROUND_CAP_S / 3600 * m.GPU_USD_H, 2)


def check_caps(cfg):
    notes, state = [], "pass"
    try:
        from wattnot.meta.ledger import SpendLedger
        s = SpendLedger().summary()
        left = cfg["caps"]["meta_soft_usd"] - float(s["total_usd"])
        notes.append(f"Meta ${s['total_usd']:.2f} of ${cfg['caps']['meta_hard_usd']:.0f} hard / "
                     f"${cfg['caps']['meta_soft_usd']:.0f} soft")
        if left < 5:
            state = "fail"
    except Exception:  # noqa: BLE001
        notes.append("Meta ledger unreadable")
        state = "warn"
    if cfg["provider"] in ("modal_a100", "combined"):
        try:
            used = float(json.loads((ROOT / "data" / "modal-full-1101" / "stage_spend.json").read_text())["usd"])
        except (OSError, ValueError, KeyError):
            used = 0.0
        notes.append(f"Modal ${used:.2f} of ${cfg['caps']['modal_stage_usd']:.0f} (ESTIMATE)")
        worst = _modal_round_worst()
        notes.append(f"Modal per-round worst case ${worst:.2f} (cap ${cfg['caps']['modal_round_usd']:.0f})")
        if worst > cfg["caps"]["modal_round_usd"]:
            state = "fail"
        if used + 3.6 * cfg["rounds"] > cfg["caps"]["modal_stage_usd"]:
            state = "fail"
    if cfg["provider"] in ("baseten", "combined"):
        notes.append(f"Baseten hard stop ${cfg['caps']['baseten_round_usd']:.0f} per round")
        if cfg["caps"]["baseten_round_usd"] > 8:
            state = "fail"
    return _item("caps", "Spend caps and ledger headroom", state, "; ".join(notes),
                 "lower the rounds or raise nothing: caps are fixed at Baseten $8/round, Modal $10/round worst case, Modal $60 stage, Modal smoke $1.50, Meta $120/$100"
                 if state != "pass" else "")


REFUSAL = re.compile(r"deactivat|payment required|\b402\b|billing.{0,20}(fail|suspend)|account.{0,10}suspend", re.I)


def check_refusals(cfg, hours=24):
    if cfg["provider"] not in ("baseten", "combined"):
        return _item("refusal", "No recent Baseten refusals", "pass", "not used by this configuration",
                     required=False)
    hits, cut = [], time.time() - hours * 3600
    for f in list((ROOT / "data" / "baseten-1101").rglob("*.log")) + list((ROOT / "data" / "baseten-1101").rglob("*calls*.jsonl")):
        try:
            if f.stat().st_mtime < cut:
                continue
            if REFUSAL.search(f.read_text(errors="ignore")[-200_000:]):
                hits.append(f.relative_to(ROOT).as_posix())
        except OSError:
            pass
    return _item("refusal", "No recent Baseten refusals or deactivation", "fail" if hits else "pass",
                 ("refusal text in " + ", ".join(hits[:3])) if hits else f"none in the last {hours} h of round logs",
                 "open the Baseten billing page, settle any invoice, then re-check" if hits else "")


def check_modal(cfg):
    if cfg["provider"] not in ("modal_a100", "combined"):
        return _item("modal", "Modal credits and GPU type", "pass", "not used by this configuration", required=False)
    src = (ROOT / "modal" / "full_stage.py")
    gpu = "A100-80GB" if src.exists() and "A100" in src.read_text() else None
    cli = shutil.which("modal") is not None
    st = "pass" if gpu else "fail"
    return _item("modal", "Modal credits and GPU type", st,
                 f"GPU in the stage file: {gpu or 'not found'}; Modal CLI {'found' if cli else 'missing'}; "
                 "credit balance is not exposed by Modal's API, so it is checked against the $60 stage cap",
                 "" if st == "pass" else "set gpu='A100-80GB' in modal/full_stage.py", label="ESTIMATE")


def dash_servers() -> list[str]:
    try:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln for ln in out.splitlines() if "wattnot.dash" in ln and "--port" in ln and "grep" not in ln]


def check_servers(cfg):
    n = len(dash_servers())
    return _item("servers", "Dashboard servers within limit (max 2)", "pass" if n <= 2 else "fail",
                 f"{n} running on this machine (the stage recorder adds one short-lived server per arm)",
                 "stop the extra dashboard servers" if n > 2 else "")


def check_git(cfg):
    try:
        br = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "scripts", "modal"], cwd=ROOT,
                               capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return _item("git", "Code tree clean and on the right branch", "warn", "git not available", required=False)
    ok_branch = br == "main" or br.startswith(("feat/ui-", "data/"))
    st = "fail" if dirty else ("pass" if ok_branch else "warn")
    return _item("git", "Code tree clean and on the right branch", st,
                 f"branch {br}; " + (f"{len(dirty)} uncommitted code file(s)" if dirty else "no uncommitted code"),
                 "commit or stash the code changes so the receipt points at a real commit" if dirty
                 else ("" if ok_branch else "switch to main"))


def check_recorder(cfg):
    ff = shutil.which("ffmpeg")
    chrome = Path("/opt/google/chrome/chrome").exists() or shutil.which("google-chrome")
    pw = False
    for py in (os.environ.get("WN_PY"), "/home/box/.local/bin/python", "/usr/bin/python3", sys.executable):
        if py and Path(py).exists():
            try:
                if subprocess.run([py, "-c", "import playwright"], capture_output=True, timeout=20).returncode == 0:
                    pw = True
                    break
            except (OSError, subprocess.SubprocessError):
                pass
    miss = [n for n, ok in (("ffmpeg", ff), ("Chrome", chrome), ("Playwright", pw)) if not ok]
    return _item("recorder", "Recorder ready (graph clips, PR review clip)", "warn" if miss else "pass",
                 "missing: " + ", ".join(miss) if miss else "ffmpeg, Chrome and Playwright found",
                 "pip install playwright; apt install ffmpeg" if miss else "", required=False)


def check_eligibility(cfg):
    r = E.check(cfg)
    drift = []
    try:
        from wattnot.e2e import fullstack as F
        tabs = {"baseten": F.feature_table, "modal_a100": F.gpu_feature_table}
        if cfg["provider"] in tabs:
            drift = E.normalize_stage_features({"features": tabs[cfg["provider"]]()}, cfg["provider"])["drift"]
    except Exception as e:  # noqa: BLE001
        drift = [{"id": "import", "runner": type(e).__name__, "eligibility": "?"}]
    bad = r["problems"] + [f"{d['id']}: runner {d['runner']} vs {d['eligibility']}" for d in drift]
    return _item("eligibility", "Eligibility table matches the runner's feature table", "fail" if bad else "pass",
                 "; ".join(bad) if bad else f"{r['counts']['eligible']} eligible, {r['counts']['ineligible']} "
                 f"ineligible of {r['counts']['total']}; runner table agrees",
                 "fix the model choice or the runner table" if bad else "")


def check_runner(cfg):
    ok = E.PROVIDERS[cfg["provider"]]["runner"] is not None
    return _item("runner", "A runner exists for this configuration", "pass" if ok else "fail",
                 "scripts/e2e/" + ("baseten_stage.py" if cfg["provider"] == "baseten" else "modal_stage.py")
                 if ok else "E2E H100 and combined stages are not wired to the UI yet",
                 "" if ok else "pick Baseten or Modal A100")


CHECKS = [check_runner, check_keys, check_model, check_caps, check_refusals, check_modal, check_servers,
          check_git, check_recorder, check_eligibility]


def checklist(cfg: dict, *, skip_network: bool = False) -> dict:
    items = []
    for fn in CHECKS:
        if skip_network and fn is check_model:
            items.append(_item("model", "Model id reachable", "warn", "skipped in this check", required=False))
            continue
        try:
            items.append(fn(cfg))
        except Exception as e:  # noqa: BLE001
            items.append(_item(fn.__name__, fn.__name__, "fail", f"check crashed ({type(e).__name__})"))
    ready = all(i["state"] == "pass" for i in items if i["required"])
    return {"checked_at": now(), "items": items, "ready": ready, "spends_money": False}


# ---------------------------------------------------------------- jobs
def command(cfg: dict, mode: str) -> tuple[list[str], dict, str]:
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"}
    n = cfg["rounds"]
    if cfg["provider"] == "modal_a100":
        rounds = ["r04", "r05", "r06"][:n]
        if cfg.get("smoke") and mode == "live":
            env.update(WATTNOT_MODAL_GO="1")
            tag = "smoke-ui" + datetime.now(UTC).strftime("%H%M")
            return ([PY, "scripts/e2e/modal_full_round.py", "--round", tag, "--limit", str(cfg["smoke"]["limit"]),
                     "--no-spark", "--events", "{job}/events.jsonl"], env, "MEASURED")
        if mode == "mock":
            return ([PY, "scripts/e2e/modal_full_round.py", "--round", "ui-" + rounds[0], "--fake", "--limit", "3"],
                    env, "MOCK")
        env.update(WATTNOT_MODAL_GO="1", WN_PY="/usr/bin/python3")
        cmd = ["/usr/bin/python3", "scripts/e2e/modal_stage.py", "--rounds", *rounds]
        return (cmd + ([] if cfg["open_pr"] else ["--no-pr"]), env, "MEASURED")
    if cfg["provider"] == "baseten":
        rounds = ["r01", "r02", "r03"][:n]
        if mode == "mock":
            return ([], env, "REPLAY")
        cmd = [PY, "scripts/e2e/baseten_stage.py", "--rounds", *rounds, "--pr-round", rounds[-1] if cfg["open_pr"] else "none"]
        return (cmd, env, "MEASURED")
    raise ValueError("no runner for this provider")


def _replay(job: dict, src: Path):
    """Mock Baseten: replay the recorded r01 events for both arms, labelled REPLAY."""
    log = job["dir"] / "events.jsonl"
    for arm in ("without_wattnot", "with_wattnot"):
        f = src / f"events-{arm}.jsonl"
        if not f.exists():
            continue
        for ln in f.read_text().splitlines():
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            ev["label_override"] = "REPLAY"
            ev["arm"] = arm
            with log.open("a") as fh:
                fh.write(json.dumps(ev) + "\n")
            time.sleep(job.get("replay_delay", 0.15))
    job.update(state="finished", rc=0, ended=now())


def start_job(cfg: dict, mode: str, *, spawn=subprocess.Popen) -> dict:
    cmd, env, label = command(cfg, mode)
    jid = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    d = JOBS_DIR / jid
    d.mkdir(parents=True, exist_ok=True)
    cmd = [x.replace("{job}", str(d)) for x in cmd]
    (d / "config.json").write_text(json.dumps({"config": cfg, "mode": mode, "label": label, "cmd": cmd}, indent=1))
    job = {"id": jid, "config": cfg, "mode": mode, "label": label, "state": "running", "started": now(),
           "dir": d, "cmd": " ".join(cmd) or "replay of data/baseten-1101/r01/live"}
    JOBS[jid] = job
    if not cmd:
        threading.Thread(target=_replay, args=(job, ROOT / "data" / "baseten-1101" / "r01" / "live"),
                         daemon=True).start()
        return job
    out = (d / "runner.log").open("w")
    p = spawn(cmd, cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
    job["pid"] = getattr(p, "pid", None)

    def wait():
        rc = p.wait()
        job.update(state="finished" if rc == 0 else "failed", rc=rc, ended=now())
    threading.Thread(target=wait, daemon=True).start()
    return job


def public(job: dict) -> dict:
    return {k: (str(v) if isinstance(v, Path) else v) for k, v in job.items()}


SECRETISH = re.compile(r"(api[-_ ]?key|token|secret|authorization)\s*[:=]\s*\S+", re.I)


# ---------------------------------------------------------------- real round data
def _get(d, *ks):
    for k in ks:
        d = (d or {}).get(k) if isinstance(d, dict) else None
    return d


def rounds() -> list[dict]:
    files = sorted((ROOT / "data" / "baseten-1101").glob("*/smoke-matrix/*.json")) + \
        sorted((ROOT / "out" / "matrix").glob("r0*.json")) + \
        sorted((ROOT / "data" / "modal-full-1101").glob("*/round.json"))
    out = []
    for f in files:
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        rdir = f.parent.parent if f.parent.name == "smoke-matrix" else f.parent
        prov = "modal_a100" if "modal" in f.as_posix() else "baseten"
        res = d.get("results") or {}
        pa = res.get("per_arm") or {}
        arms = {}
        for arm in ("without_wattnot", "with_wattnot"):
            a = pa.get(arm) or {}
            en = a.get("energy_j") or a.get("energy_wh") or {}
            arms[arm] = {"accuracy": a.get("accuracy"), "answers": a.get("answers"), "correct": a.get("correct"),
                         "energy": en.get("value"), "energy_unit": "J" if "energy_j" in a else "Wh",
                         "energy_label": en.get("label"), "usd": _get(a, "usd", "value"),
                         "p99_s": _get(a, "item_latency_s", "p99"),
                         "j_per_correct": res.get("j_per_correct_" + arm.split("_")[0])}
        feats = rdir / "features"
        sf = feats / "stage_features.json"
        table = E.normalize_stage_features(json.loads(sf.read_text()) if sf.exists()
                                           else {"features": d.get("features") or []}, prov)
        smoke = bool(d.get("smoke") or d.get("fake"))
        out.append({"id": f"{prov}:{d.get('round') or d.get('tag') or rdir.name}", "provider": prov,
                    "round": d.get("round"), "status": d.get("status"), "model": d.get("model"),
                    "label": "MOCK" if d.get("fake") else (d.get("label") or "MEASURED"), "smoke": smoke,
                    "started_utc": d.get("started_utc"), "ended_utc": d.get("ended_utc"),
                    "arms": arms, "energy_saved_pct": res.get("energy_saved_pct"),
                    "usd_saved_pct": res.get("usd_saved_pct"), "spend": d.get("spend"),
                    "spark_agreement": _get(d, "spark_summary", "agreement"),
                    "features": table["table"], "drift": table["drift"],
                    "source": f.relative_to(ROOT).as_posix(),
                    "receipt": (feats / "receipt.json").relative_to(ROOT).as_posix() if (feats / "receipt.json").exists() else None,
                    "events": sorted(p.relative_to(ROOT).as_posix() for p in rdir.rglob("events*.jsonl"))[:4]})
    return out


# ---------------------------------------------------------------- approvals evidence
def approvals() -> list[dict]:
    out = []
    for ap in sorted((ROOT / "data").glob("*-1101/**/features/approval.json")):
        if "/fake/" in ap.as_posix():
            continue
        feats = ap.parent
        try:
            a = json.loads(ap.read_text())
        except ValueError:
            continue
        pr = {}
        if (feats / "pr" / "result.json").exists():
            try:
                pr = json.loads((feats / "pr" / "result.json").read_text())
            except ValueError:
                pr = {}
        rid = feats.parent.relative_to(ROOT).as_posix()
        ev = a.get("evidence") or _get(a, "review", "evidence") or _matrix_evidence(feats.parent)
        rv = REVIEWS / (rid.replace("/", "__") + ".json")
        out.append({"id": rid, "risk": a.get("risk"), "reasons": a.get("reasons"),
                    "held_change": a.get("held_change") or {"kind": "reasoning_effort",
                                                             "change": {"reasoning_effort": "low", "step_up": "medium"}},
                    "evidence": ev, "checks": verify(ev, pr), "runner_review": a.get("review") or {},
                    "pr": {"patch_sha256": pr.get("patch_sha256"), "receipt_sha256": pr.get("receipt_sha256"),
                           "files": pr.get("files_modified"), "mock": pr.get("mock"), "opened": pr.get("open_pr"),
                           "url": pr.get("pr_url") or pr.get("url"),
                           "label": "MOCK" if pr.get("mock") else "LIVE"} if pr else None,
                    "decision": json.loads(rv.read_text()) if rv.exists() else None,
                    "source": ap.relative_to(ROOT).as_posix()})
    return out


def _matrix_evidence(rdir) -> dict:
    """Modal rounds keep the numbers in the round matrix, not in approval.json."""
    for m in sorted(rdir.glob("smoke-matrix/*.json")) + sorted(rdir.glob("matrix/*.json")):
        try:
            d = json.loads(m.read_text())
        except ValueError:
            continue
        r = d.get("results") or {}
        arms = r.get("per_arm") or {}
        p99 = lambda k: _get(arms, k, "item_latency_s", "p99")
        return {"accuracy_without": r.get("accuracy_without"), "accuracy_with": r.get("accuracy_with"),
                "p99_without": p99("without_wattnot"), "p99_with": p99("with_wattnot"),
                "j_per_correct_without": r.get("j_per_correct_without"),
                "j_per_correct_with": r.get("j_per_correct_with"),
                "spark_agreement": _get(d, "spark_summary", "agreement"),
                "eye_check": None, "hearing_check": None,
                "source": m.relative_to(ROOT).as_posix()}
    return {}


def verify(ev: dict, pr: dict, p99_pct: float = 10.0) -> list[dict]:
    def c(i, title, ok, detail, required=True):
        return {"id": i, "title": title, "ok": ok, "detail": detail, "required": required}
    aw, aww = ev.get("accuracy_without"), ev.get("accuracy_with")
    pw, pww = ev.get("p99_without"), ev.get("p99_with")
    jw, jww = ev.get("j_per_correct_without"), ev.get("j_per_correct_with")
    sa = ev.get("spark_agreement")
    lim = pw * (1 + p99_pct / 100) if pw else None
    return [
        c("accuracy", "Accuracy not lower", None if aw is None else aww >= aw, f"{aww} vs {aw} without"),
        c("p99", f"p99 within +{p99_pct:g}%", None if lim is None else pww <= lim,
          f"{pww} s vs limit {lim:.3f} s" if lim else "no p99 recorded"),
        c("energy", "Joules per correct answer lower", None if jw is None else jww < jw, f"{jww} vs {jw} J"),
        c("spark", "Spark agrees with the answer checks (>= 90%)", None if sa is None else sa >= 0.9, f"{sa}"),
        c("eye", "Eye check passed", ev.get("eye_check"), "not run (smoke)" if ev.get("eye_check") is None else str(ev.get("eye_check")), False),
        c("hearing", "Hearing check passed", ev.get("hearing_check"), "not run (smoke)" if ev.get("hearing_check") is None else str(ev.get("hearing_check")), False),
        c("pr", "PR patch is tied to this round's receipt", bool(pr.get("patch_sha256") and pr.get("receipt_sha256")) if pr else None,
          (pr.get("receipt_uri") or "no PR in this round") if pr else "no PR in this round", False),
    ]


def decide(rid: str, decision: str, note: str, reviewer: str) -> dict:
    if decision not in ("approve", "reject", "hold"):
        raise ValueError("decision must be approve, reject or hold")
    item = next((a for a in approvals() if a["id"] == rid), None)
    if item is None:
        raise KeyError(rid)
    req = [c for c in item["checks"] if c["required"]]
    if decision == "approve" and not all(c["ok"] is True for c in req):
        raise PermissionError("cannot approve: a required check did not pass or has no data")
    rec = {"round": rid, "decision": decision, "reviewer": reviewer, "note": note[:500], "at": now(),
           "risk": item["risk"], "held_change": item["held_change"], "checks": item["checks"],
           "pr": item["pr"], "auto_approve": False, "label": "MEASURED",
           "evidence_sha256": hashlib.sha256(json.dumps(item["evidence"], sort_keys=True, default=str).encode()).hexdigest()}
    REVIEWS.mkdir(parents=True, exist_ok=True)
    (REVIEWS / (rid.replace("/", "__") + ".json")).write_text(json.dumps(rec, indent=1))
    return rec


# ---------------------------------------------------------------- routes
def router() -> APIRouter:
    r = APIRouter(prefix="/api/launch")

    async def body(request: Request) -> dict:
        try:
            return await request.json()
        except ValueError:
            return {}

    @r.get("/options")
    def options():
        return {"providers": {k: {"label": v["label"], "default_model": v["default_model"],
                                  "wired": v["runner"] is not None, "energy_label": v["energy_label"],
                                  "spend": v["spend"]} for k, v in E.PROVIDERS.items()},
                "models": E.MODELS, "main_models": list(E.MAIN_MODELS), "datasets": DATASETS, "caps": CAPS,
                "approvals": "hold_high_risk"}

    @r.post("/eligibility")
    async def elig(request: Request):
        try:
            return E.check(norm_config((await body(request)).get("config") or {}))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)

    @r.post("/checklist")
    async def chk(request: Request):
        try:
            cfg = norm_config((await body(request)).get("config") or {})
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        return await asyncio.to_thread(checklist, cfg)

    @r.post("/estimate")
    async def est(request: Request):
        try:
            cfg = norm_config((await body(request)).get("config") or {})
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        e = estimate(cfg)
        if e.get("available"):
            e["confirm_token"] = sign(cfg, int(time.time()) + TOKEN_TTL_S)
        return {"config": cfg, **e}

    @r.post("/start")
    async def start(request: Request):
        b = await body(request)
        try:
            cfg = norm_config(b.get("config") or {})
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        mode = b.get("mode") or "mock"
        if mode not in ("mock", "live"):
            return JSONResponse({"error": "mode must be mock or live"}, 400)
        if not E.PROVIDERS[cfg["provider"]]["runner"]:
            return JSONResponse({"error": "no runner for this configuration"}, 400)
        if mode == "live":
            if not verify_token(cfg, b.get("confirm_token")):
                return JSONResponse({"error": "confirm the cost first (token missing, expired or for another config)"}, 403)
            cl = await asyncio.to_thread(checklist, cfg)
            if not cl["ready"]:
                return JSONResponse({"error": "pre-launch checklist not passing", "checklist": cl}, 409)
            if any(j["state"] == "running" and j["mode"] == "live" for j in JOBS.values()):
                return JSONResponse({"error": "a paid run is already going; one heavy job at a time"}, 409)
        return public(start_job(cfg, mode))

    @r.get("/jobs")
    def jobs():
        return {"jobs": [public(j) for j in sorted(JOBS.values(), key=lambda j: j["started"], reverse=True)]}

    @r.get("/jobs/{jid}")
    def job(jid: str):
        j = JOBS.get(jid)
        return public(j) if j else JSONResponse({"error": "no such job"}, 404)

    @r.get("/jobs/{jid}/stream")
    async def stream(jid: str, request: Request):
        j = JOBS.get(jid)
        if not j:
            return JSONResponse({"error": "no such job"}, 404)

        async def gen():
            pos = {"runner.log": 0}
            while True:
                if await request.is_disconnected():
                    return
                for f in j["dir"].glob("*events*.jsonl"):
                    pos.setdefault(f.name, 0)
                for name in list(pos):
                    f = j["dir"] / name
                    if f.exists():
                        with f.open(errors="ignore") as fh:
                            fh.seek(pos[name])
                            chunk = fh.read()
                            pos[name] = fh.tell()
                        for ln in chunk.splitlines():
                            ln = SECRETISH.sub(r"\1=[hidden]", ln)
                            kind = "log" if name == "runner.log" else "event"
                            yield f"event: {kind}\ndata: {json.dumps({'line': ln, 'label': j['label']})}\n\n"
                if j["state"] != "running":
                    yield f"event: done\ndata: {json.dumps(public(j))}\n\n"
                    return
                await asyncio.sleep(0.5)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @r.get("/file")
    def file(path: str):
        """Read-only view of a run artefact so every number can link to its source.
        Only data/, out/ and docs/ files; never env or key files."""
        from fastapi.responses import FileResponse, PlainTextResponse
        f = (ROOT / path).resolve()
        ok = any(f.is_relative_to(ROOT / d) for d in ("data", "out", "docs")) and f.is_file() \
            and f.suffix in (".json", ".jsonl", ".html", ".md", ".csv", ".log", ".txt", ".mp4", ".png") \
            and not re.search(r"(env|key|secret|token)", f.name, re.I)
        if not ok:
            return JSONResponse({"error": "not a viewable run file"}, 404)
        if f.suffix in (".log", ".jsonl", ".txt"):
            return PlainTextResponse(SECRETISH.sub(r"\1=[hidden]", f.read_text(errors="ignore")[-400_000:]))
        return FileResponse(f)

    @r.get("/rounds")
    def rnds():
        return {"rounds": rounds(), "note": "smoke rounds are 3 items per arm; full rounds land in out/matrix"}

    @r.get("/approvals")
    def apps():
        return {"items": approvals(), "policy": "high-risk changes are held for a reviewer; never auto-approved"}

    @r.post("/approvals/decide")
    async def dec(request: Request):
        b = await body(request)
        try:
            return decide(str(b.get("id")), str(b.get("decision")), str(b.get("note") or ""),
                          str(b.get("reviewer") or "Akash K Raju"))
        except KeyError:
            return JSONResponse({"error": "no such held change"}, 404)
        except PermissionError as e:
            return JSONResponse({"error": str(e)}, 409)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)

    return r
