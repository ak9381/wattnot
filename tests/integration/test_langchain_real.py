"""Real LangChain -> WattNot proxy -> live Meta Muse API. MEASURED, costs a few cents at most.

Skipped unless a Meta key is available (META_API_KEY or `muse login`) and
WATTNOT_LIVE=1 is set, so CI and plain `pytest` never make paid calls.

    WATTNOT_LIVE=1 pytest tests/integration/test_langchain_real.py -s

LangChain only changes its base URL. The proxy runs small-model-first routing over two
live Muse tiers (Spark contributor first, then Spark 1.3), the agent-waste guard and the
context saver. A JSON summary (receipt, stats, spend) is written to
receipts/frameworks/langchain-live.json (override with WATTNOT_LC_OUT).
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("langchain_openai")
pytest.importorskip("langchain")
uvicorn = pytest.importorskip("uvicorn")

from wattnot.meta.client import GuardedClient, load_key  # noqa: E402

if os.environ.get("WATTNOT_LIVE") != "1" or not load_key():
    pytest.skip("live test: set WATTNOT_LIVE=1 and META_API_KEY (or muse login)",
                allow_module_level=True)

from langchain.agents import create_agent  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

from wattnot.agentwaste.config import WasteConfig  # noqa: E402
from wattnot.agentwaste.server import create_app  # noqa: E402
from wattnot.meta.ledger import SpendLedger  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("WATTNOT_LC_OUT", ROOT / "receipts" / "frameworks" / "langchain-live.json"))
TIERS = (
    {"name": "spark-contributor", "model": "muse-spark-1.3-contributor", "where": "hosted"},
    {"name": "spark-1.3", "model": "muse-spark-1.3", "where": "hosted"},
)
# Keys LangChain may send that the Meta API does not need.
_DROP = ("stream", "n", "stream_options", "parallel_tool_calls")

QA = [  # (prompt, regex the answer must match)
    ("What is 17 * 23? Reply with just the number.", r"\b391\b"),
    ("What is the capital of Japan? One word.", r"tokyo"),
    ("How many sides does a hexagon have? Reply with just the number.", r"\b6\b|six"),
    ("Spell the word 'watt' backwards. Reply with just the word.", r"ttaw"),
    ("Which planet is known as the Red Planet? One word.", r"mars"),
    ("What is the chemical symbol for gold? Reply with just the symbol.", r"\bau\b"),
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def proxy(tmp_path_factory):
    from wattnot.context_saver.middleware import ContextSaver

    ledger = SpendLedger(path=tmp_path_factory.mktemp("ledger") / "meta-spend.jsonl")
    client = GuardedClient(ledger=ledger, timeout_s=90)
    seen: list[str] = []

    def upstream(body: dict, headers: dict) -> dict:
        body = {k: v for k, v in body.items() if k not in _DROP}
        body.setdefault("max_completion_tokens", 400)
        seen.append(str(body.get("model")))
        return client(body, purpose="langchain-live-test")

    app = create_app(upstream, WasteConfig(), context_saver=ContextSaver(), tiers=TIERS,
                     label="MEASURED")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield {"base": f"http://127.0.0.1:{port}/v1", "ledger": ledger, "seen": seen}
    server.should_exit = True
    th.join(5)


def _llm(base: str, run: str) -> ChatOpenAI:
    return ChatOpenAI(base_url=base, api_key="unused-by-proxy", model="muse-spark-1.3",
                      temperature=0, max_retries=0, timeout=120,
                      default_headers={"X-WattNot-Framework": "langchain", "X-WattNot-Run": run})


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read())


@tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers exactly."""
    return a * b


def test_langchain_live_through_wattnot(proxy):
    base = proxy["base"]
    results = []
    for i, (q, pat) in enumerate(QA):
        t0 = time.monotonic()
        ans = _llm(base, f"qa-{i}").invoke([("user", q)]).content
        ok = bool(re.search(pat, str(ans), re.I))
        results.append({"prompt": q, "answer": str(ans)[:200], "correct": ok,
                        "latency_s": round(time.monotonic() - t0, 2)})

    # One real LangChain agent with a tool. The model must call multiply and use its result.
    agent = create_agent(_llm(base, "agent-0"), tools=[multiply],
                         system_prompt="Use the multiply tool for any multiplication.")
    out = agent.invoke({"messages": [("user", "Use the tool to compute 12345 * 6789.")]})
    msgs = out["messages"]
    tool_used = any(getattr(m, "type", "") == "tool" and m.name == "multiply" for m in msgs)
    final = str(msgs[-1].content)
    agent_ok = tool_used and "83810205" in final.replace(",", "")
    results.append({"prompt": "agent: 12345 * 6789 via multiply tool", "answer": final[:200],
                    "correct": agent_ok, "tool_used": tool_used})

    receipt = _get(base, "/wattnot/receipt")
    stats = _get(base, "/wattnot/stats")
    lp = proxy["ledger"].path
    spend = json.loads(lp.read_text()) if lp.exists() else None
    summary = {"label": "MEASURED", "when": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "frameworks_client": "langchain-openai ChatOpenAI + langchain.agents.create_agent",
               "upstream_models_called": proxy["seen"], "results": results,
               "receipt": receipt, "stats_total": stats["total"], "ledger": spend}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps({"results": results, "receipt": receipt}, indent=2))

    assert all(r["correct"] for r in results), results
    assert receipt["label"] == "MEASURED"
    assert receipt["frameworks"] == ["langchain"]
    assert receipt["calls"] >= len(QA) + 2          # agent makes >= 2 model calls
    assert receipt["routing"]["enabled"] and receipt["routing"]["routed_calls"] == receipt["calls"]
    assert receipt["context_saver"]["tokens_before"] > 0
    assert stats["total"]["upstream_calls"] >= receipt["calls"]


def test_waste_guard_stops_live_loop(proxy):
    """A LangChain client replays the same tool call; the guard stops it before Muse is billed."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    base = proxy["base"]
    before = len(proxy["seen"])
    llm = _llm(base, "loop-0")
    msgs = [HumanMessage("Read a.py and tell me what f returns.")]
    stopped = False
    for i in range(6):
        msgs.append(AIMessage("", tool_calls=[{"id": f"c{i}", "name": "read_file",
                                               "args": {"path": "a.py"}}]))
        msgs.append(ToolMessage("def f():\n    return 1\n", tool_call_id=f"c{i}"))
        out = llm.invoke(msgs)
        if "stopped early" in str(out.content).lower():
            stopped = True
            break
    stats = _get(base, "/wattnot/stats")
    assert stopped
    assert stats["runs"]["loop-0"]["stopped"]
    assert len(proxy["seen"]) - before < 2 * 6     # the loop did not reach all its upstream calls
