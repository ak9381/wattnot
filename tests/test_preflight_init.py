import json
import subprocess

import pytest

from wattnot import preflight, settings
from wattnot.approvals.appliers import ApplyError, PowerCapApplier


def test_no_gpu_reports_cleanly():
    r = preflight.run(which=lambda _n: None, env={})
    assert r["gpu"]["present"] is False and r["power_cap"]["allowed"] is False
    f = {x["id"]: x for x in r["features"]}
    assert not f["power_caps"]["available"] and f["routing"]["available"]
    assert "no GPU" in preflight.render(r)


def _fake_smi(args, **_k):
    out = ("NVIDIA H100 80GB HBM3, 550.54, 700.00, 200.00, 700.00\n" if args[1:]
           else "| NVIDIA-SMI 550.54   Driver Version: 550.54   CUDA Version: 12.4 |\n")
    return subprocess.CompletedProcess(args, 0, out, "")


def test_gpu_root_and_serverless():
    r = preflight.run(runner=_fake_smi, which=lambda _n: "/usr/bin/nvidia-smi", env={}, euid=0)
    assert r["cuda"] == "12.4" and r["driver"] == "550.54" and r["power_cap"]["allowed"]
    r = preflight.run(runner=_fake_smi, which=lambda _n: "/usr/bin/nvidia-smi",
                      env={"MODAL_TASK_ID": "x"}, euid=0)
    assert r["power_cap"]["allowed"] is False and "serverless" in r["power_cap"]["reason"]


def test_init_writes_watch_only_and_blocks_changes(tmp_path, monkeypatch):
    from wattnot.init import init
    cfg = tmp_path / "config.json"
    out = init(cfg)
    assert out["mode"] == "watch" and json.loads(cfg.read_text())["mode"] == "watch"
    monkeypatch.setenv("WATTNOT_CONFIG", str(cfg))
    assert settings.watch_only()
    a = PowerCapApplier(lambda i: 700, lambda i, w: pytest.fail("must not write"))
    with pytest.raises(ApplyError, match="watch-only"):
        a.apply({"kind": "power_cap", "params": {"gpu": 0, "cap_w": 350}})
    init(cfg, act=True)
    assert not settings.watch_only()


def test_cli_preflight_json(tmp_path):
    import sys
    r = subprocess.run([sys.executable, "-m", "wattnot.cli", "preflight", "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["format"] == "wattnot.preflight/1"
