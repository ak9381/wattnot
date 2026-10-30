#!/usr/bin/env python3
"""ONE-TIME PAID script: transcribe the spoken-answer recordings with Muse Voice.

This is the only place a paid transcription call lives. It is NEVER run by the
test suite or any automated flow — it needs an explicit go-ahead
(``WATTNOT_GEN_SPOKEN_GO=1``) and a real API key, and it costs money.

It reads the SYNTHETIC recordings listed in ``data/voice/spoken/answers.json``,
calls ``POST {META_BASE_URL}/asr/transcribe`` with ``muse-voice-transcribe-1.0``
for each ``.wav``, and writes the cached text to ``transcripts.json`` next to the
manifest (both the clean and the data-center-noise take of a question transcribe
to the same text). The spoken gate later feeds this cached text to the served
model (Glimmer) to answer — no transcription happens at test time.

    WATTNOT_GEN_SPOKEN_GO=1 META_API_KEY=... python scripts/gen_spoken_assets.py

Importing this module does nothing: no key is read and no call is made until
``main()`` runs. The committed ``.wav`` files and ``transcripts.json`` are offline
placeholders so the gate and its tests work without ever running this.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "data" / "voice" / "spoken"
MODEL = "muse-voice-transcribe-1.0"


def _transcribe(base_url: str, key: str, wav: Path, timeout: float = 120) -> str:
    """POST one recording to Muse Voice Transcribe and return the text."""
    import mimetypes
    import uuid

    boundary = f"----wattnot{uuid.uuid4().hex}"
    audio = wav.read_bytes()
    ctype = mimetypes.guess_type(wav.name)[0] or "audio/wav"
    parts = [
        f"--{boundary}\r\n".encode()
        + f'Content-Disposition: form-data; name="model"\r\n\r\n{MODEL}\r\n'.encode(),
        f"--{boundary}\r\n".encode()
        + f'Content-Disposition: form-data; name="file"; filename="{wav.name}"\r\n'.encode()
        + f"Content-Type: {ctype}\r\n\r\n".encode() + audio + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/asr/transcribe", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        data = json.loads(r.read() or b"{}")
    return str(data.get("text", "")).strip()


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("WATTNOT_GEN_SPOKEN_GO") != "1":
        print("refusing: this is a PAID one-time script. Set WATTNOT_GEN_SPOKEN_GO=1 to run.",
              file=sys.stderr)
        return 2
    # Imported lazily so this module stays importable without the meta package wired.
    from wattnot.meta.client import load_key
    key = load_key()
    if not key:
        print("no API key: set META_API_KEY or log in with `muse login`", file=sys.stderr)
        return 2
    base_url = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")

    manifest = json.loads((ASSETS / "answers.json").read_text(encoding="utf-8"))
    items = manifest["items"]
    print(f"PAID: transcribing {len(items)} recordings with {MODEL} at {base_url}")
    transcripts: dict[str, str] = {}
    for item in items:
        wav = ASSETS / item["file"]
        if not wav.exists():
            print(f"  skip {item['id']}: missing {wav.name}", file=sys.stderr)
            continue
        text = _transcribe(base_url, key, wav)
        transcripts[item["id"]] = text
        print(f"  {item['id']}: {text!r}")

    out = {
        "spec": "wattnot.spokenanswer.transcripts/1",
        "label": "SYNTHETIC",
        "note": ("Cached output of Muse Voice Transcribe over the SYNTHETIC recordings, written "
                 "once by scripts/gen_spoken_assets.py (a one-time paid script). The gate feeds "
                 "this text to the served model (Glimmer) to answer; no transcription at test time."),
        "transcriber": MODEL,
        "transcripts": transcripts,
    }
    (ASSETS / "transcripts.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"done; rewrote transcripts.json ({len(transcripts)} SYNTHETIC transcripts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
