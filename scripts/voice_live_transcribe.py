"""One LIVE Muse Voice Transcribe call on a short synthetic (TTS, ffmpeg flite) clip. No human voice."""
import base64, hashlib, json, sys, time, urllib.request, uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wattnot.meta.client import DEFAULT_BASE, load_key
from wattnot.meta.ledger import SpendLedger
wav = ROOT / "data/voice/tts-question.wav"
audio = wav.read_bytes()
key = load_key()
MODEL = "muse-voice-transcribe-1.0"
def multipart():
    b = uuid.uuid4().hex
    reqj = json.dumps({"model": MODEL, "audioEncoding": "WAV", "sampleRateHertz": 16000})
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="request"\r\nContent-Type: application/json\r\n\r\n{reqj}\r\n'.encode(),
             f'--{b}\r\nContent-Disposition: form-data; name="audio"; filename="q.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode() + audio + b"\r\n",
             f"--{b}--\r\n".encode()]
    return b"".join(parts), f"multipart/form-data; boundary={b}"
body, ct = multipart()
req = urllib.request.Request(f"{DEFAULT_BASE}/asr/transcribe", data=body,
                             headers={"Authorization": f"Bearer {key}", "Content-Type": ct})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        st, raw = r.status, r.read(); hdr = dict(r.headers)
except urllib.error.HTTPError as e:
    st, raw, hdr = e.code, e.read(), dict(e.headers)
lat = round(time.time() - t0, 2)
print(st, raw[:600])
if st != 200:
    sys.exit(1)
d = json.loads(raw)
dur_s = (len(audio) - 44) / 32000
usd = round(dur_s / 3600 * 0.18, 6)
led = SpendLedger()
eid, _ = led.reserve({"model": "muse-spark-1.3-contributor", "max_tokens": 1, "messages": []}, purpose="voice:transcribe-live")
led.settle(eid, {"prompt_tokens": 0, "completion_tokens": 0}, model="muse-spark-1.3-contributor", status="ok",
           note=f"voice transcribe {dur_s:.1f}s ~${usd} (ESTIMATE at $0.18/h)")
doc = {"section": "voice_transcribe_live", "label": "LIVE", "model": MODEL,
       "audio": {"path": "data/voice/tts-question.wav", "sha256": hashlib.sha256(audio).hexdigest(),
                 "duration_s": round(dur_s, 2), "source": "SYNTHETIC: ffmpeg flite TTS (voice slt), not a human recording",
                 "script": "Why was three hundred twenty five watts rejected?"},
       "transcript": d.get("transcript"), "session_id": d.get("sessionId"), "audio_duration_ms": d.get("audioDurationMs"),
       "request_id_header": hdr.get("x-request-id") or hdr.get("X-Request-Id"), "usage": d.get("usage"),
       "latency_s": lat, "spend_usd_estimate": usd, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
(ROOT / "receipts/run2/voice_transcribe_live.json").write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps(doc, indent=1))
