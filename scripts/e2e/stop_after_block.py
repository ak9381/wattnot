"""Stop a running long-run driver cleanly right after one block ends, and never relaunch it.

Polls the driver's own driver_state.json and heartbeat.json (read-only). Once BLOCK has really
finished (done, skipped, dropped, lost, or failed on its last attempt), or the heartbeat shows any
block whose id starts with a stop prefix (a safety net: nothing past BLOCK may run), it sends the
driver one SIGINT. The driver's own handler then kills its child processes and keeps the VM
(a stop is never "end of plan", so the VM is not deleted). The watcher waits for the driver to
exit, snapshots driver_state.json, and exits. It does nothing else: no SSH, no relaunch.

    python scripts/e2e/stop_after_block.py --out OUT --pid PID --block p2-agentwaste --stop-prefix p1r
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

FINAL = {"done", "skipped", "dropped", "lost"}


def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def should_stop(state: dict, hb: dict, block: str, stop_prefixes: tuple[str, ...]) -> str | None:
    """Why to stop now, or None to keep waiting."""
    cur = str(hb.get("block") or "")
    if any(cur.startswith(p) for p in stop_prefixes):
        return f"heartbeat already on {cur}"
    info = (state.get("blocks") or {}).get(block) or {}
    st = info.get("status")
    if st in FINAL:
        return f"{block} {st}"
    if st == "failed" and (info.get("attempt") == 2 or (cur and cur != block)):
        return f"{block} failed (attempt {info.get('attempt')})"   # no retry left
    return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:   # an exited child not yet reaped is a zombie: gone for our purposes
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--block", required=True)
    ap.add_argument("--stop-prefix", action="append", default=[])
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--grace", type=float, default=300.0)
    ap.add_argument("--dry", action="store_true", help="say what it would do, send nothing")
    a = ap.parse_args(argv)
    log = lambda m: print(f"{time.strftime('%Y-%m-%d %H:%M:%S %Z')} {m}", flush=True)  # noqa: E731
    log(f"armed: pid {a.pid}, stop after {a.block}, safety prefixes {a.stop_prefix}")
    why = None
    while alive(a.pid):
        why = should_stop(_read(a.out / "driver_state.json"), _read(a.out / "heartbeat.json"),
                          a.block, tuple(a.stop_prefix))
        if why:
            break
        time.sleep(a.interval)
    if not why:
        log("driver exited on its own; nothing sent")
        return 0
    if a.dry:
        log(f"DRY: would SIGINT {a.pid} ({why})")
        return 0
    log(f"{why}: SIGINT {a.pid}")
    os.kill(a.pid, signal.SIGINT)
    t0 = time.time()
    while alive(a.pid) and time.time() - t0 < a.grace:
        time.sleep(1)
    if alive(a.pid):
        log(f"driver still alive after {a.grace:.0f} s; NOT escalating, a person must look")
        return 1
    snap = a.out / "driver_state.after-pass2-stop.json"
    shutil.copy2(a.out / "driver_state.json", snap)
    log(f"driver stopped; state saved to {snap.name}; nothing relaunched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
