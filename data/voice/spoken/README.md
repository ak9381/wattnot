# Spoken test questions

These are the voice test cases for the watts-per-spoken-answer gate: 10 questions, each with a clean take and a take with seeded data-centre noise.

**The question audio is synthetic.** It is voiced with Piper, an open TTS, only to stand in for a user speaking. All transcription and answering is done by Meta's Muse Voice Transcribe and the Meta models. Piper plays no part in the pipeline being tested.

- Voice: Piper `en_US-ljspeech-medium`. The voice model is MIT ([rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/ljspeech/medium)) and was trained on the public-domain [LJ Speech](https://keithito.com/LJ-Speech-Dataset/) dataset. Piper itself is MIT.
- Text: ours, CC0. The transcript of each take is the question text (`transcripts.json`).
- Audio licence: CC0, same as the text.
- Rebuild: put the Piper CLI and the voice model on the box, then run
  `WATTNOT_PIPER_MODEL=/path/en_US-ljspeech-medium.onnx python scripts/gen_spoken_assets.py --engine piper`.
  It runs on CPU only. The noise is seeded, so the noisy takes come out the same every time.

The earlier takes were voiced with edge-tts (Microsoft Edge voices). Those are no longer in the repo or the release set, because the licence for that audio is unclear.
