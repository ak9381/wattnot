"""llama.cpp `llama-server` adapter for Muse Spark Open.

`server_args` builds the command line. Attention and shared layers go on the
GPU (`--n-gpu-layers 99`). `--cpu-moe` keeps every layer's experts in host RAM;
`--n-cpu-moe N` keeps only the first N layers' experts there, so the other 62-N
layers' experts stay in VRAM (the hot tier). `-ub`/`-b` set the batch size that
op offload sees (warm tier), `--no-op-offload` turns the warm tier off (CPU
comparison), `-t` sets CPU threads for the cold tier. Flags checked against
llama.cpp master `common/arg.cpp` (Oct 2026).

`LlamaServer(dry_run=True)` never starts a process; tests and `--dry-run` use it.
"""

from __future__ import annotations

import subprocess
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from wattnot.spark_open.knobs import SparkKnobs
from wattnot.spark_open.placement import LayerPlacement, Placement


def server_args(gguf: str | Path, knobs: SparkKnobs, *, port: int = 8090,
                binary: str = "llama-server", ctx: int = 16384,
                placement: Placement | None = None) -> list[str]:
    args = [binary, "--model", str(gguf), "--port", str(port), "--host", "127.0.0.1",
            "--ctx-size", str(ctx), "--n-gpu-layers", "99", "--jinja", "--flash-attn", "on",
            "--ubatch-size", str(knobs.ubatch), "--batch-size", str(max(knobs.ubatch, 2048)),
            "--threads", str(knobs.threads)]
    args += (placement or LayerPlacement()).args(knobs)
    if not knobs.op_offload:
        args.append("--no-op-offload")
    return args


def gguf_path(models_dir: str | Path, quant: str) -> Path:
    """First shard of a split GGUF under models_dir/<quant>/."""

    d = Path(models_dir) / quant
    shards = sorted(d.glob("*.gguf")) if d.is_dir() else []
    return shards[0] if shards else d / f"Muse-Spark-Open-816B-A42B-{quant}.gguf"


class LlamaServer:
    """Start, health-check and restart llama-server. One process at a time."""

    def __init__(self, models_dir: str | Path, *, port: int = 8090, dry_run: bool = False,
                 log_dir: str | Path | None = None, load_timeout_s: float = 1800,
                 popen: Callable = subprocess.Popen) -> None:
        self.models_dir, self.port, self.dry_run = Path(models_dir), port, dry_run
        self.log_dir = Path(log_dir) if log_dir else None
        self.load_timeout_s = load_timeout_s
        self._popen = popen
        self._proc = None
        self.knobs: SparkKnobs | None = None
        self.argv: list[str] = []
        self.restarts = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self, knobs: SparkKnobs) -> float:
        """Load the model with these knobs. Returns load seconds (0 in dry run)."""

        self.stop()
        self.knobs = knobs
        self.argv = server_args(gguf_path(self.models_dir, knobs.quant), knobs, port=self.port)
        self.restarts += 1
        if self.dry_run:
            return 0.0
        t0 = time.monotonic()  # pragma: no cover - real host only
        log = None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log = open(self.log_dir / f"llama-{knobs.tag}.log", "w")  # noqa: SIM115
        self._proc = self._popen(self.argv, stdout=log, stderr=subprocess.STDOUT)
        self._wait_healthy()
        return time.monotonic() - t0

    restart = start

    def _wait_healthy(self) -> None:  # pragma: no cover - real host only
        deadline = time.monotonic() + self.load_timeout_s
        url = f"http://127.0.0.1:{self.port}/health"
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError("llama-server exited while loading")
            try:
                with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310
                    if r.status == 200:
                        return
            except OSError:
                pass
            time.sleep(5)
        raise TimeoutError("llama-server did not become healthy")

    def stop(self) -> None:
        if self._proc is not None:  # pragma: no cover - real host only
            self._proc.terminate()
            try:
                self._proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
