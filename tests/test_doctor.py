# ruff: noqa: E501
from collections import namedtuple

from wattnot import doctor

Disk = namedtuple("Disk", "total used free")
disk = lambda p: Disk(0, 0, 50e9)  # noqa: E731


def pf(name=None, allowed=True):
    if name is None:
        return lambda env: {"gpu": {"present": False, "reason": "no nvidia-smi"},
                            "power_cap": {"allowed": False, "reason": "no GPU"}}
    return lambda env: {"gpu": {"present": True, "driver": "550", "cuda": "12.4",
                                "gpus": [{"name": name}]},
                        "power_cap": {"allowed": allowed, "reason": "x"}}


def up(url):
    return True, "serving m"


def test_no_gpu_no_key_is_sim_and_never_crashes():
    r = doctor.run({}, preflight=pf(), disk=disk, reach=up)
    assert r["mode"] == "sim" and r["power_caps"] == "off" and r["python_ok"]


def test_no_gpu_with_key_runs_savers():
    r = doctor.run({"META_API_KEY": "k"}, preflight=pf(), disk=disk, reach=up)
    assert r["mode"] == "savers-only"
    assert "k" not in doctor.render(r).split("META_API_KEY")[-1][:3]


def test_tested_h100_with_root_and_vllm_is_full():
    r = doctor.run({"WATTNOT_VLLM_URL": "http://x/v1/chat/completions"},
                   preflight=pf("NVIDIA H100 80GB HBM3"), disk=disk, reach=up,
                   nvml_ok=lambda: (True, "ok"))
    assert r["mode"] == "full" and r["power_caps"] == "on"


def test_no_permission_turns_caps_off():
    r = doctor.run({"WATTNOT_VLLM_URL": "http://x/v1"}, preflight=pf("NVIDIA H100 80GB HBM3", False),
                   disk=disk, reach=up, nvml_ok=lambda: (True, "ok"))
    assert r["power_caps"] == "off" and r["mode"] == "savers-only"
    assert any(c["check"] == "power-limit permission" and c["fix"] for c in r["checks"])


def test_untested_gpu_caps_off_unless_opted_in():
    env = {"WATTNOT_VLLM_URL": "http://x/v1"}
    r = doctor.run(env, preflight=pf("NVIDIA L4"), disk=disk, reach=up, nvml_ok=lambda: (True, "ok"))
    assert r["power_caps"] == "off"
    r = doctor.run({**env, "WATTNOT_ALLOW_UNTESTED_GPU": "1"}, preflight=pf("NVIDIA L4"),
                   disk=disk, reach=up, nvml_ok=lambda: (True, "ok"))
    assert r["power_caps"] == "on"


def test_preflight_error_degrades():
    def boom(env):
        raise RuntimeError("bad")
    r = doctor.run({}, preflight=boom, disk=disk, reach=up)
    assert r["mode"] == "sim"


def test_old_python_fails():
    r = doctor.run({}, preflight=pf(), disk=disk, reach=up, py=(3, 9))
    assert not r["python_ok"]


def test_meta_client_retries_429_then_gives_a_hint(tmp_path, monkeypatch):
    import pytest

    from wattnot.meta import client as mc
    from wattnot.meta.ledger import SpendLedger
    monkeypatch.setattr(mc, "RETRY_WAITS_S", (0, 0))
    calls = []

    def opener(req, t):
        calls.append(1)
        return 429, b'{"error":{"message":"slow down"}}'
    c = mc.GuardedClient(key="k", ledger=SpendLedger(str(tmp_path / "l.json")), opener=opener)
    with pytest.raises(mc.MetaError) as e:
        c({"model": "muse-spark-1.3", "messages": [{"role": "user", "content": "hi"}]})
    assert len(calls) == 3 and "Rate limited" in str(e.value)


def test_dotenv_is_read_without_overriding(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("META_API_KEY=abc\nWATTNOT_PORT=9000\n# c\n")
    env = {"WATTNOT_PORT": "1"}
    doctor.load_dotenv(tmp_path / ".env", env)
    assert env == {"WATTNOT_PORT": "1", "META_API_KEY": "abc"}
