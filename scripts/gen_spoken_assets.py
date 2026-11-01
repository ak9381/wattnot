#!/usr/bin/env python3
"""Generate REAL spoken-answer WAVs from the question text with a neural TTS.

This replaces the 364-byte placeholder stubs with real audio so the
watts-per-spoken-answer gate runs on real recordings, not stubs. It makes NO
paid call and needs no API key. The spoken content IS the question text, so the
transcript of every take is exactly that text (no separate transcription step).

    python scripts/gen_spoken_assets.py --engine edge-tts   # neural voice (preferred)
    python scripts/gen_spoken_assets.py                       # auto-pick best engine

The preferred engine is ``edge-tts`` - a FREE neural TTS (Microsoft Edge voices,
no API key). The committed flite/espeak voices are robotic and the hosted ASR
(Muse Voice Transcribe) garbled them ("How many many US are interacting?"); an
edge-tts clip of the same text transcribed EXACTLY in a live test. edge-tts is
online (its only network use); the offline flite/espeak engines stay as a
fallback when it is unavailable. All are SYNTHETIC - a machine voice, never a
human.

edge-tts writes an mp3, which ffmpeg converts to 16 kHz mono s16le PCM with 300 ms
of leading and trailing silence (the lead-in silence matters for the ASR). Pass
``--engine`` to force one, ``--voice`` to pick an edge-tts voice (default
en-US-AndrewNeural). The CLI is found at ``$WATTNOT_EDGE_TTS`` or ``edge-tts`` on
PATH. If no engine is available it stops and changes nothing (never a fake WAV).

For each manifest item it writes ``data/voice/spoken/<file>.wav``. The
``datacenter`` takes get low-level noise mixed in (real speech + real added
noise), so the clean/noisy split is genuine. It then rewrites ``transcripts.json``
(transcript = the question text) and stamps the manifest ``source`` honestly as
``TTS-<engine>`` so the no-mock guard accepts the live run.

Importing this module does nothing until ``main()`` runs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "data" / "voice" / "spoken"

EDGE_DEFAULT_VOICE = "en-US-AndrewNeural"

# Offline engine -> argv builder: (text, out_wav) -> command, each writes a WAV directly.
# edge-tts is handled separately (online, two-step: mp3 -> ffmpeg) and preferred over these.
ENGINES = {
    "espeak-ng": lambda text, out: ["espeak-ng", "-v", "en", "-s", "150", "-w", str(out), text],
    "espeak": lambda text, out: ["espeak", "-v", "en", "-s", "150", "-w", str(out), text],
    "pico2wave": lambda text, out: ["pico2wave", "-l", "en-US", "-w", str(out), text],
    "flite": lambda text, out: ["flite", "-t", text, "-o", str(out)],
    # ffmpeg built with libflite: same voice, no separate CLI needed. 16 kHz mono 16-bit PCM.
    "ffmpeg-flite": lambda text, out: ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                                       "-i", "flite=text='" + text.replace("'", "").replace(":", " ") + "'",
                                       "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out)],
}

# Preferred first: a neural voice the hosted ASR transcribes cleanly.
ENGINE_ORDER = ["edge-tts", "ffmpeg-flite", "espeak-ng", "espeak", "pico2wave", "flite"]


def _edge_cli() -> str | None:
    """The edge-tts CLI: $WATTNOT_EDGE_TTS (path or name) or ``edge-tts`` on PATH, else None."""
    cli = os.environ.get("WATTNOT_EDGE_TTS") or "edge-tts"
    return shutil.which(cli)


def _ffmpeg_has_flite() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return " flite " in r.stdout


def _engine_available(name: str) -> bool:
    if name == "edge-tts":
        return _edge_cli() is not None and shutil.which("ffmpeg") is not None
    if name == "ffmpeg-flite":
        return _ffmpeg_has_flite()
    return shutil.which(name) is not None


def pick_engine(preferred: str | None = None) -> str | None:
    """The chosen engine if available, else the first available engine in preference order."""
    if preferred:
        return preferred if _engine_available(preferred) else None
    for name in ENGINE_ORDER:
        if _engine_available(name):
            return name
    return None


def _synthesize_edge(text: str, out: Path, voice: str) -> None:
    """edge-tts (free neural voice) -> mp3, then ffmpeg -> 16 kHz mono s16le with lead/tail silence.

    The 300 ms of leading silence (adelay) and trailing pad (apad) matter: without a lead-in the
    hosted ASR clipped the first word. Online (edge-tts is the only network use here).
    """
    cli = _edge_cli()
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as mp3:
        subprocess.run([cli, "--voice", voice, "--text", text, "--write-media", mp3.name],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3.name,
                        "-af", "adelay=300,apad=pad_dur=0.3",
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _synthesize(engine: str, text: str, out: Path, voice: str = EDGE_DEFAULT_VOICE) -> None:
    if engine == "edge-tts":
        _synthesize_edge(text, out, voice)
        return
    subprocess.run(ENGINES[engine](text, out), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _add_datacenter_noise(wav_path: Path, *, seed: int, level: float = 0.06) -> None:
    """Mix low-level steady noise into a 16-bit PCM WAV in place (data-center hum + hiss).

    Real speech with real added noise; deterministic given the seed so the asset is reproducible.
    """
    with wave.open(str(wav_path), "rb") as w:
        n, sw, fr, nf = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        frames = w.readframes(nf)
    if sw != 2:  # the engines above emit 16-bit PCM; leave anything else untouched
        return
    rng = random.Random(seed)
    samples = list(struct.unpack("<%dh" % (len(frames) // 2), frames))
    peak = max((abs(s) for s in samples), default=1) or 1
    amp = int(peak * level)
    out = []
    for i, s in enumerate(samples):
        hum = amp * 0.5 * math.sin(2 * math.pi * 120.0 * (i / (fr * max(n, 1))))  # 120 Hz hum
        hiss = rng.uniform(-amp, amp)
        v = int(max(-32768, min(32767, s + hum + hiss)))
        out.append(v)
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(n)
        w.setsampwidth(sw)
        w.setframerate(fr)
        w.writeframes(struct.pack("<%dh" % len(out), *out))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="synthesize spoken-answer WAVs from question text")
    ap.add_argument("--engine", choices=ENGINE_ORDER, default=None,
                    help="force an engine (default: first available, edge-tts preferred)")
    ap.add_argument("--voice", default=EDGE_DEFAULT_VOICE, help="edge-tts voice (ignored by others)")
    a = ap.parse_args([] if argv is None else argv)

    engine = pick_engine(a.engine)
    if engine is None:
        if a.engine:
            print(f"engine {a.engine!r} is not available. edge-tts needs the CLI on PATH (or "
                  f"$WATTNOT_EDGE_TTS) and ffmpeg; the offline engines need their CLI on PATH. "
                  f"Nothing was changed.", file=sys.stderr)
        else:
            print("no TTS found. Put edge-tts (free neural, online) or an offline engine "
                  "(espeak-ng, espeak, pico2wave, flite) on PATH. Nothing was changed.",
                  file=sys.stderr)
        return 3

    online = engine == "edge-tts"
    where = "free neural voice, online" if online else "offline"
    manifest = json.loads((ASSETS / "answers.json").read_text(encoding="utf-8"))
    items = manifest["items"]
    print(f"synthesizing {len(items)} WAVs from question text with {engine} ({where}, no API key)")
    transcripts: dict[str, str] = {}
    for idx, item in enumerate(items):
        text = str(item["question"])
        out = ASSETS / item["file"]
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            _synthesize(engine, text, Path(tmp.name), a.voice)
            shutil.copyfile(tmp.name, out)
        if item.get("noise") == "datacenter":
            _add_datacenter_noise(out, seed=1000 + idx)
        transcripts[item["id"]] = text  # the spoken content is the question text itself
        print(f"  {item['id']}: {out.stat().st_size} B  {text!r}")

    source = f"TTS-{engine}"
    voice_note = f", voice {a.voice}" if online else ""
    engine_desc = f"{engine} (free neural TTS{voice_note}, online, no API key)" if online \
        else f"{engine} (offline TTS, no API)"
    (ASSETS / "transcripts.json").write_text(json.dumps({
        "spec": "wattnot.spokenanswer.transcripts/1",
        "label": "SYNTHETIC",
        "source": source,
        "note": (f"Transcripts of the spoken takes. The audio was synthesized from the question "
                 f"text with {engine_desc}, so each transcript IS the question text. SYNTHETIC - a "
                 f"machine voice, not a human. The gate feeds this text to the served model "
                 f"(Glimmer) to answer."),
        "transcriber": source,
        "transcripts": transcripts,
    }, indent=2) + "\n", encoding="utf-8")

    manifest["source"] = source
    manifest["source_note"] = (f"SYNTHETIC audio synthesized from the question text with "
                               f"{engine_desc}; a machine voice, not a human. The transcript of "
                               f"each take is the question text.")
    (ASSETS / "answers.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"done; wrote {len(items)} real WAVs + transcripts.json, stamped source={source!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
