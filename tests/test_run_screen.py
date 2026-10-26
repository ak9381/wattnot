"""Run screen: the demo event stream is valid and the graph-state tests pass."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from wattnot.schemas import validate_event

RUN = Path(__file__).resolve().parents[1] / "web" / "run"


def _events():
    return [json.loads(x) for x in (RUN / "demo-events.jsonl").read_text().splitlines()]


def test_demo_events_match_the_schema_and_are_examples():
    ev = _events()
    for e in ev:
        validate_event(e)
    assert ev[0]["data"]["label"] == "EXAMPLE"
    assert [e["type"] for e in ev][-4:] == ["recommendations", "pareto.updated", "receipt.signed",
                                              "run.finished"]
    caps = [e["data"]["change"]["power_cap_w"] for e in ev
            if e["type"] in ("action.applied", "action.rolled_back")]
    assert caps == [650, 600, 550, 500]
    assert ev[[e["type"] for e in ev].index("action.rolled_back")]["data"]["restored_cap_w"] == 550


def test_demo_script_reproduces_the_same_stream(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "demo_run", Path(__file__).resolve().parents[1] / "scripts" / "demo_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fresh = [json.loads(x) for x in mod.main(tmp_path).read_text().splitlines()]
    strip = lambda es: [(e["type"], e["step_id"], e["data"]) for e in es]  # noqa: E731
    assert strip(fresh) == strip(_events())


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_graph_state_mapping_node_tests():
    r = subprocess.run(["node", "--test", "state.test.mjs", "checklist.test.mjs",
                        "savings.test.mjs", "honesty.test.mjs"], cwd=RUN,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
