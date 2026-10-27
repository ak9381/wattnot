"""Download the Spark Open GGUF shards (UD-IQ2_S first, UD-IQ1_S as the fallback).

The shard counts and sizes below come from the Hugging Face tree listing of
unsloth/Muse-Spark-Open-816B-A42B-GGUF (MEASURED from the repo API, 27 Oct 2026).
`hf download` resumes a half-finished file, so re-running this is safe.

The token, if any, is read by `hf` from HF_TOKEN in the environment. It is never
put on a command line, never printed and never written anywhere by this module.

    python -m wattnot.spark_open.fetch --models-dir /data/spark-open --dry-run
    python -m wattnot.spark_open.fetch --models-dir /data/spark-open
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from wattnot.spark_open.knobs import REPO

# quant -> (shard count, total bytes). MEASURED from the HF tree API, 2026-10-27.
LISTING: dict[str, tuple[int, int]] = {
    "UD-IQ2_S": (7, 275_500_000_000),
    "UD-IQ1_S": (5, 175_600_000_000),
}
DEFAULT_ORDER = ("UD-IQ2_S", "UD-IQ1_S")
DISK_MARGIN = 1.05  # room for temp and cache files
SIZE_TOLERANCE = 0.02  # a finished set may differ from the listing by 2%
_SHARD = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")


@dataclass
class FetchResult:
    quant: str | None
    ok: bool
    dry_run: bool
    steps: list[str]
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "quant": self.quant,
            "ok": self.ok,
            "dry_run": self.dry_run,
            "steps": self.steps,
            "reason": self.reason,
        }


def need_gb(quant: str) -> float:
    return LISTING[quant][1] * DISK_MARGIN / 1e9


def download_cmd(
    quant: str, models_dir: Path, *, repo: str = REPO, binary: str = "hf"
) -> list[str]:
    """`hf download` for one quant folder. No secrets on the command line."""

    return [binary, "download", repo, "--include", f"{quant}/*", "--local-dir", str(models_dir)]


def verify(quant: str, models_dir: Path, *, listing=LISTING) -> tuple[bool, str]:
    """All shards present, numbered 1..N of N, and the total size close to the listing."""

    d = models_dir / quant
    shards = sorted(d.glob("*.gguf")) if d.is_dir() else []
    if not shards:
        return False, f"no .gguf files under {d}"
    marks = [_SHARD.search(p.name) for p in shards]
    if any(m is None for m in marks):
        if len(shards) == 1 and quant not in listing:
            return True, "single file"
        return False, "a file is not named like ...-00001-of-0000N.gguf"
    totals = {int(m.group(2)) for m in marks}
    if len(totals) != 1:
        return False, "shards disagree about the total count"
    n = totals.pop()
    have = sorted(int(m.group(1)) for m in marks)
    if have != list(range(1, n + 1)):
        return False, f"have shards {have}, need 1..{n}"
    if quant in listing:
        want_n, want_b = listing[quant]
        if n != want_n:
            return False, f"expected {want_n} shards, found {n}"
        got = sum(p.stat().st_size for p in shards)
        if abs(got - want_b) > want_b * SIZE_TOLERANCE:
            return False, f"size {got / 1e9:.1f} GB, listing says {want_b / 1e9:.1f} GB"
    return True, f"{n} shards ok"


def fetch(
    models_dir: str | Path,
    *,
    order=DEFAULT_ORDER,
    dry_run: bool = False,
    free_gb: float | None = None,
    runner: Callable = subprocess.run,
    repo: str = REPO,
    binary: str = "hf",
    listing=LISTING,
) -> FetchResult:
    """Get the first quant in `order` that fits the disk and downloads cleanly."""

    models_dir = Path(models_dir)
    steps: list[str] = []
    if free_gb is None:
        models_dir.mkdir(parents=True, exist_ok=True)
        free_gb = shutil.disk_usage(models_dir).free / 1e9
    for quant in order:
        ok, why = verify(quant, models_dir, listing=listing)
        if ok:
            steps.append(f"{quant}: already complete ({why})")
            return FetchResult(quant, True, dry_run, steps)
        have_gb = (
            sum(p.stat().st_size for p in (models_dir / quant).glob("*")) / 1e9
            if (models_dir / quant).is_dir()
            else 0.0
        )
        want = listing[quant][1] * DISK_MARGIN / 1e9 - have_gb
        if free_gb < want:
            steps.append(f"{quant}: skipped, {free_gb:.0f} GB free, needs {want:.0f} GB more")
            continue
        cmd = download_cmd(quant, models_dir, repo=repo, binary=binary)
        steps.append(f"{quant}: {' '.join(cmd)}")
        if dry_run:
            return FetchResult(quant, True, True, steps, "dry run: nothing downloaded")
        try:
            runner(cmd, check=True)
        except (subprocess.CalledProcessError, OSError) as e:
            steps.append(f"{quant}: download failed ({type(e).__name__}); trying the next quant")
            continue
        ok, why = verify(quant, models_dir, listing=listing)
        steps.append(f"{quant}: verify: {why}")
        if ok:
            return FetchResult(quant, True, False, steps)
    return FetchResult(None, False, dry_run, steps, "no quant could be fetched")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wattnot.spark_open.fetch")
    ap.add_argument("--models-dir", default="/data/spark-open")
    ap.add_argument("--quant", choices=sorted(LISTING), help="only this quant, no fallback")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, download nothing")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument(
        "--free-gb", type=float, help="plan as if this much disk were free (for --dry-run)"
    )
    a = ap.parse_args(argv)
    order = (a.quant,) if a.quant else DEFAULT_ORDER
    r = fetch(a.models_dir, order=order, dry_run=a.dry_run, repo=a.repo, free_gb=a.free_gb)
    for s in r.steps:
        print(s)
    print(f"fetch: {'ok' if r.ok else 'FAILED'} quant={r.quant} {r.reason}".rstrip())
    return 0 if r.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
