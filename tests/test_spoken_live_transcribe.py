"""Live spoken-answer transcription: live mode transcribes via Muse Voice Transcribe (LIVE/MEASURED),
offline mode replays the cached transcripts (REPLAY), and a failed live call FAILS, never replays."""

from __future__ import annotations

import json

import pytest

from wattnot.spokenanswer import run
from wattnot.spokenanswer.assets import load_spoken_set
from wattnot.spokenanswer.client import SpokenTranscribeError, transcribe_spoken_live


def test_transcribe_spoken_live_reads_every_recording():
    spoken = load_spoken_set(require_files=True)
    seen = []

    def fake(audio: bytes) -> str:
        seen.append(len(audio))
        return "how many watts"

    out = transcribe_spoken_live(spoken, transcribe=fake)
    assert set(out) == set(spoken.ids)                 # every recording transcribed
    assert len(seen) == len(spoken.ids) and all(n > 0 for n in seen)  # real audio bytes read


def test_transcribe_spoken_live_empty_text_fails():
    spoken = load_spoken_set(require_files=True)
    with pytest.raises(SpokenTranscribeError):
        transcribe_spoken_live(spoken, transcribe=lambda audio: "")


def test_transcribe_spoken_live_error_never_replays():
    spoken = load_spoken_set(require_files=True)

    def boom(audio: bytes) -> str:
        raise RuntimeError("meta api 503")

    with pytest.raises(SpokenTranscribeError):
        transcribe_spoken_live(spoken, transcribe=boom)


def test_live_mode_failed_transcription_fails_the_block(tmp_path, monkeypatch):
    # a live run whose live transcription fails must return non-zero and NOT fall back to replay
    def boom(*a, **k):
        raise SpokenTranscribeError("no Meta key")

    monkeypatch.setattr(run, "transcribe_spoken_live", boom)
    rc = run.main(["--base-url", "http://127.0.0.1:9/v1", "--out", str(tmp_path)])
    assert rc == 1
    # it fails before building the answerer, so no served-model call and no summary is written
    assert not (tmp_path / "spoken_answer.json").exists()


def test_fake_mode_is_replay_labelled(tmp_path):
    rc = run.main(["--fake", "--out", str(tmp_path)])
    summary = json.loads((tmp_path / "spoken_answer.json").read_text())
    assert summary["transcripts_label"] == "REPLAY"
    assert summary["answers_label"] == "MOCK"
    assert rc in (0, 1)   # pass/fail depends on the offline answerer, not on labelling
