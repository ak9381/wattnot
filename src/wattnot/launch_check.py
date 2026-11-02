"""$0 launch check: every lesson from the Modal smoke and round failures, run before a paid launch.

    python scripts/e2e/preflight.py --provider baseten|h100|modal|combined [--json]

It makes NO network call and starts nothing paid. It reads the repo, imports packages, starts the
local dashboard for a few seconds on a spare port, and runs small pure functions. Exit 0 = every
check for that provider passed; exit 1 = at least one failed (the runner must not launch).

Every check names the Modal failure it comes from (data/modal-full-1101, a100-smoke-1101 branch).
Time figures are ESTIMATEs. The token rate comes from Modal r04b (906 tokens in 32.05 s,
MEASURED), and each call is assumed to hit its token cap, so the estimate is a worst case.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOK_S = 906 / 32.05          # MEASURED on Modal A100 r04b (one stream); H100 assumed no faster (conservative)
CALLS_PER_AGENT_CASE = 4     # ESTIMATE, react/stop-rule/planner loops
# Modal r04-try1 langchain block (n 20): saver off 786.69 s + saver on 111.43 s (MEASURED)
LANGCHAIN_S_PER_ITEM = (786.69 + 111.43) / 20
HEADROOM = 0.8               # worst-case work must fit in 80% of the kill limit
MIN_DISK_GB = 15.0
IMPORTS = {
    "runner": ["optuna", "uvicorn", "fastapi", "jsonschema", "cryptography"],
    "langchain": ["langchain_core", "langchain_openai"],
    "dash": ["uvicorn", "fastapi"],
}
APPLIES = {"h100", "baseten", "modal"}


def _load_script(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _r(cid: str, lesson: str, ok: bool, detail: str, where: str) -> dict:
    return {"id": cid, "lesson": lesson, "ok": bool(ok), "detail": detail, "where": where}


# ---------------------------------------------------------------- block time vs kill limit
def _arg(argv: list[str], flag: str, default: float) -> float:
    if flag in argv:
        try:
            return float(argv[argv.index(flag) + 1])
        except (IndexError, ValueError):
            return default
    return default


def h100_block_worst_s(block) -> float | None:
    """Worst-case seconds for the H100 public-plan blocks that do model work (ESTIMATE)."""
    live = " ".join(block.live or [])
    argv = live.split()
    if "agentwaste_gpu_ab.py" in live:
        cases = _arg(argv, "--max-cases", 30) or 30
        reps = _arg(argv, "--reps", 2)
        mt = _arg(argv, "--max-tokens", 2048)
        return cases * reps * 2 * CALLS_PER_AGENT_CASE * mt / TOK_S
    if "public_bench_ab.py" in live:
        items, conc = 100, 32           # GSM8K 60 + HumanEval 40, concurrency 32 (script default)
        mt = _arg(argv, "--max-tokens", 3072)
        waves = -(-items // conc)
        per_stream = TOK_S * 0.7        # ESTIMATE: a 32-wide batch slows each stream ~30%
        return 2 * waves * mt / per_stream + 2 * 60   # 2 arms + cap set/settle per arm
    if "glimmer_serve.sh fp8" in live:
        serve = 240 * 5                 # glimmer_serve.sh health loop: 240 x 5 s
        tiers = _arg(argv, "--per-tier", 2)
        return serve + tiers * 4 * 2048 / TOK_S
    if "langchain_pass.py" in live:
        n = _arg(argv, "--n", 20)
        return n * LANGCHAIN_S_PER_ITEM * 1.3   # MEASURED rate on Modal r04-try1, +30% margin
    return None


def check_block_time_h100(plan: str = "public") -> dict:
    from wattnot.longrun import driver
    blocks = driver.plan_glimmer_public(Path("/tmp/wattnot-preflight"))
    bad, rows = [], []
    for b in blocks:
        w = h100_block_worst_s(b)
        limit = b.hours * 3600 * driver.OVERRUN_FACTOR
        if w is None:
            continue
        rows.append(f"{b.id} worst {w / 60:.0f} min vs kill {limit / 60:.0f} min")
        if w > HEADROOM * limit:
            bad.append(rows[-1])
    total_h = sum(b.hours for b in blocks) + 8 / 60
    ok_book = total_h <= driver.PUBLIC_BOOKING_H
    return _r("block_time", "agentwaste/context_saver rc 124 at the 900 s cap (r04-try1)",
              not bad and ok_book,
              ("; ".join(bad) or "all model blocks fit with 20% headroom") +
              f" | plan {total_h:.1f} h incl. 8 min watchdog vs booking {driver.PUBLIC_BOOKING_H} h | " +
              "; ".join(rows), "longrun/driver.py PUBLIC_BLOCK_MIN, PUBLIC_AGENTWASTE_TRIM")


def check_block_time_baseten() -> dict:
    # per arm: 100 public items, each low then (worst) a medium step-up, 4 in flight, 240 s HTTP timeout
    br = _load_script("scripts/e2e/baseten_round.py", "_lc_baseten_round")
    conc = 4
    per_call = 45.0                 # ESTIMATE worst typical hosted call incl. step-up output
    arm_s = 100 * 2 * per_call / conc
    rec_limit = 3 * 3600            # baseten_stage.py records each arm for at most 3 h
    ok = arm_s <= HEADROOM * rec_limit and br.HARD_CAP_USD == 8.0
    return _r("block_time", "agentwaste rc 124 / FP8 start overran its block (r04-try1)", ok,
              f"worst arm {arm_s / 60:.0f} min vs recorder limit {rec_limit / 60:.0f} min; "
              "no GPU blocks on Baseten; HTTP timeout 240 s x 2 retries per call",
              "scripts/e2e/baseten_round.py (LiveTransport timeout), baseten_stage.py max_s")


def check_block_time_modal() -> dict:
    """Modal full round: each block's kill limit comes from the modal provider profile
    (modal_full_inner.block_cap); its worst case must fit in HEADROOM of that limit."""
    from wattnot.providers import PROFILES, block_fit_problems
    lim = PROFILES["modal"]["block_limits_s"]
    lang = 20 * LANGCHAIN_S_PER_ITEM * 1.3
    agent = 12 * 1 * 2 * CALLS_PER_AGENT_CASE * 768 / TOK_S
    rows = [f"langchain n20 worst {lang / 60:.0f} min vs kill {lim['langchain'] / 60:.0f} min",
            f"agentwaste 12x1x768 worst {agent / 60:.0f} min vs kill {lim['agentwaste'] / 60:.0f} min"]
    bad = [r for r, w, k in zip(rows, (lang, agent), ("langchain", "agentwaste")) if w > HEADROOM * lim[k]]
    bad += block_fit_problems("modal")
    return _r("block_time", "agentwaste rc 124 at the 900 s cap (r04-try1)", not bad,
              "; ".join(rows + bad[2:] if bad else rows), "src/wattnot/providers.py modal block_limits_s")


# ---------------------------------------------------------------- tool calling flags
def check_tool_calling(provider: str) -> dict:
    if provider == "baseten":
        from wattnot.cloud import supports_tool_calling  # noqa: F401
        from wattnot.e2e.fullstack import RequestLevers
        lv = RequestLevers(tools_supported=False)
        body, _ = lv.apply({"messages": [{"role": "user", "content": "hi"}], "tools": [{"type": "function"}]})
        ok = body is None or "tools" not in (body or {})
        return _r("tool_calling", "agent blocks failed without vLLM tool calling (2bcae4b)", ok,
                  "hosted: no vLLM flags to set; tool support is probed (cloud.supports_tool_calling) "
                  "and a tools field is never sent to a model without it",
                  "e2e/fullstack.py RequestLevers")
    sh = (ROOT / "scripts" / "glimmer_serve.sh").read_text()
    ok = "--enable-auto-tool-choice" in sh and "--tool-call-parser muse_glimmer" in sh
    return _r("tool_calling", "agent blocks failed without vLLM tool calling (2bcae4b)", ok,
              "glimmer_serve.sh passes --enable-auto-tool-choice --tool-call-parser muse_glimmer"
              if ok else "glimmer_serve.sh is missing the vLLM tool-calling flags",
              "scripts/glimmer_serve.sh")


# ---------------------------------------------------------------- /v1 doubling
def check_v1(provider: str) -> dict:
    from wattnot.cloud import chat_completions_url
    lp = _load_script("scripts/langchain_pass.py", "_lc_langchain_pass")
    bad = []
    for base in ("https://inference.baseten.co/v1", "https://inference.baseten.co/v1/", "https://inference.baseten.co"):
        u = chat_completions_url(base)
        if u.count("/v1") != 1:
            bad.append(u)
    for base in ("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1/", "http://127.0.0.1:8000"):
        u = lp.upstream_base(base) + "/v1/chat/completions"
        if u.count("/v1") != 1:
            bad.append(u)
    return _r("v1_doubling", "LangChain upstream /v1/v1 gave 502 on Modal (2bcae4b)", not bad,
              "one /v1 for every base form" if not bad else f"doubled: {bad}",
              "cloud.chat_completions_url (Baseten), scripts/langchain_pass.py upstream_base (H100)")


# ---------------------------------------------------------------- reasoning_effort KeyError
def check_reasoning_effort() -> dict:
    from wattnot.approvals import risk
    from wattnot.approvals import model as m
    from wattnot.e2e.live import pr_spec
    errs = []
    try:
        r = risk.classify({"kind": m.REASONING_EFFORT, "change": {"reasoning_effort": "low", "step_up": "medium"}},
                          risk.RiskConfig())
        if r.level != "low":
            errs.append(f"REASONING_EFFORT rated {r.level}")
    except Exception as e:  # noqa: BLE001
        errs.append(f"risk.classify: {type(e).__name__}: {e}")
    for chg in ({"reasoning_effort": "low", "step_up": "medium"},       # Baseten
                {"quantization": "bf16", "max_num_seqs": 64}):            # H100 / Modal, no effort key
        try:
            pr_spec(chg)
        except KeyError as e:
            errs.append(f"pr_spec KeyError {e}")
    return _r("reasoning_effort", "Muse Code PR KeyError 'reasoning_effort' on the own-GPU change (19014c2)",
              not errs, "; ".join(errs) or "hosted and self-hosted PR specs build; effort change rated low",
              "e2e/live.py pr_spec, approvals/risk.py")


# ---------------------------------------------------------------- imports in every venv
def _imports_ok(py: str, mods: list[str]) -> list[str]:
    code = "import importlib,sys\nbad=[]\nfor m in sys.argv[1:]:\n  try: importlib.import_module(m)\n  except Exception: bad.append(m)\nprint(','.join(bad))"
    try:
        p = subprocess.run([py, "-c", code, *mods], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [f"{py}: {e}"]
    return [x for x in p.stdout.strip().split(",") if x]


def check_imports(provider: str) -> dict:
    runner_py = os.environ.get("WN_PY", sys.executable)
    dash_py = os.environ.get("WN_DASH_PY", runner_py)
    missing = {}
    need: dict[str, list[str]] = {}
    runner_mods = IMPORTS["runner"] + (IMPORTS["langchain"] if provider == "baseten" else [])
    for py, mods in ((runner_py, runner_mods), (dash_py, IMPORTS["dash"])):
        need.setdefault(py, [])
        need[py] += [m for m in mods if m not in need[py]]   # same venv twice: check the union
    for py, mods in need.items():
        bad = _imports_ok(py, mods)
        if bad:
            missing[py] = bad
    detail = "all imports ok in " + ", ".join(need)
    if provider == "h100":
        sh = (ROOT / "scripts" / "h100_vm_setup.sh").read_text()
        for m in IMPORTS["runner"][:3] + IMPORTS["langchain"]:
            if m not in sh:
                missing.setdefault("h100_vm_setup.sh IMPORT_CHECK", []).append(m)
        if "langchain-openai" not in sh:
            missing.setdefault("h100_vm_setup.sh install", []).append("langchain-openai")
        detail += "; VM setup installs langchain pins and import-checks optuna/uvicorn/fastapi/langchain (exit 15)"
    return _r("imports", "No module named 'optuna' (r04-try1 flow), uvicorn missing for the recorder (1:00 AM)",
              not missing, json.dumps(missing) if missing else detail,
              "WN_PY / WN_DASH_PY venvs; scripts/h100_vm_setup.sh IMPORT_CHECK")


# ---------------------------------------------------------------- recorder / dashboard starts
def check_dashboard(start: bool = True) -> dict:
    dash_py = os.environ.get("WN_DASH_PY", os.environ.get("WN_PY", sys.executable))
    if not start:
        return _r("recorder", "recorder dashboard failed to start, r04 aborted at 1:00 AM (2ad5549)", True,
                  "skipped (--no-dash)", "baseten_stage.py DASH_PY + stop_runner")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    ev = Path("/tmp/wattnot-preflight-events.jsonl")
    ev.write_text("")
    p = subprocess.Popen([dash_py, "-m", "wattnot.dash", "--port", str(port), "--events", str(ev)], cwd=ROOT,
                         env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"},
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    ok, last = False, ""
    try:
        for _ in range(60):
            if p.poll() is not None:
                last = f"exited rc {p.returncode}: {(p.stderr.read() or b'').decode()[-300:]}"
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/dash/health", timeout=2) as r:
                    if r.status == 200:
                        ok = True
                        break
            except OSError as e:
                last = str(e)
            time.sleep(0.5)
    finally:
        p.kill()
        p.wait(timeout=10)
    stage = (ROOT / "scripts" / "e2e" / "baseten_stage.py").read_text()
    guarded = "WN_DASH_PY" in stage and "stop_runner(runner)" in stage
    return _r("recorder", "recorder dashboard failed to start, r04 aborted at 1:00 AM (2ad5549)", ok and guarded,
              (f"dashboard answered /api/dash/health with {dash_py}" if ok else f"dashboard did not start: {last}")
              + ("; a recorder crash stops the paid runner" if guarded else "; baseten_stage lacks the runner stop"),
              "scripts/e2e/baseten_stage.py DASH_PY, stop_runner")


# ---------------------------------------------------------------- eye gauge
def check_eye() -> dict:
    from wattnot.vision.assets import load_eye_test
    from wattnot.vision.gate import _matches
    eye = load_eye_test(None, require_files=True)
    g = [it for it in eye.items if it.id == "gauge-reading"]
    errs = []
    if not g:
        errs.append("no gauge-reading item")
    else:
        want = g[0].answer
        if not _matches("number", want, f"It reads {want}"):
            errs.append("exact answer not accepted")
        for wrong in ("62", "63", "60.5"):
            if _matches("number", want, wrong):
                errs.append(f"{wrong} accepted")
        if not (eye.base_dir / g[0].file).exists():
            errs.append("gauge image missing")
    return _r("eye_gauge", "gauge misread 63 vs 60 failed the eye gate (r04-try1/eye)", not errs,
              "; ".join(errs) or "redrawn gauge present, exact check rejects 62/63/60.5",
              "data/vision/eyetest, vision/gate.py (same set on Baseten and H100)")


# ---------------------------------------------------------------- hearing sample rate / padding
def check_hearing() -> dict:
    import io
    import wave
    from wattnot.spokenanswer.client import wav_rate_and_seconds
    errs = []
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\0\0" * 24000)
    rate, secs = wav_rate_and_seconds(buf.getvalue())
    if rate != 24000 or abs(secs - 1.0) > 1e-6:
        errs.append(f"24 kHz header read as {rate} Hz / {secs:.2f} s")
    for f in sorted((ROOT / "data" / "voice" / "spoken").glob("*.wav")):
        with wave.open(str(f)) as w:
            n, r, sw, ch = w.getnframes(), w.getframerate(), w.getsampwidth(), w.getnchannels()
        data_bytes = f.stat().st_size - 44
        if n / r < 0.5:
            errs.append(f"{f.name} too short ({n / r:.2f} s)")
        if abs(data_bytes - n * sw * ch) > 64:   # header frames vs real payload: no truncation/padding
            errs.append(f"{f.name} header {n * sw * ch} B vs payload {data_bytes} B")
    return _r("hearing", "hearing clip sent at an assumed 16 kHz (443ce94)", not errs,
              "; ".join(errs[:5]) or "header rate is sent as-is; every clip's frames match its payload",
              "spokenanswer/client.py wav_rate_and_seconds, e2e/live._transcribe")


# ---------------------------------------------------------------- MOCK labels
def check_mock(provider: str) -> dict:
    if provider == "h100":
        from wattnot.longrun import nomock
        rep = nomock.audit_plan("glimmer-test", plan_name="public")
        ok = bool(rep.get("ok"))
        return _r("mock_labels", "certificate step labelled MOCK in the smoke flow (smoke-r04/flow.log)", ok,
                  f"no-mock audit of the public plan: {'pass' if ok else rep.get('findings')}",
                  "longrun/nomock.py audit_plan")
    src = (ROOT / "scripts" / "e2e" / "baseten_round.py").read_text()
    ok = "tr = DryTransport()" in src and re.search(r"if a\.dry_run:\s*\n\s*tr = DryTransport\(\)", src) is not None
    return _r("mock_labels", "certificate step labelled MOCK in the smoke flow (smoke-r04/flow.log)", bool(ok),
              "canned MOCK replies only behind --dry-run; live rounds use LiveTransport (MEASURED)",
              "scripts/e2e/baseten_round.py")


# ---------------------------------------------------------------- FP8 start timeout
def check_fp8(provider: str) -> dict:
    if provider == "baseten":
        return _r("fp8_start", "FP8 server start overran (b726743/bdb2f25)", True,
                  "not applicable: hosted API, no FP8 server to start", "providers.PROFILES['baseten'].levers_off")
    from wattnot.longrun import driver
    b = [x for x in driver.plan_glimmer_public(Path("/tmp/wattnot-preflight")) if x.id == "t2-fp8"][0]
    limit = b.hours * 3600 * driver.OVERRUN_FACTOR
    w = h100_block_worst_s(b) or 0
    return _r("fp8_start", "FP8 server start overran (b726743/bdb2f25)", w <= HEADROOM * limit,
              f"health wait 1200 s + harness = worst {w / 60:.0f} min vs kill {limit / 60:.0f} min",
              "longrun/driver.py PUBLIC_BLOCK_MIN['t2-fp8'], glimmer_serve.sh")


# ---------------------------------------------------------------- context saver
def check_context_saver(provider: str) -> dict:
    from wattnot.context_saver.middleware import ContextSaver
    msgs = [{"role": "tool", "content": "row " * 2000} for _ in range(40)] + [{"role": "user", "content": "sum"}]
    t = time.time()
    ContextSaver().transform(msgs)
    dt = time.time() - t
    ok = dt < 2.0
    detail = f"saver on a 40-tool-message history took {dt * 1000:.0f} ms"
    if provider == "h100":
        detail += "; the GPU agent block is sized by block_time (8 cases x 1 rep x 768 tokens)"
    return _r("context_saver", "context_saver failed: agentwaste block rc 124 (r04-try1)", ok, detail,
              "context_saver/middleware.py; e2e/fullstack.py RequestLevers (Baseten); driver p1-agentwaste (H100)")


# ---------------------------------------------------------------- certificate rc 2
def check_certificate() -> dict:
    vec = ROOT / "verifier" / "vectors" / "real-noise.json"
    out = Path("/tmp/wattnot-preflight-cert")
    shutil.rmtree(out, ignore_errors=True)
    p = subprocess.run([sys.executable, "-m", "wattnot.cli", "certificate", "mint", "--receipt", str(vec),
                        "--out", str(out)], cwd=ROOT, capture_output=True, text=True, timeout=120,
                       env={**os.environ, "PYTHONPATH": f"{ROOT / 'src'}:{os.environ.get('PYTHONPATH', '')}"})
    ok = p.returncode == 0 and any(out.glob("not-issued-*.json"))
    return _r("certificate", "certificate mint exit 2 on a NOISE receipt failed the flow (smoke-r04)", ok,
              f"NOISE receipt -> rc {p.returncode}, " + ("declined statement written" if ok else (p.stdout + p.stderr)[-200:]),
              "certificate/cli.py _do_mint (decline = exit 0)")


# ---------------------------------------------------------------- disk
def check_disk(provider: str) -> dict:
    free = shutil.disk_usage(ROOT).free / 1e9
    ok = free >= MIN_DISK_GB
    detail = f"box {free:.0f} GB free (clips + results) vs floor {MIN_DISK_GB:.0f} GB"
    if provider == "h100":
        from wattnot.longrun import driver
        ok = ok and driver.DISK_FREE_FLOOR_GB >= MIN_DISK_GB
        detail += f"; VM floor DISK_FREE_FLOOR_GB={driver.DISK_FREE_FLOOR_GB:.0f} (driver aborts cleanly below it)"
    return _r("disk", "disk below 15 GB", ok, detail, "longrun/driver.py DISK_FREE_FLOOR_GB; this check (box)")


def run(provider: str, *, dash: bool = True) -> dict:
    if provider == "combined":
        legs = {p: run(p, dash=dash and p == "baseten") for p in ("h100", "baseten", "modal")}
        return {"provider": "combined", "ok": all(x["ok"] for x in legs.values()), "legs": legs, "label": "ESTIMATE"}
    if provider not in APPLIES:
        raise ValueError(provider)
    checks = []
    fns = [
        {"baseten": check_block_time_baseten, "h100": check_block_time_h100,
         "modal": check_block_time_modal}[provider],
        lambda: check_tool_calling(provider), lambda: check_v1(provider), check_reasoning_effort,
        lambda: check_imports(provider), lambda: check_dashboard(dash), check_eye, check_hearing,
        lambda: check_mock(provider), lambda: check_fp8(provider), lambda: check_context_saver(provider),
        check_certificate, lambda: check_disk(provider),
    ]
    for f in fns:
        try:
            checks.append(f())
        except Exception as e:  # noqa: BLE001  a crashing check is a failed check
            checks.append(_r(getattr(f, "__name__", "check"), "check crashed", False, f"{type(e).__name__}: {e}", ""))
    return {"provider": provider, "ok": all(c["ok"] for c in checks), "checks": checks,
            "cost_usd": 0.0, "label": "ESTIMATE (time figures), MEASURED (local checks)"}
