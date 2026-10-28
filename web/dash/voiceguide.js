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
      <input type="text" class="vg-input" id="vg-text-input" placeholder="Ask Voice Guide (MOCK)..." />
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
        // Fallback local response
        displayResponse({
          intent: quick.intent,
          label: "MOCK",
          text: `[Quick Path MOCK] ${quick.ack} Query '${query}' processed locally.`,
        });
      }
    } catch {
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
      const buf = new Uint8Array(await blob.arrayBuffer());
      let bin = "";
      for (const b of buf) bin += String.fromCharCode(b);
      const res = await fetch("/api/voiceguide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio_b64: btoa(bin) }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      displayResponse(await res.json());
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
        } catch {
          stopStream();
          // Mic permission or unavailable, stay in listening simulation
        }
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
