"""H100 hearing path: header sample rate, Meta spend in the ledger, and a working timeout."""

from __future__ import annotations

import io
import json
import urllib.error
import wave

import pytest

from wattnot.meta.ledger import BudgetExceeded, SpendLedger
from wattnot.spokenanswer import client


def _wav(rate=24000, secs=2.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * secs))
    return buf.getvalue()


class _Resp:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return json.dumps({"transcript": " forty watts "}).encode()


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr("wattnot.meta.client.load_key", lambda: "k")


def test_sends_header_sample_rate(tmp_path, key):
    seen = {}
    def op(req, timeout=0):
        seen["data"] = req.data
        return _Resp()
    led = SpendLedger(tmp_path / "l.json", cap_usd=1.0, soft_usd=0.5)
    assert client.transcribe_wav_live(_wav(24000), ledger=led, urlopen=op) == "forty watts"
    assert b'"sampleRateHertz": 24000' in seen["data"]


def test_records_meta_spend_in_ledger(tmp_path, key):
    led = SpendLedger(tmp_path / "l.json", cap_usd=1.0, soft_usd=0.5)
    client.transcribe_wav_live(_wav(16000, 3600.0 / 100), ledger=led, urlopen=lambda r, timeout=0: _Resp())
    assert led.total() == pytest.approx(0.18 / 100)
    assert led.entries()[0]["purpose"] == "voice:transcribe-spoken"


def test_cap_refuses_before_the_call(tmp_path, key):
    led = SpendLedger(tmp_path / "l.json", cap_usd=0.0001, soft_usd=0.0001)
    called = []
    with pytest.raises(BudgetExceeded):
        client.transcribe_wav_live(_wav(16000, 60.0), ledger=led,
                                   urlopen=lambda r, timeout=0: called.append(1))
    assert not called


def test_rejected_call_is_refunded(tmp_path, key):
    led = SpendLedger(tmp_path / "l.json", cap_usd=1.0, soft_usd=0.5)
    def op(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, None)
    with pytest.raises(urllib.error.HTTPError):
        client.transcribe_wav_live(_wav(), ledger=led, urlopen=op)
    assert led.total() == 0.0


def test_timeout_is_honoured(tmp_path, key):
    seen = {}
    def op(req, timeout=0):
        seen["t"] = timeout
        return _Resp()
    led = SpendLedger(tmp_path / "l.json", cap_usd=1.0, soft_usd=0.5)
    client.transcribe_wav_live(_wav(), timeout_s=7.5, ledger=led, urlopen=op)
    assert seen["t"] == 7.5


def test_cli_timeout_reaches_the_transcriber(monkeypatch):
    from wattnot.spokenanswer import run
    got = {}
    def fake_live(spoken, *, timeout_s=60.0, **k):
        got["t"] = timeout_s
        raise client.SpokenTranscribeError("stop here")
    monkeypatch.setattr(run, "transcribe_spoken_live", fake_live)
    monkeypatch.delenv("WATTNOT_META_MOCK", raising=False)
    assert run.main(["--base-url", "http://127.0.0.1:9", "--transcribe-timeout-s", "12"]) == 1
    assert got["t"] == 12.0


def test_default_transcriber_passes_timeout(monkeypatch):
    from wattnot.spokenanswer.assets import load_spoken_set
    got = []
    monkeypatch.setattr("wattnot.meta.client.meta_mock_on", lambda: False)
    monkeypatch.setattr(client, "transcribe_wav_live", lambda a, timeout_s: got.append(timeout_s) or "x")
    client.transcribe_spoken_live(load_spoken_set(require_files=True), timeout_s=9.0)
    assert got and set(got) == {9.0}
