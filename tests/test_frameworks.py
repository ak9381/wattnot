"""Drop-in tests: real framework clients talk to the WattNot proxy over HTTP. MOCK upstream."""
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("uvicorn")
from wattnot.agentwaste.frameworks import serve_mock  # noqa: E402

EX = Path(__file__).resolve().parents[1] / "examples" / "frameworks"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"ex_{name}", EX / name / "agent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def proxy():
    base, server = serve_mock()
    yield base
    server.should_exit = True


def _receipt(base):
    with urllib.request.urlopen(base + "/wattnot/receipt", timeout=5) as r:
        return json.loads(r.read())


@pytest.mark.parametrize("name,pkg", [("openai_python", "openai"),
                                      ("langchain", "langchain_openai"),
                                      ("llamaindex", "llama_index.llms.openai_like")])
def test_framework_drop_in(proxy, name, pkg):
    pytest.importorskip(pkg)
    mod = _load(name)
    easy = mod.run(proxy, "Where do the signed receipts live?")
    hard = mod.run(proxy, "HARD: which module signs receipts?")
    assert "llama-3.1-8b" in easy.lower()          # small model kept
    assert "llama-3.1-8b" not in hard.lower()      # escalated past the small model
    rc = _receipt(proxy)
    assert rc["label"] == "MOCK"
    expect = {"openai_python": "openai-python"}.get(name, name)
    assert rc["frameworks"] == [expect]
    assert rc["routing"]["enabled"] and rc["routing"]["escalation_rate"] == 0.5
    assert rc["routing"]["small_model_pass_rate"] == 0.5
    assert rc["gate"]["pass_pct"] == 100.0
    assert rc["context_saver"]["enabled"] and rc["context_saver"]["tokens_before"] > 0
    assert rc["agent_waste"]["upstream_calls"] >= 2


def test_waste_saver_stops_loop(proxy):
    pytest.importorskip("openai")
    from openai import OpenAI
    c = OpenAI(base_url=proxy, api_key="unused", default_headers={"X-WattNot-Framework": "openai-python"})
    msgs = [{"role": "user", "content": "loop task"}]
    stopped = False
    for i in range(12):
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "def f():\n    return 1\n" * 40})
        out = c.chat.completions.create(model="meta-llama/Llama-3.1-8B-Instruct", messages=msgs)
        if "Stopped early" in (out.choices[0].message.content or ""):
            stopped = True
            break
    assert stopped
    rc = _receipt(proxy)
    assert rc["agent_waste"]["runs_stopped_early"] >= 1
    assert rc["context_saver"]["tokens_saved"] > 0   # repeated tool output was deduped
    assert rc["context_saver"]["tokens_after"] <= rc["context_saver"]["tokens_before"]


def test_detect_framework_from_user_agent():
    from wattnot.agentwaste.server import detect_framework
    assert detect_framework({"user-agent": "OpenAI/Python 2.5.0"}) == "openai-python"
    assert detect_framework({"x-wattnot-framework": "LangChain"}) == "langchain"
    assert detect_framework({}) == "unknown"
