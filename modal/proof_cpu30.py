"""Modal proof: one ~30 s CPU function (1 core, 1 GiB) through the $98 spend guard.
Ledger bills the whole local wall time of the ephemeral app (boot + run + teardown) at list
rates, which is deliberately >= what Modal bills for the container."""
import json, os, subprocess, threading, time
from pathlib import Path
import modal

CPU_PER_S = 0.0473 / 3600   # modal billing rates, 29 Oct 2026, 1 core
MEM_PER_S = 0.008 / 3600    # per GiB
APP_NAME = "wattnot-proof-cpu30"
SERVER_TIMEOUT_S = 90                       # the busy() function's own Modal timeout
CLIENT_KILL_S = SERVER_TIMEOUT_S + 30       # local backstop, slightly above the server timeout
app = modal.App(APP_NAME)


def arm_kill_timer(seconds: int = CLIENT_KILL_S, app_name: str = APP_NAME) -> threading.Timer:
    """Last-resort local watchdog: if the remote call hangs past the server timeout (a wedged
    socket the Modal timeout never surfaces), stop the app and exit so no container keeps billing."""
    def fire():
        subprocess.run(["modal", "app", "stop", app_name], timeout=60, check=False)
        os._exit(3)
    t = threading.Timer(seconds, fire)
    t.daemon = True
    t.start()
    return t


@app.function(cpu=1.0, memory=1024, timeout=SERVER_TIMEOUT_S)
def busy(seconds: float = 30.0) -> dict:
    t0 = time.time(); x = 0
    while time.time() - t0 < seconds:
        x += 1
    return {"ran_s": time.time() - t0}


def apps_running() -> list[str]:
    out = subprocess.run(["modal", "app", "list", "--json"], capture_output=True, text=True).stdout
    return [a.get("Description") or a.get("description") for a in json.loads(out or "[]")
            if str(a.get("State") or a.get("state")).lower() not in ("stopped", "stopping")
            and "wattnot-proof" in str(a.get("Description") or a.get("description"))]


if __name__ == "__main__":
    # local-only import: the container does not ship the wattnot package
    from wattnot.spend.guard import GPU_SECONDS, Guard, Ledger, Price, finish
    OUT = Path("docs/runs/proof-20261029")
    g = Guard("modal", Price(GPU_SECONDS, usd_per_s=CPU_PER_S + MEM_PER_S),
              Ledger(OUT / "modal-proof-ledger-run2.jsonl"), hard_usd=0.01, soft_usd=0.009, lag_margin_usd=0)

    def run():
        t0 = time.time()
        with modal.enable_output(), app.run():
            r = busy.remote(30.0)
        return {"seconds": time.time() - t0, **r}

    killer = arm_kill_timer()
    try:
        out = g.call(run, worst_usd=g.price.seconds(120), item="cpu30")
        rec = finish(g, "done", "proof finished", [out], OUT, None, apps_running)
    finally:
        killer.cancel()
    print(json.dumps({"out": out, "receipt": rec, "t_end_utc": time.strftime("%H:%M:%SZ", time.gmtime())}, indent=1))
