"""The GPU-host setup script, run end to end in a clean sandbox on a CPU box.

Slow and needs the network (pip, uv), so it is off by default:

    WATTNOT_TEST_SETUP=1 PYTHONPATH=.:src python -m pytest tests/test_vm_setup.py

What it checks is the three first-boot problems RUN2 hit (ninja not on PATH, no Python.h for
Triton, Hugging Face xet stalls) and the uv fallback for a python without ensurepip. The GPU is a
stub `nvidia-smi`; vLLM and the model weights are skipped (SETUP_SKIP_MODELS). Everything else is
the real script: a fresh HOME, a fresh venv, real pip/uv installs. The sandbox is bubblewrap with
an empty /usr/include, so "the image has no Python headers" is true, as on the E2E image.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "h100_vm_setup.sh"

pytestmark = pytest.mark.skipif(
    os.environ.get("WATTNOT_TEST_SETUP") != "1" or not shutil.which("bwrap"),
    reason="set WATTNOT_TEST_SETUP=1 (needs network and bwrap)",
)

NVIDIA_SMI = """#!/usr/bin/env bash
# a stub GPU: 700 W default limit, the limit can be changed and read back
state="${NVSMI_STATE:-/tmp/nvsmi-limit}"
[ -f "$state" ] || echo 700 > "$state"
case "$*" in
  *"-l 1"*) exit 0 ;;
  *"-pl "*) echo "${2:-700}" > "$state"; exit 0 ;;
  *power.default_limit*) echo 700; exit 0 ;;
  *power.limit,memory.total*) echo "NVIDIA H100 80GB HBM3, 550.00, $(cat "$state"), 81559"
      exit 0 ;;
  *power.limit*) cat "$state"; exit 0 ;;
  *clocks_throttle_reasons*|*clocks_event_reasons*) echo "Not Active"; exit 0 ;;
esac
exit 0
"""

# a python3 whose `-m venv` fails, like the stock image without ensurepip
PY_NO_VENV = """#!/usr/bin/env bash
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then echo "ensurepip is not available" >&2; exit 1; fi
exec /usr/bin/python3 "$@"
"""


def _run(tmp_path: Path, *, no_ensurepip: bool) -> tuple[subprocess.CompletedProcess, Path]:
    home, root, bindir = tmp_path / "home", tmp_path / "root", tmp_path / "bin"
    for d in (home, root, bindir):
        d.mkdir()
    (bindir / "nvidia-smi").write_text(NVIDIA_SMI)
    (bindir / "nvidia-smi").chmod(0o755)
    if no_ensurepip:
        (bindir / "python3").write_text(PY_NO_VENV)
        (bindir / "python3").chmod(0o755)
    env = {
        "PATH": f"{bindir}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "SETUP_ROOT": str(root),
        "SETUP_SKIP_MODELS": "1",
        "MIN_FREE_GB": "5",
        "SETUP_PIP_EXTRA": "",
        "NVSMI_STATE": str(tmp_path / "limit"),
        "UV_CACHE_DIR": str(tmp_path / "uvcache"),
        "LANG": "C.UTF-8",
    }
    # an empty, writable /usr/include: the stock image ships no Python headers
    cmd = ["bwrap", "--dev-bind", "/", "/", "--tmpfs", "/usr/include", "--unshare-pid",
           "bash", str(SCRIPT)]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1500)
    return p, root


def test_setup_with_system_venv_and_no_headers(tmp_path):
    p, root = _run(tmp_path, no_ensurepip=False)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out[-3000:]
    assert "power cap check ok" in out
    assert "no Python.h in" in out and "fetching headers" in out  # the stock image case
    assert "env check ok" in out and "READY (models skipped)" in out
    assert (root / "venv" / "bin" / "ninja").exists()
    # a fresh shell that only sources the venv (as every later step does) still gets the switch
    assert "HF_HUB_DISABLE_XET" in (root / "venv" / "bin" / "activate").read_text()


def test_setup_falls_back_to_uv_when_venv_fails(tmp_path):
    p, root = _run(tmp_path, no_ensurepip=True)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out[-3000:]
    assert "python3 -m venv failed; using uv" in out
    assert "env check ok" in out
    assert (root / "venv" / "bin" / "python").exists()
