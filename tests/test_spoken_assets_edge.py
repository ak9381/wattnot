"""The edge-tts (neural) engine for spoken assets, and that live._transcribe reads the real WAV
sample rate from the header instead of assuming 16 kHz / 32 kB/s.

Root cause this covers: the committed flite/espeak clips were robotic and the hosted ASR garbled
them; an edge-tts clip of the same text transcribed exactly. edge-tts clips may carry their own
sample rate, so the transcribe call and its duration must come from the WAV header.
"""

from __future__ import annotations

import importlib.util
import io
import json
import wave
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "gen_spoken_assets.py"
_spec = importlib.util.spec_from_file_location("gen_spoken_assets", _PATH)
gsa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gsa)


# --- engine selection -----------------------------------------------------------------------

def test_edge_tts_is_the_preferred_engine():
    assert gsa.ENGINE_ORDER[0] == "edge-tts"
    assert gsa.EDGE_DEFAULT_VOICE == "en-US-AndrewNeural"


def test_pick_engine_prefers_edge_then_falls_back(monkeypatch):
    # edge-tts available -> picked over the offline engines
    monkeypatch.setattr(gsa, "_engine_available", lambda n: True)
    assert gsa.pick_engine() == "edge-tts"
    # edge-tts unavailable -> next available in order
    monkeypatch.setattr(gsa, "_engine_available", lambda n: n != "edge-tts")
    assert gsa.pick_engine() == "ffmpeg-flite"
    # a forced engine that is unavailable -> None (never silently substitute)
    monkeypatch.setattr(gsa, "_engine_available", lambda n: False)
    assert gsa.pick_engine("edge-tts") is None


def test_edge_cli_reads_env_override(monkeypatch):
    monkeypatch.setenv("WATTNOT_EDGE_TTS", "/tmp/ttsvenv/bin/edge-tts")
    seen = {}

    def fake_which(name):
        seen["name"] = name
        return name  # pretend it exists

    monkeypatch.setattr(gsa.shutil, "which", fake_which)
    assert gsa._edge_cli() == "/tmp/ttsvenv/bin/edge-tts"
    assert seen["name"] == "/tmp/ttsvenv/bin/edge-tts"


# --- live._transcribe reads the real sample rate --------------------------------------------

def _wav_bytes(sample_rate: int, seconds: float) -> bytes:
    buf = io.BytesIO()
    n = int(sample_rate * seconds)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n)
    return buf.getvalue()


def test_transcribe_uses_header_sample_rate_and_duration(tmp_path, monkeypatch):
    from wattnot.e2e import live

    sr, secs = 24000, 2.0   # a non-16 kHz clip, like an edge-tts take
    wav = tmp_path / "q.wav"
    wav.write_bytes(_wav_bytes(sr, secs))

    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"transcript": "how many GPUs are in the rack?"}).encode()

    def fake_urlopen(req, timeout=0):
        captured["data"] = req.data
        return _Resp()

    charged = []

    class _FakeLedger:
        def charge_usd(self, model, purpose, usd, note=""):
            charged.append((model, purpose, usd))
            return "eid"

        def refund(self, eid, note=""):
            charged.append(("refund", eid, 0))

    monkeypatch.setattr(live.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("wattnot.meta.client.load_key", lambda: "k")
    monkeypatch.setattr("wattnot.meta.ledger.SpendLedger", lambda *a, **k: _FakeLedger())

    text, usd = live._transcribe(wav)
    assert text == "how many GPUs are in the rack?"
    # the request carries the real header rate, not a hard-coded 16000
    assert b'"sampleRateHertz": 24000' in captured["data"]
    # duration (hence cost) comes from frames / sample_rate = 2.0 s, not (len-44)/32000
    assert usd == pytest.approx(round(2.0 / 3600 * 0.18, 6))
    # the ledger gets the real (non-zero) cost before the call, not a 0-token entry
    assert charged == [("muse-voice-transcribe-1.0", "voice:transcribe-e2e", usd)] and usd > 0
