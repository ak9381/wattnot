"""LangChain pass on Baseten: plumbing against a local MOCK Baseten server (no paid call)."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from wattnot.agentwaste import baseten_upstream as B
from wattnot.spend.guard import HardStop

ROOT = Path(__file__).resolve().parents[1]
SEEN: list = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": b})
        d = {"id": "m", "object": "chat.completion", "created": 0, "model": b["model"],
             "choices": [{"index": 0, "finish_reason": "stop",
                          "message": {"role": "assistant", "content": "Done.\n#### 42"}}],
             "usage": {"prompt_tokens": 300, "completion_tokens": 50, "total_tokens": 350}}
        out = json.dumps(d).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture()
def mock_bt():
    SEEN.clear()
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_body_uses_effort_not_template_kwargs():
    up = B.BasetenUpstream("k", B.make_guard("/tmp/_lcbt_unused.jsonl"))
    b = up.body({"model": "x", "messages": [], "chat_template_kwargs": {"enable_thinking": False},
                 "max_completion_tokens": 2000})
    assert b["model"] == "meta-models/muse-spark-open" and "chat_template_kwargs" not in b
    assert b["reasoning_effort"] == "medium" and b["max_tokens"] == 2000
    assert [t["body_extra"]["reasoning_effort"] for t in B.tiers()] == ["low", "medium"]


def test_hard_stop_before_spend(tmp_path, mock_bt):
    g = B.make_guard(tmp_path / "l.jsonl", hard_usd=0.0001)
    up = B.BasetenUpstream("k", g, base_url=mock_bt)
    with pytest.raises(HardStop):
        up({"messages": [{"role": "user", "content": "hi"}]}, {})
    assert SEEN == [] and up.stopped


def test_dead_key_refused(monkeypatch):
    from wattnot.cloud.transport import read_key
    monkeypatch.delenv("BASETEN_METRICS_KEY", raising=False)
    monkeypatch.setenv("BASETEN_API_KEY", "dead")
    with pytest.raises(Exception):
        read_key()


def test_full_pass_on_mock(tmp_path, mock_bt, monkeypatch):
    pytest.importorskip("langchain_openai")
    monkeypatch.setenv("BASETEN_METRICS_KEY", "mock-key-not-real")
    spec = importlib.util.spec_from_file_location("lcp_bt", ROOT / "scripts/langchain_pass.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    out = tmp_path / "o"
    m.main(["--provider", "baseten", "--baseten-url", mock_bt, "--out", str(out), "--n", "3",
            "--route", "final-check"])
    res = json.loads((out / "langchain_pass.json").read_text())
    assert res["label"] == "MOCK" and res["provider"] == "baseten"
    assert all(p == "/v1/chat/completions" for p in {s["path"] for s in SEEN})
    assert all(s["auth"] == "Api-Key mock-key-not-real" for s in SEEN)
    assert all("chat_template_kwargs" not in s["body"] for s in SEEN)
    assert all(s["body"]["model"] == "meta-models/muse-spark-open" for s in SEEN)
    off, on = res["arms"]["saver_off"]["baseten"], res["arms"]["saver_on"]["baseten"]
    assert set(off["efforts"]) == {"medium"} and "low" in on["efforts"]
    assert off["calls"] + on["calls"] == len(SEEN)
    assert on["energy_wh"]["label"].startswith("ESTIMATE")
    led = [json.loads(x) for x in (out / "baseten_spend.ledger.jsonl").read_text().splitlines()]
    assert sum(e["kind"] == "settle" for e in led) == len(SEEN)
    assert res["spend_usd"] == pytest.approx(off["usd"] + on["usd"], abs=1e-5)
    assert "mock-key-not-real" not in (out / "langchain_pass.json").read_text()
