"""End-to-end validation of the thinking-budget enforcer over real HTTP.

Not a mock: a tiny stdlib HTTP server binds an ephemeral localhost port and speaks the
chat-completions wire protocol (streaming SSE and non-streaming JSON), emulating the four
server behaviours the enforcer must survive on the GPU host:

  1. ``raw``         thinking leaks into ``content`` wrapped in ``<think>...</think>``
                     (the historical assumption; the reasoning parser is off/failed).
  2. ``split``       a working reasoning parser routes thinking into ``reasoning_content``
                     and *consumes* the close marker (non-stream message and stream deltas).
  3. ``strip-close`` a half-working parser removes ``</think>`` but leaves the whole chain of
                     thought (and the answer) in ``content`` -- the degraded case.
  4. ``nothink``     a non-reasoning model: plain answer, no markers, no reasoning channel.

Each behaviour is driven at a budget hit mid-thinking (the stream is cut and the answer is
forced) and at an early answer (the thought finishes inside the budget, nothing is forced).
The real ``think_stream`` / ``thinking_budget`` code runs against the socket -- probe, stream
enforcer, non-stream fallback and the full ``run()`` block that writes the receipt.

No network beyond loopback, no GPU, no money, nothing launched.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from wattnot.longrun import think_stream as S
from wattnot.longrun.thinking_budget import BudgetChat
from wattnot.longrun.thinking_budget import run as run_block
from wattnot.proof.client import THINK_CLOSE, THINK_OPEN, Reply

# A chain of thought of a known size: 40 x 10 chars = 400 chars ~= 100 reasoning tokens at the
# harness' 4 chars/token. Chosen so an easy task (base budget 128 tokens) finishes its thought at
# 1x (early answer) but is cut at 0.25x/0.5x (32/64 tokens) -- both paths in one run().
THOUGHT = ("reasoning " * 40)
assert len(THOUGHT) == 400
ANSWER = "FINAL 42"            # terminates the complex agent loop in one turn; a number elsewhere
MODEL = "muse-glimmer-30b"

# server behaviour -> the transport mode the self-probe should detect
EXPECT_MODE = {"raw": S.MODE_MARKER, "split": S.MODE_SPLIT,
               "strip-close": S.MODE_MARKER, "nothink": S.MODE_NOTHINK}


def _chunk(delta: dict, finish=None, usage=None) -> dict:
    ch: dict = {"choices": [{"delta": delta, "finish_reason": finish}]}
    if usage is not None:
        ch["usage"] = {"completion_tokens": usage}
    return ch


def _pieces(text: str, n: int = 20):
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


class FakeChatServer(ThreadingHTTPServer):
    """A chat-completions server whose reply shape is fixed by ``behaviour``."""

    daemon_threads = True

    def __init__(self, behaviour: str):
        self.behaviour = behaviour
        self.thought = THOUGHT
        self.answer = ANSWER
        super().__init__(("127.0.0.1", 0), _Handler)

    @property
    def url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}/v1/chat/completions"


def _honor(answer: str, max_tokens: int):
    """Truncate an answer to a token (==chars/4) cap, reporting the finish_reason vLLM would."""
    if max_tokens and len(answer) // 4 > max_tokens:
        answer = answer[:max_tokens * 4]
        return answer, "length"
    return answer, "stop"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"      # connection closes at end -> client reads to EOF

    def log_message(self, *a):         # keep the test output clean
        pass

    # -- response writers -----------------------------------------------------------------
    def _json(self, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, chunks):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # the enforcer cut the stream at its budget and stopped reading -- expected.
            pass

    # -- the wire protocol ----------------------------------------------------------------
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        beh = self.server.behaviour
        thought, answer = self.server.thought, self.server.answer

        if body.get("continue_final_message"):
            text, finish = _honor(answer, int(body.get("max_tokens") or 0))
            self._json({"choices": [{"finish_reason": finish, "message": {"content": text}}],
                        "usage": {"completion_tokens": max(1, len(text) // 4)}})
            return

        if body.get("stream"):
            self._sse(self._stream(beh, thought, answer))
            return

        self._json(self._nonstream(beh, thought, answer, int(body.get("max_tokens") or 0)))

    def _stream(self, beh, thought, answer):
        rtoks, atoks = len(thought) // 4, len(answer) // 4
        if beh == "split":
            for p in _pieces(thought):
                yield _chunk({"reasoning_content": p})
            for p in _pieces(answer):
                yield _chunk({"content": p})
        elif beh == "raw":
            for p in _pieces(f"{THINK_OPEN}{thought}{THINK_CLOSE}{answer}"):
                yield _chunk({"content": p})
        elif beh == "strip-close":                   # open stays, close is stripped by the parser
            for p in _pieces(f"{THINK_OPEN}{thought}{answer}"):
                yield _chunk({"content": p})
        else:                                        # nothink
            for p in _pieces(answer):
                yield _chunk({"content": p})
        yield _chunk({}, finish="stop", usage=rtoks + atoks)

    def _nonstream(self, beh, thought, answer, max_tokens):
        """The non-streaming path the raw-marker BudgetChat fallback uses (phase A sends a budget
        as max_tokens and stop=[</think>])."""
        cap = (max_tokens or 10_000) * 4
        if beh == "split":
            if len(thought) >= cap:                  # ran out of budget before closing the thought
                return {"choices": [{"finish_reason": "length",
                                     "message": {"reasoning_content": thought[:cap], "content": ""}}],
                        "usage": {"completion_tokens": max_tokens}}
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"reasoning_content": thought, "content": answer}}],
                    "usage": {"completion_tokens": len(thought) // 4 + len(answer) // 4}}
        if beh in ("raw", "strip-close"):
            full = f"{THINK_OPEN}{thought}"           # vLLM stops at </think>, removing it
            if len(full) >= cap:
                return {"choices": [{"finish_reason": "length",
                                     "message": {"content": full[:cap]}}],
                        "usage": {"completion_tokens": max_tokens}}
            return {"choices": [{"finish_reason": "stop", "message": {"content": full}}],
                    "usage": {"completion_tokens": len(full) // 4}}
        text, finish = _honor(answer, max_tokens)     # nothink
        return {"choices": [{"finish_reason": finish, "message": {"content": text}}],
                "usage": {"completion_tokens": max(1, len(text) // 4)}}


@pytest.fixture
def server(request):
    srv = FakeChatServer(request.param)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


class FakeMeter:
    """A stand-in energy meter (NOT a GPU): monotonically increasing millijoules so the gate can
    compute a positive J/correct without any hardware."""

    def __init__(self):
        self.mj = 0

    def total_energy_mj(self, handle):
        self.mj += 1_000
        return self.mj


def _probe(srv) -> S.ThinkProbe:
    """The real startup self-probe, over the socket, exactly as run() builds it."""
    stream_post = S.StreamBudgetChat(srv.url, MODEL, 0)._stream_post
    return S.probe_mode(stream_post, MODEL)


# ---- 1. the self-probe picks the right mode (real streaming HTTP) ------------------------------

@pytest.mark.parametrize("server", list(EXPECT_MODE), indirect=True)
def test_probe_detects_mode_over_http(server):
    p = _probe(server)
    assert p.mode == EXPECT_MODE[server.behaviour]
    assert p.confident
    assert p.as_receipt()["mode"] == EXPECT_MODE[server.behaviour]


# ---- 2. budget hit mid-thinking: the stream is cut and the answer is forced --------------------

@pytest.mark.parametrize("server", ["raw", "split", "strip-close"], indirect=True)
def test_budget_hit_forces_answer_over_http(server):
    p = _probe(server)
    chat = S.StreamBudgetChat(server.url, MODEL, budget=5, mode=p.mode)  # 5 tok << 100 tok thought
    r = chat([{"role": "user", "content": "q"}], 64)
    assert isinstance(r, Reply)
    assert chat.forced["q"] == 1            # the thinking budget was enforced -> forced close
    assert r.text == ANSWER                 # phase B produced the forced answer
    assert chat.cut.get("q") is None        # the short answer itself was not cut


@pytest.mark.parametrize("server", ["raw", "split", "strip-close"], indirect=True)
def test_forced_answer_itself_can_be_cut_over_http(server):
    p = _probe(server)
    chat = S.StreamBudgetChat(server.url, MODEL, budget=5, mode=p.mode, answer_cap=1)
    r = chat([{"role": "user", "content": "q"}], 64)
    assert chat.forced["q"] == 1 and chat.cut["q"] == 1 and r.finish == "length"


# ---- 3. early answer inside the budget: nothing is forced --------------------------------------

@pytest.mark.parametrize("server", ["raw", "split"], indirect=True)
def test_early_answer_skips_forcing_over_http(server):
    p = _probe(server)
    chat = S.StreamBudgetChat(server.url, MODEL, budget=10_000, mode=p.mode)  # roomy budget
    r = chat([{"role": "user", "content": "q"}], 64)
    assert r.text == ANSWER and r.finish == "stop"
    assert not chat.forced and not chat.cut   # the thought finished on its own; no phase B


@pytest.mark.parametrize("server", ["nothink"], indirect=True)
def test_nothink_is_single_phase_over_http(server):
    p = _probe(server)
    chat = S.StreamBudgetChat(server.url, MODEL, budget=5, mode=p.mode)
    r = chat([{"role": "user", "content": "q"}], 64)
    assert r.text == ANSWER and not chat.forced   # no thinking channel -> no budget to force


# ---- 4. the non-streaming raw-marker fallback (BudgetChat) also forces over HTTP ---------------

@pytest.mark.parametrize("server", ["split"], indirect=True)
def test_budgetchat_fallback_forces_over_http(server):
    chat = BudgetChat(server.url, MODEL, budget=5)   # 20-char budget << 400-char thought
    r = chat([{"role": "user", "content": "q"}], 64)
    assert chat.forced["q"] == 1 and r.text == ANSWER


# ---- 5. the full run() block end to end: receipt records the probed mode -----------------------

@pytest.mark.parametrize("server", list(EXPECT_MODE), indirect=True)
def test_run_block_end_to_end_records_mode(server, tmp_path, monkeypatch):
    monkeypatch.setattr(S, "ENABLED", True)
    doc = run_block(tmp_path, fake=False, per_tier=1, url=server.url, model=MODEL,
                    nvml=FakeMeter(), handle=None, log=lambda *a: None)

    # the receipt is on disk and records the transport the probe chose
    written = json.loads((tmp_path / "thinking_policy.json").read_text())
    assert written["sha256"] == doc["sha256"]
    assert written["think_transport"] == doc["think_transport"]
    assert doc["label"] == "MEASURED"
    assert doc["think_transport"]["mode"] == EXPECT_MODE[server.behaviour]
    assert doc["think_transport"]["confident"] is True
    assert doc["think_transport"]["enforcer"] == "stream-reasoning-budget"

    easy_cells = {c["budget_x"]: c for c in doc["tiers"]["easy"]["cells"]}
    if server.behaviour != "nothink":
        # the budget was actually enforced on the tight easy cells (0.25x = 32 tok < 100 tok thought)
        assert any(c["forced_close_rate"] > 0 for c in easy_cells.values())
    if server.behaviour in ("raw", "split"):
        # ... but the roomy 1x easy cell (128 tok > 100 tok) let the thought finish: not over-forced
        assert easy_cells[1.0]["forced_close_rate"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
