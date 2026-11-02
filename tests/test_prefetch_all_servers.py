"""Every vLLM start in a full round (without arm, with arm, sweep, FP8) and in a block-only run
goes through Server.start, and each passes --safetensors-load-strategy prefetch."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "e2e"))
import modal_full_inner as I  # noqa: E402


class Stop(Exception):
    pass


@pytest.mark.parametrize("cfg", [dict(fp8=False, prefix=False, max_seqs=64),   # without WattNot
                                 dict(fp8=False, prefix=True, max_seqs=128),   # with WattNot / block-only
                                 dict(fp8=True, prefix=True, max_seqs=128)])   # FP8 arm
def test_prefetch_on_every_server_start(tmp_path, monkeypatch, cfg):
    seen = {}

    def popen(argv, **k):
        seen["argv"] = argv
        raise Stop
    monkeypatch.setattr(I.subprocess, "Popen", popen)
    monkeypatch.setattr(I, "left", lambda: 3600)
    monkeypatch.setattr(I.Server, "stop", lambda self: None)   # stop() pkills old servers
    with pytest.raises(Stop):
        I.Server("/weights/m", tmp_path).start(**cfg)
    a = seen["argv"]
    assert a[a.index("--safetensors-load-strategy") + 1] == "prefetch"
    assert ("--quantization" in a) == cfg["fp8"]


def test_no_other_vllm_launch_path():
    src = (ROOT / "scripts" / "e2e" / "modal_full_inner.py").read_text()
    assert src.count('"vllm", "serve"') == 1
