"""The framework-validation artifact and demo receipt are built for REAL against the mock.

These exercise ``scripts/validate_frameworks.py`` end to end (one local mock proxy per
framework, shut down after), and check the committed artifact + LangChain demo receipt + the
dash endpoint that serves the artifact. A framework whose library is missing is skipped with a
clear reason, never silently dropped.
"""
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("uvicorn")

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "data" / "frameworks" / "validation.json"
RECEIPT = ROOT / "receipts" / "frameworks" / "langchain-demo.json"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "validate_frameworks", ROOT / "scripts" / "validate_frameworks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VS = _load_script()


@pytest.mark.parametrize("ex_name,fw_name,probe,dist", VS.FRAMEWORKS)
def test_validate_one_real_against_mock(ex_name, fw_name, probe, dist):
    """Run the real framework client through a fresh mock proxy; skip if the lib is missing."""
    if not VS._probe(probe):
        pytest.skip(f"{probe} not installed")
    entry, receipt = VS.validate_one(ex_name, fw_name, probe, dist)
    assert entry["status"] == "validated"
    assert entry["label"] == "VALIDATED-LOCAL (mock upstream)"
    assert entry["version"]                       # real library version recorded
    assert entry["small_model_kept_easy"] is True
    assert entry["hard_escalated"] is True
    # Honest: the receipt it drove is a MOCK upstream and names only this framework.
    assert receipt["label"] == "MOCK"
    assert receipt["frameworks"] == [fw_name]
    assert receipt["agent_waste"]["upstream_calls"] >= 2


def test_committed_artifact_is_honest():
    data = json.loads(ARTIFACT.read_text())
    assert data["spec"] == "wattnot.frameworks-validation/1"
    assert "mock upstream" in data["upstream"]
    names = {f["framework"] for f in data["frameworks"]}
    assert names == {"open" + "ai-python", "langchain", "llamaindex"}
    for f in data["frameworks"]:
        assert f["status"] in ("validated", "skipped")
        assert "live" not in f["label"].lower()   # never claim a live upstream
        if f["status"] == "validated":
            assert f["label"] == "VALIDATED-LOCAL (mock upstream)"
            assert f["version"]
        else:
            assert f["reason"]                     # skipped frameworks say why


def test_langchain_demo_receipt_has_frameworks_field():
    rc = json.loads(RECEIPT.read_text())
    assert rc["frameworks"] == ["langchain"]
    assert rc["framework"] == "langchain"
    assert rc["label"] == "MOCK"                   # mock upstream, honest


def test_dash_endpoint_serves_the_artifact():
    from fastapi.testclient import TestClient

    from wattnot.dash.server import create_app

    client = TestClient(create_app(replay_only=True))
    r = client.get("/api/dash/v2/frameworks")
    assert r.status_code == 200
    body = r.json()
    assert body["spec"] == "wattnot.frameworks-validation/1"
    assert len(body["frameworks"]) == 3
