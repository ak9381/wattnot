"""Every compare lists all three placements; Modal runs through the same router and receipt."""

import json

from wattnot.cloud.transport import ModalLiveTransport, Request
from wattnot.runner.workload import load_workload
from wattnot.spark_open import router as R
from wattnot.spark_open.h2h import PLACEMENTS, run_h2h
from pathlib import Path

from wattnot.runner.workload import workloads_dir

WL = workloads_dir() / "cloud-effort-v1.jsonl"
FIXTURE = Path(__file__).resolve().parent.parent / "data" / "cloud" / "recorded-smoke.json"


def test_three_placements_listed_and_routed():
    r = run_h2h(WL, list(load_workload(WL)), baseten="replay", fixture=FIXTURE,
                modal="replay")
    rows = {p["id"]: p for p in r["placements"]}
    assert tuple(rows) == PLACEMENTS
    assert rows["local"]["status"] == "not_run" and "local" in rows["local"]["why"]
    assert rows["modal"]["status"] == "ran" and rows["modal"]["usd_per_1k"]["label"].startswith("REPLAY")
    assert r["legs"]["modal"]["energy_per_answer"]["label"] == "ESTIMATE"
    R.attach(r)
    assert R.verify(r)
    ids = {c["id"] for d in r["routing"]["decisions"] for c in d["candidates"]}
    assert {"baseten", "modal"} <= ids


def test_modal_transport_auth_and_model(monkeypatch):
    env = {"MODAL_ENDPOINT_URL": "https://ws--llama.modal.run", "MODAL_PROXY_TOKEN_ID": "wk-a",
           "MODAL_PROXY_TOKEN_SECRET": "ws-b"}
    t = ModalLiveTransport(env=env)
    seen = {}

    def once(data, headers):
        seen.update(headers=headers, body=json.loads(data))
        return 200, b'{"choices":[{"message":{"content":"ok"}}],"usage":{}}', {}

    monkeypatch.setattr(t, "_once", once)
    rep = t(Request("x", {"model": t.model, "messages": []}))
    assert rep.status == 200 and t.base_url.endswith("/v1")
    assert seen["headers"]["Authorization"] == "Bearer wk-a.ws-b"
    assert "ws-b" not in repr(t) and t.provider == "modal"


def test_cloud_receipt_names_modal_host(monkeypatch):
    from wattnot.cloud import MODEL
    from wattnot.cloud.run import host_info

    env = {"MODAL_ENDPOINT_URL": "https://ws--llama.modal.run/v1", "MODAL_PROXY_TOKEN_ID": "wk-a",
           "MODAL_PROXY_TOKEN_SECRET": "ws-b", "MODAL_MODEL": "meta-models/Muse-Glimmer-30B"}
    t = ModalLiveTransport(env=env)
    sent = {}
    monkeypatch.setattr(t, "_once", lambda d, h: (sent.update(b=json.loads(d)) or
                                                  (200, b"{}", {})))
    t(Request("x", {"model": MODEL, "messages": []}))
    assert sent["b"]["model"] == "meta-models/Muse-Glimmer-30B"
    info = host_info(t)
    assert info["provider"] == "modal" and info["endpoint"].endswith(".modal.run/v1")
    assert "ws-b" not in json.dumps(info)


def test_modal_dedicated_gpu_billing_is_an_estimate():
    r = run_h2h(WL, list(load_workload(WL)), baseten="off", modal="replay", fixture=FIXTURE,
                modal_billing="gpu")
    leg = r["legs"]["modal"]
    assert leg["cost_per_1k_answers"]["label"].startswith("ESTIMATE") and leg["gpu"] == "H100"
