"""A tiny fake OpenAI chat server for plumbing tests (MOCK answers, no model)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send({"data": [{"id": "meta-models/Muse-Glimmer-30B"}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        think = (body.get("chat_template_kwargs") or {}).get("enable_thinking", True)
        self._send({"choices": [{"message": {"content": "<think>x</think>The answer is 42.",
                                             "reasoning_content": "hmm" if think else ""},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 40 if think else 10,
                              "completion_tokens_details": {"reasoning_tokens": 30 if think else 0}}})


def start_fake(port: int):
    srv = ThreadingHTTPServer(("127.0.0.1", port), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
