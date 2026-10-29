"""The GO gate on the real 8xB200 serve path, proven with a FAKE modal module (no SDK calls,
no network, no GPU, no money).

A stand-in ``modal`` module is injected into ``sys.modules`` and the sprint file is loaded fresh
against it, so every Modal object (App, Image, Volume, Secret, the web-server Function) is a
recorder. The tests assert: nothing on a GPU can start without ``WATTNOT_MODAL_GO=1``; the GO
gate is checked *before* any Modal call; the arm's server-side ``timeout`` is exactly the guard's
``cloud_timeout_s``; the GPU spec re-asserted at serve time is ``B200:8``; and the per-arm top-k
is carried to the container as a Secret.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---- a fake `modal` that records every call instead of touching the network ----------------

class _Fn:
    """Stand-in for a deployed web-server Function. Records with_options/get_web_url calls."""

    def __init__(self, calls):
        self.calls = calls

    def with_options(self, **kw):
        self.calls.append(("with_options", kw))
        return self

    def get_web_url(self):
        self.calls.append(("get_web_url", {}))
        return "https://ws--wattnot-spark-topk-draft-b200-serve.modal.run"

    def remote(self, *a, **k):
        self.calls.append(("remote", {}))
        return ""


class _App:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    def function(self, **kw):
        def deco(fn):
            return _Fn(self.calls)        # the decorated fn becomes a recording Function
        return deco

    def local_entrypoint(self):
        def deco(fn):
            return fn
        return deco

    def deploy(self, **kw):
        self.calls.append(("deploy", kw))


class _Image:
    def from_registry(self, *a, **k):
        return self

    def pip_install(self, *a, **k):
        return self

    def env(self, *a, **k):
        return self

    def add_local_dir(self, *a, **k):
        return self


class _Secret:
    def __init__(self, data=None):
        self.data = data


def _make_fake_modal():
    import types
    calls: list = []
    mod = types.ModuleType("modal")
    mod.CALLS = calls
    mod.is_local = lambda: True
    mod.App = lambda name: _App(name, calls)
    mod.Image = _Image()
    mod.Volume = types.SimpleNamespace(from_name=lambda *a, **k: object())
    mod.Secret = types.SimpleNamespace(
        from_name=lambda *a, **k: _Secret(),
        from_dict=lambda d: (calls.append(("Secret.from_dict", d)), _Secret(d))[1])
    mod.concurrent = lambda **k: (lambda fn: fn)
    mod.web_server = lambda *a, **k: (lambda fn: fn)
    return mod, calls


def _load_with_fake_modal():
    mod, calls = _make_fake_modal()
    saved = sys.modules.get("modal")
    sys.modules["modal"] = mod
    try:
        spec = importlib.util.spec_from_file_location(
            "spark_topk_sprint_fakemodal", ROOT / "modal" / "spark_topk_sprint.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        if saved is not None:
            sys.modules["modal"] = saved
        else:
            del sys.modules["modal"]
    return m, calls


def test_module_builds_against_fake_modal():
    m, _ = _load_with_fake_modal()
    assert m.app is not None and m.GPU_B200 == "B200:8"
    assert m.plan()["gpu"] == "B200:8"


def test_no_go_flag_refuses_before_any_modal_call(monkeypatch):
    monkeypatch.delenv("WATTNOT_MODAL_GO", raising=False)
    m, calls = _load_with_fake_modal()
    seen = {"deploy": 0, "discover": 0}
    with pytest.raises(SystemExit, match="WATTNOT_MODAL_GO"):
        m._deploy_b200(8, m.MODEL, 1000,
                       deploy=lambda: seen.__setitem__("deploy", seen["deploy"] + 1),
                       discover=lambda: seen.__setitem__("discover", seen["discover"] + 1))
    # nothing on a GPU started: no deploy, no url discovery, no with_options/Secret call
    assert seen == {"deploy": 0, "discover": 0}
    assert calls == []


def test_serve_b200_also_refuses_without_go(monkeypatch):
    monkeypatch.delenv("WATTNOT_MODAL_GO", raising=False)
    m, calls = _load_with_fake_modal()
    with pytest.raises(SystemExit, match="WATTNOT_MODAL_GO"):
        m._serve_b200(6, m.MODEL, 500)
    assert calls == []


def test_go_flag_deploys_with_b200_8_and_the_guard_timeout(monkeypatch):
    monkeypatch.setenv("WATTNOT_MODAL_GO", "1")
    m, calls = _load_with_fake_modal()
    fn_timeout = 4321
    url = m._deploy_b200(4, m.MODEL, fn_timeout)
    assert url.startswith("https://") and "modal.run" in url
    wo = next(kw for name, kw in calls if name == "with_options")
    # the 8xB200 spec is re-asserted and the server-side timeout is exactly fn_timeout
    assert wo["gpu"] == "B200:8"
    assert wo["timeout"] == fn_timeout and isinstance(wo["timeout"], int)
    # the per-arm top-k rides along as a Secret the container reads
    sec = next(d for name, d in calls if name == "Secret.from_dict")
    assert sec["WATTNOT_SERVE_TOPK"] == "4"
    # the app was deployed and the url discovered
    assert any(name == "deploy" for name, _ in calls)
    assert any(name == "get_web_url" for name, _ in calls)


def test_serve_b200_publishes_v1_endpoint_and_handle(monkeypatch):
    monkeypatch.setenv("WATTNOT_MODAL_GO", "1")
    monkeypatch.delenv("MODAL_ENDPOINT_URL", raising=False)
    m, _ = _load_with_fake_modal()
    handle = m._serve_b200(8, m.MODEL, 1200)
    import os
    assert handle["base_url"].endswith("/v1") and handle["topk"] == 8
    assert os.environ["MODAL_ENDPOINT_URL"] == handle["base_url"]


def test_smoke_arm_never_requests_b200(monkeypatch, tmp_path):
    """The whole sprint through the fake-modal deploy path: the 1B smoke arm must re-assert a
    small cheap GPU (L4), never the 8xB200 spec, while the real top-k arm still asks for B200:8."""
    monkeypatch.setenv("WATTNOT_MODAL_GO", "1")
    m, calls = _load_with_fake_modal()
    from wattnot.spend.guard import GPU_SECONDS, Guard, Ledger, Price
    g = Guard("modal", Price(GPU_SECONDS, usd_per_s=m.B200_8_USD_H / 3600),
              Ledger(tmp_path / "l.jsonl"), hard_usd=98.0, soft_usd=90.0)

    class _T:
        def cancel(self):
            pass

    res = m.sprint(
        out_dir=tmp_path / "o",
        make_transport=lambda kind, model, k: m.FakeServer(
            provider="modal", topk=k,
            model=(m.SMOKE_MODEL if kind == "smoke" else f"Muse-Spark-Open#topk{k}")),
        serve=m._serve_b200, wait_serving=lambda *a: True, mode="replay-synthetic",
        now=1_790_000_000.0, topks=(8,), repeat_k=None, smoke_first=True,
        guard=g, meter=m.fake_meter(), make_timer=lambda s, name: _T(), teardown=lambda: [])

    assert [r["kind"] for r in res] == ["smoke", "topk"]
    gpus = [kw["gpu"] for name, kw in calls if name == "with_options"]
    assert len(gpus) == 2                               # one deploy per arm, in arm order
    # the smoke arm (first) took the cheap single GPU and NEVER the 8xB200 spec
    assert gpus[0] == m.GPU_SMOKE == "L4"
    assert gpus[0] != m.GPU_B200
    # the real top-k arm still re-asserts 8xB200
    assert gpus[1] == m.GPU_B200 == "B200:8"
    # and the smoke arm's per-arm Secret carried a single-GPU tensor-parallel size
    smoke_sec = next(d for name, d in calls if name == "Secret.from_dict")
    assert smoke_sec["WATTNOT_SERVE_MODEL"] == m.SMOKE_MODEL and smoke_sec["WATTNOT_SERVE_TP"] == "1"


def test_run_topk_fn_timeout_is_the_guard_cloud_timeout(monkeypatch, tmp_path):
    """End to end through run_topk with the GO flag: the timeout handed to the serve path is the
    guard's cloud_timeout_s, and the deploy re-asserts B200:8 -- all on the fake modal."""
    monkeypatch.setenv("WATTNOT_MODAL_GO", "1")
    m, calls = _load_with_fake_modal()
    from wattnot.spend.guard import GPU_SECONDS, Guard, Ledger, Price, cloud_timeout_s
    g = Guard("modal", Price(GPU_SECONDS, usd_per_s=m.B200_8_USD_H / 3600),
              Ledger(tmp_path / "l.jsonl"), hard_usd=98.0, soft_usd=90.0)
    expect = cloud_timeout_s(g)

    def fake_timer():
        class T:
            def cancel(self):
                pass
        return lambda s, name: T()

    r = m.run_topk(8, out_dir=tmp_path / "o", transport=m.FakeServer(), mode="replay-synthetic",
                   guard=g, now=1_790_000_000.0, serve=m._serve_b200,
                   wait_serving=lambda *a: True, make_timer=fake_timer(), teardown=lambda: [])
    assert r["serving"] is True
    wo = next(kw for name, kw in calls if name == "with_options")
    assert wo["gpu"] == "B200:8" and wo["timeout"] == expect
