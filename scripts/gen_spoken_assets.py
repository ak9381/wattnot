#!/usr/bin/env python3
"""Generate REAL spoken-answer WAVs from the question text with an OFFLINE TTS.

This replaces the 364-byte placeholder stubs with real audio so the
watts-per-spoken-answer gate runs on real recordings, not stubs. It makes NO
paid call and needs no API key: it speaks each question with an offline TTS that
is already on this Linux box. The spoken content IS the question text, so the
transcript of every take is exactly that text (no separate transcription step).

    python scripts/gen_spoken_assets.py            # synthesize all WAVs + transcripts

It picks the first available engine of: espeak-ng, espeak, pico2wave, flite. If
none is installed it stops with a clear message and changes nothing (it never
writes a fake WAV). Do NOT install system packages with sudo; if you have a venv
TTS instead, put its CLI on PATH.

For each manifest item it writes ``data/voice/spoken/<file>.wav``. The
``datacenter`` takes get low-level noise mixed in (real speech + real added
noise), so the clean/noisy split is genuine. It then rewrites ``transcripts.json``
(transcript = the question text) and stamps the manifest ``source`` honestly as
``TTS-<engine>`` so the no-mock guard accepts the live run.

Importing this module does nothing until ``main()`` runs.
"""

from __future__ import annotations

import json
import math
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

# Engine -> argv builder: (text, out_wav) -> command. All are offline and free.
ENGINES = {
    "espeak-ng": lambda text, out: ["espeak-ng", "-v", "en", "-s", "150", "-w", str(out), text],
    "espeak": lambda text, out: ["espeak", "-v", "en", "-s", "150", "-w", str(out), text],
    "pico2wave": lambda text, out: ["pico2wave", "-l", "en-US", "-w", str(out), text],
    "flite": lambda text, out: ["flite", "-t", text, "-o", str(out)],
}


def pick_engine() -> str | None:
    """The first offline TTS CLI on PATH, or None."""
    for name in ENGINES:
        if shutil.which(name):
            return name
    return None


def _synthesize(engine: str, text: str, out: Path) -> None:
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
    engine = pick_engine()
    if engine is None:
        print("no offline TTS found. Install one and put it on PATH (no sudo needed if it is a "
              "venv CLI): tried espeak-ng, espeak, pico2wave, flite. Nothing was changed.",
              file=sys.stderr)
        return 3

    manifest = json.loads((ASSETS / "answers.json").read_text(encoding="utf-8"))
    items = manifest["items"]
    print(f"synthesizing {len(items)} WAVs from question text with {engine} (offline, no API)")
    transcripts: dict[str, str] = {}
    for idx, item in enumerate(items):
        text = str(item["question"])
        out = ASSETS / item["file"]
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            _synthesize(engine, text, Path(tmp.name))
            shutil.copyfile(tmp.name, out)
        if item.get("noise") == "datacenter":
            _add_datacenter_noise(out, seed=1000 + idx)
        transcripts[item["id"]] = text  # the spoken content is the question text itself
        print(f"  {item['id']}: {out.stat().st_size} B  {text!r}")

    source = f"TTS-{engine}"
    (ASSETS / "transcripts.json").write_text(json.dumps({
        "spec": "wattnot.spokenanswer.transcripts/1",
        "label": "SYNTHETIC",
        "source": source,
        "note": (f"Transcripts of the spoken takes. The audio was synthesized from the question "
                 f"text with {engine} (offline TTS, no API), so each transcript IS the question "
                 f"text. The gate feeds this text to the served model (Glimmer) to answer."),
        "transcriber": source,
        "transcripts": transcripts,
    }, indent=2) + "\n", encoding="utf-8")

    manifest["source"] = source
    manifest["source_note"] = (f"Real audio synthesized from the question text with {engine} "
                               f"(offline TTS). The transcript of each take is the question text.")
    (ASSETS / "answers.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"done; wrote {len(items)} real WAVs + transcripts.json, stamped source={source!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
