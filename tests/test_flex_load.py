"""The flex load generator: windowing, scoring, the HTTP sender, and the progress file."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from wattnot.flex import load as ld
from wattnot.flex.hw import ProgressFile


def test_bundled_prompts_load_and_score():
    ps = ld.load_prompts(ld.DEFAULT_PROMPTS)
    assert len(ps) >= 20
    assert all(p.prompt and p.expect for p in ps)
    assert ld.passes("It is Paris.", ["paris"])
    assert not ld.passes("It is Lyon.", ["paris"])


def test_prompt_file_needs_expect(tmp_path):
    f = tmp_path / "p.jsonl"
    f.write_text('{"prompt": "hi"}\n')
    with pytest.raises(ValueError):
        ld.load_prompts(f)


def test_p99_is_nearest_rank():
    assert ld.p99([1.0]) == 1.0
    assert ld.p99(list(map(float, range(1, 101)))) == 99.0
    assert ld.p99([5.0] * 99 + [900.0]) == 5.0  # 1 slow request in 100 is not the 99th


def test_window_drops_old_rows_and_counts_failures():
    w = ld.Window(10.0)
    w.add(100.0, 50.0, True)
    w.add(105.0, 70.0, False, error=True)
    w.add(111.0, 90.0, True)
    snap = w.snapshot(112.0)
    assert snap["n"] == 2 and snap["pass_rate"] == 0.5 and snap["errors"] == 1
    assert w.snapshot(500.0) is None


def test_run_load_writes_lines_the_gpu_backend_can_read(tmp_path):
    ps = ld.load_prompts(ld.DEFAULT_PROMPTS)
    send = ld.fake_sender({p.prompt: p.expect for p in ps}, base_s=0.01, sleep=time.sleep)
    prog = tmp_path / "progress.jsonl"
    s = ld.run_load(send, ps, prog, rps=40, concurrency=8, duration_s=2.6, interval_s=0.5,
                    window_s=5, label="SIMULATED")
    assert s.requests > 40 and s.progress_lines >= 3 and s.label == "SIMULATED"
    rows = [json.loads(x) for x in prog.read_text().splitlines()]
    assert all({"p99_ms", "pass_rate", "rps"} <= r.keys() for r in rows)
    got = ProgressFile(prog).latest()  # the same reader flex run --backend gpu uses
    assert 0 < got["pass_rate"] <= 1 and got["p99_ms"] > 0


def test_latency_counts_from_the_scheduled_time(tmp_path):
    # one slow server, one worker: the queue builds, so p99 must show the wait
    ps = [ld.Prompt("a", "q", ["x"])]
    s = ld.run_load(lambda p, m: (time.sleep(0.05), "x")[1], ps, tmp_path / "p.jsonl", rps=40,
                    concurrency=1, duration_s=1.5, interval_s=0.5, window_s=5)
    assert s.p99_ms and s.p99_ms > 200


class _Fake(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Fake.seen.append((self.path, body, self.headers.get("Authorization")))
        out = json.dumps({"choices": [{"message": {"content": "Paris"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_http_sender_talks_chat_completions():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_port}"
        send = ld.http_sender(url, "m1", api_key="k-test")
        assert send("Capital of France?", 16) == "Paris"
        path, body, auth = _Fake.seen[-1]
        assert path == "/v1/chat/completions"
        assert body["model"] == "m1" and body["temperature"] == 0 and body["max_tokens"] == 16
        assert auth == "Bearer k-test"
        assert ld.http_sender(url + "/v1", "m")("q", 4) == "Paris"
        assert _Fake.seen[-1][0] == "/v1/chat/completions"
    finally:
        srv.shutdown()


def test_errors_count_as_failures(tmp_path):
    def boom(prompt, n):
        raise OSError("down")

    s = ld.run_load(boom, [ld.Prompt("a", "q", ["x"])], tmp_path / "p.jsonl", rps=20,
                    concurrency=2, duration_s=1.2, interval_s=0.4)
    assert s.errors > 5 and s.pass_rate == 0.0


def test_cli_load_needs_url_or_fake(tmp_path, capsys):
    from wattnot.cli import main

    with pytest.raises(SystemExit):
        main(["flex", "load", "--progress", str(tmp_path / "p.jsonl"), "--duration", "1"])
    assert "--fake" in capsys.readouterr().err


def test_cli_fake_dry_run(tmp_path, capsys):
    from wattnot.cli import main

    prog = tmp_path / "p.jsonl"
    rc = main(["flex", "load", "--fake", "--rps", "30", "--duration", "2.2",
               "--progress", str(prog)])
    assert rc == 0 and "SIMULATED" in capsys.readouterr().out and prog.read_text().strip()
