"""r04-try1: all 20 LangChain items shared one guard run id, so the run's step budget stopped
items 13-20 and the saver arm scored 2/20. Each item must be its own guard run."""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_each_item_gets_its_own_guard_run(monkeypatch):
    seen = []

    class FakeLLM:
        def __init__(self, **kw):
            seen.append(kw["default_headers"]["X-WattNot-Run"])

        def bind_tools(self, _):
            return self

        def invoke(self, msgs, config=None):
            return types.SimpleNamespace(content="42", tool_calls=None)

    mod = types.ModuleType("langchain_openai")
    mod.ChatOpenAI = FakeLLM
    monkeypatch.setitem(sys.modules, "langchain_openai", mod)
    spec = importlib.util.spec_from_file_location("lcp", ROOT / "scripts/langchain_pass.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "tools", lambda: [])
    rows = [{"prompt": "q", "item": None, "ok": lambda it, t: True} for _ in range(3)]
    m.run_agent("http://x/v1", "m", rows, 2, "lc-saver_on")
    assert seen == ["lc-saver_on-0", "lc-saver_on-1", "lc-saver_on-2"]
