from pathlib import Path

from wattnot.gateway.traffic import TrafficRun


def test_voice_item_becomes_a_chat_message(tmp_path: Path):
    wav = tmp_path / "q.wav"
    wav.write_bytes(b"RIFF")
    calls = []
    tr = TrafficRun.__new__(TrafficRun)
    tr.transcribe = lambda b: calls.append(b) or "How much power does the bay use?"
    body = tr._voice_body({"wav": str(wav)})
    assert body["messages"][0]["content"].startswith("How much power")
    tr._voice_body({"wav": str(wav)})          # the second arm reuses the transcript
    assert len(calls) == 1


def test_voice_item_falls_back_when_transcribe_fails():
    tr = TrafficRun.__new__(TrafficRun)
    tr.transcribe = None
    assert "6 times 7" in tr._voice_body({"wav": None})["messages"][0]["content"]
