import { answerFromReceipts, loadReceiptDocs } from "./receiptanswers.js";
// Voice Guide Self-Mounting Component - Meta Tools v5

export function classifyLocalIntent(text) {
  const t = (text || "").toLowerCase().trim();
  if (t.includes("configure") || t.includes("setup") || t.includes("prefill") || t.includes("prepare")) {
    return "configure";
  }
  if (t.includes("mean") || t.includes("explain") || t.includes("tell me about this number") || t.includes("what is this")) {
    return "explain";
  }
  if (t.includes("compare") || t.includes("report") || t.includes("latest") || t.includes("last night")) {
    return "navigate";
  }
  return "answer";
}

export function quickAckForQuery(text) {
  const intent = classifyLocalIntent(text);
  if (intent === "navigate") {
    return {
      intent,
      ack: text.toLowerCase().includes("compare") ? "Opening Compare view..." : "Resolving latest report route...",
      label: "MOCK",
    };
  }
  if (intent === "configure") {
    return {
      intent,
      ack: "Preparing run setup pre-fill...",
      label: "MOCK",
    };
  }
  if (intent === "explain") {
    return {
      intent,
      ack: "Locating element on page...",
      label: "MOCK",
    };
  }
  return {
    intent,
    ack: "Querying signed receipts and EU report...",
    label: "MOCK",
  };
}

export function buildVoiceGuideDOM() {
  const container = document.createElement("div");
  container.className = "vg-slot-container";
  container.id = "wattnot-voiceguide-slot";

  container.innerHTML = `
    <div class="vg-bar">
      <button class="vg-mic-btn" id="vg-mic-btn" title="Hold/Click to speak with Muse Voice" aria-label="Voice guide microphone">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
          <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
        </svg>
      </button>
      <button class="vg-mic-btn vg-live-btn" id="vg-live-btn" title="Replay the real Muse Voice Transcribe result (synthetic TTS clip)" aria-label="Replay live transcript">&#9654;</button>
      <input type="text" class="vg-input" id="vg-text-input" placeholder="Ask Voice Guide (type, or press ▶ for the live clip)..." />
      <button class="vg-submit-btn" id="vg-submit-btn" title="Submit query">&rarr;</button>
      <span class="vg-status-bubble" id="vg-status-bubble">Thinking…</span>
    </div>
    <div class="vg-panel" id="vg-panel">
      <div class="vg-panel-header">
        <span class="vg-panel-intent" id="vg-panel-intent">Intent</span>
        <span class="vg-panel-tag" id="vg-panel-tag">MOCK</span>
        <button class="vg-panel-close" id="vg-panel-close">&times;</button>
      </div>
      <div class="vg-panel-body" id="vg-panel-body">...</div>
      <div class="vg-panel-route" id="vg-panel-route" style="display:none"></div>
    </div>
  `;
  return container;
}

// Decode any recorded blob and re-encode as 16 kHz mono 16-bit PCM WAV.
export async function toWav16k(blob) {
  const AC = globalThis.OfflineAudioContext || globalThis.webkitOfflineAudioContext;
  const ctxAC = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!AC || !ctxAC) throw new Error("no AudioContext");
  const dec = await new ctxAC().decodeAudioData(await blob.arrayBuffer());
  const rate = 16000, n = Math.max(1, Math.ceil(dec.duration * rate));
  const off = new AC(1, n, rate);
  const src = off.createBufferSource(); src.buffer = dec; src.connect(off.destination); src.start();
  const pcm = (await off.startRendering()).getChannelData(0);
  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const w = (o, t) => { for (let i = 0; i < t.length; i++) out.setUint8(o + i, t.charCodeAt(i)); };
  w(0, "RIFF"); out.setUint32(4, 36 + pcm.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
  out.setUint32(24, rate, true); out.setUint32(28, rate * 2, true); out.setUint16(32, 2, true);
  out.setUint16(34, 16, true); w(36, "data"); out.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) {
    const v = Math.max(-1, Math.min(1, pcm[i])); out.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  return new Uint8Array(out.buffer);
}

export function mountVoiceGuide(customMountEl = null) {
  if (typeof document === "undefined") return null;
  if (document.getElementById("wattnot-voiceguide-slot")) return null;

  const target = customMountEl || document.getElementById("wn-mic-slot") || document.querySelector("#wn-slot-wrap") || document.querySelector("header") || document.body;
  if (!target) return null;

  const dom = buildVoiceGuideDOM();
  target.appendChild(dom);

  const micBtn = dom.querySelector("#vg-mic-btn");
  const inputEl = dom.querySelector("#vg-text-input");
  const submitBtn = dom.querySelector("#vg-submit-btn");
  const statusBubble = dom.querySelector("#vg-status-bubble");
  const panel = dom.querySelector("#vg-panel");
  const panelIntent = dom.querySelector("#vg-panel-intent");
  const panelTag = dom.querySelector("#vg-panel-tag");
  const panelBody = dom.querySelector("#vg-panel-body");
  const panelRoute = dom.querySelector("#vg-panel-route");
  const panelClose = dom.querySelector("#vg-panel-close");

  let mediaRecorder = null;
  let mediaStream = null;
  let audioChunks = [];
  let isRecording = false;

  function showStatus(text) {
    statusBubble.textContent = text;
    statusBubble.style.display = "inline-block";
  }

  function hideStatus() {
    statusBubble.style.display = "none";
  }

  function routeLine(label, value) {
    // API data is rendered as text, never as markup (receipt ids etc. could carry HTML).
    panelRoute.textContent = "";
    const strong = document.createElement("strong");
    strong.textContent = label;
    panelRoute.appendChild(strong);
    panelRoute.appendChild(document.createTextNode(` ${value}`));
  }

  function displayResponse(data) {
    panelIntent.textContent = data.intent || "ANSWER";
    panelTag.textContent = data.label || "MOCK";
    panelBody.textContent = data.text || data.ack || "";

    if (data.route) {
      panelRoute.style.display = "block";
      routeLine("Route:", data.route);
    } else if (data.prefill) {
      panelRoute.style.display = "block";
      const caps = Array.isArray(data.prefill.caps_w) ? `${data.prefill.caps_w.join(", ")}W` : "caps pending";
      routeLine("Prefill:", `${data.prefill.stack || "vllm"} (${caps})`);
    } else {
      panelRoute.style.display = "none";
    }
    panel.classList.add("active");
  }

  // No API (e.g. GitHub Pages): answer from the signed RUN2 receipt and addenda, labelled REPLAY.
  async function groundedAnswer(query) {
    const docs = await loadReceiptDocs();
    return answerFromReceipts(query, docs);
  }

  async function handleQuery(query) {
    if (!query || !query.trim()) return;

    // Fast quick ack within <300ms
    const quick = quickAckForQuery(query);
    showStatus("Thinking…");
    panelIntent.textContent = quick.intent;
    panelTag.textContent = quick.label;
    panelBody.textContent = quick.ack;
    panelRoute.style.display = "none";
    panel.classList.add("active");

    if (quick.intent === "explain" && typeof window !== "undefined" && window.WattNotExplain) {
      const expRes = window.WattNotExplain.handleExplainQuery(query);
      if (expRes && expRes.status !== "none") {
        displayResponse({
          intent: "explain",
          label: expRes.candidate?.label || "MOCK",
          text: expRes.text,
        });
        hideStatus();
        return;
      }
    }

    try {
      const res = await fetch("/api/voiceguide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      if (res.ok) {
        const full = await res.json();
        if (full.intent === "explain" && typeof window !== "undefined" && window.WattNotExplain) {
          window.WattNotExplain.handleExplainQuery(query);
        }
        displayResponse(full);
      } else {
        const grounded = await groundedAnswer(query);
        if (grounded) { displayResponse(grounded); return; }
        // Fallback local response
        displayResponse({
          intent: quick.intent,
          label: "MOCK",
          text: `[Quick Path MOCK] ${quick.ack} Query '${query}' processed locally.`,
        });
      }
    } catch {
      const grounded = await groundedAnswer(query).catch(() => null);
      if (grounded) { displayResponse(grounded); return; }
      // Offline fallback
      displayResponse({
        intent: quick.intent,
        label: "MOCK",
        text: `[Offline MOCK] ${quick.ack} Grounded action prepared for '${query}'.`,
      });
    } finally {
      hideStatus();
    }
  }

  panelClose.addEventListener("click", () => {
    panel.classList.remove("active");
  });

  // Replay of one real Muse Voice Transcribe call (LIVE transcript of a synthetic TTS clip),
  // saved as a signed addendum to the RUN2 receipt. The answer that follows keeps its own label.
  const liveBtn = dom.querySelector("#vg-live-btn");
  if (liveBtn) liveBtn.addEventListener("click", async () => {
    showStatus("Listening…");
    try {
      const url = new URL("../receipts/run2/rcpt_20261027_run2_cap350.voice_transcribe.json", import.meta.url);
      const doc = await (await fetch(url)).json();
      const v = doc.data || {};
      try { new Audio(new URL("media/tts-question.wav", import.meta.url)).play(); } catch { /* autoplay blocked */ }
      displayResponse({ intent: "TRANSCRIPT", label: "LIVE",
        text: `Muse Voice Transcribe heard: “${v.transcript}” (synthetic text-to-speech clip, ${v.audio?.duration_s}s; session ${v.session_id}).` });
      inputEl.value = v.transcript || "";
      hideStatus();
      setTimeout(() => handleQuery(inputEl.value), 900);
    } catch {
      hideStatus();
      displayResponse({ intent: "TRANSCRIPT", label: "MOCK", text: "The live transcript file could not be loaded. Type your question instead." });
    }
  });

  submitBtn.addEventListener("click", () => {
    const val = inputEl.value;
    inputEl.value = "";
    handleQuery(val);
  });

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const val = inputEl.value;
      inputEl.value = "";
      handleQuery(val);
    }
  });

  function stopStream() {
    if (mediaStream) {
      for (const track of mediaStream.getTracks()) track.stop();
      mediaStream = null;
    }
  }

  async function sendRecording() {
    const blob = new Blob(audioChunks, { type: audioChunks[0]?.type || "audio/webm" });
    audioChunks = [];
    if (!blob.size) {
      displayResponse({ intent: "answer", label: "MOCK",
        text: "No audio was captured. Type your question instead." });
      hideStatus();
      return;
    }
    try {
      // Muse Voice Transcribe takes WAV; MediaRecorder gives webm/ogg, so re-encode.
      const buf = await toWav16k(blob).catch(() => new Uint8Array(0));
      if (!buf.length) throw new Error("wav encode failed");
      let bin = "";
      for (const b of buf) bin += String.fromCharCode(b);
      const res = await fetch("/api/voiceguide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio_b64: btoa(bin) }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => null);
        if (j && j.text) { displayResponse(j); return; }
        throw new Error(`HTTP ${res.status}`);
      }
      const out = await res.json();
      if (out && out.transcript) inputEl.value = out.transcript;
      displayResponse(out);
    } catch {
      // No transcription available offline: say so rather than invent a query.
      displayResponse({ intent: "answer", label: "MOCK",
        text: "[Offline MOCK] Transcription is unavailable without the API. Type your question instead." });
    } finally {
      hideStatus();
    }
  }

  micBtn.addEventListener("click", async () => {
    if (!isRecording) {
      // Instant 'Listening…' state
      isRecording = true;
      micBtn.classList.add("listening");
      showStatus("Listening…");

      if (typeof navigator !== "undefined" && navigator.mediaDevices?.getUserMedia) {
        try {
          mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
          audioChunks = [];
          mediaRecorder = new MediaRecorder(mediaStream);
          mediaRecorder.addEventListener("dataavailable", (e) => {
            if (e.data && e.data.size) audioChunks.push(e.data);
          });
          mediaRecorder.addEventListener("stop", () => {
            stopStream();
            sendRecording();
          });
          mediaRecorder.start();
        } catch (err) {
          stopStream();
          isRecording = false;
          micBtn.classList.remove("listening");
          hideStatus();
          // Only a genuinely absent device is "not found". A denial, a security
          // block, or anything else means the mic is unavailable to us.
          const notFound = err && err.name === "NotFoundError";
          displayResponse({ intent: "answer", label: "MOCK",
            text: notFound ? "No microphone was found. Type your question instead."
                           : "Microphone permission was blocked or the mic is unavailable. Type your question instead." });
        }
      } else {
        isRecording = false;
        micBtn.classList.remove("listening");
        hideStatus();
        displayResponse({ intent: "answer", label: "MOCK",
          text: "This browser cannot record audio here. Type your question instead." });
      }
    } else {
      // Instant 'Thinking…' state
      isRecording = false;
      micBtn.classList.remove("listening");
      showStatus("Thinking…");

      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop(); // 'stop' handler posts the recording and releases the mic
      } else {
        stopStream();
        displayResponse({ intent: "answer", label: "MOCK",
          text: "Microphone is unavailable here. Type your question instead." });
        hideStatus();
      }
    }
  });

  return dom;
}

// Auto self-mount if running in browser script tag
if (typeof window !== "undefined" && typeof document !== "undefined") {
  window.WattNotVoiceGuide = {
    mountVoiceGuide,
    buildVoiceGuideDOM,
    classifyLocalIntent,
    quickAckForQuery,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => mountVoiceGuide());
  } else {
    mountVoiceGuide();
  }
}
