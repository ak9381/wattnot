// Ask about a card. Press Ask (the voice guide's → button) with an empty box: the dashboard's
// cards glow; click one and Muse Spark explains it as text, with the card highlighted and
// scrolled into view and the phrase it explains marked. A spoken or typed question that names
// a card's title goes to that card the same way. No spoken output. POST /api/dash/ask/card;
// a MOCK answer says MOCK. Pure helpers are node-tested; the DOM part runs only in a browser.

export const CARD_SEL = [".mx-card", ".eucard", ".bento-card", ".card", ".hotspot-card", "[data-ask-card]", "section.glass", ".glass.wn-bento", ".wn-bento"].join(",");

export function cardTitle(el) {
  const h = el?.querySelector?.("h1,h2,h3,.title,[class*=title]");
  return (h?.textContent || el?.getAttribute?.("aria-label") || el?.id || "card").trim().replace(/\s+/g, " ").slice(0, 160);
}

export function cardText(el, max = 2500) {
  return String(el?.innerText ?? el?.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, max);
}

// Outermost matches only, so a card inside a section is not offered twice.
export function pickCards(all) {
  return all.filter((el) => el && !all.some((o) => o !== el && o.contains?.(el)));
}

const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();

// The card whose title appears in the question (longest title wins); null if none.
export function matchCard(query, cards) {
  const q = norm(query);
  let best = null;
  for (const c of cards) {
    const t = norm(String(c.title || "").split(/ [·|:(—-] /)[0]);   // "Run matrix · 0 of 12" -> "run matrix"
    if (t.length >= 4 && q.includes(t) && (!best || t.length > best._n)) best = { ...c, _n: t.length };
  }
  if (best) delete best._n;
  return best;
}

export function answerFor(res, title) {
  return { intent: "EXPLAIN", label: res.label || "ERROR",
           text: `${title}: ${res.explanation || "No explanation was generated."}${res.label === "LIVE" ? ` (Muse Spark, ${res.model || "muse-spark"})` : ""}` };
}

export async function explainCard(el, fetchFn = fetch) {
  const title = cardTitle(el);
  try {
    const r = await fetchFn("/api/dash/ask/card", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ card_id: el.id || title, title, text: cardText(el) }) });
    const j = await r.json().catch(() => ({ label: "ERROR", explanation: `Ask failed (HTTP ${r.status}).` }));
    return { res: j, title };
  } catch {
    return { res: { label: "ERROR", explanation: "Ask failed (no connection to the dashboard API).", focus: "" }, title };
  }
}

// Wrap the first text match of `phrase` inside el in <mark class="wn-ask-mark">. Returns the mark.
export function markPhrase(el, phrase, doc = globalThis.document) {
  if (!el || !phrase || !doc?.createTreeWalker) return null;
  const want = phrase.toLowerCase();
  const w = doc.createTreeWalker(el, 4 /* NodeFilter.SHOW_TEXT */);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const i = n.nodeValue.toLowerCase().indexOf(want);
    if (i < 0) continue;
    const r = doc.createRange(); r.setStart(n, i); r.setEnd(n, i + phrase.length);
    const m = doc.createElement("mark"); m.className = "wn-ask-mark";
    try { r.surroundContents(m); return m; } catch { return null; }
  }
  return null;
}

function clearMarks(doc) {
  doc.querySelectorAll(".wn-ask-mark").forEach((m) => m.replaceWith(...m.childNodes));
  doc.querySelectorAll(".wn-ask-focus").forEach((c) => c.classList.remove("wn-ask-focus"));
}

export function stopGlow(doc = globalThis.document) {
  doc?.querySelectorAll?.(".wn-ask-glow").forEach((c) => { c.classList.remove("wn-ask-glow"); });
  doc?.body?.classList?.remove("wn-ask-picking");
  if (doc?.__wnAskHandler) { doc.removeEventListener("click", doc.__wnAskHandler, true); doc.__wnAskHandler = null; }
}

export async function showExplanation(el, onAnswer, fetchFn = fetch, doc = globalThis.document) {
  clearMarks(doc);
  el.classList.add("wn-ask-focus");
  el.scrollIntoView?.({ behavior: "smooth", block: "center" });
  onAnswer({ intent: "EXPLAIN", label: "…", text: `Explaining ${cardTitle(el)}…` });
  const { res, title } = await explainCard(el, fetchFn);
  const m = markPhrase(el, res.focus, doc);
  m?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  onAnswer(answerFor(res, title));
  return res;
}

export function listCards(doc = globalThis.document) {
  return pickCards([...doc.querySelectorAll(CARD_SEL)]).filter((el) => !el.closest("#wattnot-voiceguide-slot") && el.offsetParent !== null);
}

// Ask mode: glow every card; the next click on a card explains it (Esc or a click elsewhere cancels).
export function startAskCards(onAnswer, fetchFn = fetch, doc = globalThis.document) {
  stopGlow(doc);
  const cards = listCards(doc);
  if (!cards.length) { onAnswer({ intent: "EXPLAIN", label: "MOCK", text: "No cards on this page to explain." }); return 0; }
  cards.forEach((c) => c.classList.add("wn-ask-glow"));
  doc.body.classList.add("wn-ask-picking");
  onAnswer({ intent: "EXPLAIN", label: "PICK", text: "Pick a glowing card and WattNot will explain it." });
  const h = (ev) => {
    if (ev.target.closest?.("#wattnot-voiceguide-slot")) return;
    const card = cards.find((c) => c.contains(ev.target));
    ev.preventDefault(); ev.stopPropagation();
    stopGlow(doc);
    if (card) showExplanation(card, onAnswer, fetchFn, doc);
  };
  doc.__wnAskHandler = h;
  doc.addEventListener("click", h, true);
  doc.addEventListener("keydown", (e) => { if (e.key === "Escape") stopGlow(doc); }, { once: true });
  return cards.length;
}

// A typed or transcribed question that names a card: explain that card. Returns true if handled.
export function askByName(query, onAnswer, fetchFn = fetch, doc = globalThis.document) {
  if (typeof doc?.querySelectorAll !== "function") return false;
  const cards = listCards(doc).map((el) => ({ el, title: cardTitle(el) }));
  const hit = matchCard(query, cards);
  if (!hit) return false;
  showExplanation(hit.el, onAnswer, fetchFn, doc);
  return true;
}
