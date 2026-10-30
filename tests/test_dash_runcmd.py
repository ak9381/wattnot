"""The run command panel: selection -> exact argv, checked against the real parsers."""
from __future__ import annotations

import shlex

import pytest
from fastapi.testclient import TestClient

from wattnot.dash import runcmd
from wattnot.dash.server import create_app
from wattnot.proof import orchestrate, run

CAP = {"model": "llama", "place": "h100", "workload": "coding",
       "levers": ["cap"], "p99_slack_pct": 10, "min_quality_pct": 95}
TOPK = {"model": "granite", "place": "h100", "workload": "moe",
        "levers": ["topk"], "p99_slack_pct": 10, "min_quality_pct": 95}


def test_cap_selection_builds_both_commands():
    c = runcmd.command_for(CAP)
    assert c["run"]["argv"][:4] == ["python", "-m", "wattnot.proof.run", "full"]
    assert "--model" in c["run"]["argv"] and "Llama-3.1-8B" in c["run"]["argv"][-3]
    assert c["flow"]["argv"][:3] == ["python", "-m", "wattnot.proof.orchestrate"]
    assert "--mode" in c["flow"]["argv"] and "live" in c["flow"]["argv"]
    assert "--no-adaptive" in c["flow"]["argv"]
    assert c["run"]["shell"] == shlex.join(c["run"]["argv"])
    assert c["flow"]["shell"] == shlex.join(c["flow"]["argv"])
    text = " ".join(c["notes"])
    assert "H100" in text and "never starts it" in text


def test_topk_selection_uses_the_adaptive_command():
    c = runcmd.command_for(TOPK)
    assert c["run"]["argv"][3] == "adaptive"
    assert "--adaptive-model" in c["run"]["argv"]
    assert "--adaptive-model" in c["flow"]["argv"]
    assert "--no-adaptive" not in c["flow"]["argv"]


def test_out_is_passed_through():
    c = runcmd.command_for({**CAP, "out": "results/proof"})
    assert c["run"]["argv"][-2:] == ["--out", "results/proof"]
    assert c["flow"]["argv"][-2:] == ["--out", "results/proof"]


@pytest.mark.parametrize("sel", [
    {**CAP, "model": "nope"},
    {**CAP, "model": "qwen3"},
    {**CAP, "model": "spark_open"},
    {**CAP, "levers": ["cap", "turbo"]},
    {**CAP, "levers": []},
    {**CAP, "levers": ["topk"]},
    {**TOPK, "levers": ["cap"]},
    {**CAP, "place": "baseten"},
    {**CAP, "workload": "nope"},
    {**CAP, "p99_slack_pct": 500},
    {**CAP, "min_quality_pct": "lots"},
])
def test_bad_selections_are_refused(sel):
    with pytest.raises(ValueError):
        runcmd.command_for(sel)


def _parses(module, argv):
    """The real argparse parser accepts every flag: --help parses, then exits 0."""
    with pytest.raises(SystemExit) as e:
        module.main([*argv[3:], "--help"])
    assert e.value.code == 0


def test_every_flag_is_accepted_by_the_real_parsers(capsys):
    for sel in (CAP, TOPK, {**CAP, "model": "glimmer", "out": "results/proof"}):
        c = runcmd.command_for(sel)
        _parses(run, c["run"]["argv"])
        _parses(orchestrate, c["flow"]["argv"])
    capsys.readouterr()  # --help prints; keep the log clean


def test_endpoint_returns_text_and_never_executes():
    for app in (create_app(), create_app(replay_only=True)):
        c = TestClient(app)
        r = c.post("/api/dash/run/command", json=CAP)
        assert r.status_code == 200
        body = r.json()
        assert body["run"]["shell"].startswith("python -m wattnot.proof.run")
        assert body["flow"]["shell"].startswith("python -m wattnot.proof.orchestrate")
        assert body["notes"]
        assert c.post("/api/dash/run/command", json={**CAP, "model": "nope"}).status_code == 400
        assert c.post("/api/dash/run/command", json=[1]).status_code == 400
