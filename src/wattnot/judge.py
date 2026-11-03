# ruff: noqa: E501
"""WattNot for judges, one command on Windows, macOS and Linux.

    python -m wattnot.judge            # doctor, then the best mode this machine supports
    python -m wattnot.judge doctor     # checks only, changes nothing
    python -m wattnot.judge savers     # live token savers on the Meta API (needs META_API_KEY)
    python -m wattnot.judge replay [name]   # dashboard on a recorded real run (MEASURED)
    python -m wattnot.judge sim        # SIMULATED run, every number MOCK
    python -m wattnot.judge smoke      # under a minute, no GPU, no key, no spend
    python -m wattnot.judge gpu        # optional: power caps on your own GPU + vLLM (needs root)

Settings come from .env (copy .env.example). Results go to out/judge-<time>/.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path.cwd() if (Path.cwd() / "replays").is_dir() else Path(__file__).resolve().parents[2]
PY = sys.executable
REPLAYS = ROOT / "replays"


def say(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


def env(name: str, default: str = "") -> str:
    v = os.environ.get(name, "").strip()
    return default if not v or v.startswith("your-") else v


def load_env() -> None:
    from wattnot.doctor import load_dotenv
    load_dotenv(ROOT / ".env", os.environ)
    if os.environ.get("META_API_KEY", "").startswith("your-"):
        os.environ["META_API_KEY"] = ""


def run(args: list[str], **kw) -> int:
    """Run a step with this Python; never raise, return the exit code."""
    try:
        return subprocess.call([PY, *args], cwd=ROOT, **kw)
    except KeyboardInterrupt:
        return 130


def doctor_mode() -> str:
    out = subprocess.run([PY, "-m", "wattnot.cli", "doctor", "--json"], cwd=ROOT,
                         capture_output=True, text=True)
    try:
        return json.loads(out.stdout)["mode"]
    except (ValueError, KeyError):
        return "sim"


def free_port() -> int:
    from wattnot.demo import find_free_port
    want = int(env("WATTNOT_PORT", "8765"))
    port = find_free_port(want)
    if port != want:
        print(f"port {want} is busy, using {port}")
    return port


def run_savers(out: Path) -> int:
    if not env("META_API_KEY"):
        print("META_API_KEY is not set. Put your key in .env (see .env.example).")
        return 1
    s = out / "savers"
    s.mkdir(parents=True, exist_ok=True)
    ledger = str(s / "meta_spend.json")
    cap = env("WATTNOT_MAX_SPEND", "0.50")
    say("Key check: one tiny Meta API call")
    if run(["-m", "wattnot.cli", "meta", "check", "--ledger", ledger]) != 0:
        print("key check failed: check META_API_KEY in .env")
        return 1
    say("Cheapest-first routing with step-up and thinking budget (live Meta API, MEASURED tokens/latency/cost)")
    if run(["-m", "wattnot.cli", "meta", "session", "--steps", "3", "--ledger", ledger,
            "--out", str(s / "session.json"), "--cache-dir", str(s / "cache")]) != 0:
        print("session step failed; continuing")
    say(f"Agentwaste proxy + context saver on agent tasks (live, capped at ${cap})")
    if run(["scripts/agentwaste_v3_bench.py", "--reps", "1", "--max-spend", cap, "--out", str(s / "agentwaste")],
           env={**os.environ, "WATTNOT_AW_CEILING": cap}) != 0:
        print("agentwaste step failed; continuing")
    run(["-m", "wattnot.cli", "meta", "spend", "--ledger", ledger])
    print("Energy for hosted API calls is an ESTIMATE (no GPU meter on a hosted API).")
    return 0


def run_sim(out: Path, quiet: bool = False) -> int:
    out.mkdir(parents=True, exist_ok=True)
    if not quiet:
        say("SIMULATED run: fake GPU + oracle model. Every number is MOCK.")
    kw = {"stdout": subprocess.DEVNULL} if quiet else {}
    rc = run(["-m", "wattnot.longrun.harness", "--fake", "--cap", "375", "--per-tier", "3",
              "--out", str(out / "sim")], **kw)
    if not quiet:
        print("Open the dashboard on a recorded real run with: python -m wattnot.judge replay")
    return rc


def replay_names() -> list[str]:
    return sorted(p.name for p in REPLAYS.iterdir() if (p / "events.jsonl").exists())


def run_replay(name: str = "h100-max-1103") -> int:
    ev = REPLAYS / name / "events.jsonl"
    if not ev.exists():
        print(f"no replay '{name}'. Try: {' '.join(replay_names())}")
        return 1
    say(f"MEASURED replay of {name} (recorded run, nothing live). Ctrl-C to stop.")
    port = free_port()
    print(f"Open: http://127.0.0.1:{port}/")
    return run(["-m", "wattnot.dash", "--replay-only", "--events", str(ev), "--port", str(port)])


def run_smoke(out: Path) -> int:
    say("Smoke test: doctor, simulated run, replay server up and down (no GPU, no key, no spend)")
    if run(["-m", "wattnot.cli", "doctor"], stdout=subprocess.DEVNULL) != 0:
        print("SMOKE FAIL: doctor")
        return 1
    if run_sim(out, quiet=True) != 0 or not (out / "sim" / "tiers_summary.json").exists():
        print("SMOKE FAIL: simulated run")
        return 1
    port = free_port()
    log = open(out / "dash.log", "w")
    p = subprocess.Popen([PY, "-m", "wattnot.dash", "--replay-only", "--events",
                          str(REPLAYS / "h100-max-1103" / "events.jsonl"), "--port", str(port)],
                         cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    ok = False
    try:
        for _ in range(30):
            time.sleep(1)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read()
                ok = True
                break
            except OSError:
                if p.poll() is not None:
                    break
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
        log.close()
    if not ok:
        print(f"SMOKE FAIL: dashboard did not answer (see {out / 'dash.log'})")
        return 1
    print("SMOKE PASS")
    return 0


def stock_limit_w() -> str:
    try:
        r = subprocess.run(["nvidia-smi", "-i", "0", "--query-gpu=power.default_limit",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip().split(".")[0]
    except (OSError, subprocess.SubprocessError):
        return ""


def restore_cap(stock: str) -> None:
    if env("WATTNOT_RESTORE_CAP", "1") != "1" or not stock:
        return
    try:
        rc = subprocess.call(["nvidia-smi", "-i", "0", "-pl", stock],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        rc = 1
    print(f"power limit restored to stock {stock} W" if rc == 0
          else f"WARNING: could not restore {stock} W; run: sudo nvidia-smi -i 0 -pl {stock}")


def run_gpu(out: Path) -> int:
    url = env("WATTNOT_VLLM_URL")
    if not url:
        print("set WATTNOT_VLLM_URL in .env (your vLLM chat URL)")
        return 1
    stock = stock_limit_w()
    cap = env("WATTNOT_CAP_W", "375")
    out.mkdir(parents=True, exist_ok=True)
    try:
        say(f"GPU run: stock vs {cap} W on 4 prompt tiers, quality gate on (MEASURED via NVML)")
        if run(["-m", "wattnot.longrun.harness", "--cap", cap, "--url", url,
                "--model-config", env("WATTNOT_MODEL_CONFIG", "muse-glimmer-30b"),
                "--per-tier", env("WATTNOT_PER_TIER", "5"), "--out", str(out / "gpu")]) != 0:
            print("GPU step failed (see above); the power limit is restored on exit")
        if env("META_API_KEY"):
            run_savers(out)
    finally:
        restore_cap(stock)
    return 0


def _stop(*_):
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv.pop(0) if argv else "auto"
    load_env()
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    out = ROOT / "out" / f"judge-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    try:
        if mode == "doctor":
            return run(["-m", "wattnot.cli", "doctor"])
        if mode == "auto":
            rc = run(["-m", "wattnot.cli", "doctor"])
            if rc == 2:
                return rc
            m = doctor_mode()
            if m == "full":
                rc = run_gpu(out)
            elif env("META_API_KEY"):
                rc = run_savers(out)
            else:
                print("No META_API_KEY: showing our recorded real runs instead (MEASURED replay).")
                return run_replay()
        elif mode == "gpu":
            run(["-m", "wattnot.cli", "doctor"])
            if doctor_mode() != "full":
                print("power caps are off on this machine (see doctor above); running the token savers instead")
                if not env("META_API_KEY"):
                    return run_replay()
                rc = run_savers(out)
            else:
                rc = run_gpu(out)
        elif mode == "savers":
            rc = run_savers(out)
        elif mode == "sim":
            rc = run_sim(out)
        elif mode == "smoke":
            out.mkdir(parents=True, exist_ok=True)
            rc = run_smoke(out)
        elif mode == "replay":
            return run_replay(*(argv[:1] or []))
        else:
            print(__doc__)
            return 1
    except KeyboardInterrupt:
        print("\nstopped")
        return 130
    if out.exists():
        print(f"\nResults: {out.relative_to(ROOT)}{os.sep}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
